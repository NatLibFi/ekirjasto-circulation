from __future__ import annotations

import datetime
from unittest.mock import call, patch

import pytest

from core.config import Configuration, ConfigurationConstants
from core.jobs.notifications.loan_expiration_notification import (
    LoanNotificationsMonitor,
)
from core.model import ConfigurationSetting, Work, get_one_or_create
from core.model.devicetokens import DeviceToken, DeviceTokenTypes
from core.model.patron import Patron
from core.util.datetime_helpers import utc_now
from core.util.notifications import PushNotifications
from tests.fixtures.database import DatabaseTransactionFixture


class LoanNotificationsFixture:
    def __init__(self, db: DatabaseTransactionFixture) -> None:
        self.db = db
        self.monitor = LoanNotificationsMonitor(self.db.session)
        self.patron: Patron = db.patron()
        self.work: Work = db.work(with_license_pool=True)
        # Cache the LicensePool to avoid `Optional` typing fights at each
        # call site: `Work.active_license_pool()` is typed as
        # `LicensePool | None`, but we know it exists because we just
        # created the work with `with_license_pool=True`.
        pool = self.work.active_license_pool()
        assert pool is not None
        self.pool = pool
        # process_items() skips loans whose patron has no FCM-eligible
        # device tokens, so the default test patron is given one here.
        self.device_token, _ = get_one_or_create(
            db.session,
            DeviceToken,
            patron=self.patron,
            token_type=DeviceTokenTypes.FCM_ANDROID,
            device_token="atesttoken",
        )
        PushNotifications.TESTING_MODE = True


@pytest.fixture(scope="function")
def loan_fixture(db: DatabaseTransactionFixture) -> LoanNotificationsFixture:
    return LoanNotificationsFixture(db)


class TestLoanNotificationsMonitor:
    def test_item_query(self, loan_fixture: LoanNotificationsFixture):
        """item_query() must select only loans inside the [3d, 1d] expiry
        windows that have not already been notified today and whose patron
        is non-null."""
        db = loan_fixture.db
        now = utc_now()

        # Inside the 1-day window → selected.
        loan_1d, _ = loan_fixture.pool.loan_to(
            loan_fixture.patron,
            now,
            now + datetime.timedelta(hours=23),
        )

        # Inside the 3-day window → selected.
        work_3d = db.work(with_license_pool=True)
        loan_3d, _ = work_3d.active_license_pool().loan_to(
            loan_fixture.patron,
            now,
            now + datetime.timedelta(days=2, hours=23),
        )

        # Outside any window (between 1d and 3d) → NOT selected.
        work_2d = db.work(with_license_pool=True)
        work_2d.active_license_pool().loan_to(
            loan_fixture.patron,
            now,
            now + datetime.timedelta(days=1, hours=23),
        )

        # Inside the 1-day window but already notified today → NOT selected.
        work_notified = db.work(with_license_pool=True)
        loan_notified, _ = work_notified.active_license_pool().loan_to(
            loan_fixture.patron,
            now,
            now + datetime.timedelta(hours=22),
        )
        loan_notified.patron_last_notified = now.date()

        # Inside the 1-day window, notified yesterday → selected.
        work_yesterday = db.work(with_license_pool=True)
        loan_yesterday, _ = work_yesterday.active_license_pool().loan_to(
            loan_fixture.patron,
            now,
            now + datetime.timedelta(hours=21),
        )
        loan_yesterday.patron_last_notified = now.date() - datetime.timedelta(days=1)

        results = set(loan_fixture.monitor.item_query().all())
        assert results == {loan_1d, loan_3d, loan_yesterday}

    def test_item_query_bucket_aware_cooldown(
        self, loan_fixture: LoanNotificationsFixture
    ):
        """A loan that has already received its 3-day notification must NOT
        be selected on subsequent cron runs that still fall inside the same
        3-day window (regression test for the duplicate-per-bucket bug).

        The 3-day window is `(now+2d, now+3d]` — a sliding 24h slice. A loan
        ending just past midnight of (today+2d) is inside the window on any
        cron tick from yesterday's late ticks through today, so the patron
        could otherwise be notified on both days.
        """
        db = loan_fixture.db
        now = utc_now()
        today = now.date()

        # End at 00:01 UTC, two calendar days from today.
        # → loan.end - 3d = yesterday 00:01 UTC, so date(loan.end - 3d) is
        #   yesterday — i.e. the 3-day bucket opened yesterday.
        end = datetime.datetime.combine(
            today + datetime.timedelta(days=2),
            datetime.time(0, 1),
            tzinfo=datetime.timezone.utc,
        )
        work = db.work(with_license_pool=True)
        pool = work.active_license_pool()
        assert pool is not None
        loan, _ = pool.loan_to(loan_fixture.patron, now, end)

        # Patron was already notified for this loan yesterday — which was
        # inside the 3-day bucket.
        loan.patron_last_notified = today - datetime.timedelta(days=1)

        # Without bucket-aware cooldown, this loan would be returned again
        # today and the patron would receive a duplicate 3-day notification.
        assert loan not in loan_fixture.monitor.item_query().all()

    def test_item_query_resends_when_loan_enters_next_bucket(
        self, loan_fixture: LoanNotificationsFixture
    ):
        """After the 3-day notification, the loan must be picked up again
        once it enters the 1-day bucket (different bucket, different msg)."""
        db = loan_fixture.db
        now = utc_now()

        # End is 23h out → loan is now in the 1-day bucket.
        work = db.work(with_license_pool=True)
        pool = work.active_license_pool()
        assert pool is not None
        loan, _ = pool.loan_to(
            loan_fixture.patron,
            now,
            now + datetime.timedelta(hours=23),
        )
        # Suppose we sent the 3-day notification 2 days ago. The 1-day
        # bucket only opens at loan.end - 1d ≈ today, so a 2-day-old
        # notification is before the 1-day bucket opens → must be sent.
        loan.patron_last_notified = now.date() - datetime.timedelta(days=2)

        assert loan in loan_fixture.monitor.item_query().all()

    def test_run_sends_notifications(self, loan_fixture: LoanNotificationsFixture):
        """The monitor's run() should invoke PushNotifications once per
        eligible loan with the correct (loan, days, tokens) arguments."""
        db = loan_fixture.db
        now = utc_now()
        loan_1d, _ = loan_fixture.pool.loan_to(
            loan_fixture.patron,
            now,
            now + datetime.timedelta(hours=23),
        )

        work_3d = db.work(with_license_pool=True)
        loan_3d, _ = work_3d.active_license_pool().loan_to(
            loan_fixture.patron,
            now,
            now + datetime.timedelta(days=2, hours=23),
        )

        with patch(
            "core.jobs.notifications.loan_expiration_notification.PushNotifications"
        ) as mock_notf:
            loan_fixture.monitor.run()

        assert mock_notf.send_loan_expiry_message.call_count == 2
        # Order within the batch is by Loan.id (the sweep ordering): loan_1d
        # is processed first with 1 day remaining, then loan_3d with 3 days.
        assert mock_notf.send_loan_expiry_message.call_args_list == [
            call(loan_1d, 1, [loan_fixture.device_token]),
            call(loan_3d, 3, [loan_fixture.device_token]),
        ]

    def test_run_skipped_when_kill_switch_off(
        self, loan_fixture: LoanNotificationsFixture
    ):
        """Sitewide PUSH_NOTIFICATIONS_STATUS=FALSE must short-circuit run()
        before any loan processing happens."""
        db = loan_fixture.db
        now = utc_now()
        loan_fixture.pool.loan_to(
            loan_fixture.patron,
            now,
            now + datetime.timedelta(hours=23),
        )
        ConfigurationSetting.sitewide(
            db.session, Configuration.PUSH_NOTIFICATIONS_STATUS
        ).value = ConfigurationConstants.FALSE

        with patch(
            "core.jobs.notifications.loan_expiration_notification.PushNotifications"
        ) as mock_notf:
            loan_fixture.monitor.run()

        assert mock_notf.send_loan_expiry_message.call_count == 0

    def test_process_items_skips_loans_without_fcm_tokens(
        self, loan_fixture: LoanNotificationsFixture
    ):
        """A loan whose patron has no FCM-eligible device tokens must be
        skipped silently. Only FCM_ANDROID and FCM_IOS are valid token
        types in the DB schema, so 'no FCM tokens' means 'no device
        tokens at all'."""
        db = loan_fixture.db
        other_patron = db.patron()
        # No device tokens for this patron at all.
        now = utc_now()
        work = db.work(with_license_pool=True)
        loan, _ = work.active_license_pool().loan_to(
            other_patron,
            now,
            now + datetime.timedelta(hours=23),
        )

        with patch(
            "core.jobs.notifications.loan_expiration_notification.PushNotifications"
        ) as mock_notf:
            loan_fixture.monitor.process_items([loan])

        assert mock_notf.send_loan_expiry_message.call_count == 0
