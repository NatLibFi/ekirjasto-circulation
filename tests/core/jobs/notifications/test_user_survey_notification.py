from unittest.mock import call, patch

import pytest

from core.config import Configuration, ConfigurationConstants
from core.jobs.notifications.user_survey_notification import UserSurveyNotificationScript
from core.model.configuration import ConfigurationSetting
from tests.fixtures.database import DatabaseTransactionFixture


class UserSurveyNotificationFixture:
    def __init__(self, db: DatabaseTransactionFixture) -> None:
        self.db = db
        self.monitor = UserSurveyNotificationScript(self.db.session)


@pytest.fixture(scope="function")
def survey_fixture(db: DatabaseTransactionFixture) -> UserSurveyNotificationFixture:
    return UserSurveyNotificationFixture(db)


class TestUserSurveyNotification:
    def test_item_query(self, survey_fixture: UserSurveyNotificationFixture):
        """Check that item_query() finds all patrons in the database."""
        db = survey_fixture.db
        patrons = [db.patron() for _ in range(1000)]

        assert survey_fixture.monitor.item_query().count() == 1000

    def test_script_run(self, survey_fixture: UserSurveyNotificationFixture):
        """Test that a large amount of patrons are processed in batches."""
        db = survey_fixture.db
        # Create a list of 1000 patrons
        patrons = [db.patron() for _ in range(1000)]

        with patch("core.jobs.notifications.user_survey_notification.PushNotifications") as mock_notf:
            survey_fixture.monitor.run()
            # The patrons are handled in batches of 100
            assert mock_notf.send_user_survey_message.call_count == 10

            # Sanity check with Sitewide notifications turned off
            mock_notf.send_user_survey_message.reset_mock()
            ConfigurationSetting.sitewide(
                db.session, Configuration.PUSH_NOTIFICATIONS_STATUS
            ).value = ConfigurationConstants.FALSE
            survey_fixture.monitor.run()
            assert mock_notf.send_user_survey_message.call_count == 0
