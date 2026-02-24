from __future__ import annotations

from sqlalchemy.orm import Query

from core.config import Configuration, ConfigurationConstants
from core.model.configuration import ConfigurationSetting
from core.model.patron import Patron
from core.monitor import PatronSweepMonitor
from core.util.notifications import PushNotifications


class UserSurveyNotificationScript(PatronSweepMonitor):
    """
    Send notifications to all users about a new user survey.
    """

    SERVICE_NAME: str | None = "User Survey Notification"

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
        """Query all patrons"""
        query: Query = super().item_query()
        self.log.info(f"{query.count()} patrons found")
        return query

    def process_items(self, items: list[Patron]) -> None:
        """Send notifications to patrons"""
        self.log.info(f"Processing {len(items)} patrons.")
        PushNotifications.send_user_survey_message(items)
