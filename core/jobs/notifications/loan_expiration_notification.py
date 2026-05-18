from __future__ import annotations

import datetime
import math

from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload

from core.config import Configuration, ConfigurationConstants
from core.model import Base
from core.model.configuration import ConfigurationSetting
from core.model.devicetokens import DeviceTokenTypes
from core.model.licensing import LicensePool
from core.model.patron import Loan, Patron
from core.monitor import SweepMonitor
from core.util.datetime_helpers import utc_now
from core.util.notifications import PushNotifications


class LoanNotificationsMonitor(SweepMonitor):
    """Sweep across all loans that are expiring soon and need notifications."""

    # Required by SweepMonitor: which table to sweep and which `id` column to
    # use for offset tracking.
    MODEL_CLASS: type[Base] | None = Loan
    # Stored in the `Timestamp` row so progress (counter/offset) persists
    # across runs and crashes resume where they left off.
    SERVICE_NAME: str | None = "Loan Expiration Notification"
    # Days-before-expiry at which a notification should be sent.
    LOAN_EXPIRATION_DAYS = [3, 1]
    # Override the framework default (100). Notifications fan out to network
    # I/O (FCM), so smaller batches keep per-batch blocking and memory low.
    DEFAULT_BATCH_SIZE = 25

    def run_once(self, *ignore):
        # Honor the sitewide kill switch before doing any DB work.
        setting = ConfigurationSetting.sitewide(
            self._db, Configuration.PUSH_NOTIFICATIONS_STATUS
        )
        if setting.value == ConfigurationConstants.FALSE:
            self.log.info(
                "Push notifications have been turned off in the sitewide settings, skipping this job"
            )
            return
        return super().run_once(*ignore)

    def scope_to_collection(self, qu, collection):
        """Loans are not scoped to a collection."""
        # SweepMonitor inherits from CollectionMonitor and only calls this
        # when `self.collection` is truthy. We never set a collection on this
        # monitor, but the base class requires the method to be defined.
        return qu

    def item_query(self):
        # Phase 3: select loans whose `end` falls inside a 24h interval ending
        # at `now + N days` for each configured notification day. This is more
        # correct than date-equality (`Loan.end == today + N days`), which
        # would only match loans ending at exactly midnight on the target day.
        now = utc_now()
        today = now.date()
        # One clause per notification day. Window is right-closed / left-open
        # so a loan can't be picked up by two windows on the same run.
        expiry_clauses = [
            and_(
                Loan.end <= now + datetime.timedelta(days=days),
                Loan.end > now + datetime.timedelta(days=days - 1),
            )
            for days in self.LOAN_EXPIRATION_DAYS
        ]
        query = (
            super()
            .item_query()
            .filter(
                # Cooldown: skip loans already notified today. NULL means
                # "never notified", which still qualifies.
                or_(
                    Loan.patron_last_notified == None,
                    Loan.patron_last_notified != today,
                ),
                # Loan must be in one of the expiry windows.
                or_(*expiry_clauses),
                # Defensive: orphan loans without a patron would crash later
                # when we dereference `loan.patron`. Filter them out in SQL.
                Loan.patron_id != None,
            )
            # Eager-load every relationship that PushNotifications dereferences
            # while building the message. Without this, we'd issue ~5 extra
            # SELECTs per loan (classic N+1). joinedload is appropriate here
            # because all of these are one-to-one / many-to-one (low fan-out).
            .options(
                joinedload(Loan.patron).joinedload(Patron.device_tokens),
                joinedload(Loan.patron).joinedload(Patron.library),
                joinedload(Loan.license_pool).joinedload(LicensePool.identifier),
                joinedload(Loan.license_pool).joinedload(
                    LicensePool.presentation_edition
                ),
            )
        )
        return query

    def process_items(self, items: list[Loan]) -> None:
        # Capture `now` once per batch so all loans in the batch are evaluated
        # against the same reference point.
        now = utc_now()
        for loan in items:
            patron: Patron = loan.patron
            # Race-safety: the patron could have been deleted between the SQL
            # fetch and now, even though we filtered patron_id != None.
            if not patron:
                continue
            # Only FCM (Android / iOS) tokens can receive push notifications.
            tokens = [
                t
                for t in patron.device_tokens
                if t.token_type
                in (DeviceTokenTypes.FCM_ANDROID, DeviceTokenTypes.FCM_IOS)
            ]
            if not tokens:
                continue
            if loan.end is None:
                # Should be impossible given the SQL filter, but the model
                # types `end` as Optional[DateTime] so we appease type checks
                # and guard against any race.
                self.log.warning(f"Loan: {loan.id} has no end date, skipping")
                continue
            # Round up to the nearest whole day. Matches the SQL window:
            # a loan inside the `days=N` window always rounds up to N.
            days = math.ceil((loan.end - now) / datetime.timedelta(days=1))
            # Belt-and-suspenders: SQL `now` and Python `now` differ slightly,
            # so a loan right on a boundary could compute a different bucket
            # in Python than SQL selected. Skip those.
            if days in self.LOAN_EXPIRATION_DAYS:
                self.log.info(
                    f"Patron {patron.authorization_identifier} has an expiring loan on "
                    f"({loan.license_pool.identifier.urn})"
                )
                # send_loan_expiry_message also sets loan.patron_last_notified;
                # SweepMonitor.run_once commits after every batch.
                PushNotifications.send_loan_expiry_message(loan, days, tokens)
