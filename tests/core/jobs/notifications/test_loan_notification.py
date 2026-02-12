from __future__ import annotations

import datetime
from unittest.mock import MagicMock, call, patch

from core.config import Configuration, ConfigurationConstants
from core.jobs.notifications.loan_expiration_notification import LoanNotificationsScript
from core.model import ConfigurationSetting, Work, get_one_or_create
from core.model.devicetokens import DeviceToken, DeviceTokenTypes
from core.model.patron import Patron
from core.util.datetime_helpers import utc_now
from core.util.notifications import PushNotifications
from tests.fixtures.database import DatabaseTransactionFixture


class TestLoanNotificationsScript:
    def _setup_method(self, db: DatabaseTransactionFixture):
        self.script = LoanNotificationsScript(_db=db.session)
        self.patron: Patron = db.patron()
        self.work: Work = db.work(with_license_pool=True)
        self.device_token, _ = get_one_or_create(
            db.session,
            DeviceToken,
            patron=self.patron,
            token_type=DeviceTokenTypes.FCM_ANDROID,
            device_token="atesttoken",
        )
        PushNotifications.TESTING_MODE = True

    def test_loan_notification(self, db: DatabaseTransactionFixture):
        self._setup_method(db)
        p = self.work.active_license_pool()
        if p:  # mypy complains if we don't do this
            loan, _ = p.loan_to(
                self.patron,
                utc_now(),
                utc_now() + datetime.timedelta(days=1, hours=1),
            )

        work2 = db.work(with_license_pool=True)
        loan2, _ = work2.active_license_pool().loan_to(
            self.patron,
            utc_now(),
            utc_now() + datetime.timedelta(days=2, hours=1),
        )  # Should not get notified

        with patch(
            "core.jobs.notifications.loan_expiration_notification.PushNotifications"
        ) as mock_notf:
            self.script.process_loan(loan)
            self.script.process_loan(loan2)

        assert mock_notf.send_loan_expiry_message.call_count == 1
        assert mock_notf.send_loan_expiry_message.call_args[0] == (
            loan,
            1,
            [self.device_token],
        )

    def test_do_run(self, db: DatabaseTransactionFixture):
        now = utc_now()
        self._setup_method(db)
        loan, _ = self.work.active_license_pool().loan_to(  # type: ignore
            self.patron,
            now,
            now + datetime.timedelta(days=1, hours=1),
        )

        work2 = db.work(with_license_pool=True)
        loan2, _ = work2.active_license_pool().loan_to(
            self.patron,
            now,
            now + datetime.timedelta(days=2, hours=1),
        )

        work3 = db.work(with_license_pool=True)
        p = work3.active_license_pool()
        loan3, _ = p.loan_to(
            self.patron,
            now,
            now + datetime.timedelta(days=1, hours=1),
        )
        # loan 3 was notified today already, so should get skipped
        loan3.patron_last_notified = now.date()

        work4 = db.work(with_license_pool=True)
        p = work4.active_license_pool()
        loan4, _ = p.loan_to(
            self.patron,
            now,
            now + datetime.timedelta(days=1, hours=1),
        )
        # loan 4 was notified yesterday, so should NOT get skipped
        loan4.patron_last_notified = now.date() - datetime.timedelta(days=1)

        self.script.process_loan = MagicMock()
        self.script.BATCH_SIZE = 1
        self.script.do_run()

        assert self.script.process_loan.call_count == 3
        assert self.script.process_loan.call_args_list == [
            call(loan),
            call(loan2),
            call(loan4),
        ]

        # Sitewide notifications are turned off
        self.script.process_loan.reset_mock()
        ConfigurationSetting.sitewide(
            db.session, Configuration.PUSH_NOTIFICATIONS_STATUS
        ).value = ConfigurationConstants.FALSE
        self.script.do_run()
        assert self.script.process_loan.call_count == 0
