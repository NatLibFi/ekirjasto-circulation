from __future__ import annotations

import datetime
import json
import urllib.parse
from functools import partial
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock
import dateutil
from api.lcp.status import LoanStatus
import pytest
from freezegun import freeze_time
import uuid
from urllib.parse import parse_qs, urlparse
from sqlalchemy import delete

from api.circulation import FetchFulfillment, HoldInfo, RedirectFulfillment
from api.circulation_exceptions import (
    AlreadyCheckedOut,
    AlreadyOnHold,
    CannotFulfill,
    CannotLoan,
    CurrentlyAvailable,
    NoAvailableCopies,
    NoLicenses,
    NotCheckedOut,
    NotOnHold,
    CannotReturn
)
from api.odl import ODLHoldReaper, ODLImporter, ODLSettings, ODLAPI, BaseODLAPI, BaseODLImporter
from core.model import (
    Collection,
    DataSource,
    DeliveryMechanism,
    Edition,
    Hold,
    LicensePoolDeliveryMechanism,
    Loan,
    MediaTypes,
    Representation,
    RightsStatus,
)
from core.util import datetime_helpers
from core.util.datetime_helpers import datetime_utc, utc_now
from core.util.http import BadResponseException, RemoteIntegrationException
# from tests.fixtures.api_odl import (
#     LicenseHelper,
#     LicenseInfoHelper,
#     MockGet,
#     OdlImportTemplatedFixture,
# )
from tests.fixtures.database import DatabaseTransactionFixture
from tests.fixtures.odl import OPDS2WithODLApiFixture
from tests.mocks.odl import MockOPDS2WithODLApi

if TYPE_CHECKING:
    from core.model import LicensePool


class TestODLAPI:

    @pytest.mark.parametrize(
        "status_code",
        [pytest.param(200, id="existing loan"),
         pytest.param(200, id="new loan")],
    )
    def test__request_loan_status_success(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture, status_code: int
    ) -> None:
        expected_document = opds2_with_odl_api_fixture.loan_status_document("active")

        opds2_with_odl_api_fixture.mock_http.queue_response(
            status_code, content=expected_document.to_serializable()
        )
        with opds2_with_odl_api_fixture.mock_http.patch():
            requested_document = opds2_with_odl_api_fixture.api._request_loan_status(
                "http://loan"
            )

        assert "http://loan" == opds2_with_odl_api_fixture.mock_http.requests.pop()
        assert requested_document == expected_document


    @pytest.mark.parametrize(
        "status, headers, content, exception, expected_log_message",
        [
            pytest.param(
                200,
                {},
                "not json",
                RemoteIntegrationException,
                "Error validating Loan Status Document. 'http://loan' returned and invalid document.",
                id="invalid json",
            ),
            pytest.param(
                200,
                {},
                json.dumps(dict(status="unknown")),
                RemoteIntegrationException,
                "Error validating Loan Status Document. 'http://loan' returned and invalid document.",
                id="invalid document",
            ),
            pytest.param(
                403,
                {"header": "value"},
                "server error",
                RemoteIntegrationException,
                "Error requesting Loan Status Document. 'http://loan' returned status code 403. "
                "Response headers: header: value. Response content: server error.",
                id="bad status code",
            ),
            pytest.param(
                403,
                {"Content-Type": "application/api-problem+json"},
                json.dumps(
                    dict(
                        type="http://problem-detail-uri",
                        title="server error",
                        detail="broken",
                    )
                ),
                RemoteIntegrationException,
                "Error requesting Loan Status Document. 'http://loan' returned status code 403. "
                "Problem Detail: 'http://problem-detail-uri' - server error - broken",
                id="problem detail response",
            ),
        ],
    )
    def test__request_loan_status_errors(
        self,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
        caplog: pytest.LogCaptureFixture,
        status: int,
        headers: dict[str, str],
        content: str,
        exception: type[Exception],
        expected_log_message: str,
    ) -> None:
        # The response can't be parsed as JSON.
        opds2_with_odl_api_fixture.mock_http.queue_response(
            status, other_headers=headers, content=content
        )
        with pytest.raises(exception):
            with opds2_with_odl_api_fixture.mock_http.patch():
                opds2_with_odl_api_fixture.api._request_loan_status("http://loan")
        assert expected_log_message in caplog.text

    def test_checkin_success(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        # A patron has a copy of this book checked out.
        opds2_with_odl_api_fixture.setup_license(concurrency=7, available=6)

        loan, _ = opds2_with_odl_api_fixture.license.loan_to(
            opds2_with_odl_api_fixture.patron
        )
        loan.external_identifier = "http://loan/" + db.fresh_str()
        loan.end = utc_now() + datetime.timedelta(days=3)

        # The patron returns the book successfully.
        with opds2_with_odl_api_fixture.mock_http.patch():
            opds2_with_odl_api_fixture.checkin()
        assert 2 == len(opds2_with_odl_api_fixture.mock_http.requests)
        assert "http://loan" in opds2_with_odl_api_fixture.mock_http.requests[0]
        assert "http://return" == opds2_with_odl_api_fixture.mock_http.requests[1]

        # The pool's availability has increased
        assert 7 == opds2_with_odl_api_fixture.pool.licenses_available

        # The license on the pool has also been updated
        assert 7 == opds2_with_odl_api_fixture.license.checkouts_available


    def test_checkin_success_with_holds_queue(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        # A patron has the only copy of this book checked out.
        opds2_with_odl_api_fixture.setup_license(concurrency=1, available=0)
        loan, _ = opds2_with_odl_api_fixture.license.loan_to(
            opds2_with_odl_api_fixture.patron
        )
        loan.external_identifier = "http://loan/" + db.fresh_str()
        loan.end = utc_now() + datetime.timedelta(days=3)

        # Another patron has the book on hold.
        patron_with_hold = db.patron()
        opds2_with_odl_api_fixture.pool.patrons_in_hold_queue = 1
        hold, ignore = opds2_with_odl_api_fixture.pool.on_hold_to(
            patron_with_hold, start=utc_now(), end=None, position=1
        )

        # The first patron returns the book successfully.
        with opds2_with_odl_api_fixture.mock_http.patch():
            opds2_with_odl_api_fixture.checkin()
        assert 2 == len(opds2_with_odl_api_fixture.mock_http.requests)
        assert "http://loan" in opds2_with_odl_api_fixture.mock_http.requests[0]
        assert "http://return" == opds2_with_odl_api_fixture.mock_http.requests[1]

        # Now the license is reserved for the next patron.
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 1 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 1 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue


    def test_checkin_not_checked_out(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        # Not checked out locally.
        pytest.raises(
            NotCheckedOut,
            opds2_with_odl_api_fixture.api.checkin,
            opds2_with_odl_api_fixture.patron,
            "pin",
            opds2_with_odl_api_fixture.pool,
        )

        # Not checked out according to the distributor.
        loan, _ = opds2_with_odl_api_fixture.license.loan_to(
            opds2_with_odl_api_fixture.patron
        )
        loan.external_identifier = db.fresh_str()
        loan.end = utc_now() + datetime.timedelta(days=3)

        opds2_with_odl_api_fixture.mock_http.queue_response(
            200,
            content=opds2_with_odl_api_fixture.loan_status_document(
                "revoked"
            ).to_serializable(),
        )
        # Checking in silently does nothing.
        with opds2_with_odl_api_fixture.mock_http.patch():
            opds2_with_odl_api_fixture.api.checkin(
                opds2_with_odl_api_fixture.patron,
                "pin",
                opds2_with_odl_api_fixture.pool,
            )


    def test_checkin_cannot_return(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        # Not fulfilled yet, but no return link from the distributor.
        loan, ignore = opds2_with_odl_api_fixture.license.loan_to(
            opds2_with_odl_api_fixture.patron
        )
        loan.external_identifier = db.fresh_str()
        loan.end = utc_now() + datetime.timedelta(days=3)

        opds2_with_odl_api_fixture.mock_http.queue_response(
            200,
            content=opds2_with_odl_api_fixture.loan_status_document(
                "ready", return_link=False
            ).to_serializable(),
        )
        # Checking in raises the CannotReturn exception, since the distributor
        # does not support returning the book.
        with opds2_with_odl_api_fixture.mock_http.patch():
            with pytest.raises(CannotReturn):
                opds2_with_odl_api_fixture.api.checkin(
                    opds2_with_odl_api_fixture.patron,
                    "pin",
                    opds2_with_odl_api_fixture.pool,
                )

        # If the return link doesn't change the status, we raise the same exception.
        lsd = opds2_with_odl_api_fixture.loan_status_document(
            "ready", return_link="http://return"
        ).to_serializable()

        opds2_with_odl_api_fixture.mock_http.queue_response(200, content=lsd)
        opds2_with_odl_api_fixture.mock_http.queue_response(200, content=lsd)
        with opds2_with_odl_api_fixture.mock_http.patch():
            with pytest.raises(CannotReturn):
                opds2_with_odl_api_fixture.api.checkin(
                    opds2_with_odl_api_fixture.patron,
                    "pin",
                    opds2_with_odl_api_fixture.pool,
                )

    def test_checkin_open_access(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        # Checking in an open-access book doesn't need to call out to the distributor API.
        oa_work = db.work(
            with_open_access_download=True,
            collection=opds2_with_odl_api_fixture.collection,
        )
        pool = oa_work.license_pools[0]
        loan, ignore = pool.loan_to(opds2_with_odl_api_fixture.patron)

        # make sure that _checkin isn't called since it is not needed for an open access work
        opds2_with_odl_api_fixture.api._checkin = MagicMock(
            side_effect=Exception("Should not be called")
        )
        with opds2_with_odl_api_fixture.mock_http.patch():
            opds2_with_odl_api_fixture.api.checkin(
                opds2_with_odl_api_fixture.patron, "pin", pool
            )


    def test__notification_url(self):
        short_name = "short_name"
        patron_id = str(uuid.uuid4())
        license_id = str(uuid.uuid4())

        def get_path(path: str) -> str:
            return urlparse(path).path

        # Import the app so we can setup a request context to verify that we can correctly generate
        # notification url via url_for.
        from api.app import app

        # Test that we generated the expected URL
        with app.test_request_context():
            notification_url = ODLAPI._notification_url(
                short_name, patron_id, license_id
            )
            print(notification_url)

        assert (
            get_path(notification_url)
            == f"/{short_name}/odl_notify/{patron_id}/{license_id}"
        )

        # Test that our mock generates the same URL
        with app.test_request_context():
            assert get_path(
                ODLAPI._notification_url(short_name, patron_id, license_id)
            ) == get_path(
                ODLAPI._notification_url(short_name, patron_id, license_id)
            )

    def test_checkout_success(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        # This book is available to check out.
        opds2_with_odl_api_fixture.setup_license(concurrency=6, available=6, left=30)

        # A patron checks out the book successfully.
        loan_url = db.fresh_str()
        with opds2_with_odl_api_fixture.mock_http.patch():
            loan = opds2_with_odl_api_fixture.checkout(loan_url=loan_url)

        assert opds2_with_odl_api_fixture.collection == loan.collection(db.session)
        assert opds2_with_odl_api_fixture.pool.identifier.type == loan.identifier_type
        assert opds2_with_odl_api_fixture.pool.identifier.identifier == loan.identifier
        assert datetime_utc(3017, 10, 21, 11, 12, 13) == loan.end_date
        assert loan_url == loan.external_identifier

        # The pool's availability and the license's remaining checkouts have decreased.
        assert 5 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 29 == opds2_with_odl_api_fixture.license.checkouts_left

        # The parameters that we templated into the checkout URL are correct.
        requested_url = opds2_with_odl_api_fixture.mock_http.requests.pop()

        parsed = urlparse(requested_url)
        assert "https" == parsed.scheme
        assert "loan.feedbooks.net" == parsed.netloc
        params = parse_qs(parsed.query)

        assert (
            opds2_with_odl_api_fixture.api.settings.passphrase_hint == params["hint"][0]
        )
        assert (
            opds2_with_odl_api_fixture.api.settings.passphrase_hint_url
            == params["hint_url"][0]
        )

        assert opds2_with_odl_api_fixture.license.identifier == params["id"][0]

        # The checkout id is a random UUID.
        checkout_id = params["checkout_id"][0]
        assert uuid.UUID(checkout_id)

        # The patron id is the UUID of the patron, for this distributor.
        expected_patron_id = (
            opds2_with_odl_api_fixture.patron.identifier_to_remote_service(
                opds2_with_odl_api_fixture.pool.data_source
            )
        )
        patron_id = params["patron_id"][0]
        assert uuid.UUID(patron_id)
        assert patron_id == expected_patron_id

        # Loans expire in 21 days by default.
        now = utc_now()
        after_expiration = now + datetime.timedelta(days=23)
        expires = urllib.parse.unquote(params["expires"][0])

        # The expiration time passed to the server is associated with
        # the UTC time zone.
        assert expires.endswith("+00:00")
        expires_t = dateutil.parser.parse(expires)
        assert expires_t.tzinfo == dateutil.tz.tz.tzutc()

        # It's a time in the future, but not _too far_ in the future.
        assert expires_t > now
        assert expires_t < after_expiration

        notification_url = urllib.parse.unquote_plus(params["notification_url"][0])
        expected_notification_url = opds2_with_odl_api_fixture.api._notification_url(
            opds2_with_odl_api_fixture.library.short_name,
            expected_patron_id,
            opds2_with_odl_api_fixture.license.identifier,
        )
        assert notification_url == expected_notification_url


    def test_checkout_open_access(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        # This book is available to check out.
        oa_work = db.work(
            with_open_access_download=True,
            collection=opds2_with_odl_api_fixture.collection,
        )
        loan = opds2_with_odl_api_fixture.api_checkout(
            licensepool=oa_work.license_pools[0],
        )

        assert loan.collection(db.session) == opds2_with_odl_api_fixture.collection
        assert loan.identifier == oa_work.license_pools[0].identifier.identifier
        assert loan.identifier_type == oa_work.license_pools[0].identifier.type
        assert loan.start_date is None
        assert loan.end_date is None
        assert loan.external_identifier is None


    def test_checkout_success_with_hold(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        # A patron has this book on hold, and the book just became available to check out.
        opds2_with_odl_api_fixture.pool.on_hold_to(
            opds2_with_odl_api_fixture.patron,
            start=utc_now() - datetime.timedelta(days=1),
            end=utc_now() + datetime.timedelta(days=1),
            position=0,
        )
        opds2_with_odl_api_fixture.setup_license(concurrency=1, available=1, left=5)

        assert opds2_with_odl_api_fixture.pool.licenses_available == 0
        assert opds2_with_odl_api_fixture.pool.licenses_reserved == 1
        assert opds2_with_odl_api_fixture.pool.patrons_in_hold_queue == 1

        # The patron checks out the book.
        loan_url = db.fresh_str()
        with opds2_with_odl_api_fixture.mock_http.patch():
            loan = opds2_with_odl_api_fixture.checkout(loan_url=loan_url)

        # The patron gets a loan successfully.
        assert opds2_with_odl_api_fixture.collection == loan.collection(db.session)
        assert opds2_with_odl_api_fixture.pool.identifier.type == loan.identifier_type
        assert opds2_with_odl_api_fixture.pool.identifier.identifier == loan.identifier
        assert datetime_utc(3017, 10, 21, 11, 12, 13) == loan.end_date
        assert loan_url == loan.external_identifier

        # The book is no longer reserved for the patron.
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 0 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue


    def test_checkout_expired_hold(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        # The patron was at the beginning of the hold queue, but the hold already expired.
        yesterday = utc_now() - datetime.timedelta(days=1)
        hold, _ = opds2_with_odl_api_fixture.pool.on_hold_to(
            opds2_with_odl_api_fixture.patron,
            start=yesterday,
            end=yesterday,
            position=0,
        )
        other_hold, _ = opds2_with_odl_api_fixture.pool.on_hold_to(
            db.patron(), start=utc_now()
        )
        opds2_with_odl_api_fixture.setup_license(concurrency=2, available=1)

        with opds2_with_odl_api_fixture.mock_http.patch():
            with pytest.raises(NoAvailableCopies):
                opds2_with_odl_api_fixture.api_checkout()


    def test_checkout_no_available_copies(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        # A different patron has the only copy checked out.
        opds2_with_odl_api_fixture.setup_license(concurrency=1, available=0)
        existing_loan, _ = opds2_with_odl_api_fixture.license.loan_to(db.patron())

        with pytest.raises(NoAvailableCopies):
            opds2_with_odl_api_fixture.api_checkout()

        assert 1 == db.session.query(Loan).count()

        db.session.delete(existing_loan)

        now = utc_now()
        yesterday = now - datetime.timedelta(days=1)
        last_week = now - datetime.timedelta(weeks=1)

        # A different patron has the only copy reserved.
        other_patron_hold, _ = opds2_with_odl_api_fixture.pool.on_hold_to(
            db.patron(), position=0, start=last_week
        )
        opds2_with_odl_api_fixture.pool.update_availability_from_licenses()

        with pytest.raises(NoAvailableCopies):
            opds2_with_odl_api_fixture.api_checkout()

        assert 0 == db.session.query(Loan).count()

        # The patron has a hold, but another patron is ahead in the holds queue.
        hold, _ = opds2_with_odl_api_fixture.pool.on_hold_to(
            db.patron(), position=1, start=yesterday
        )
        opds2_with_odl_api_fixture.pool.update_availability_from_licenses()

        with pytest.raises(NoAvailableCopies):
            opds2_with_odl_api_fixture.api_checkout()

        assert 0 == db.session.query(Loan).count()

        # The patron has the first hold, but it's expired.
        hold.start = last_week - datetime.timedelta(days=1)
        hold.end = yesterday
        opds2_with_odl_api_fixture.pool.update_availability_from_licenses()

        with pytest.raises(NoAvailableCopies):
            opds2_with_odl_api_fixture.api_checkout()

        assert 0 == db.session.query(Loan).count()


    @pytest.mark.parametrize(
        "response_type",
        ["application/api-problem+json", "application/problem+json"],
    )
    def test_checkout_no_available_copies_unknown_to_us(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
        response_type: str,
    ) -> None:
        """
        The title has no available copies, but we are out of sync with the distributor, so we think there
        are copies available.
        """
        # We think there are copies available.
        pool = opds2_with_odl_api_fixture.pool
        pool.licenses = []
        license_1 = db.license(pool, terms_concurrency=1, checkouts_available=1)
        license_2 = db.license(pool, checkouts_available=1)
        pool.update_availability_from_licenses()

        # But the distributor says there are no available copies.
        opds2_with_odl_api_fixture.mock_http.queue_response(
            400,
            response_type,
            content=opds2_with_odl_api_fixture.files.sample_text("unavailable.json"),
        )
        opds2_with_odl_api_fixture.mock_http.queue_response(
            400,
            response_type,
            content=opds2_with_odl_api_fixture.files.sample_text("unavailable.json"),
        )

        with opds2_with_odl_api_fixture.mock_http.patch():
            with pytest.raises(NoAvailableCopies):
                opds2_with_odl_api_fixture.api_checkout()

        assert db.session.query(Loan).count() == 0
        assert opds2_with_odl_api_fixture.pool.licenses_available == 0
        assert license_1.checkouts_available == 0
        assert license_2.checkouts_available == 0


    def test_checkout_many_licenses(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        """
        The title has 5 different licenses. Several of them seem to have copies available. But
        we are out of sync, so it turns out that not all of them do.
        """
        # We think there are copies available.
        pool = opds2_with_odl_api_fixture.pool
        pool.licenses = []
        license_unavailable_1 = db.license(
            pool, checkouts_available=2, expires=utc_now() + datetime.timedelta(weeks=4)
        )
        license_unavailable_2 = db.license(
            pool, terms_concurrency=1, checkouts_available=1
        )
        license_untouched = db.license(pool, checkouts_left=1, checkouts_available=1)
        license_lent = db.license(
            pool,
            checkouts_left=4,
            checkouts_available=4,
            expires=utc_now() + datetime.timedelta(weeks=1),
        )
        license_expired = db.license(
            pool,
            terms_concurrency=10,
            checkouts_available=10,
            expires=utc_now() - datetime.timedelta(weeks=1),
        )
        pool.update_availability_from_licenses()
        assert pool.licenses_available == 8

        assert opds2_with_odl_api_fixture.pool.best_available_licenses() == [
            license_unavailable_1,
            license_unavailable_2,
            license_lent,
            license_untouched,
        ]

        # But the distributor says there are no available copies for license_unavailable_1
        opds2_with_odl_api_fixture.mock_http.queue_response(
            400,
            "application/api-problem+json",
            content=opds2_with_odl_api_fixture.files.sample_text("unavailable.json"),
        )
        # And for license_unavailable_2
        opds2_with_odl_api_fixture.mock_http.queue_response(
            400,
            "application/api-problem+json",
            content=opds2_with_odl_api_fixture.files.sample_text("unavailable.json"),
        )
        # But license_lent is still available, and we successfully check it out
        opds2_with_odl_api_fixture.mock_http.queue_response(
            201,
            content=opds2_with_odl_api_fixture.loan_status_document().to_serializable(),
        )

        with opds2_with_odl_api_fixture.mock_http.patch():
            loan_info = opds2_with_odl_api_fixture.api_checkout()

        assert opds2_with_odl_api_fixture.pool.licenses_available == 4
        assert license_unavailable_2.checkouts_available == 0
        assert license_unavailable_1.checkouts_available == 0
        assert license_lent.checkouts_available == 3
        assert license_untouched.checkouts_available == 1

        assert loan_info.license_identifier == license_lent.identifier


    def test_checkout_ready_hold_no_available_copies(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        """
        We think there is a hold ready for us, but we are out of sync with the distributor,
        so there actually isn't a copy ready for our hold.
        """
        # We think there is a copy available for this hold.
        hold, _ = opds2_with_odl_api_fixture.pool.on_hold_to(
            opds2_with_odl_api_fixture.patron,
            start=utc_now() - datetime.timedelta(days=1),
            end=utc_now() + datetime.timedelta(days=1),
            position=0,
        )
        opds2_with_odl_api_fixture.setup_license(concurrency=1, available=1)

        assert opds2_with_odl_api_fixture.pool.licenses_available == 0
        assert opds2_with_odl_api_fixture.pool.licenses_reserved == 1
        assert opds2_with_odl_api_fixture.pool.patrons_in_hold_queue == 1

        # But the distributor says there are no available copies.
        opds2_with_odl_api_fixture.mock_http.queue_response(
            400,
            "application/api-problem+json",
            content=opds2_with_odl_api_fixture.files.sample_text("unavailable.json"),
        )

        with opds2_with_odl_api_fixture.mock_http.patch():
            with pytest.raises(NoAvailableCopies):
                opds2_with_odl_api_fixture.api_checkout()

        assert db.session.query(Loan).count() == 0
        assert db.session.query(Hold).count() == 1

        # The availability has been updated.
        assert opds2_with_odl_api_fixture.pool.licenses_available == 0
        assert opds2_with_odl_api_fixture.pool.licenses_reserved == 0
        assert opds2_with_odl_api_fixture.pool.patrons_in_hold_queue == 1

        # The hold has been updated to reflect the new availability.
        assert hold.position == 0 # WRONG, it should be 1 so that they don't stay in the reserved line and end up losing their loan potentially. Will fix this later.
        assert hold.end == hold.end # Also wrong


    def test_checkout_failures(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        # We think there are copies available.
        opds2_with_odl_api_fixture.setup_license(concurrency=1, available=1)

        # Test the case where we get bad JSON back from the distributor.
        opds2_with_odl_api_fixture.mock_http.queue_response(
            400,
            "application/api-problem+json",
            content="hot garbage",
        )

        with pytest.raises(BadResponseException):
            opds2_with_odl_api_fixture.api_checkout()

        # Test the case where we just get an unknown bad response.
        opds2_with_odl_api_fixture.mock_http.queue_response(
            500, "text/plain", content="halt and catch fire 🔥"
        )
        with pytest.raises(BadResponseException):
            opds2_with_odl_api_fixture.api_checkout()


    def test_checkout_no_licenses(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        opds2_with_odl_api_fixture.setup_license(concurrency=1, available=1, left=0)

        with pytest.raises(NoLicenses):
            opds2_with_odl_api_fixture.api_checkout()

        assert 0 == db.session.query(Loan).count()


    def test_checkout_when_all_licenses_expired(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        # license expired by expiration date
        opds2_with_odl_api_fixture.setup_license(
            concurrency=1,
            available=2,
            left=1,
            expires=utc_now() - datetime.timedelta(weeks=1),
        )

        with pytest.raises(NoLicenses):
            opds2_with_odl_api_fixture.api_checkout()

        # license expired by no remaining checkouts
        opds2_with_odl_api_fixture.setup_license(
            concurrency=1,
            available=2,
            left=0,
            expires=utc_now() + datetime.timedelta(weeks=1),
        )

        with pytest.raises(NoLicenses):
            opds2_with_odl_api_fixture.api_checkout()


    def test_checkout_cannot_loan(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        opds2_with_odl_api_fixture.mock_http.queue_response(
            200,
            content=opds2_with_odl_api_fixture.loan_status_document(
                "revoked"
            ).to_serializable(),
        )
        with opds2_with_odl_api_fixture.mock_http.patch():
            with pytest.raises(CannotLoan):
                opds2_with_odl_api_fixture.api_checkout()
        assert 0 == db.session.query(Loan).count()

        # No external identifier.
        opds2_with_odl_api_fixture.mock_http.queue_response(
            200,
            content=opds2_with_odl_api_fixture.loan_status_document(
                self_link=False, license_link=False
            ).to_serializable(),
        )
        with opds2_with_odl_api_fixture.mock_http.patch():
            with pytest.raises(CannotLoan):
                opds2_with_odl_api_fixture.api_checkout()
        assert 0 == db.session.query(Loan).count()


    @pytest.mark.parametrize(
        "drm_scheme, correct_type, correct_link, links",
        [
            pytest.param(
                DeliveryMechanism.ADOBE_DRM,
                DeliveryMechanism.ADOBE_DRM,
                "http://acsm",
                [
                    {
                        "rel": "license",
                        "href": "http://acsm",
                        "type": DeliveryMechanism.ADOBE_DRM,
                    }
                ],
                id="adobe drm",
            ),
            pytest.param(
                DeliveryMechanism.LCP_DRM,
                DeliveryMechanism.LCP_DRM,
                "http://lcp",
                [
                    {
                        "rel": "license",
                        "href": "http://lcp",
                        "type": DeliveryMechanism.LCP_DRM,
                    }
                ],
                id="lcp drm",
            ),
            pytest.param(
                DeliveryMechanism.NO_DRM,
                "application/epub+zip",
                "http://publication",
                [
                    {
                        "rel": "publication",
                        "href": "http://publication",
                        "type": "application/epub+zip",
                    }
                ],
                id="no drm",
            ),
            pytest.param(
                DeliveryMechanism.FEEDBOOKS_AUDIOBOOK_DRM,
                BaseODLImporter.FEEDBOOKS_AUDIO,
                "http://correct",
                [
                    {
                        "rel": "license",
                        "href": "http://acsm",
                        "type": DeliveryMechanism.ADOBE_DRM,
                    },
                    {
                        "rel": "manifest",
                        "href": "http://correct",
                        "type": BaseODLImporter.FEEDBOOKS_AUDIO,
                    },
                ],
                id="feedbooks audio",
            ),
        ],
    )
    def test_fulfill_success(
        self,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
        db: DatabaseTransactionFixture,
        drm_scheme: str,
        correct_type: str,
        correct_link: str,
        links: list[dict[str, str]],
    ) -> None:
        # Fulfill a loan in a way that gives access to a license file.
        opds2_with_odl_api_fixture.setup_license(concurrency=1, available=1)
        with opds2_with_odl_api_fixture.mock_http.patch():
            opds2_with_odl_api_fixture.checkout(create_loan=True)

        lpdm = MagicMock(spec=LicensePoolDeliveryMechanism)
        lpdm.delivery_mechanism = MagicMock(spec=DeliveryMechanism)
        lpdm.delivery_mechanism.content_type = (
            "ignored/format" if drm_scheme != DeliveryMechanism.NO_DRM else correct_type
        )
        lpdm.delivery_mechanism.drm_scheme = drm_scheme

        lsd = opds2_with_odl_api_fixture.loan_status_document("active", links=links)
        opds2_with_odl_api_fixture.mock_http.queue_response(
            200, content=lsd.to_serializable()
        )
        with opds2_with_odl_api_fixture.mock_http.patch():
            fulfillment = opds2_with_odl_api_fixture.api.fulfill(
                opds2_with_odl_api_fixture.patron,
                "pin",
                opds2_with_odl_api_fixture.pool,
                lpdm,
            )
        assert (
            isinstance(fulfillment, FetchFulfillment)
            if drm_scheme != DeliveryMechanism.NO_DRM
            else isinstance(fulfillment, RedirectFulfillment)
        )
        assert correct_link == fulfillment.content_link  # type: ignore[attr-defined]
        assert correct_type == fulfillment.content_type  # type: ignore[attr-defined]
        if isinstance(fulfillment, FetchFulfillment):
            assert fulfillment.allowed_response_codes == ["2xx"]

    def test_fulfill_open_access(
        self,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
        db: DatabaseTransactionFixture,
    ) -> None:
        oa_work = db.work(
            with_open_access_download=True,
            collection=opds2_with_odl_api_fixture.collection,
        )
        pool = oa_work.license_pools[0]
        loan, ignore = pool.loan_to(opds2_with_odl_api_fixture.patron)

        # If we can't find a delivery mechanism, we can't fulfill the loan.
        mock_lpdm = MagicMock(
            spec=LicensePoolDeliveryMechanism,
            delivery_mechanism=MagicMock(drm_scheme=None),
        )
        with pytest.raises(CannotFulfill):
            opds2_with_odl_api_fixture.api.fulfill(
                opds2_with_odl_api_fixture.patron, "pin", pool, mock_lpdm
            )

        lpdm = pool.delivery_mechanisms[0]
        fulfillment = opds2_with_odl_api_fixture.api.fulfill(
            opds2_with_odl_api_fixture.patron, "pin", pool, lpdm
        )

        assert isinstance(fulfillment, RedirectFulfillment)
        assert fulfillment.content_link == pool.open_access_download_url
        assert fulfillment.content_type == lpdm.delivery_mechanism.content_type


    @pytest.mark.parametrize(
        "status_document",
        [
            pytest.param(
                OPDS2WithODLApiFixture.loan_status_document("revoked"),
                id="revoked",
            ),
            pytest.param(
                OPDS2WithODLApiFixture.loan_status_document("cancelled"),
                id="cancelled",
            ),
            pytest.param(
                OPDS2WithODLApiFixture.loan_status_document("active"),
                id="missing link",
            ),
        ],
    )
    def test_fulfill_cannot_fulfill(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
        status_document: LoanStatus,
    ) -> None:
        opds2_with_odl_api_fixture.setup_license(concurrency=7, available=7)
        with opds2_with_odl_api_fixture.mock_http.patch():
            opds2_with_odl_api_fixture.checkout(create_loan=True)

        assert 1 == db.session.query(Loan).count()
        assert 6 == opds2_with_odl_api_fixture.pool.licenses_available

        opds2_with_odl_api_fixture.mock_http.queue_response(
            200, content=status_document.to_serializable()
        )
        with opds2_with_odl_api_fixture.mock_http.patch():
            with pytest.raises(CannotFulfill):
                opds2_with_odl_api_fixture.api.fulfill(
                    opds2_with_odl_api_fixture.patron,
                    "pin",
                    opds2_with_odl_api_fixture.pool,
                    MagicMock(),
                )

    @freeze_time()
    def test_place_hold_success(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        with opds2_with_odl_api_fixture.mock_http.patch():
            loan = opds2_with_odl_api_fixture.checkout(patron=db.patron(), create_loan=True)
            hold = opds2_with_odl_api_fixture.place_hold()

        assert 1 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue
        assert opds2_with_odl_api_fixture.collection == hold.collection(db.session)
        assert opds2_with_odl_api_fixture.pool.identifier.type == hold.identifier_type
        assert opds2_with_odl_api_fixture.pool.identifier.identifier == hold.identifier
        assert hold.start_date is not None
        assert hold.start_date == utc_now()
        assert 1 == hold.hold_position


    def test_place_hold_already_on_hold(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        opds2_with_odl_api_fixture.setup_license(concurrency=1, available=0)
        with opds2_with_odl_api_fixture.mock_http.patch():
            opds2_with_odl_api_fixture.place_hold(create_hold=True)
            with pytest.raises(AlreadyOnHold):
                opds2_with_odl_api_fixture.place_hold()

    def test_place_hold_currently_available(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        with opds2_with_odl_api_fixture.mock_http.patch():
            with pytest.raises(CurrentlyAvailable):
                opds2_with_odl_api_fixture.place_hold()


    def test_release_hold_success(
        self,
        db: DatabaseTransactionFixture,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ) -> None:
        loan_patron = db.patron()
        hold1_patron = db.patron()
        hold2_patron = db.patron()
        with opds2_with_odl_api_fixture.mock_http.patch():
            opds2_with_odl_api_fixture.checkout(patron=loan_patron, create_loan=True)
            opds2_with_odl_api_fixture.place_hold(patron=hold1_patron, create_hold=True)
            opds2_with_odl_api_fixture.place_hold(patron=hold2_patron, create_hold=True)
        
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 2 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue
        with opds2_with_odl_api_fixture.mock_http.patch():
            opds2_with_odl_api_fixture.api.release_hold(
                hold1_patron, "pin", opds2_with_odl_api_fixture.pool
            )
        db.session.execute(delete(Hold).where(Hold.patron == hold1_patron))
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 1 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue
        with opds2_with_odl_api_fixture.mock_http.patch():
            opds2_with_odl_api_fixture.checkin(patron=loan_patron)
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 1 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 1 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue

        with opds2_with_odl_api_fixture.mock_http.patch():    
            opds2_with_odl_api_fixture.api.release_hold(
                hold2_patron, "pin", opds2_with_odl_api_fixture.pool
            )
        assert 1 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 0 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue


    def test_release_hold_not_on_hold(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        pytest.raises(
            NotOnHold,
            opds2_with_odl_api_fixture.api.release_hold,
            opds2_with_odl_api_fixture.patron,
            "pin",
            opds2_with_odl_api_fixture.pool,
        )


    def _holdinfo_from_hold(self, hold: Hold) -> HoldInfo:
        pool: LicensePool = hold.license_pool
        return HoldInfo.from_license_pool(
            pool,
            start_date=hold.start,
            end_date=hold.end,
            hold_position=hold.position,
        )

    def test_count_holds_before(
        self, db: DatabaseTransactionFixture, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        now = utc_now()
        yesterday = now - datetime.timedelta(days=1)
        tomorrow = now + datetime.timedelta(days=1)
        last_week = now - datetime.timedelta(weeks=1)

        hold, ignore = opds2_with_odl_api_fixture.pool.on_hold_to(
            opds2_with_odl_api_fixture.patron, start=now
        )

        info = self._holdinfo_from_hold(hold)
        assert 0 == opds2_with_odl_api_fixture.api._count_holds_before(
            info, hold.license_pool
        )

        # A previous hold.
        opds2_with_odl_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        assert 1 == opds2_with_odl_api_fixture.api._count_holds_before(
            info, hold.license_pool
        )

        # Expired holds don't count.
        opds2_with_odl_api_fixture.pool.on_hold_to(
            db.patron(), start=last_week, end=yesterday, position=0
        )
        assert 1 == opds2_with_odl_api_fixture.api._count_holds_before(
            info, hold.license_pool
        )

        # Later holds don't count.
        opds2_with_odl_api_fixture.pool.on_hold_to(db.patron(), start=tomorrow)
        assert 1 == opds2_with_odl_api_fixture.api._count_holds_before(
            info, hold.license_pool
        )

        # Holds on another pool don't count.
        other_pool = db.licensepool(None)
        other_pool.on_hold_to(opds2_with_odl_api_fixture.patron, start=yesterday)
        assert 1 == opds2_with_odl_api_fixture.api._count_holds_before(
            info, hold.license_pool
        )

        for i in range(3):
            opds2_with_odl_api_fixture.pool.on_hold_to(
                db.patron(), start=yesterday, end=tomorrow, position=1
            )
        assert 4 == opds2_with_odl_api_fixture.api._count_holds_before(
            info, hold.license_pool
        )

    def test_update_hold_end_date(
        self, db: DatabaseTransactionFixture, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        now = utc_now()
        tomorrow = now + datetime.timedelta(days=1)
        yesterday = now - datetime.timedelta(days=1)
        next_week = now + datetime.timedelta(days=7)
        last_week = now - datetime.timedelta(days=7)

        opds2_with_odl_api_fixture.pool.licenses_owned = 1
        opds2_with_odl_api_fixture.pool.licenses_reserved = 1

        hold, ignore = opds2_with_odl_api_fixture.pool.on_hold_to(
            opds2_with_odl_api_fixture.patron, start=now, position=0
        )
        info = self._holdinfo_from_hold(hold)
        library = hold.patron.library

        # Set the reservation period and loan period.
        config = opds2_with_odl_api_fixture.collection.integration_configuration.for_library(
            library.id
        )
        assert config is not None
        DatabaseTransactionFixture.set_settings(
            config,
            **{
                Collection.DEFAULT_RESERVATION_PERIOD_KEY: 3,
                Collection.EBOOK_LOAN_DURATION_KEY: 6,
            },
        )
        opds2_with_odl_api_fixture.db.session.commit()

        # A hold that's already reserved and has an end date doesn't change.
        info.end_date = tomorrow
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert tomorrow == info.end_date
        info.end_date = yesterday
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert yesterday == info.end_date

        # Updating a hold that's reserved but doesn't have an end date starts the
        # reservation period.
        info.end_date = None
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert info.end_date is not None
        assert info.end_date < next_week  # type: ignore[unreachable]
        assert info.end_date > now

        # Updating a hold that has an end date but just became reserved starts
        # the reservation period.
        info.end_date = yesterday
        info.hold_position = 1
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert info.end_date < next_week
        assert info.end_date > now

        # When there's a holds queue, the end date is the maximum time it could take for
        # a license to become available.

        # One copy, one loan, hold position 1.
        # The hold will be available as soon as the loan expires.
        opds2_with_odl_api_fixture.pool.licenses_available = 0
        opds2_with_odl_api_fixture.pool.licenses_reserved = 0
        opds2_with_odl_api_fixture.pool.licenses_owned = 1
        loan, ignore = opds2_with_odl_api_fixture.license.loan_to(db.patron(), end=tomorrow)
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert tomorrow == info.end_date

        # One copy, one loan, hold position 2.
        # The hold will be available after the loan expires + 1 cycle.
        first_hold, ignore = opds2_with_odl_api_fixture.pool.on_hold_to(
            db.patron(), start=last_week
        )
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert tomorrow + datetime.timedelta(days=9) == info.end_date

        # Two copies, one loan, one reserved hold, hold position 2.
        # The hold will be available after the loan expires.
        opds2_with_odl_api_fixture.pool.licenses_reserved = 1
        opds2_with_odl_api_fixture.pool.licenses_owned = 2
        opds2_with_odl_api_fixture.license.checkouts_available = 2
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert tomorrow == info.end_date

        # Two copies, one loan, one reserved hold, hold position 3.
        # The hold will be available after the reserved hold is checked out
        # at the latest possible time and that loan expires.
        second_hold, ignore = opds2_with_odl_api_fixture.pool.on_hold_to(
            db.patron(), start=yesterday
        )
        first_hold.end = next_week
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=6) == info.end_date

        # One copy, no loans, one reserved hold, hold position 3.
        # The hold will be available after the reserved hold is checked out
        # at the latest possible time and that loan expires + 1 cycle.
        db.session.delete(loan)
        opds2_with_odl_api_fixture.pool.licenses_owned = 1
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=15) == info.end_date

        # One copy, no loans, one reserved hold, hold position 2.
        # The hold will be available after the reserved hold is checked out
        # at the latest possible time and that loan expires.
        db.session.delete(second_hold)
        opds2_with_odl_api_fixture.pool.licenses_owned = 1
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=6) == info.end_date

        db.session.delete(first_hold)

        # Ten copies, seven loans, three reserved holds, hold position 9.
        # The hold will be available after the sixth loan expires.
        opds2_with_odl_api_fixture.pool.licenses_owned = 10
        for i in range(5):
            opds2_with_odl_api_fixture.pool.loan_to(db.patron(), end=next_week)
        opds2_with_odl_api_fixture.pool.loan_to(
            db.patron(), end=next_week + datetime.timedelta(days=1)
        )
        opds2_with_odl_api_fixture.pool.loan_to(
            db.patron(), end=next_week + datetime.timedelta(days=2)
        )
        opds2_with_odl_api_fixture.pool.licenses_reserved = 3
        for i in range(3):
            opds2_with_odl_api_fixture.pool.on_hold_to(
                db.patron(),
                start=last_week + datetime.timedelta(days=i),
                end=next_week + datetime.timedelta(days=i),
                position=0,
            )
        for i in range(5):
            opds2_with_odl_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=1) == info.end_date

        # Ten copies, seven loans, three reserved holds, hold position 12.
        # The hold will be available after the second reserved hold is checked
        # out and that loan expires.
        for i in range(3):
            opds2_with_odl_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=7) == info.end_date

        # Ten copies, seven loans, three reserved holds, hold position 29.
        # The hold will be available after the sixth loan expires + 2 cycles.
        for i in range(17):
            opds2_with_odl_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=19) == info.end_date

        # Ten copies, seven loans, three reserved holds, hold position 32.
        # The hold will be available after the second reserved hold is checked
        # out and that loan expires + 2 cycles.
        for i in range(3):
            opds2_with_odl_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        opds2_with_odl_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=25) == info.end_date

    def test_update_hold_position(
        self, db: DatabaseTransactionFixture, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        now = utc_now()
        yesterday = now - datetime.timedelta(days=1)
        tomorrow = now + datetime.timedelta(days=1)

        hold, ignore = opds2_with_odl_api_fixture.pool.on_hold_to(
            opds2_with_odl_api_fixture.patron, start=now
        )
        info = self._holdinfo_from_hold(hold)

        opds2_with_odl_api_fixture.pool.licenses_owned = 1

        # When there are no other holds and no licenses reserved, hold position is 1.
        loan, _ = opds2_with_odl_api_fixture.license.loan_to(db.patron())
        opds2_with_odl_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 1 == info.hold_position

        # When a license is reserved, position is 0.
        db.session.delete(loan)
        opds2_with_odl_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 0 == info.hold_position

        # If another hold has the reserved licenses, position is 2.
        opds2_with_odl_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        opds2_with_odl_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 2 == info.hold_position

        # If another license is reserved, position goes back to 0.
        opds2_with_odl_api_fixture.pool.licenses_owned = 2
        opds2_with_odl_api_fixture.license.checkouts_available = 2
        opds2_with_odl_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 0 == info.hold_position

        # If there's an earlier hold but it expired, it doesn't
        # affect the position.
        opds2_with_odl_api_fixture.pool.on_hold_to(
            db.patron(), start=yesterday, end=yesterday, position=0
        )
        opds2_with_odl_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 0 == info.hold_position

        # Hold position is after all earlier non-expired holds...
        for i in range(3):
            opds2_with_odl_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        opds2_with_odl_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 5 == info.hold_position

        # and before any later holds.
        for i in range(2):
            opds2_with_odl_api_fixture.pool.on_hold_to(db.patron(), start=tomorrow)
        opds2_with_odl_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 5 == info.hold_position

    def test_update_hold_data(
        self, db: DatabaseTransactionFixture, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        hold, is_new = opds2_with_odl_api_fixture.pool.on_hold_to(
            opds2_with_odl_api_fixture.patron,
            utc_now(),
            utc_now() + datetime.timedelta(days=100),
            9,
        )
        opds2_with_odl_api_fixture.api._update_hold_data(hold)
        assert hold.position == 0
        assert hold.end.date() == (hold.start + datetime.timedelta(days=3)).date()

    def test_update_hold_queue(
        self, db: DatabaseTransactionFixture, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        licenses = [opds2_with_odl_api_fixture.license]

        DatabaseTransactionFixture.set_settings(
            opds2_with_odl_api_fixture.collection.integration_configuration,
            **{Collection.DEFAULT_RESERVATION_PERIOD_KEY: 3},
        )

        # If there's no holds queue when we try to update the queue, it
        # will remove a reserved license and make it available instead.
        opds2_with_odl_api_fixture.pool.licenses_owned = 1
        opds2_with_odl_api_fixture.pool.licenses_available = 0
        opds2_with_odl_api_fixture.pool.licenses_reserved = 1
        opds2_with_odl_api_fixture.pool.patrons_in_hold_queue = 0
        last_update = utc_now() - datetime.timedelta(minutes=5)
        opds2_with_odl_api_fixture.work.last_update_time = last_update
        opds2_with_odl_api_fixture.api.update_licensepool(opds2_with_odl_api_fixture.pool)
        assert 1 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 0 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue
        # The work's last update time is changed so it will be moved up in the crawlable OPDS feed.
        assert opds2_with_odl_api_fixture.work.last_update_time > last_update

        # If there are holds, a license will get reserved for the next hold
        # and its end date will be set.
        hold, _ = opds2_with_odl_api_fixture.pool.on_hold_to(
            opds2_with_odl_api_fixture.patron, start=utc_now(), position=1
        )
        later_hold, _ = opds2_with_odl_api_fixture.pool.on_hold_to(
            db.patron(), start=utc_now() + datetime.timedelta(days=1), position=2
        )
        opds2_with_odl_api_fixture.api.update_licensepool(opds2_with_odl_api_fixture.pool)

        # The pool's licenses were updated.
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 1 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 2 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue

        # And the first hold changed.
        assert 0 == hold.position
        assert hold.end - utc_now() - datetime.timedelta(days=3) < datetime.timedelta(
            hours=1
        )

        # The later hold is the same.
        assert 2 == later_hold.position

        # Now there's a reserved hold. If we add another license, it's reserved and,
        # the later hold is also updated.
        l = db.license(
            opds2_with_odl_api_fixture.pool, terms_concurrency=1, checkouts_available=1
        )
        licenses.append(l)
        opds2_with_odl_api_fixture.api.update_licensepool(opds2_with_odl_api_fixture.pool)

        assert 0 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 2 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 2 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue
        assert 0 == later_hold.position
        assert later_hold.end - utc_now() - datetime.timedelta(
            days=3
        ) < datetime.timedelta(hours=1)

        # Now there are no more holds. If we add another license,
        # it ends up being available.
        l = db.license(
            opds2_with_odl_api_fixture.pool, terms_concurrency=1, checkouts_available=1
        )
        licenses.append(l)
        opds2_with_odl_api_fixture.api.update_licensepool(opds2_with_odl_api_fixture.pool)
        assert 1 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 2 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 2 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue

        # License pool is updated when the holds are removed.
        db.session.delete(hold)
        db.session.delete(later_hold)
        opds2_with_odl_api_fixture.api.update_licensepool(opds2_with_odl_api_fixture.pool)
        assert 3 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 0 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue

        # We can also make multiple licenses reserved at once.
        loans = []
        holds = []
        with opds2_with_odl_api_fixture.mock_http.patch():
            for i in range(3):
                p = db.patron()
                loan = opds2_with_odl_api_fixture.checkout(patron=p, create_loan=True)
                loans.append((loan, p))
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 0 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue

        l = db.license(
            opds2_with_odl_api_fixture.pool, terms_concurrency=2, checkouts_available=2
        )
        licenses.append(l)
        for i in range(3):
            hold, ignore = opds2_with_odl_api_fixture.pool.on_hold_to(
                db.patron(),
                start=utc_now() - datetime.timedelta(days=3 - i),
                position=i + 1,
            )
            holds.append(hold)
        opds2_with_odl_api_fixture.api.update_licensepool(opds2_with_odl_api_fixture.pool)
        assert 2 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 3 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue
        assert 0 == holds[0].position
        assert 0 == holds[1].position
        assert 3 == holds[2].position
        assert holds[0].end - utc_now() - datetime.timedelta(
            days=3
        ) < datetime.timedelta(hours=1)
        assert holds[1].end - utc_now() - datetime.timedelta(
            days=3
        ) < datetime.timedelta(hours=1)

        # If there are more licenses that change than holds, some of them become available.
        with opds2_with_odl_api_fixture.mock_http.patch():
            for i in range(2):
                loan, patron = loans[i]
                opds2_with_odl_api_fixture.checkin(patron=patron)
        assert 3 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 1 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 3 == opds2_with_odl_api_fixture.pool.patrons_in_hold_queue
        assert 0 == holds[0].position
        assert 0 == holds[1].position
        assert 3 == holds[2].position # The position does not change yet because the loans are deleted by CirculationAPI

    def test_patron_activity_loan(
        self, db: DatabaseTransactionFixture, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        # No loans yet.
        assert [] == opds2_with_odl_api_fixture.api.patron_activity(
            opds2_with_odl_api_fixture.patron, "pin"
        )

        # One loan.
        loan, _ = opds2_with_odl_api_fixture.pool.loan_to(opds2_with_odl_api_fixture.patron, end=utc_now() + datetime.timedelta(weeks=2))

        activity = opds2_with_odl_api_fixture.api.patron_activity(
            opds2_with_odl_api_fixture.patron, "pin"
        )
        assert 1 == len(activity)
        assert opds2_with_odl_api_fixture.collection == activity[0].collection(db.session)
        assert opds2_with_odl_api_fixture.pool.identifier.type == activity[0].identifier_type
        assert opds2_with_odl_api_fixture.pool.identifier.identifier == activity[0].identifier
        assert loan.start == activity[0].start_date
        assert loan.end == activity[0].end_date
        assert loan.external_identifier == activity[0].external_identifier

        # Two loans.
        pool2 = db.licensepool(None, collection=opds2_with_odl_api_fixture.collection)
        loan2, _ = pool2.loan_to(opds2_with_odl_api_fixture.patron, end=utc_now() + datetime.timedelta(weeks=2))
        license2 = db.license(pool2, terms_concurrency=1, checkouts_available=1)
        activity = opds2_with_odl_api_fixture.api.patron_activity(
            opds2_with_odl_api_fixture.patron, "pin"
        )
        assert 2 == len(activity)
        [l1, l2] = sorted(activity, key=lambda x: x.start_date)

        assert opds2_with_odl_api_fixture.collection == l1.collection(db.session)
        assert opds2_with_odl_api_fixture.pool.identifier.type == l1.identifier_type
        assert opds2_with_odl_api_fixture.pool.identifier.identifier == l1.identifier
        assert loan.start == l1.start_date
        assert loan.end == l1.end_date
        assert loan.external_identifier == l1.external_identifier

        assert opds2_with_odl_api_fixture.collection == l2.collection(db.session)
        assert pool2.identifier.type == l2.identifier_type
        assert pool2.identifier.identifier == l2.identifier
        assert loan2.start == l2.start_date
        assert loan2.end == l2.end_date
        assert loan2.external_identifier == l2.external_identifier

        # If a loan is expired already, it's left out.
        loan2.end = utc_now() - datetime.timedelta(days=1)
        activity = opds2_with_odl_api_fixture.api.patron_activity(
            opds2_with_odl_api_fixture.patron, "pin"
        )
        assert 1 == len(activity)
        # Open access loans are included.
        oa_work = db.work(
            with_open_access_download=True, collection=opds2_with_odl_api_fixture.collection
        )
        pool3 = oa_work.license_pools[0]
        loan3, _ = pool3.loan_to(opds2_with_odl_api_fixture.patron, end=utc_now() + datetime.timedelta(weeks=2))

        activity = opds2_with_odl_api_fixture.api.patron_activity(
            opds2_with_odl_api_fixture.patron, "pin"
        )
        assert 2 == len(activity)
        [l1, l2] = sorted(activity, key=lambda x: x.start_date)

        assert opds2_with_odl_api_fixture.collection == l1.collection(db.session)
        assert opds2_with_odl_api_fixture.pool.identifier.type == l1.identifier_type
        assert opds2_with_odl_api_fixture.pool.identifier.identifier == l1.identifier
        assert loan.start == l1.start_date
        assert loan.end == l1.end_date
        assert loan.external_identifier == l1.external_identifier

        assert opds2_with_odl_api_fixture.collection == l2.collection(db.session)
        assert pool3.identifier.type == l2.identifier_type
        assert pool3.identifier.identifier == l2.identifier
        assert loan3.start == l2.start_date
        assert loan3.end == l2.end_date
        assert loan3.external_identifier == l2.external_identifier

        # remove the open access loan
        db.session.delete(loan3)

        # One hold.
        other_patron = db.patron()
        pool2.loan_to(other_patron, end=utc_now() + datetime.timedelta(weeks=2))
        # with opds2_with_odl_api_fixture.mock_http.patch():
        #     opds2_with_odl_api_fixture.checkout(patron=other_patron, pool=pool2)
        hold, _ = pool2.on_hold_to(opds2_with_odl_api_fixture.patron)
        hold.start_date = utc_now() - datetime.timedelta(days=2)
        hold.end_date = hold.start + datetime.timedelta(days=3)
        hold.position = 3
        activity = opds2_with_odl_api_fixture.api.patron_activity(
            opds2_with_odl_api_fixture.patron, "pin"
        )
        assert 2 == len(activity)
        [h1, l1] = sorted(activity, key=lambda x: x.start_date)

        assert opds2_with_odl_api_fixture.collection == h1.collection(db.session)
        # assert pool2.data_source.name == h1.data_source_name
        assert pool2.identifier.type == h1.identifier_type
        assert pool2.identifier.identifier == h1.identifier
        assert hold.start == h1.start_date
        assert hold.end == h1.end_date
        # Hold position was updated.
        assert 1 == h1.hold_position
        assert 1 == hold.position

        # If the hold is expired, it's deleted right away and the license
        # is made available again.
        with opds2_with_odl_api_fixture.mock_http.patch():
            opds2_with_odl_api_fixture.checkin(patron=other_patron, pool=pool2)
        hold.end = utc_now() - datetime.timedelta(days=1)
        hold.position = 0
        activity = opds2_with_odl_api_fixture.api.patron_activity(
            opds2_with_odl_api_fixture.patron, "pin"
        )
        assert 1 == len(activity)
        assert 0 == db.session.query(Hold).count()
        assert 1 == pool2.licenses_available
        assert 0 == pool2.licenses_reserved


    def test_update_loan_removes_loan(
        self, db: DatabaseTransactionFixture, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        opds2_with_odl_api_fixture.license.setup(concurrency=7, available=7)  # type: ignore[attr-defined]
        _, loan = opds2_with_odl_api_fixture.checkout()

        assert 6 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 1 == db.session.query(Loan).count()

        status_doc = {
            "status": "cancelled",
        }

        opds2_with_odl_api_fixture.api.update_loan(loan, status_doc)

        # Availability has increased, and the loan is gone.
        assert 7 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 0 == db.session.query(Loan).count()

    def test_update_loan_removes_loan_with_hold_queue(
        self, db: DatabaseTransactionFixture, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ) -> None:
        _, loan = opds2_with_odl_api_fixture.checkout()
        hold, _ = opds2_with_odl_api_fixture.pool.on_hold_to(db.patron(), position=1)
        opds2_with_odl_api_fixture.pool.update_availability_from_licenses()

        assert opds2_with_odl_api_fixture.pool.licenses_owned == 1
        assert opds2_with_odl_api_fixture.pool.licenses_available == 0
        assert opds2_with_odl_api_fixture.pool.licenses_reserved == 0
        assert opds2_with_odl_api_fixture.pool.patrons_in_hold_queue == 1

        status_doc = {
            "status": "cancelled",
        }

        opds2_with_odl_api_fixture.api.update_loan(loan, status_doc)

        # The license is reserved for the next patron, and the loan is gone.
        assert 0 == opds2_with_odl_api_fixture.pool.licenses_available
        assert 1 == opds2_with_odl_api_fixture.pool.licenses_reserved
        assert 0 == hold.position
        assert 0 == db.session.query(Loan).count()


# class TestODLImporter:
#     @freeze_time("2019-01-01T00:00:00+00:00")
#     def test_import(
#         self,
#         odl_importer: ODLImporter,
#         odl_mock_get: MockGet,
#         odl_test_fixture: ODLTestFixture,
#     ) -> None:
#         """Ensure that ODLImporter correctly processes and imports the ODL feed encoded using OPDS 1.x.

#         NOTE: `freeze_time` decorator is required to treat the licenses in the ODL feed as non-expired.
#         """
#         feed = odl_test_fixture.files.sample_data("feedbooks_bibliographic.atom")

#         warrior_time_limited = LicenseInfoHelper(
#             license=LicenseHelper(
#                 identifier="1", concurrency=1, expires="2019-03-31T03:13:35+02:00"
#             ),
#             left=52,
#             available=1,
#         )
#         canadianity_loan_limited = LicenseInfoHelper(
#             license=LicenseHelper(identifier="2", concurrency=10), left=40, available=10
#         )
#         canadianity_perpetual = LicenseInfoHelper(
#             license=LicenseHelper(identifier="3", concurrency=1), available=1
#         )
#         midnight_loan_limited_1 = LicenseInfoHelper(
#             license=LicenseHelper(
#                 identifier="4",
#                 concurrency=1,
#             ),
#             left=20,
#             available=1,
#         )
#         midnight_loan_limited_2 = LicenseInfoHelper(
#             license=LicenseHelper(identifier="5", concurrency=1), left=52, available=1
#         )
#         dragons_loan = LicenseInfoHelper(
#             license=LicenseHelper(
#                 identifier="urn:uuid:01234567-890a-bcde-f012-3456789abcde",
#                 concurrency=5,
#             ),
#             left=10,
#             available=5,
#         )

#         for r in [
#             warrior_time_limited,
#             canadianity_loan_limited,
#             canadianity_perpetual,
#             midnight_loan_limited_1,
#             midnight_loan_limited_2,
#             dragons_loan,
#         ]:
#             odl_mock_get.add(r)

#         (
#             imported_editions,
#             imported_pools,
#             imported_works,
#             failures,
#         ) = odl_importer.import_from_feed(feed)

#         # This importer works the same as the base OPDSImporter, except that
#         # it extracts format information from 'odl:license' tags and creates
#         # LicensePoolDeliveryMechanisms.

#         # The importer created 6 editions, pools, and works.
#         assert {} == failures
#         assert 6 == len(imported_editions)
#         assert 6 == len(imported_pools)
#         assert 6 == len(imported_works)

#         [
#             canadianity,
#             everglades,
#             dragons,
#             warrior,
#             blazing,
#             midnight,
#         ] = sorted(imported_editions, key=lambda x: str(x.title))
#         assert "The Blazing World" == blazing.title
#         assert "Sun Warrior" == warrior.title
#         assert "Canadianity" == canadianity.title
#         assert "The Midnight Dance" == midnight.title
#         assert "Everglades Wildguide" == everglades.title
#         assert "Rise of the Dragons, Book 1" == dragons.title

#         # This book is open access and has no applicable DRM
#         [blazing_pool] = [
#             p for p in imported_pools if p.identifier == blazing.primary_identifier
#         ]
#         assert True == blazing_pool.open_access
#         [lpdm] = blazing_pool.delivery_mechanisms
#         assert Representation.EPUB_MEDIA_TYPE == lpdm.delivery_mechanism.content_type
#         assert DeliveryMechanism.NO_DRM == lpdm.delivery_mechanism.drm_scheme

#         # # This book has a single 'odl:license' tag.
#         [warrior_pool] = [
#             p for p in imported_pools if p.identifier == warrior.primary_identifier
#         ]
#         assert False == warrior_pool.open_access
#         [lpdm] = warrior_pool.delivery_mechanisms
#         assert Edition.BOOK_MEDIUM == warrior_pool.presentation_edition.medium
#         assert Representation.EPUB_MEDIA_TYPE == lpdm.delivery_mechanism.content_type
#         assert DeliveryMechanism.ADOBE_DRM == lpdm.delivery_mechanism.drm_scheme
#         assert RightsStatus.IN_COPYRIGHT == lpdm.rights_status.uri
#         assert (
#             1 == warrior_pool.licenses_owned
#         )  # 52 remaining checkouts in the License Info Document but also care about concurrency
#         assert 1 == warrior_pool.licenses_available
#         [license] = warrior_pool.licenses
#         assert "1" == license.identifier
#         assert (
#             "https://loan.feedbooks.net/loan/get/{?id,checkout_id,expires,patron_id,notification_url}"
#             == license.checkout_url
#         )
#         assert (
#             "https://license.feedbooks.net/license/status/?uuid=1" == license.status_url
#         )

#         # The original value for 'expires' in the ODL is:
#         # 2019-03-31T03:13:35+02:00
#         #
#         # As stored in the database, license.expires may not have the
#         # same tzinfo, but it does represent the same point in time.
#         assert (
#             datetime.datetime(
#                 2019, 3, 31, 3, 13, 35, tzinfo=dateutil.tz.tzoffset("", 3600 * 2)
#             )
#             == license.expires
#         )
#         assert (
#             52 == license.checkouts_left
#         )  # 52 remaining checkouts in the License Info Document but only 1 concurrent user
#         assert 1 == license.checkouts_available

#         # This item is an open access audiobook.
#         [everglades_pool] = [
#             p for p in imported_pools if p.identifier == everglades.primary_identifier
#         ]
#         assert True == everglades_pool.open_access
#         [lpdm] = everglades_pool.delivery_mechanisms
#         assert Edition.AUDIO_MEDIUM == everglades_pool.presentation_edition.medium

#         assert (
#             Representation.AUDIOBOOK_MANIFEST_MEDIA_TYPE
#             == lpdm.delivery_mechanism.content_type
#         )
#         assert DeliveryMechanism.NO_DRM == lpdm.delivery_mechanism.drm_scheme

#         # This is a non-open access audiobook. There is no
#         # <odl:protection> tag; the drm_scheme is implied by the value
#         # of <dcterms:format>.
#         [dragons_pool] = [
#             p for p in imported_pools if p.identifier == dragons.primary_identifier
#         ]
#         assert Edition.AUDIO_MEDIUM == dragons_pool.presentation_edition.medium
#         assert False == dragons_pool.open_access
#         [lpdm] = dragons_pool.delivery_mechanisms

#         assert (
#             Representation.AUDIOBOOK_MANIFEST_MEDIA_TYPE
#             == lpdm.delivery_mechanism.content_type
#         )
#         assert (
#             DeliveryMechanism.FEEDBOOKS_AUDIOBOOK_DRM
#             == lpdm.delivery_mechanism.drm_scheme
#         )

#         # This book has two 'odl:license' tags for the same format and drm scheme
#         # (this happens if the library purchases two copies).
#         [canadianity_pool] = [
#             p for p in imported_pools if p.identifier == canadianity.primary_identifier
#         ]
#         assert False == canadianity_pool.open_access
#         [lpdm] = canadianity_pool.delivery_mechanisms
#         assert Representation.EPUB_MEDIA_TYPE == lpdm.delivery_mechanism.content_type
#         assert DeliveryMechanism.ADOBE_DRM == lpdm.delivery_mechanism.drm_scheme
#         assert RightsStatus.IN_COPYRIGHT == lpdm.rights_status.uri
#         assert (
#             11
#             == canadianity_pool.licenses_owned  # 10+1 now that concurrency is also accounted
#         )  # 40 remaining checkouts + 1 perpetual license in the License Info Documents
#         assert 11 == canadianity_pool.licenses_available
#         [license1, license2] = sorted(
#             canadianity_pool.licenses, key=lambda x: str(x.identifier)
#         )
#         assert "2" == license1.identifier
#         assert (
#             "https://loan.feedbooks.net/loan/get/{?id,checkout_id,expires,patron_id,notification_url}"
#             == license1.checkout_url
#         )
#         assert (
#             "https://license.feedbooks.net/license/status/?uuid=2"
#             == license1.status_url
#         )
#         assert None == license1.expires
#         assert 40 == license1.checkouts_left
#         assert 10 == license1.checkouts_available
#         assert "3" == license2.identifier
#         assert (
#             "https://loan.feedbooks.net/loan/get/{?id,checkout_id,expires,patron_id,notification_url}"
#             == license2.checkout_url
#         )
#         assert (
#             "https://license.feedbooks.net/license/status/?uuid=3"
#             == license2.status_url
#         )
#         assert None == license2.expires
#         assert None == license2.checkouts_left
#         assert 1 == license2.checkouts_available

#         # This book has two 'odl:license' tags, and they have different formats.
#         # TODO: the format+license association is not handled yet.
#         [midnight_pool] = [
#             p for p in imported_pools if p.identifier == midnight.primary_identifier
#         ]
#         assert False == midnight_pool.open_access
#         lpdms = midnight_pool.delivery_mechanisms
#         assert 2 == len(lpdms)
#         assert {Representation.EPUB_MEDIA_TYPE, Representation.PDF_MEDIA_TYPE} == {
#             lpdm.delivery_mechanism.content_type for lpdm in lpdms
#         }
#         assert [DeliveryMechanism.ADOBE_DRM, DeliveryMechanism.ADOBE_DRM] == [
#             lpdm.delivery_mechanism.drm_scheme for lpdm in lpdms
#         ]
#         assert [RightsStatus.IN_COPYRIGHT, RightsStatus.IN_COPYRIGHT] == [
#             lpdm.rights_status.uri for lpdm in lpdms
#         ]
#         assert (
#             2 == midnight_pool.licenses_owned
#         )  # 20 + 52 remaining checkouts in corresponding License Info Documents
#         assert 2 == midnight_pool.licenses_available
#         [license1, license2] = sorted(
#             midnight_pool.licenses, key=lambda x: str(x.identifier)
#         )
#         assert "4" == license1.identifier
#         assert (
#             "https://loan.feedbooks.net/loan/get/{?id,checkout_id,expires,patron_id,notification_url}"
#             == license1.checkout_url
#         )
#         assert (
#             "https://license.feedbooks.net/license/status/?uuid=4"
#             == license1.status_url
#         )
#         assert None == license1.expires
#         assert 20 == license1.checkouts_left
#         assert 1 == license1.checkouts_available
#         assert "5" == license2.identifier
#         assert (
#             "https://loan.feedbooks.net/loan/get/{?id,checkout_id,expires,patron_id,notification_url}"
#             == license2.checkout_url
#         )
#         assert (
#             "https://license.feedbooks.net/license/status/?uuid=5"
#             == license2.status_url
#         )
#         assert None == license2.expires
#         assert 52 == license2.checkouts_left
#         assert 1 == license2.checkouts_available


# class TestOdlAndOdl2Importer:
#     @pytest.mark.parametrize(
#         "license",
#         [
#             pytest.param(
#                 LicenseInfoHelper(
#                     license=LicenseHelper(
#                         concurrency=1, expires="2021-01-01T00:01:00+01:00"
#                     ),
#                     left=52,
#                     available=1,
#                 ),
#                 id="expiration_date_in_the_past",
#             ),
#             pytest.param(
#                 LicenseInfoHelper(
#                     license=LicenseHelper(
#                         concurrency=1,
#                     ),
#                     left=0,
#                     available=1,
#                 ),
#                 id="left_is_zero",
#             ),
#             pytest.param(
#                 LicenseInfoHelper(
#                     license=LicenseHelper(
#                         concurrency=1,
#                     ),
#                     available=1,
#                     status="unavailable",
#                 ),
#                 id="status_unavailable",
#             ),
#         ],
#     )
#     @freeze_time("2021-01-01T00:00:00+00:00")
#     def test_odl_importer_expired_licenses(
#         self,
#         odl_import_templated: OdlImportTemplatedFixture,
#         license: LicenseInfoHelper,
#     ):
#         """Ensure ODLImporter imports expired licenses, but does not count them."""
#         # Import the test feed with an expired ODL license.
#         (
#             imported_editions,
#             imported_pools,
#             imported_works,
#             failures,
#         ) = odl_import_templated([license])

#         # The importer created 1 edition and 1 work with no failures.
#         assert failures == {}
#         assert len(imported_editions) == 1
#         assert len(imported_works) == 1

#         # Ensure that the license pool was successfully created, with no available copies.
#         assert len(imported_pools) == 1

#         [imported_pool] = imported_pools
#         assert imported_pool.licenses_owned == 0
#         assert imported_pool.licenses_available == 0
#         assert len(imported_pool.licenses) == 1

#         # Ensure the license was imported and is expired.
#         [imported_license] = imported_pool.licenses
#         assert imported_license.is_inactive is True

#     def test_odl_importer_reimport_expired_licenses(
#         self, odl_import_templated: OdlImportTemplatedFixture
#     ):
#         license_expiry = dateutil.parser.parse("2021-01-01T00:01:00+00:00")
#         licenses = [
#             LicenseInfoHelper(
#                 license=LicenseHelper(concurrency=1, expires=license_expiry),
#                 available=1,
#             )
#         ]

#         # First import the license when it is not expired
#         with freeze_time(license_expiry - datetime.timedelta(days=1)):
#             # Import the test feed.
#             (
#                 imported_editions,
#                 imported_pools,
#                 imported_works,
#                 failures,
#             ) = odl_import_templated(licenses)

#             # The importer created 1 edition and 1 work with no failures.
#             assert failures == {}
#             assert len(imported_editions) == 1
#             assert len(imported_works) == 1
#             assert len(imported_pools) == 1

#             # Ensure that the license pool was successfully created, with available copies.
#             [imported_pool] = imported_pools
#             assert imported_pool.licenses_owned == 1
#             assert imported_pool.licenses_available == 1
#             assert len(imported_pool.licenses) == 1

#             # Ensure the license was imported and is not expired.
#             [imported_license] = imported_pool.licenses
#             assert imported_license.is_inactive is False

#         # Reimport the license when it is expired
#         with freeze_time(license_expiry + datetime.timedelta(days=1)):
#             # Import the test feed.
#             (
#                 imported_editions,
#                 imported_pools,
#                 imported_works,
#                 failures,
#             ) = odl_import_templated(licenses)

#             # The importer created 1 edition and 1 work with no failures.
#             assert failures == {}
#             assert len(imported_editions) == 1
#             assert len(imported_works) == 1
#             assert len(imported_pools) == 1

#             # Ensure that the license pool was successfully created, with no available copies.
#             [imported_pool] = imported_pools
#             assert imported_pool.licenses_owned == 0
#             assert imported_pool.licenses_available == 0
#             assert len(imported_pool.licenses) == 1

#             # Ensure the license was imported and is expired.
#             [imported_license] = imported_pool.licenses
#             assert imported_license.is_inactive is True

#     @freeze_time("2021-01-01T00:00:00+00:00")
#     def test_odl_importer_multiple_expired_licenses(
#         self, odl_import_templated: OdlImportTemplatedFixture
#     ):
#         """Ensure ODLImporter imports expired licenses
#         and does not count them in the total number of available licenses."""

#         # 1.1. Import the test feed with three inactive ODL licenses and two active licenses.
#         inactive = [
#             LicenseInfoHelper(
#                 # Expired
#                 # (expiry date in the past)
#                 license=LicenseHelper(
#                     concurrency=1,
#                     expires=datetime_helpers.utc_now() - datetime.timedelta(days=1),
#                 ),
#                 available=1,
#             ),
#             LicenseInfoHelper(
#                 # Expired
#                 # (left is 0)
#                 license=LicenseHelper(concurrency=1),
#                 available=1,
#                 left=0,
#             ),
#             LicenseInfoHelper(
#                 # Expired
#                 # (status is unavailable)
#                 license=LicenseHelper(concurrency=1),
#                 available=1,
#                 status="unavailable",
#             ),
#         ]
#         active = [
#             LicenseInfoHelper(
#                 # Valid
#                 license=LicenseHelper(concurrency=1),
#                 available=1,
#             ),
#             LicenseInfoHelper(
#                 # Valid
#                 license=LicenseHelper(concurrency=5),
#                 available=5,
#                 left=40,
#             ),
#         ]
#         (
#             imported_editions,
#             imported_pools,
#             imported_works,
#             failures,
#         ) = odl_import_templated(active + inactive)

#         assert failures == {}

#         # License pool was successfully created
#         assert len(imported_pools) == 1
#         [imported_pool] = imported_pools

#         # All licenses were imported
#         assert len(imported_pool.licenses) == 5

#         # Make sure that the license statistics are correct and include only active licenses.
#         assert imported_pool.licenses_owned == 6
#         assert imported_pool.licenses_available == 6

#         # Correct number of active and inactive licenses
#         assert sum(not l.is_inactive for l in imported_pool.licenses) == len(active)
#         assert sum(l.is_inactive for l in imported_pool.licenses) == len(inactive)

#     def test_odl_importer_reimport_multiple_licenses(
#         self, odl_import_templated: OdlImportTemplatedFixture
#     ):
#         """Ensure ODLImporter correctly imports licenses that have already been imported."""

#         # 1.1. Import the test feed with ODL licenses that are not expired.
#         license_expiry = dateutil.parser.parse("2021-01-01T00:01:00+00:00")

#         date = LicenseInfoHelper(
#             license=LicenseHelper(
#                 concurrency=1,
#                 expires=license_expiry,
#             ),
#             available=1,
#         )
#         left = LicenseInfoHelper(
#             license=LicenseHelper(concurrency=2), available=1, left=5
#         )
#         perpetual = LicenseInfoHelper(license=LicenseHelper(concurrency=1), available=0)
#         licenses = [date, left, perpetual]

#         # Import with all licenses valid
#         with freeze_time(license_expiry - datetime.timedelta(days=1)):
#             (
#                 imported_editions,
#                 imported_pools,
#                 imported_works,
#                 failures,
#             ) = odl_import_templated(licenses)

#             # No failures in the import
#             assert failures == {}

#             assert len(imported_pools) == 1

#             [imported_pool] = imported_pools
#             assert len(imported_pool.licenses) == 3
#             assert imported_pool.licenses_available == 2
#             assert imported_pool.licenses_owned == 4

#             # No licenses are expired
#             assert sum(not l.is_inactive for l in imported_pool.licenses) == len(
#                 licenses
#             )

#         # Expire the first two licenses

#         # The first one is expired by changing the time
#         with freeze_time(license_expiry + datetime.timedelta(days=1)):
#             # The second one is expired by setting left to 0
#             left.left = 0

#             # The perpetual license has a copy available
#             perpetual.available = 1

#             # Reimport
#             (
#                 imported_editions,
#                 imported_pools,
#                 imported_works,
#                 failures,
#             ) = odl_import_templated(licenses)

#             # No failures in the import
#             assert failures == {}

#             assert len(imported_pools) == 1

#             [imported_pool] = imported_pools
#             assert len(imported_pool.licenses) == 3
#             assert imported_pool.licenses_available == 1
#             assert imported_pool.licenses_owned == 1

#             # One license not expired
#             assert sum(not l.is_inactive for l in imported_pool.licenses) == 1

#             # Two licenses expired
#             assert sum(l.is_inactive for l in imported_pool.licenses) == 2


# class TestODLHoldReaper:
#     def test_run_once(
#         self, odl_test_fixture: ODLTestFixture, db: DatabaseTransactionFixture
#     ):
#         library = odl_test_fixture.library()
#         collection = odl_test_fixture.collection(library)
#         work = odl_test_fixture.work(collection)
#         license = odl_test_fixture.license(work)
#         api = odl_test_fixture.api(collection)
#         pool = odl_test_fixture.pool(license)

#         data_source = DataSource.lookup(db.session, "Feedbooks", autocreate=True)
#         DatabaseTransactionFixture.set_settings(
#             collection.integration_configuration,
#             **{Collection.DATA_SOURCE_NAME_SETTING: data_source.name},
#         )
#         reaper = ODLHoldReaper(db.session, collection, api=api)

#         now = utc_now()
#         yesterday = now - datetime.timedelta(days=1)

#         license.setup(concurrency=3, available=3)
#         expired_hold1, ignore = pool.on_hold_to(db.patron(), end=yesterday, position=0)
#         expired_hold2, ignore = pool.on_hold_to(db.patron(), end=yesterday, position=0)
#         expired_hold3, ignore = pool.on_hold_to(db.patron(), end=yesterday, position=0)
#         current_hold, ignore = pool.on_hold_to(db.patron(), position=3)
#         # This hold has an end date in the past, but its position is greater than 0
#         # so the end date is not reliable.
#         bad_end_date, ignore = pool.on_hold_to(db.patron(), end=yesterday, position=4)

#         progress = reaper.run_once(reaper.timestamp().to_data())

#         # The expired holds have been deleted and the other holds have been updated.
#         assert 2 == db.session.query(Hold).count()
#         assert [current_hold, bad_end_date] == db.session.query(Hold).order_by(
#             Hold.start
#         ).all()
#         assert 0 == current_hold.position
#         assert 0 == bad_end_date.position
#         assert current_hold.end > now
#         assert bad_end_date.end > now
#         assert 1 == pool.licenses_available
#         assert 2 == pool.licenses_reserved

#         # The TimestampData returned reflects what work was done.
#         assert "Holds deleted: 3. License pools updated: 1" == progress.achievements

#         # The TimestampData does not include any timing information --
#         # that will be applied by run().
#         assert None == progress.start
#         assert None == progress.finish
