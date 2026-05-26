import datetime
from unittest.mock import call, patch

import pytest

from core.config import Configuration, ConfigurationConstants
from core.jobs.notifications.holds_notification import HoldsNotificationMonitor
from core.model.configuration import ConfigurationSetting
from core.model.devicetokens import DeviceToken, DeviceTokenTypes
from core.util.datetime_helpers import utc_now
from tests.fixtures.database import DatabaseTransactionFixture


class HoldsNotificationFixture:
    def __init__(self, db: DatabaseTransactionFixture) -> None:
        self.db = db
        self.monitor = HoldsNotificationMonitor(self.db.session)

    def patron_with_token(self):
        """Create a patron with an FCM device token.

        The item_query() in HoldsNotificationMonitor filters out patrons
        without an FCM-eligible device token, so tests must give every
        patron at least one token to be picked up by the sweep.
        """
        patron = self.db.patron()
        DeviceToken.create(
            self.db.session,
            DeviceTokenTypes.FCM_ANDROID,
            f"token-{patron.id}",
            patron,
        )
        return patron


@pytest.fixture(scope="function")
def holds_fixture(db: DatabaseTransactionFixture) -> HoldsNotificationFixture:
    return HoldsNotificationFixture(db)


class TestHoldsNotifications:
    def test_item_query(self, holds_fixture: HoldsNotificationFixture):
        db = holds_fixture.db
        patron1 = holds_fixture.patron_with_token()
        # Patron with no device tokens. Holds for this patron must be excluded
        # by the EXISTS subquery in item_query().
        patron_no_token = db.patron()

        work1 = db.work(with_license_pool=True)
        work2 = db.work(with_license_pool=True)
        work3 = db.work(with_license_pool=True)
        work4 = db.work(with_license_pool=True)
        work5 = db.work(with_license_pool=True)
        work6 = db.work(with_license_pool=True)
        hold1, _ = work1.active_license_pool().on_hold_to(patron1, position=1)
        hold2, _ = work2.active_license_pool().on_hold_to(patron1, position=0)
        hold3, _ = work3.active_license_pool().on_hold_to(patron1, position=0)
        hold4, _ = work4.active_license_pool().on_hold_to(patron1, position=None)
        hold5, _ = work5.active_license_pool().on_hold_to(patron1, position=0)
        hold5.patron_last_notified = utc_now().date()
        hold2.patron_last_notified = utc_now().date() - datetime.timedelta(days=1)
        # hold6 is ready (position=0) and not notified, but its patron has no
        # FCM token → must be filtered out by the EXISTS subquery.
        hold6, _ = work6.active_license_pool().on_hold_to(patron_no_token, position=0)

        # Only position 0 holds, that haven't been notified today, whose
        # patron has at least one FCM token, should be queried for.
        assert holds_fixture.monitor.item_query().all() == [hold2, hold3]

    def test_script_run(self, holds_fixture: HoldsNotificationFixture):
        db = holds_fixture.db
        patron1 = holds_fixture.patron_with_token()
        work1 = db.work(with_license_pool=True)
        work2 = db.work(with_license_pool=True)
        hold1, _ = work1.active_license_pool().on_hold_to(patron1, position=0)
        hold2, _ = work2.active_license_pool().on_hold_to(patron1, position=0)

        with patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as mock_notf:
            holds_fixture.monitor.run()
            assert mock_notf.send_holds_notifications.call_count == 1
            assert mock_notf.send_holds_notifications.call_args_list == [
                call([hold1, hold2])
            ]

            # Sitewide notifications are turned off
            mock_notf.send_holds_notifications.reset_mock()
            ConfigurationSetting.sitewide(
                db.session, Configuration.PUSH_NOTIFICATIONS_STATUS
            ).value = ConfigurationConstants.FALSE
            holds_fixture.monitor.run()
            assert mock_notf.send_holds_notifications.call_count == 0

    def test_item_query_skips_legacy_datetime_notified_today(
        self, holds_fixture: HoldsNotificationFixture
    ):
        """Regression: legacy rows whose `patron_last_notified` is a full
        timestamp later than midnight today must still be filtered out by
        the cooldown. The SQL filter uses `func.date(...) < today` so any
        non-midnight value collected today is normalized to today's date
        and excluded."""
        db = holds_fixture.db
        patron = holds_fixture.patron_with_token()
        work = db.work(with_license_pool=True)
        hold, _ = work.active_license_pool().on_hold_to(patron, position=0)
        # Simulate a legacy/raw timestamp stored mid-day today.
        hold.patron_last_notified = datetime.datetime.combine(
            utc_now().date(), datetime.time(14, 23)
        )

        assert hold not in holds_fixture.monitor.item_query().all()

    def test_process_items_re_checks_cooldown(
        self, holds_fixture: HoldsNotificationFixture
    ):
        """If patron_last_notified is bumped to today between the SQL
        fetch and process_items() (e.g. another worker raced ahead),
        process_items() must drop that hold from the batch before passing
        it to send_holds_notifications()."""
        db = holds_fixture.db
        patron = holds_fixture.patron_with_token()
        work_eligible = db.work(with_license_pool=True)
        work_raced = db.work(with_license_pool=True)
        hold_eligible, _ = work_eligible.active_license_pool().on_hold_to(
            patron, position=0
        )
        hold_raced, _ = work_raced.active_license_pool().on_hold_to(patron, position=0)
        # Simulate the race: another worker already notified for hold_raced.
        hold_raced.patron_last_notified = utc_now().date()

        with patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as mock_notf:
            holds_fixture.monitor.process_items([hold_eligible, hold_raced])

        assert mock_notf.send_holds_notifications.call_count == 1
        assert mock_notf.send_holds_notifications.call_args_list == [
            call([hold_eligible])
        ]

    def test_process_items_no_eligible_holds_skips_send(
        self, holds_fixture: HoldsNotificationFixture
    ):
        """When the race-safety re-check filters out every hold in the
        batch, we must not call send_holds_notifications at all (avoids
        an unnecessary DB session lookup and a no-op call)."""
        db = holds_fixture.db
        patron = holds_fixture.patron_with_token()
        work = db.work(with_license_pool=True)
        hold, _ = work.active_license_pool().on_hold_to(patron, position=0)
        hold.patron_last_notified = utc_now().date()

        with patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as mock_notf:
            holds_fixture.monitor.process_items([hold])

        assert mock_notf.send_holds_notifications.call_count == 0
