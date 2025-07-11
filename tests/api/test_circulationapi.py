"""Test the CirculationAPI."""

import datetime
from datetime import timedelta
from typing import cast
from unittest.mock import MagicMock

import flask
import pytest
from flask import Flask
from freezegun import freeze_time

from api.circulation import (
    APIAwareFulfillmentInfo,
    BaseCirculationAPI,
    CirculationAPI,
    CirculationInfo,
    FulfillmentInfo,
    HoldInfo,
    LoanInfo,
)
from api.circulation_exceptions import *
from core.analytics import Analytics
from core.config import CannotLoadConfiguration
from core.integration.goals import Goals
from core.integration.registry import IntegrationRegistry
from core.mock_analytics_provider import MockAnalyticsProvider
from core.model import (
    CirculationEvent,
    DataSource,
    ExternalIntegration,
    Hold,
    Identifier,
    Loan,
)
from core.util.datetime_helpers import utc_now
from tests.api.mockapi.bibliotheca import MockBibliothecaAPI
from tests.api.mockapi.circulation import (
    MockCirculationAPI,
    MockPatronActivityCirculationAPI,
    MockRemoteAPI,
)
from tests.fixtures.database import DatabaseTransactionFixture
from tests.fixtures.library import LibraryFixture


class CirculationAPIFixture:
    def __init__(self, db: DatabaseTransactionFixture):
        self.db = db
        self.collection = MockBibliothecaAPI.mock_collection(
            db.session, db.default_library()
        )
        edition, self.pool = db.edition(
            data_source_name=DataSource.BIBLIOTHECA,
            identifier_type=Identifier.BIBLIOTHECA_ID,
            with_license_pool=True,
            collection=self.collection,
        )
        self.pool.open_access = False
        self.identifier = self.pool.identifier
        [self.delivery_mechanism] = self.pool.delivery_mechanisms
        self.patron = db.patron()
        self.analytics = MockAnalyticsProvider()
        registry: IntegrationRegistry[BaseCirculationAPI] = IntegrationRegistry(
            Goals.LICENSE_GOAL
        )
        registry.register(MockBibliothecaAPI, canonical=ExternalIntegration.BIBLIOTHECA)
        self.circulation = MockCirculationAPI(
            db.session,
            db.default_library(),
            analytics=self.analytics,
            registry=registry,
        )
        self.remote = self.circulation.api_for_license_pool(self.pool)


@pytest.fixture(scope="function")
def circulation_api(db: DatabaseTransactionFixture) -> CirculationAPIFixture:
    return CirculationAPIFixture(db)


class TestCirculationAPI:
    YESTERDAY = utc_now() - timedelta(days=1)
    TODAY = utc_now()
    TOMORROW = utc_now() + timedelta(days=1)
    IN_TWO_WEEKS = utc_now() + timedelta(days=14)

    @staticmethod
    def borrow(circulation_api: CirculationAPIFixture):
        return circulation_api.circulation.borrow(
            circulation_api.patron,
            "1234",
            circulation_api.pool,
            circulation_api.delivery_mechanism,
        )

    @staticmethod
    def sync_bookshelf(circulation_api: CirculationAPIFixture):
        return circulation_api.circulation.sync_bookshelf(
            circulation_api.patron, "1234"
        )

    def test_circulationinfo_collection_id(
        self, circulation_api: CirculationAPIFixture
    ):
        # It's possible to instantiate CirculationInfo (the superclass of all
        # other circulation-related *Info classes) with either a
        # Collection-like object or a numeric collection ID.
        cls = CirculationInfo
        other_args = [None] * 3

        info = cls(100, *other_args)
        assert 100 == info.collection_id

        info = cls(circulation_api.pool.collection, *other_args)
        assert circulation_api.pool.collection.id == info.collection_id

    def test_borrow_sends_analytics_event(self, circulation_api: CirculationAPIFixture):
        now = utc_now()
        loaninfo = LoanInfo.from_license_pool(
            circulation_api.pool,
            start_date=now,
            end_date=now + timedelta(seconds=3600),
            external_identifier=circulation_api.db.fresh_str(),
        )
        circulation_api.remote.queue_checkout(loaninfo)
        now = utc_now()

        loan, hold, is_new = self.borrow(circulation_api)

        # The Loan looks good.
        assert loaninfo.identifier == loan.license_pool.identifier.identifier
        assert circulation_api.patron == loan.patron
        assert None == hold
        assert True == is_new
        assert loaninfo.external_identifier == loan.external_identifier

        # An analytics event was created.
        assert 1 == circulation_api.analytics.count
        assert CirculationEvent.CM_CHECKOUT == circulation_api.analytics.event_type

        # Try to 'borrow' the same book again.
        circulation_api.remote.queue_checkout(AlreadyCheckedOut())
        loan, hold, is_new = self.borrow(circulation_api)
        assert False == is_new
        assert loaninfo.external_identifier == loan.external_identifier

        # Since the loan already existed, no new analytics event was
        # sent.
        assert 1 == circulation_api.analytics.count

        # Now try to renew the book.
        circulation_api.remote.queue_checkout(loaninfo)
        loan, hold, is_new = self.borrow(circulation_api)
        assert False == is_new

        # Renewals are counted as loans, since from an accounting
        # perspective they _are_ loans.
        assert 2 == circulation_api.analytics.count

        # Loans of open-access books go through a different code
        # path, but they count as loans nonetheless.
        circulation_api.pool.open_access = True
        circulation_api.remote.queue_checkout(loaninfo)
        loan, hold, is_new = self.borrow(circulation_api)
        assert 3 == circulation_api.analytics.count

    # Finland
    def test_borrow_is_added_to_checkout_history(
        self, circulation_api: CirculationAPIFixture
    ):
        now = utc_now()
        loaninfo = LoanInfo.from_license_pool(
            circulation_api.pool,
            start_date=now,
            end_date=now + timedelta(seconds=3600),
            external_identifier=circulation_api.db.fresh_str(),
        )
        circulation_api.remote.queue_checkout(loaninfo)
        now = utc_now()

        loan, hold, is_new = self.borrow(circulation_api)

        # A checkout history row was created
        assert 1 == len(circulation_api.patron.loan_checkouts)

        # Try to 'borrow' the same book again.
        circulation_api.remote.queue_checkout(AlreadyCheckedOut())
        loan, hold, is_new = self.borrow(circulation_api)

        assert loaninfo.external_identifier == loan.external_identifier

        # Since the loan already existed, no new history item was created.
        assert 1 == len(circulation_api.patron.loan_checkouts)

        # Now try to renew the book.
        circulation_api.remote.queue_checkout(loaninfo)
        loan, hold, is_new = self.borrow(circulation_api)

        # Renewals are counted as checkouts
        assert 2 == len(circulation_api.patron.loan_checkouts)

        # Loans of open-access books go through a different code
        # path, but they count as loans nonetheless.
        circulation_api.pool.open_access = True
        circulation_api.remote.queue_checkout(loaninfo)
        loan, hold, is_new = self.borrow(circulation_api)
        assert 3 == len(circulation_api.patron.loan_checkouts)

    @freeze_time()
    def test_attempt_borrow_with_existing_loan(
        self, circulation_api: CirculationAPIFixture
    ):
        """The patron has a loan that the circ manager doesn't know
        about, and they just tried to borrow a book they already have
        a loan for.
        """
        circulation_api.remote.queue_checkout(AlreadyCheckedOut())
        now = utc_now()
        loan, hold, is_new = self.borrow(circulation_api)

        # There is now a new local loan representing the remote loan.
        assert True == is_new
        assert circulation_api.pool == loan.license_pool
        assert circulation_api.patron == loan.patron
        assert None == hold

        # The server told us 'there's already a loan for this book'
        # but didn't give us any useful information on when that loan
        # was created. We've faked it with values that should be okay
        # until the next sync.
        assert (loan.start - now).seconds == 0
        assert (loan.end - loan.start).seconds == 3600

    def test_attempt_borrow_with_existing_remote_hold(
        self, circulation_api: CirculationAPIFixture
    ):
        """The patron has a remote hold that the circ manager doesn't know
        about, and they just tried to borrow a book they already have
        on hold.
        """
        circulation_api.remote.queue_checkout(AlreadyOnHold())
        now = utc_now()
        loan, hold, is_new = self.borrow(circulation_api)

        # There is now a new local hold representing the remote hold.
        assert True == is_new
        assert None == loan
        assert circulation_api.pool == hold.license_pool
        assert circulation_api.patron == hold.patron

        # The server told us 'you already have this book on hold' but
        # didn't give us any useful information on when that hold was
        # created. We've set the hold start time to the time we found
        # out about it. We'll get the real information the next time
        # we do a sync.
        assert abs((hold.start - now).seconds) < 2
        assert None == hold.end
        assert None == hold.position

    def test_attempt_premature_renew_with_local_loan(
        self, circulation_api: CirculationAPIFixture
    ):
        """We have a local loan and a remote loan but the patron tried to
        borrow again -- probably to renew their loan.
        """
        # Local loan.
        loan, ignore = circulation_api.pool.loan_to(circulation_api.patron)

        # This is the expected behavior in most cases--you tried to
        # renew the loan and failed because it's not time yet.
        circulation_api.remote.queue_checkout(CannotRenew())
        with pytest.raises(CannotRenew) as excinfo:
            self.borrow(circulation_api)
        assert "CannotRenew" in str(excinfo.value)

    def test_attempt_renew_with_local_loan_and_no_available_copies(
        self, circulation_api: CirculationAPIFixture
    ):
        """We have a local loan and a remote loan but the patron tried to
        borrow again -- probably to renew their loan.
        """
        # Local loan.
        loan, ignore = circulation_api.pool.loan_to(circulation_api.patron)

        # Remote loan.

        # NoAvailableCopies can happen if there are already people
        # waiting in line for the book. This case gives a more
        # specific error message.
        #
        # Contrast with the way NoAvailableCopies is handled in
        # test_loan_becomes_hold_if_no_available_copies.
        circulation_api.remote.queue_checkout(NoAvailableCopies())
        with pytest.raises(CannotRenew) as excinfo:
            self.borrow(circulation_api)
        assert "You cannot renew a loan if other patrons have the work on hold." in str(
            excinfo.value
        )

    def test_loan_becomes_hold_if_no_available_copies(
        self, circulation_api: CirculationAPIFixture
    ):
        # We want to borrow this book but there are no copies.
        circulation_api.remote.queue_checkout(NoAvailableCopies())
        holdinfo = HoldInfo.from_license_pool(
            circulation_api.pool,
            hold_position=10,
        )
        circulation_api.remote.queue_hold(holdinfo)

        # As such, an attempt to renew our loan results in us actually
        # placing a hold on the book.
        loan, hold, is_new = self.borrow(circulation_api)
        assert None == loan
        assert True == is_new
        assert circulation_api.pool == hold.license_pool
        assert circulation_api.patron == hold.patron

    def test_borrow_creates_hold_if_api_returns_hold_info(
        self, circulation_api: CirculationAPIFixture
    ):
        # There are no available copies, but the remote API
        # places a hold for us right away instead of raising
        # an error.
        holdinfo = HoldInfo.from_license_pool(
            circulation_api.pool,
            hold_position=10,
        )
        circulation_api.remote.queue_checkout(holdinfo)

        # As such, an attempt to borrow results in us actually
        # placing a hold on the book.
        loan, hold, is_new = self.borrow(circulation_api)
        assert None == loan
        assert True == is_new
        assert circulation_api.pool == hold.license_pool
        assert circulation_api.patron == hold.patron

    def test_vendor_side_loan_limit_allows_for_hold_placement(
        self, circulation_api: CirculationAPIFixture
    ):
        # Attempting to borrow a book will trigger a vendor-side loan
        # limit.
        circulation_api.remote.queue_checkout(PatronLoanLimitReached())

        # But the point is moot because the book isn't even available.
        # Attempting to place a hold will succeed.
        holdinfo = HoldInfo.from_license_pool(
            circulation_api.pool,
            hold_position=10,
        )
        circulation_api.remote.queue_hold(holdinfo)

        loan, hold, is_new = self.borrow(circulation_api)

        # No exception was raised, and the Hold looks good.
        assert holdinfo.identifier == hold.license_pool.identifier.identifier
        assert circulation_api.patron == hold.patron
        assert None == loan
        assert True == is_new

    def test_loan_exception_reraised_if_hold_placement_fails(
        self, circulation_api: CirculationAPIFixture
    ):
        # Attempting to borrow a book will trigger a vendor-side loan
        # limit.
        circulation_api.remote.queue_checkout(PatronLoanLimitReached())

        # Attempting to place a hold will fail because the book is
        # available. (As opposed to the previous test, where the book
        # was _not_ available and hold placement succeeded.) This
        # indicates that we should have raised PatronLoanLimitReached
        # in the first place.
        circulation_api.remote.queue_hold(CurrentlyAvailable())

        assert len(circulation_api.remote.responses["checkout"]) == 1
        assert len(circulation_api.remote.responses["hold"]) == 1

        # The exception raised is PatronLoanLimitReached, the first
        # one we encountered...
        pytest.raises(PatronLoanLimitReached, lambda: self.borrow(circulation_api))

        # ...but we made both requests and have no more responses
        # queued.
        assert not circulation_api.remote.responses["checkout"]
        assert not circulation_api.remote.responses["hold"]

    def test_hold_sends_analytics_event(self, circulation_api: CirculationAPIFixture):
        circulation_api.remote.queue_checkout(NoAvailableCopies())
        holdinfo = HoldInfo.from_license_pool(
            circulation_api.pool,
            hold_position=10,
        )
        circulation_api.remote.queue_hold(holdinfo)

        loan, hold, is_new = self.borrow(circulation_api)

        # The Hold looks good.
        assert holdinfo.identifier == hold.license_pool.identifier.identifier
        assert circulation_api.patron == hold.patron
        assert None == loan
        assert True == is_new

        # An analytics event was created.
        assert 1 == circulation_api.analytics.count
        assert CirculationEvent.CM_HOLD_PLACE == circulation_api.analytics.event_type

        # Try to 'borrow' the same book again.
        circulation_api.remote.queue_checkout(AlreadyOnHold())
        loan, hold, is_new = self.borrow(circulation_api)
        assert False == is_new

        # Since the hold already existed, no new analytics event was
        # sent.
        assert 1 == circulation_api.analytics.count

    @freeze_time()
    def test_attempt_borrow_with_existing_remote_loan(
        self, circulation_api: CirculationAPIFixture
    ):
        # Remote loan.
        circulation_api.remote.queue_checkout(AlreadyCheckedOut())
        now = utc_now()
        loan, hold, is_new = self.borrow(circulation_api)

        # There is now a new local loan representing the remote loan.
        assert True == is_new
        assert circulation_api.pool == loan.license_pool
        assert circulation_api.patron == loan.patron
        assert None == hold

        # The server told us 'there's already a loan for this book'
        # but didn't give us any useful information on when that loan
        # was created. We've faked it with values that should be okay
        # until the next sync.
        assert (loan.start - now).seconds == 0
        assert (loan.end - loan.start).seconds == 3600

    def test_borrow_with_expired_card_fails(
        self, circulation_api: CirculationAPIFixture
    ):
        # This checkout would succeed...
        # We use local time here, rather than UTC time, because we use
        # local time when checking for expired cards in authorization_is_active.
        now = datetime.datetime.now()
        loaninfo = LoanInfo.from_license_pool(
            circulation_api.pool,
            start_date=now,
            end_date=now + timedelta(seconds=3600),
            external_identifier=circulation_api.db.fresh_str(),
        )
        circulation_api.remote.queue_checkout(loaninfo)

        # ...except the patron's library card has expired.
        old_expires = circulation_api.patron.authorization_expires
        yesterday = now - timedelta(days=1)
        circulation_api.patron.authorization_expires = yesterday

        pytest.raises(AuthorizationExpired, lambda: self.borrow(circulation_api))
        circulation_api.patron.authorization_expires = old_expires

    def test_borrow_with_outstanding_fines(
        self, circulation_api: CirculationAPIFixture, library_fixture: LibraryFixture
    ):
        # This checkout would succeed...
        now = utc_now()
        loaninfo = LoanInfo.from_license_pool(
            circulation_api.pool,
            start_date=now,
            end_date=now + timedelta(seconds=3600),
            external_identifier=circulation_api.db.fresh_str(),
        )
        circulation_api.remote.queue_checkout(loaninfo)

        # ...except the patron has too many fines.
        old_fines = circulation_api.patron.fines
        circulation_api.patron.fines = 1000
        library = circulation_api.db.default_library()
        library_settings = library_fixture.settings(library)
        library_settings.max_outstanding_fines = 0.50

        pytest.raises(OutstandingFines, lambda: self.borrow(circulation_api))

        # Test the case where any amount of fines is too much.
        library_settings.max_outstanding_fines = 0
        pytest.raises(OutstandingFines, lambda: self.borrow(circulation_api))

        # Remove the fine policy, and borrow succeeds.
        library_settings.max_outstanding_fines = None
        loan, i1, i2 = self.borrow(circulation_api)
        assert isinstance(loan, Loan)

        circulation_api.patron.fines = old_fines

    def test_borrow_with_block_fails(self, circulation_api: CirculationAPIFixture):
        # This checkout would succeed...
        now = utc_now()
        loaninfo = LoanInfo.from_license_pool(
            circulation_api.pool,
            start_date=now,
            end_date=now + timedelta(seconds=3600),
            external_identifier=circulation_api.db.fresh_str(),
        )
        circulation_api.remote.queue_checkout(loaninfo)

        # ...except the patron is blocked
        circulation_api.patron.block_reason = "some reason"
        pytest.raises(AuthorizationBlocked, lambda: self.borrow(circulation_api))
        circulation_api.patron.block_reason = None

    def test_no_licenses_prompts_availability_update(
        self, circulation_api: CirculationAPIFixture
    ):
        # Once the library offered licenses for this book, but
        # the licenses just expired.
        circulation_api.remote.queue_checkout(NoLicenses())
        assert [] == circulation_api.remote.availability_updated_for

        # We're not able to borrow the book...
        pytest.raises(NoLicenses, lambda: self.borrow(circulation_api))

        # But the availability of the book gets immediately updated,
        # so that we don't keep offering the book.
        assert [circulation_api.pool] == circulation_api.remote.availability_updated_for

    def test_borrow_calls_enforce_limits(self, circulation_api: CirculationAPIFixture):
        # Verify that the normal behavior of CirculationAPI.borrow()
        # is to call enforce_limits() before trying to check out the
        # book.

        mock_api = MagicMock(spec=MockPatronActivityCirculationAPI)
        mock_api.checkout.side_effect = NotImplementedError()

        mock_circulation = CirculationAPI(
            circulation_api.db.session, circulation_api.db.default_library()
        )
        mock_circulation.enforce_limits = MagicMock()  # type: ignore[method-assign]
        mock_circulation.api_for_license_pool = MagicMock(return_value=mock_api)  # type: ignore[method-assign]

        # checkout() raised the expected NotImplementedError
        with pytest.raises(NotImplementedError):
            mock_circulation.borrow(
                circulation_api.patron,
                "",
                circulation_api.pool,
                circulation_api.pool,
                circulation_api.delivery_mechanism,
            )

        # But before that happened, enforce_limits was called once.
        mock_circulation.enforce_limits.assert_called_once_with(
            circulation_api.patron, circulation_api.pool
        )

    def test_patron_at_loan_limit(
        self, circulation_api: CirculationAPIFixture, library_fixture: LibraryFixture
    ):
        # The loan limit is a per-library setting.
        settings = library_fixture.settings(circulation_api.patron.library)

        future = utc_now() + timedelta(hours=1)

        # This patron has two loans that count towards the loan limit
        patron = circulation_api.patron
        circulation_api.pool.loan_to(circulation_api.patron, end=future)
        pool2 = circulation_api.db.licensepool(None)
        pool2.loan_to(circulation_api.patron, end=future)

        # An open-access loan doesn't count towards the limit.
        open_access_pool = circulation_api.db.licensepool(
            None, with_open_access_download=True
        )
        open_access_pool.loan_to(circulation_api.patron)

        # A loan of indefinite duration (no end date) doesn't count
        # towards the limit.
        indefinite_pool = circulation_api.db.licensepool(None)
        indefinite_pool.loan_to(circulation_api.patron, end=None)

        # Another patron's loans don't affect your limit.
        patron2 = circulation_api.db.patron()
        circulation_api.pool.loan_to(patron2)

        # patron_at_loan_limit returns True if your number of relevant
        # loans equals or exceeds the limit.
        m = circulation_api.circulation.patron_at_loan_limit
        assert settings.loan_limit is None
        assert m(patron) is False

        settings.loan_limit = 1
        assert m(patron) is True
        settings.loan_limit = 2
        assert m(patron) is True
        settings.loan_limit = 3
        assert m(patron) is False

        # Setting the loan limit to 0 is treated the same as disabling it.
        settings.loan_limit = 0
        assert m(patron) is False

        # Another library's setting doesn't affect your limit.
        other_library = circulation_api.db.library()
        library_fixture.settings(other_library).loan_limit = 1
        assert False is m(patron)

    def test_patron_at_hold_limit(
        self, circulation_api: CirculationAPIFixture, library_fixture: LibraryFixture
    ):
        # The hold limit is a per-library setting.
        library = circulation_api.patron.library
        library_settings = library_fixture.settings(library)

        # Unlike the loan limit, it's pretty simple -- every hold counts towards your limit.
        patron = circulation_api.patron
        circulation_api.pool.on_hold_to(circulation_api.patron)
        pool2 = circulation_api.db.licensepool(None)
        pool2.on_hold_to(circulation_api.patron)

        # Another patron's holds don't affect your limit.
        patron2 = circulation_api.db.patron()
        circulation_api.pool.on_hold_to(patron2)

        # patron_at_hold_limit returns True if your number of holds
        # equals or exceeds the limit.
        m = circulation_api.circulation.patron_at_hold_limit
        assert library.settings.hold_limit == None
        assert m(patron) is False

        library_settings.hold_limit = 1
        assert m(patron) is True
        library_settings.hold_limit = 2
        assert m(patron) is True
        library_settings.hold_limit = 3
        assert m(patron) is False

        # Setting the hold limit to 0 is treated the same as disabling it.
        library_settings.hold_limit = 0
        assert m(patron) is False

        # Another library's setting doesn't affect your limit.
        other_library = library_fixture.library()
        library_fixture.settings(other_library).hold_limit = 1
        assert m(patron) is False

    def test_enforce_limits(
        self, circulation_api: CirculationAPIFixture, library_fixture: LibraryFixture
    ):
        # Verify that enforce_limits works whether the patron is at one, both,
        # or neither of their loan limits.

        class MockVendorAPI:
            # Simulate a vendor API so we can watch license pool
            # availability being updated.
            def __init__(self):
                self.availability_updated = []

            def update_availability(self, pool):
                self.availability_updated.append(pool)

        # Set values for loan and hold limit, so we can verify those
        # values are propagated to the circulation exceptions raised
        # when a patron would exceed one of the limits.
        #
        # Both limits are set to the same value for the sake of
        # convenience in testing.

        mock_settings = library_fixture.mock_settings()
        mock_settings.loan_limit = 12
        mock_settings.hold_limit = 12
        library = library_fixture.library(settings=mock_settings)
        api = MockVendorAPI()

        class Mock(MockCirculationAPI):
            # Mock the loan and hold limit settings, and return a mock
            # CirculationAPI as needed.
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.api = api
                self.api_for_license_pool_calls = []
                self.patron_at_loan_limit_calls = []
                self.patron_at_hold_limit_calls = []
                self.at_loan_limit = False
                self.at_hold_limit = False

            def api_for_license_pool(self, pool):
                # Always return the same mock vendor API
                self.api_for_license_pool_calls.append(pool)
                return self.api

            def patron_at_loan_limit(self, patron):
                # Return the value set for self.at_loan_limit
                self.patron_at_loan_limit_calls.append(patron)
                return self.at_loan_limit

            def patron_at_hold_limit(self, patron):
                # Return the value set for self.at_hold_limit
                self.patron_at_hold_limit_calls.append(patron)
                return self.at_hold_limit

        circulation = Mock(circulation_api.db.session, library)

        # Sub-test 1: patron has reached neither limit.
        #
        patron = circulation_api.db.patron(library=library)
        pool = MagicMock()
        pool.open_access = False
        pool.unlimited_access = False
        circulation.at_loan_limit = False
        circulation.at_hold_limit = False

        circulation.enforce_limits(patron, pool)

        # To determine that the patron is under their limit, it was
        # necessary to call patron_at_loan_limit and
        # patron_at_hold_limit.
        assert patron == circulation.patron_at_loan_limit_calls.pop()
        assert patron == circulation.patron_at_hold_limit_calls.pop()

        # But it was not necessary to update the availability for the
        # LicensePool, since the patron was not at either limit.
        assert [] == api.availability_updated

        # Sub-test 2: patron has reached both limits.
        #
        circulation.at_loan_limit = True
        circulation.at_hold_limit = True

        with pytest.raises(PatronLoanLimitReached) as excinfo:
            circulation.enforce_limits(patron, pool)
        # If .limit is set it means we were able to find a
        # specific limit, which means the exception was instantiated
        # correctly.
        #
        # The presence of .limit will let us give a more specific
        # error message when the exception is converted to a
        # problem detail document.
        assert 12 == excinfo.value.limit

        # We were able to deduce that the patron can't do anything
        # with this book, without having to ask the API about
        # availability.
        assert patron == circulation.patron_at_loan_limit_calls.pop()
        assert patron == circulation.patron_at_hold_limit_calls.pop()
        assert [] == api.availability_updated

        # At this point we need to start using a real LicensePool.
        pool = circulation_api.pool

        # Sub-test 3: patron is at loan limit but not hold limit.
        #
        circulation.at_loan_limit = True
        circulation.at_hold_limit = False

        # If the book is available, we get PatronLoanLimitReached
        pool.licenses_available = 1
        with pytest.raises(PatronLoanLimitReached) as loan_limit_info:
            circulation.enforce_limits(patron, pool)
        assert 12 == loan_limit_info.value.limit

        # Reaching this conclusion required checking both patron
        # limits and asking the remote API for updated availability
        # information for this LicensePool.
        assert patron == circulation.patron_at_loan_limit_calls.pop()
        assert patron == circulation.patron_at_hold_limit_calls.pop()
        assert pool == api.availability_updated.pop()

        # If the LicensePool is not available, we pass the
        # test. Placing a hold is fine here.
        pool.licenses_available = 0
        circulation.enforce_limits(patron, pool)
        assert patron == circulation.patron_at_loan_limit_calls.pop()
        assert patron == circulation.patron_at_hold_limit_calls.pop()
        assert pool == api.availability_updated.pop()

        # Sub-test 4: patron is at hold limit but not loan limit
        #
        circulation.at_loan_limit = False
        circulation.at_hold_limit = True

        # Test updated: If the book is not available, we should NOT yet
        # raise PatronHoldLimitReached. The patron is at their hold limit
        # but they may be trying to take out a loan on a book that is
        # reserved for them (hold position 0). We want to let them proceed
        # at this point.
        pool.licenses_available = 0
        try:
            circulation.enforce_limits(patron, pool)
        except PatronHoldLimitReached:
            assert False, "PatronHoldLimitReached is raised when it shouldn't"
        else:
            # If no exception is raised, the test should pass
            assert True

        # Reaching this conclusion required checking both patron
        # limits. The remote API isn't queried for updated availability
        # information for this LicensePool.
        assert patron == circulation.patron_at_loan_limit_calls.pop()
        assert patron == circulation.patron_at_hold_limit_calls.pop()
        assert [] == api.availability_updated

        # If the book is available, we're fine -- we're not at our loan limit.
        # The remote API isn't queried for updated availability
        # information for this LicensePool.
        pool.licenses_available = 1
        circulation.enforce_limits(patron, pool)
        assert patron == circulation.patron_at_loan_limit_calls.pop()
        assert patron == circulation.patron_at_hold_limit_calls.pop()
        assert [] == api.availability_updated

    def test_borrow_hold_limit_reached(
        self, circulation_api: CirculationAPIFixture, library_fixture: LibraryFixture
    ):
        # Verify that you can't place a hold on an unavailable book
        # if you're at your hold limit.
        #
        # NOTE: This is redundant except as an end-to-end test.

        # The hold limit is 1, and the patron has a previous hold.
        library_fixture.settings(circulation_api.patron.library).hold_limit = 1
        other_pool = circulation_api.db.licensepool(None)
        other_pool.open_access = False
        other_pool.on_hold_to(circulation_api.patron)
        # The patron wants to take out a loan on another title which is not available.
        circulation_api.remote.queue_checkout(NoAvailableCopies())

        try:
            self.borrow(circulation_api)
        except Exception as e:
            # The result is a PatronHoldLimitReached.
            assert isinstance(e, PatronHoldLimitReached)

        # If we increase the limit, borrow succeeds.
        library_fixture.settings(circulation_api.patron.library).hold_limit = 2
        circulation_api.remote.queue_checkout(NoAvailableCopies())
        now = utc_now()
        holdinfo = HoldInfo.from_license_pool(
            circulation_api.pool,
            hold_position=10,
            start_date=now,
            end_date=now + timedelta(seconds=3600),
        )
        circulation_api.remote.queue_hold(holdinfo)
        loan, hold, is_new = self.borrow(circulation_api)
        assert hold != None

    def test_borrow_no_available_copies_when_reserved(
        self, circulation_api: CirculationAPIFixture, library_fixture: LibraryFixture
    ):
        """
        The hold limit is 1, and the patron has a hold with position 0 in the hold queue on a book they're
        trying to checkout. When the patron tries to borrow the book but it turns out to not be available
        and a specific exception is raised. Hold data has been updated prior to raising the specific exception
        on the remote (ODL api).
        """

        # The hold limit is 1
        library_fixture.settings(circulation_api.patron.library).hold_limit = 1

        # The patron has a hold with position 0 in the hold queue
        existing_hold, is_new = circulation_api.pool.on_hold_to(
            circulation_api.patron,
            start=self.YESTERDAY,
            end=self.TOMORROW,
            position=0,
        )
        # The patron wants to take out their hold for loan but which turns out to not be available.
        circulation_api.remote.queue_checkout(NoAvailableCopiesWhenReserved())

        # The patron tries to borrow the book but gets a NoAvailableCopiesWhenReserved exception
        with pytest.raises(NoAvailableCopiesWhenReserved):
            self.borrow(circulation_api)

    def test_fulfill_errors(self, circulation_api: CirculationAPIFixture):
        # Here's an open-access title.
        collection = circulation_api.db.collection()
        circulation_api.pool.open_access = True
        circulation_api.pool.collection = collection

        # The patron has the title on loan.
        circulation_api.pool.loan_to(circulation_api.patron)

        # It has a LicensePoolDeliveryMechanism that is broken (has no
        # associated Resource).
        circulation_api.circulation.queue_fulfill(
            circulation_api.pool, FormatNotAvailable()
        )

        # fulfill() will raise FormatNotAvailable.
        pytest.raises(
            FormatNotAvailable,
            circulation_api.circulation.fulfill,
            circulation_api.patron,
            "1234",
            circulation_api.pool,
            circulation_api.delivery_mechanism,
        )

    def test_fulfill(self, circulation_api: CirculationAPIFixture):
        circulation_api.pool.loan_to(circulation_api.patron)

        fulfillment = circulation_api.pool.delivery_mechanisms[0]
        fulfillment.content = "Fulfilled."
        fulfillment.content_link = None
        circulation_api.remote.queue_fulfill(fulfillment)

        result = circulation_api.circulation.fulfill(
            circulation_api.patron,
            "1234",
            circulation_api.pool,
            circulation_api.pool.delivery_mechanisms[0],
        )

        # The fulfillment looks good.
        assert fulfillment == result

        # An analytics event was created.
        assert 1 == circulation_api.analytics.count
        assert CirculationEvent.CM_FULFILL == circulation_api.analytics.event_type

    def test_fulfill_without_loan(self, circulation_api: CirculationAPIFixture):
        # By default, a title cannot be fulfilled unless there is an active
        # loan for the title (tested above, in test_fulfill).
        fulfillment = circulation_api.pool.delivery_mechanisms[0]
        fulfillment.content = "Fulfilled."
        fulfillment.content_link = None
        circulation_api.remote.queue_fulfill(fulfillment)

        def try_to_fulfill():
            # Note that we're passing None for `patron`.
            return circulation_api.circulation.fulfill(
                None,
                "1234",
                circulation_api.pool,
                circulation_api.pool.delivery_mechanisms[0],
            )

        pytest.raises(NoActiveLoan, try_to_fulfill)

        # However, if CirculationAPI.can_fulfill_without_loan() says it's
        # okay, the title will be fulfilled anyway.
        def yes_we_can(*args, **kwargs):
            return True

        circulation_api.circulation.can_fulfill_without_loan = yes_we_can
        result = try_to_fulfill()
        assert fulfillment == result

    @pytest.mark.parametrize("open_access", [True, False])
    def test_revoke_loan(self, circulation_api: CirculationAPIFixture, open_access):
        circulation_api.pool.open_access = open_access

        circulation_api.patron.last_loan_activity_sync = utc_now()
        circulation_api.pool.loan_to(circulation_api.patron)
        circulation_api.remote.queue_checkin(True)
        result = circulation_api.circulation.revoke_loan(
            circulation_api.patron, "1234", circulation_api.pool
        )
        assert True == result

        # The patron's loan activity is now out of sync.
        assert None == circulation_api.patron.last_loan_activity_sync

        # An analytics event was created.
        assert 1 == circulation_api.analytics.count
        assert CirculationEvent.CM_CHECKIN == circulation_api.analytics.event_type

    @pytest.mark.parametrize("open_access", [True, False])
    def test_release_hold(self, circulation_api: CirculationAPIFixture, open_access):
        circulation_api.pool.open_access = open_access

        circulation_api.patron.last_loan_activity_sync = utc_now()
        circulation_api.pool.on_hold_to(circulation_api.patron)
        circulation_api.remote.queue_release_hold(True)

        result = circulation_api.circulation.release_hold(
            circulation_api.patron, "1234", circulation_api.pool
        )
        assert True == result

        # The patron's loan activity is now out of sync.
        assert None == circulation_api.patron.last_loan_activity_sync

        # An analytics event was created.
        assert 1 == circulation_api.analytics.count
        assert CirculationEvent.CM_HOLD_RELEASE == circulation_api.analytics.event_type

    def test__collect_event(self, circulation_api: CirculationAPIFixture):
        # Test the _collect_event method, which gathers information
        # from the current request and sends out the appropriate
        # circulation events.
        class MockAnalytics:
            def __init__(self):
                self.events = []

            def collect_event(self, library, licensepool, name, neighborhood, duration):
                self.events.append((library, licensepool, name, neighborhood, duration))
                return True

        analytics = MockAnalytics()

        l1 = circulation_api.db.default_library()
        l2 = circulation_api.db.library()

        p1 = circulation_api.db.patron(library=l1)
        p2 = circulation_api.db.patron(library=l2)

        lp1 = circulation_api.db.licensepool(edition=None)
        lp2 = circulation_api.db.licensepool(edition=None)

        api = CirculationAPI(circulation_api.db.session, l1, cast(Analytics, analytics))

        def assert_event(inp, outp):
            # Assert that passing `inp` into the mock _collect_event
            # method calls collect_event() on the MockAnalytics object
            # with `outp` as the arguments

            # Call the method
            api._collect_event(*inp)

            # Check the 'event' that was created inside the method.
            assert outp == analytics.events.pop()

            # Validate that only one 'event' was created.
            assert [] == analytics.events

        # Worst case scenario -- the only information we can find is
        # the Library associated with the CirculationAPI object itself.
        assert_event((None, None, "event"), (l1, None, "event", None, None))

        # If a LicensePool is provided, it's passed right through
        # to Analytics.collect_event.
        assert_event((None, lp2, "event"), (l1, lp2, "event", None, None))

        # If a Patron is provided, their Library takes precedence over
        # the Library associated with the CirculationAPI (though this
        # shouldn't happen).
        assert_event((p2, None, "event"), (l2, None, "event", None, None))

        # We must run the rest of the tests in a simulated Flask request
        # context.
        app = Flask(__name__)
        with app.test_request_context():
            # The request library takes precedence over the Library
            # associated with the CirculationAPI (though this
            # shouldn't happen).
            flask.request.library = l2  # type: ignore
            assert_event((None, None, "event"), (l2, None, "event", None, None))

        with app.test_request_context():
            # The library of the request patron also takes precedence
            # over both (though again, this shouldn't happen).
            flask.request.library = l1  # type: ignore
            flask.request.patron = p2  # type: ignore
            assert_event((None, None, "event"), (l2, None, "event", None, None))

        # Now let's check neighborhood gathering.
        p2.neighborhood = "Compton"
        with app.test_request_context():
            # Neighborhood is only gathered if we explicitly ask for
            # it.
            flask.request.patron = p2  # type: ignore
            assert_event((p2, None, "event"), (l2, None, "event", None, None))
            assert_event((p2, None, "event", False), (l2, None, "event", None, None))
            assert_event(
                (p2, None, "event", True), (l2, None, "event", "Compton", None)
            )

            # Neighborhood is not gathered if the request's active
            # patron is not the patron who triggered the event.
            assert_event((p1, None, "event", True), (l1, None, "event", None, None))

        with app.test_request_context():
            # Even if we ask for it, neighborhood is not gathered if
            # the data isn't available.
            flask.request.patron = p1  # type: ignore
            assert_event((p1, None, "event", True), (l1, None, "event", None, None))

        # Finally, remove the mock Analytics object entirely and
        # verify that calling _collect_event doesn't cause a crash.
        api.analytics = None
        api._collect_event(p1, None, "event")

    def test_sync_bookshelf_ignores_local_loan_with_no_identifier(
        self, circulation_api: CirculationAPIFixture
    ):
        loan, ignore = circulation_api.pool.loan_to(circulation_api.patron)
        loan.start = self.YESTERDAY
        circulation_api.pool.identifier = None

        # Verify that we can sync without crashing.
        self.sync_bookshelf(circulation_api)

        # The invalid loan was ignored and is still there.
        loans = circulation_api.db.session.query(Loan).all()
        assert [loan] == loans

        # Even worse - the loan has no license pool!
        loan.license_pool = None

        # But we can still sync without crashing.
        self.sync_bookshelf(circulation_api)

    def test_sync_bookshelf_ignores_local_hold_with_no_identifier(
        self, circulation_api: CirculationAPIFixture
    ):
        hold, ignore = circulation_api.pool.on_hold_to(circulation_api.patron)
        circulation_api.pool.identifier = None

        # Verify that we can sync without crashing.
        self.sync_bookshelf(circulation_api)

        # The invalid hold was ignored and is still there.
        holds = circulation_api.db.session.query(Hold).all()
        assert [hold] == holds

        # Even worse - the hold has no license pool!
        hold.license_pool = None

        # But we can still sync without crashing.
        self.sync_bookshelf(circulation_api)

    def test_sync_bookshelf_updates_hold_with_modified_timestamps(
        self, circulation_api: CirculationAPIFixture
    ):
        edition, pool2 = circulation_api.db.edition(
            data_source_name=DataSource.BIBLIOTHECA,
            identifier_type=Identifier.BIBLIOTHECA_ID,
            with_license_pool=True,
            collection=circulation_api.collection,
        )
        # Don't really see this happening but let's say we have a local hold...
        hold, ignore = pool2.on_hold_to(circulation_api.patron)
        hold.start = self.YESTERDAY
        hold.end = self.TOMORROW
        hold.position = 10
        # Let's pretend that for some weird reason the "remote" hold (ODL holds are local) data differs from the local hold
        circulation_api.circulation.add_remote_hold(
            HoldInfo.from_license_pool(
                pool2,
                start_date=self.TODAY,
                end_date=self.IN_TWO_WEEKS,
                hold_position=0,
            )
        )

        circulation_api.circulation.sync_bookshelf(circulation_api.patron, "1234")

        assert self.TODAY == hold.start
        assert self.IN_TWO_WEEKS == hold.end
        assert 0 == hold.position

    def test_sync_bookshelf_respects_last_loan_activity_sync(
        self, circulation_api: CirculationAPIFixture
    ):
        # We believe we have up-to-date loan activity for this patron.
        now = utc_now()
        circulation_api.patron.last_loan_activity_sync = now

        # Syncing our loans with the remote won't actually do anything.
        circulation_api.circulation.sync_bookshelf(circulation_api.patron, "1234")
        assert [] == circulation_api.patron.loans

        # But eventually, our local knowledge will grow stale.
        long_ago = now - timedelta(
            seconds=circulation_api.patron.loan_activity_max_age * 2
        )
        circulation_api.patron.last_loan_activity_sync = long_ago

        # At that point, sync_bookshelf _will_ go out to the remote.
        now = utc_now()
        circulation_api.circulation.sync_bookshelf(circulation_api.patron, "1234")
        # Still no loans
        assert 0 == len(circulation_api.patron.loans)

        # Once that happens, patron.last_loan_activity_sync is updated to
        # the current time.
        updated = circulation_api.patron.last_loan_activity_sync
        assert (updated - now).total_seconds() < 2

        # It's also possible to force a sync even when one wouldn't
        # normally happen, by passing force=True into sync_bookshelf.
        circulation_api.circulation.remote_loans = []

        circulation_api.circulation.sync_bookshelf(
            circulation_api.patron, "1234", force=True
        )
        assert [] == circulation_api.patron.loans
        assert circulation_api.patron.last_loan_activity_sync > updated

    def test_can_fulfill_without_loan(self, circulation_api: CirculationAPIFixture):
        """Can a title can be fulfilled without an active loan?  It depends on
        the BaseCirculationAPI implementation for that title's colelction.
        """

        class Mock(MockPatronActivityCirculationAPI):
            def can_fulfill_without_loan(self, patron, pool, lpdm):
                return "yep"

        pool = circulation_api.db.licensepool(None)
        circulation = CirculationAPI(
            circulation_api.db.session, circulation_api.db.default_library()
        )
        mock = MagicMock(spec=MockPatronActivityCirculationAPI)
        mock.can_fulfill_without_loan = MagicMock(return_value="yep")
        circulation.api_for_collection[pool.collection.id] = mock
        assert "yep" == circulation.can_fulfill_without_loan(None, pool, MagicMock())

        # If format data is missing or the BaseCirculationAPI cannot
        # be found, we assume the title cannot be fulfilled.
        assert False == circulation.can_fulfill_without_loan(None, pool, None)
        assert False == circulation.can_fulfill_without_loan(None, None, MagicMock())

        circulation.api_for_collection = {}
        assert False == circulation.can_fulfill_without_loan(None, pool, None)

        # An open access pool can be fulfilled even without the BaseCirculationAPI.
        pool.open_access = True
        assert True == circulation.can_fulfill_without_loan(None, pool, MagicMock())


class TestBaseCirculationAPI:
    def test_default_notification_email_address(
        self, db: DatabaseTransactionFixture, library_fixture: LibraryFixture
    ):
        # Test the ability to get the default notification email address
        # for a patron or a library.
        settings = library_fixture.mock_settings()
        settings.default_notification_email_address = "help@example.com"  # type: ignore[assignment]
        library = library_fixture.library(settings=settings)
        patron = db.patron(library=library)
        m = BaseCirculationAPI.default_notification_email_address
        assert "help@example.com" == m(library, "")
        assert "help@example.com" == m(patron, "")
        other_library = library_fixture.library()
        assert "noreply@thepalaceproject.org" == m(other_library, "")

    def test_can_fulfill_without_loan(self, db: DatabaseTransactionFixture):
        """By default, there is a blanket prohibition on fulfilling a title
        when there is no active loan.
        """
        api = MockRemoteAPI(db.session, db.default_collection())
        assert False == api.can_fulfill_without_loan(
            MagicMock(), MagicMock(), MagicMock()
        )


class TestConfigurationFailures:
    def test_configuration_exception_is_stored(self, db: DatabaseTransactionFixture):
        # If the initialization of an API object raises
        # CannotLoadConfiguration, the exception is stored with the
        # CirculationAPI rather than being propagated.

        registry: IntegrationRegistry[BaseCirculationAPI] = IntegrationRegistry(
            Goals.LICENSE_GOAL
        )
        mock_api = MagicMock()
        mock_api.side_effect = CannotLoadConfiguration("doomed!")
        mock_api.__name__ = "mock api"
        registry.register(mock_api, canonical=db.default_collection().protocol)
        circulation = CirculationAPI(
            db.session, db.default_library(), registry=registry
        )

        # Although the CirculationAPI was created, it has no functioning
        # APIs.
        assert {} == circulation.api_for_collection

        # Instead, the CannotLoadConfiguration exception raised by the
        # constructor has been stored in initialization_exceptions.
        e = circulation.initialization_exceptions[db.default_collection().id]
        assert isinstance(e, CannotLoadConfiguration)
        assert "doomed!" == str(e)


class TestFulfillmentInfo:
    def test_as_response(self, db: DatabaseTransactionFixture):
        # The default behavior of as_response is to do nothing
        # and let controller code turn the FulfillmentInfo
        # into a Flask Response.
        info = FulfillmentInfo(
            db.default_collection(), None, None, None, None, None, None, None
        )
        assert None == info.as_response


class APIAwareFulfillmentFixture:
    def __init__(self, db: DatabaseTransactionFixture):
        self.db = db
        self.collection = db.default_collection()

        # Create a bunch of mock objects which will be used to initialize
        # the instance variables of MockAPIAwareFulfillmentInfo objects.
        self.mock_data_source_name = MagicMock()
        self.mock_identifier_type = MagicMock()
        self.mock_identifier = MagicMock()
        self.mock_key = MagicMock()


@pytest.fixture(scope="function")
def api_aware_fulfillment_fixture(
    db: DatabaseTransactionFixture,
) -> APIAwareFulfillmentFixture:
    return APIAwareFulfillmentFixture(db)


class TestAPIAwareFulfillmentInfo:
    # The APIAwareFulfillmentInfo class has the same properties as a
    # regular FulfillmentInfo -- content_link and so on -- but their
    # values are filled dynamically the first time one of them is
    # accessed, by calling the do_fetch() method.

    class MockAPIAwareFulfillmentInfo(APIAwareFulfillmentInfo):
        """An APIAwareFulfillmentInfo that implements do_fetch() by delegating
        to its API object.
        """

        def do_fetch(self):
            return self.api.do_fetch()

    class MockAPI:
        """An API class that sets a flag when do_fetch()
        is called.
        """

        def __init__(self, collection):
            self.collection = collection
            self.fetch_happened = False

        def do_fetch(self):
            self.fetch_happened = True

    def make_info(
        self, api_aware_fulfillment_fixture: APIAwareFulfillmentFixture, api=None
    ):
        # Create a MockAPIAwareFulfillmentInfo with
        # well-known mock values for its properties.
        return self.MockAPIAwareFulfillmentInfo(
            api,
            api_aware_fulfillment_fixture.mock_data_source_name,
            api_aware_fulfillment_fixture.mock_identifier_type,
            api_aware_fulfillment_fixture.mock_identifier,
            api_aware_fulfillment_fixture.mock_key,
        )

    def test_constructor(
        self, api_aware_fulfillment_fixture: APIAwareFulfillmentFixture
    ):
        data = api_aware_fulfillment_fixture

        # The constructor sets the instance variables appropriately,
        # but does not call do_fetch() or set any of the variables
        # that imply do_fetch() has happened.

        # Create a MockAPI
        api = self.MockAPI(data.collection)

        # Create an APIAwareFulfillmentInfo based on that API.
        info = self.make_info(api_aware_fulfillment_fixture, api)
        assert api == info.api
        assert data.mock_key == info.key
        assert data.collection == api.collection
        assert api.collection == info.collection(data.db.session)
        assert data.mock_data_source_name == info.data_source_name
        assert data.mock_identifier_type == info.identifier_type
        assert data.mock_identifier == info.identifier

        # The fetch has not happened.
        assert False == api.fetch_happened
        assert None == info._content_link
        assert None == info._content_type
        assert None == info._content
        assert None == info._content_expires

    def test_fetch(self, api_aware_fulfillment_fixture: APIAwareFulfillmentFixture):
        data = api_aware_fulfillment_fixture

        # Verify that fetch() calls api.do_fetch()
        api = self.MockAPI(data.collection)
        info = self.make_info(api_aware_fulfillment_fixture, api)
        assert False == info._fetched
        assert False == api.fetch_happened
        info.fetch()
        assert True == info._fetched
        assert True == api.fetch_happened

        # We don't check that values like _content_link were set,
        # because our implementation of do_fetch() doesn't set any of
        # them. Different implementations may set different subsets
        # of these values.

    def test_properties_fetch_on_demand(
        self, api_aware_fulfillment_fixture: APIAwareFulfillmentFixture
    ):
        data = api_aware_fulfillment_fixture

        # Verify that accessing each of the properties calls fetch()
        # if it hasn't been called already.
        api = self.MockAPI(data.collection)
        info = self.make_info(api_aware_fulfillment_fixture, api)
        assert False == info._fetched
        info.content_link
        assert True == info._fetched

        info = self.make_info(api_aware_fulfillment_fixture, api)
        assert False == info._fetched
        info.content_type
        assert True == info._fetched

        info = self.make_info(api_aware_fulfillment_fixture, api)
        assert False == info._fetched
        info.content
        assert True == info._fetched

        info = self.make_info(api_aware_fulfillment_fixture, api)
        assert False == info._fetched
        info.content_expires
        assert True == info._fetched

        # Once the data has been fetched, accessing one of the properties
        # doesn't call fetch() again.
        info.fetch_happened = False
        info.content_expires
        assert False == info.fetch_happened
