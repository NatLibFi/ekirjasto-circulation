from __future__ import annotations

from sqlalchemy import exists
from sqlalchemy.orm import Query, selectinload

from core.config import Configuration, ConfigurationConstants
from core.model.configuration import ConfigurationSetting
from core.model.devicetokens import DeviceToken, DeviceTokenTypes
from core.model.patron import Patron
from core.monitor import PatronSweepMonitor
from core.util.notifications import PushNotifications


class UserSurveyNotificationScript(PatronSweepMonitor):
    """
    Send notifications to all users about a new user survey.
    """

    SERVICE_NAME: str | None = "User Survey Notification"
    # Override the PatronSweepMonitor default (100). Each batch fans out to
    # FCM I/O via send_user_survey_message(); a smaller batch keeps per-batch
    # blocking time and memory bounded and matches the loan/hold monitors.
    DEFAULT_BATCH_SIZE = 25

    def run_once(self, *ignore):
        setting = ConfigurationSetting.sitewide(
            self._db, Configuration.PUSH_NOTIFICATIONS_STATUS
        )
        if setting.value == ConfigurationConstants.FALSE:
            self.log.info(
                "Push notifications have been turned off in the sitewide settings, skipping this job"
            )
            return
        self.log.info("Starting User Survey Notification Job")
        return super().run_once(*ignore)

    def item_query(self):
        """Query only patrons with at least one FCM-eligible device token."""
        # Correlated EXISTS: lets the planner skip patrons with no token in a
        # single index lookup, rather than scanning the patrons table and
        # filtering in Python.
        has_fcm_token = exists().where(
            DeviceToken.patron_id == Patron.id,
            DeviceToken.token_type.in_(
                (DeviceTokenTypes.FCM_ANDROID, DeviceTokenTypes.FCM_IOS)
            ),
        )
        query: Query = (
            super()
            .item_query()
            .filter(has_fcm_token)
            # selectinload (not joinedload) because device_tokens is a
            # collection, and we want one extra SELECT per batch rather than
            # a JOIN that fans out rows.
            .options(selectinload(Patron.device_tokens))
        )
        return query

    def process_items(self, items: list[Patron]) -> None:
        """Send notifications to patrons"""
        self.log.info(f"Processing {len(items)} patrons.")
        PushNotifications.send_user_survey_message(items)
