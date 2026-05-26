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

        # Inside the 1-day window but already notified for the 1-day
        # bucket → NOT selected. The 1-day bucket opens on
        # date(end - 1 day); a same-day notification is past that.
        work_notified = db.work(with_license_pool=True)
        loan_notified, _ = work_notified.active_license_pool().loan_to(
            loan_fixture.patron,
            now,
            now + datetime.timedelta(hours=22),
        )
        loan_notified.patron_last_notified = now.date()

        # Inside the 1-day window, notified well before the 1-day bucket
        # opened (>= 2 days ago) → selected. Two days is enough to be
        # robust against time-of-day jitter at the bucket boundary.
        work_yesterday = db.work(with_license_pool=True)
        loan_yesterday, _ = work_yesterday.active_license_pool().loan_to(
            loan_fixture.patron,
            now,
            now + datetime.timedelta(hours=21),
        )
        loan_yesterday.patron_last_notified = now.date() - datetime.timedelta(days=2)

        results = set(loan_fixture.monitor.item_query().all())
        assert results == {loan_1d, loan_3d, loan_yesterday}

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

    def test_item_query_skips_loan_already_notified_for_3d_bucket(
        self, loan_fixture: LoanNotificationsFixture
    ):
        """Regression: the 3-day expiry window spans a 24h slice that can
        contain the same loan on two consecutive calendar days. Once we've
        sent the 3-day notification, the loan must NOT be re-selected on
        the next day's cron even if `patron_last_notified` is a different
        calendar date than today.

        Example: a loan ending now+2d+12h enters the 3-day window today;
        after notification, patron_last_notified is set to today. Tomorrow
        the loan is still in the 3-day window (end - 1d = now+1d+12h, still
        > 2 days away) — but the 3-day bucket opened on
        date(end - 3d) = today, and patron_last_notified == today, so the
        bucket-aware cooldown must filter it out."""
        now = utc_now()
        loan, _ = loan_fixture.pool.loan_to(
            loan_fixture.patron,
            now,
            # 2d + 12h: deep inside the 3-day window, far from the 2-day
            # boundary so this stays valid regardless of time-of-day.
            now + datetime.timedelta(days=2, hours=12),
        )
        # The 3-day bucket for this loan opens on date(end - 3d).
        bucket_open = (loan.end - datetime.timedelta(days=3)).date()
        loan.patron_last_notified = bucket_open

        assert loan not in loan_fixture.monitor.item_query().all()

    def test_item_query_selects_loan_in_1d_bucket_after_3d_notification(
        self, loan_fixture: LoanNotificationsFixture
    ):
        """A loan that received the 3-day notification 2 days ago must
        still be picked up when it enters the 1-day window: the 1-day
        bucket opens on date(end - 1d), which is later than the 3-day
        bucket-open date, so the cooldown allows the new notification."""
        now = utc_now()
        loan, _ = loan_fixture.pool.loan_to(
            loan_fixture.patron,
            now,
            # Inside the 1-day window.
            now + datetime.timedelta(hours=12),
        )
        # Simulate the 3-day notification having been sent ~2 days ago,
        # when the 3-day bucket was open: that is date(end - 3d).
        loan.patron_last_notified = (
            loan.end - datetime.timedelta(days=3)
        ).date()

        assert loan in loan_fixture.monitor.item_query().all()
