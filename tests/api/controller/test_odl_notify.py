import types

import flask
import pytest
from flask import Response
from freezegun import freeze_time

from api.odl import ODLAPI
from api.odl2 import ODL2API
from api.problem_details import (
    INVALID_INPUT,
    INVALID_LOAN_FOR_ODL_NOTIFICATION,
    NO_ACTIVE_LOAN,
)
from core.model import Collection, License, Loan, Patron
from core.util.datetime_helpers import utc_now
from tests.fixtures.api_controller import ControllerFixture
from tests.fixtures.database import DatabaseTransactionFixture
from tests.fixtures.odl import OPDS2WithODLApiFixture
from tests.fixtures.problem_detail import raises_problem_detail


class ODLFixture:
    def __init__(self, db: DatabaseTransactionFixture):
        self.db = db
        self.library = self.db.default_library()

        """Create a mock ODL collection to use in tests."""
        self.collection, _ = Collection.by_name_and_protocol(
            self.db.session, "Test ODL Collection", ODLAPI.label()
        )
        self.collection.integration_configuration.settings_dict = {
            "username": "a",
            "password": "b",
            "url": "http://metadata",
            "external_integration_id": "http://odl",
            Collection.DATA_SOURCE_NAME_SETTING: "Feedbooks",
        }
        self.collection.libraries.append(self.library)
        self.work = self.db.work(with_license_pool=True, collection=self.collection)
        self.license = self.create_license()
        self.patron, self.patron_identifier = self.create_patron()

        def setup(self, available, concurrency, left=None, expires=None):
            self.checkouts_available = available
            self.checkouts_left = left
            self.terms_concurrency = concurrency
            self.expires = expires
            self.license_pool.update_availability_from_licenses()

        self.pool = self.work.license_pools[0]
        self.license = self.db.license(
            self.pool,
            checkout_url="https://loan.feedbooks.net/loan/get/{?id,checkout_id,expires,patron_id,notification_url,hint,hint_url}",
            checkouts_available=1,
            terms_concurrency=1,
        )
        types.MethodType(setup, self.license)
        self.pool.update_availability_from_licenses()
        self.patron = self.db.patron()
        self.loan_status_document = OPDS2WithODLApiFixture.loan_status_document

    @staticmethod
    def integration_protocol():
        return ODLAPI.label()

    def create_license(self, collection: Collection | None = None) -> License:
        collection = collection or self.collection
        if not collection.data_source:
            collection.data_source = "testing"  # type: ignore
        assert collection.data_source is not None
        pool = self.db.licensepool(
            None, collection=collection, data_source_name=collection.data_source.name
        )
        license = self.db.license(
            pool,
            checkout_url="https://provider.net/loan",
            checkouts_available=1,
            terms_concurrency=1,
        )
        pool.update_availability_from_licenses()
        return license

    def create_patron(self) -> tuple[Patron, str]:
        patron = self.db.patron()
        data_source = self.collection.data_source
        assert data_source is not None
        patron_identifier = patron.identifier_to_remote_service(data_source)
        return patron, patron_identifier

    def create_loan(
        self, license: License | None = None, patron: Patron | None = None
    ) -> Loan:
        if license is None:
            license = self.license
        if patron is None:
            patron = self.patron
        license.checkout()
        loan, _ = license.loan_to(patron)
        loan.external_identifier = self.db.fresh_str()
        return loan


@pytest.fixture(scope="function")
def odl_fixture(db: DatabaseTransactionFixture) -> ODLFixture:
    return ODLFixture(db)


@freeze_time()
class TestODLNotificationController:
    """Test that an ODL distributor can notify the circulation manager
    when a loan's status changes."""

    @pytest.mark.parametrize(
        "protocol",
        [
            pytest.param(ODLAPI.label(), id="ODL 1.x collection"),
            pytest.param(ODL2API.label(), id="ODL 2.x collection"),
        ],
    )
    def test__get_loan(
        self, protocol, controller_fixture: ControllerFixture, odl_fixture: ODLFixture
    ) -> None:
        db = controller_fixture.db

        odl_fixture.collection.integration_configuration.protocol = protocol

        patron1, patron_id_1 = odl_fixture.create_patron()
        patron2, patron_id_2 = odl_fixture.create_patron()
        patron3, patron_id_3 = odl_fixture.create_patron()

        license1 = odl_fixture.create_license()
        license2 = odl_fixture.create_license()
        license3 = odl_fixture.create_license()

        loan1 = odl_fixture.create_loan(license=license1, patron=patron1)
        loan2 = odl_fixture.create_loan(license=license2, patron=patron1)
        loan3 = odl_fixture.create_loan(license=license3, patron=patron2)

        # We get the correct loan for each patron and license.
        assert (
            controller_fixture.manager.odl_notification_controller._get_loan(
                patron_id_1, license1.identifier
            )
            == loan1
        )
        assert (
            controller_fixture.manager.odl_notification_controller._get_loan(
                patron_id_1, license2.identifier
            )
            == loan2
        )
        assert (
            controller_fixture.manager.odl_notification_controller._get_loan(
                patron_id_2, license3.identifier
            )
            == loan3
        )

        # We get None if the patron doesn't have a loan for the license.
        assert (
            controller_fixture.manager.odl_notification_controller._get_loan(
                patron_id_1, license3.identifier
            )
            is None
        )
        assert (
            controller_fixture.manager.odl_notification_controller._get_loan(
                patron_id_2, license1.identifier
            )
            is None
        )
        assert (
            controller_fixture.manager.odl_notification_controller._get_loan(
                patron_id_3, license1.identifier
            )
            is None
        )

        # We get None if the patron or license identifiers are None.
        assert (
            controller_fixture.manager.odl_notification_controller._get_loan(
                None, license1.identifier
            )
            is None
        )
        assert (
            controller_fixture.manager.odl_notification_controller._get_loan(
                patron_id_1, None
            )
            is None
        )
        assert (
            controller_fixture.manager.odl_notification_controller._get_loan(None, None)
            is None
        )

    @pytest.mark.parametrize(
        "protocol",
        [
            pytest.param(ODLAPI.label(), id="ODL 1.x collection"),
            pytest.param(ODL2API.label(), id="ODL 2.x collection"),
        ],
    )
    @freeze_time()
    def test_notify_success(
        self,
        protocol,
        controller_fixture: ControllerFixture,
        odl_fixture: ODLFixture,
    ):
        db = controller_fixture.db

        odl_fixture.collection.integration_configuration.protocol = protocol
        patron, patron_identifier = odl_fixture.create_patron()
        license = odl_fixture.create_license()
        loan = odl_fixture.create_loan(license=license, patron=patron)

        license.checkouts_available = 0

        status_doc = odl_fixture.loan_status_document("active")
        with controller_fixture.request_context_with_library("/", method="POST"):
            flask.request.data = status_doc.json()  # type: ignore[assignment]
            response = controller_fixture.manager.odl_notification_controller.notify(
                patron_identifier, license.identifier
            )
            assert odl_fixture.license.identifier is not None
            assert 200 == response.status_code

        assert loan.end != utc_now()

        status_doc = odl_fixture.loan_status_document("revoked")
        with controller_fixture.request_context_with_library("/", method="POST"):
            flask.request.data = status_doc.json()  # type: ignore[assignment]
            response = controller_fixture.manager.odl_notification_controller.notify(
                patron_identifier, license.identifier
            )
            assert odl_fixture.license.identifier is not None
            assert 200 == response.status_code

        # Since we had a loan and it's not active, we're out of sync with the remote. We've set the loan to end now.
        assert loan.end == utc_now()

        # The pool's availability has been updated.
        api = controller_fixture.manager.circulation_apis[
            db.default_library().id
        ].api_for_license_pool(loan.license_pool)

    def test_notify_errors(
        self, controller_fixture: ControllerFixture, odl_fixture: ODLFixture
    ):
        db = controller_fixture.db

        non_odl_collection = db.collection()
        patron, patron_identifier = odl_fixture.create_patron()
        license = odl_fixture.create_license(collection=non_odl_collection)
        odl_fixture.create_loan(patron=patron, license=license)

        # Bad JSON, no data.
        with (
            controller_fixture.request_context_with_library("/", method="POST"),
            raises_problem_detail(pd=INVALID_INPUT),
        ):
            assert odl_fixture.license.identifier is not None
            controller_fixture.manager.odl_notification_controller.notify(
                patron_identifier, license.identifier
            )

        # Loan from a non-ODL collection.
        with (
            controller_fixture.request_context_with_library("/", method="POST"),
            raises_problem_detail(pd=INVALID_LOAN_FOR_ODL_NOTIFICATION),
        ):
            flask.request.data = odl_fixture.loan_status_document("active").json()  # type: ignore[assignment]
            assert license.identifier is not None
            controller_fixture.manager.odl_notification_controller.notify(
                patron_identifier, license.identifier
            )

        # No loan, but distributor thinks it isn't active
        NON_EXISTENT_LICENSE_IDENTIFIER = "Foo"
        with controller_fixture.request_context_with_library(
            "/",
            method="POST",
            library=odl_fixture.library,
        ):
            flask.request.data = odl_fixture.loan_status_document("returned").json()  # type: ignore[assignment]
            response = controller_fixture.manager.odl_notification_controller.notify(
                odl_fixture.patron_identifier, NON_EXISTENT_LICENSE_IDENTIFIER
            )
        assert isinstance(response, Response)
        assert response.status_code == 200

        # No loan, but distributor thinks it is active
        with (
            controller_fixture.request_context_with_library(
                "/",
                method="POST",
                library=odl_fixture.library,
            ),
            raises_problem_detail(
                pd=NO_ACTIVE_LOAN.detailed("No loan was found.", 404)
            ),
        ):
            flask.request.data = odl_fixture.loan_status_document("active").json()  # type: ignore[assignment]
            controller_fixture.manager.odl_notification_controller.notify(
                odl_fixture.patron_identifier, NON_EXISTENT_LICENSE_IDENTIFIER
            )
