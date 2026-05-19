from __future__ import annotations

import datetime
import math

from sqlalchemy import and_, func, or_
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
        # Select loans whose `end` falls inside a 24h interval ending at
        # `now + N days` for each configured notification day. This is more
        # correct than date-equality (`Loan.end == today + N days`), which
        # would only match loans ending at exactly midnight on the target day.
        now = utc_now()
        # Build one (window AND per-bucket-cooldown) clause per notification
        # day. The cooldown is bucket-aware: a loan is eligible for the N-day
        # notification only if we have NOT already notified the patron during
        # the N-day window for this loan. Without this, the same loan could
        # trigger the "3 days left" message on two consecutive cron days
        # because the window spans up to 24h.
        bucket_clauses = []
        for days in self.LOAN_EXPIRATION_DAYS:
            window = and_(
                Loan.end <= now + datetime.timedelta(days=days),
                Loan.end > now + datetime.timedelta(days=days - 1),
            )
            # The N-day window opens at `loan.end - N days`. We notify at
            # most once per bucket per loan: if `patron_last_notified`
            # (a Date) is on or after the bucket's opening date, we've
            # already sent the N-day message for this loan and must skip.
            # `func.date(...)` truncates the right side to date granularity
            # to match the column type. NULL `patron_last_notified` means
            # "never notified", which is always fresh.
            cooldown = or_(
                Loan.patron_last_notified == None,
                Loan.patron_last_notified
                < func.date(Loan.end - datetime.timedelta(days=days)),
            )
            bucket_clauses.append(and_(window, cooldown))

        query = (
            super()
            .item_query()
            .filter(
                # Loan must be in at least one (window, cooldown) bucket.
                or_(*bucket_clauses),
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
            if days not in self.LOAN_EXPIRATION_DAYS:
                continue
            # Bucket-aware cooldown re-check (mirrors the SQL filter). Avoids
            # re-sending the same N-day message if the SQL filter raced.
            # Compared at date granularity because patron_last_notified is
            # a Date column.
            bucket_open_date = (loan.end - datetime.timedelta(days=days)).date()
            if (
                loan.patron_last_notified is not None
                and loan.patron_last_notified >= bucket_open_date
            ):
                continue
            self.log.info(
                f"Patron {patron.authorization_identifier} has an expiring loan on "
                f"({loan.license_pool.identifier.urn}) ending on {loan.end}, "
                f"which is in the {days}-day expiry window. "
            )
            # send_loan_expiry_message also sets loan.patron_last_notified;
            # SweepMonitor.run_once commits after every batch.
            PushNotifications.send_loan_expiry_message(loan, days, tokens)
