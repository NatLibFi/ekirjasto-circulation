from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import or_

from core.config import Configuration, ConfigurationConstants
from core.model import Base
from core.model.configuration import ConfigurationSetting
from core.model.patron import Patron, Loan
from core.model.devicetokens import DeviceTokenTypes
from core.util.notifications import PushNotifications
from core.scripts import Script
from core.util.datetime_helpers import utc_now

import datetime

from sqlalchemy import and_, exists, or_, select, tuple_
from sqlalchemy.orm import Query, Session, defer
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm.exc import MultipleResultsFound, NoResultFound

from core.config import Configuration, ConfigurationConstants
from core.model.devicetokens import DeviceToken, DeviceTokenTypes
from core.model.patron import Loan


class LoanNotificationsScript(Script):
    """Notifications must be sent to Patrons based on when their current loans
    are expiring"""

    # Days before on which to send out a notification
    LOAN_EXPIRATION_DAYS = [3, 1]
    BATCH_SIZE = 100

    def do_run(self):
        self.log.info("Loan Notifications Job started")

        setting = ConfigurationSetting.sitewide(
            self._db, Configuration.PUSH_NOTIFICATIONS_STATUS
        )
        if setting.value == ConfigurationConstants.FALSE:
            self.log.info(
                "Push notifications have been turned off in the sitewide settings, skipping this job"
            )
            return

        _query = (
            self._db.query(Loan)
            .filter(
                or_(
                    Loan.patron_last_notified != utc_now().date(),
                    Loan.patron_last_notified == None,
                )
            )
            .order_by(Loan.id)
        )
        last_loan_id = None
        processed_loans = 0

        while True:
            query = _query.limit(self.BATCH_SIZE)
            if last_loan_id:
                query = _query.filter(Loan.id > last_loan_id)

            loans = query.all()
            if len(loans) == 0:
                break

            for loan in loans:
                processed_loans += 1
                self.process_loan(loan)
            last_loan_id = loan.id
            # Commit every batch
            self._db.commit()

        self.log.info(
            f"Loan Notifications Job ended: {processed_loans} loans processed"
        )

    def process_loan(self, loan: Loan):
        tokens = []
        patron: Patron = loan.patron
        t: DeviceToken
        for t in patron.device_tokens:
            if t.token_type in [DeviceTokenTypes.FCM_ANDROID, DeviceTokenTypes.FCM_IOS]:
                tokens.append(t)

        # No tokens means no notifications
        if not tokens:
            return

        now = utc_now()
        if loan.end is None:
            self.log.warning(f"Loan: {loan.id} has no end date, skipping")
            return
        delta: datetime.timedelta = loan.end - now
        if delta.days in self.LOAN_EXPIRATION_DAYS:
            self.log.info(
                f"Patron {patron.authorization_identifier} has an expiring loan on ({loan.license_pool.identifier.urn})"
            )
            PushNotifications.send_loan_expiry_message(loan, delta.days, tokens)
