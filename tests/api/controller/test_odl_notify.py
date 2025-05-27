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
from core.model import Collection
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
    def test_notify_success(
        self,
        protocol,
        controller_fixture: ControllerFixture,
        odl_fixture: ODLFixture,
    ):
        db = controller_fixture.db

        odl_fixture.collection.integration_configuration.protocol = protocol
        odl_fixture.pool.licenses_owned = 10
        odl_fixture.pool.licenses_available = 5
        loan, ignore = odl_fixture.pool.loan_to(odl_fixture.patron)
        loan.external_identifier = db.fresh_str()

        status_doc = odl_fixture.loan_status_document("active")
        with controller_fixture.request_context_with_library("/", method="POST"):
            flask.request.data = status_doc.json()  # type: ignore[assignment]
            response = controller_fixture.manager.odl_notification_controller.notify(
                loan.id
            )
            assert odl_fixture.license.identifier is not None
            assert 200 == response.status_code

        assert loan.end != utc_now()

        status_doc = odl_fixture.loan_status_document("revoked")
        with controller_fixture.request_context_with_library("/", method="POST"):
            flask.request.data = status_doc.json()  # type: ignore[assignment]
            response = controller_fixture.manager.odl_notification_controller.notify(
                loan.id
            )
            assert odl_fixture.license.identifier is not None
            assert 200 == response.status_code

        assert loan.end == utc_now()

        # The pool's availability has been updated.
        api = controller_fixture.manager.circulation_apis[
            db.default_library().id
        ].api_for_license_pool(loan.license_pool)
        assert [loan.license_pool] == api.availability_updated_for

    def test_notify_errors(
        self, controller_fixture: ControllerFixture, odl_fixture: ODLFixture, db
    ):
        db = controller_fixture.db

        # Bad JSON.
        with (
            controller_fixture.request_context_with_library("/", method="POST"),
            raises_problem_detail(pd=INVALID_INPUT),
        ):
            assert odl_fixture.license.identifier is not None
            controller_fixture.manager.odl_notification_controller.notify(
                odl_fixture.license.identifier
            )

        # Loan from a non-ODL collection.
        patron = db.patron()
        pool = db.licensepool(None)
        loan, ignore = pool.loan_to(patron)
        loan.external_identifier = db.fresh_str()
        print(loan)
        with (
            controller_fixture.request_context_with_library("/", method="POST"),
            raises_problem_detail(pd=INVALID_LOAN_FOR_ODL_NOTIFICATION),
        ):
            flask.request.data = odl_fixture.loan_status_document("active").json()  # type: ignore[assignment]
            assert odl_fixture.license.identifier is not None
            controller_fixture.manager.odl_notification_controller.notify(loan.id)

        # No loan, but distributor thinks it isn't active
        NON_EXISTENT_LICENSE_IDENTIFIER = "123"
        with controller_fixture.request_context_with_library(
            "/",
            method="POST",
            library=odl_fixture.library,
        ):
            flask.request.data = odl_fixture.loan_status_document("returned").json()  # type: ignore[assignment]
            response = controller_fixture.manager.odl_notification_controller.notify(
                NON_EXISTENT_LICENSE_IDENTIFIER
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
                NON_EXISTENT_LICENSE_IDENTIFIER
            )
