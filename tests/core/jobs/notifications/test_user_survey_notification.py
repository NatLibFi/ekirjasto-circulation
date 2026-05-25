from unittest.mock import patch

import pytest

from core.config import Configuration, ConfigurationConstants
from core.jobs.notifications.user_survey_notification import (
    UserSurveyNotificationScript,
)
from core.model.configuration import ConfigurationSetting
from core.model.devicetokens import DeviceToken, DeviceTokenTypes
from tests.fixtures.database import DatabaseTransactionFixture


class UserSurveyNotificationFixture:
    def __init__(self, db: DatabaseTransactionFixture) -> None:
        self.db = db
        self.monitor = UserSurveyNotificationScript(self.db.session)

    def patrons_with_tokens(self, count: int):
        """Create `count` patrons, each with one FCM token.

        The monitor's item_query() uses an EXISTS subquery to skip patrons
        without an FCM-eligible token, so every patron in these tests must
        own one or they won't show up in the sweep.
        """
        patrons = []
        for i in range(count):
            patron = self.db.patron()
            DeviceToken.create(
                self.db.session,
                DeviceTokenTypes.FCM_ANDROID,
                f"token-{patron.id}",
                patron,
            )
            patrons.append(patron)
        return patrons


@pytest.fixture(scope="function")
def survey_fixture(db: DatabaseTransactionFixture) -> UserSurveyNotificationFixture:
    return UserSurveyNotificationFixture(db)


class TestUserSurveyNotification:
    def test_item_query_filters_patrons_without_fcm_tokens(
        self, survey_fixture: UserSurveyNotificationFixture
    ):
        """Only patrons with at least one FCM token should be returned."""
        db = survey_fixture.db
        with_tokens = survey_fixture.patrons_with_tokens(3)
        # These patrons should be filtered out by the EXISTS subquery.
        db.patron()
        db.patron()

        results = set(survey_fixture.monitor.item_query().all())
        assert results == set(with_tokens)

    def test_item_query_finds_all_patrons_with_tokens(
        self, survey_fixture: UserSurveyNotificationFixture
    ):
        """Sanity-check at scale: the query should return every patron that
        owns an FCM token."""
        survey_fixture.patrons_with_tokens(1000)
        assert survey_fixture.monitor.item_query().count() == 1000

    def test_script_run(self, survey_fixture: UserSurveyNotificationFixture):
        """A large number of patrons must be processed in batches sized by
        the DEFAULT_BATCH_SIZE on this monitor (25)."""
        db = survey_fixture.db
        survey_fixture.patrons_with_tokens(1000)

        with patch(
            "core.jobs.notifications.user_survey_notification.PushNotifications"
        ) as mock_notf:
            survey_fixture.monitor.run()
            # 1000 patrons / 25 batch size = 40 calls.
            assert mock_notf.send_user_survey_message.call_count == 40

            # Sanity check: sitewide kill switch turns the whole run off.
            mock_notf.send_user_survey_message.reset_mock()
            ConfigurationSetting.sitewide(
                db.session, Configuration.PUSH_NOTIFICATIONS_STATUS
            ).value = ConfigurationConstants.FALSE
            survey_fixture.monitor.run()
            assert mock_notf.send_user_survey_message.call_count == 0
