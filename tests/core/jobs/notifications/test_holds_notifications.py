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
