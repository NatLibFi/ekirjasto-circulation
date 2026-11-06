from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import joinedload

from api.odl import ODLAPI, BaseODLAPI
from api.odl2 import ODL2API
from core.config import Configuration, ConfigurationConstants
from core.model import Base
from core.model.configuration import ConfigurationSetting
from core.model.devicetokens import DeviceToken, DeviceTokenTypes
from core.model.licensing import LicensePool
from core.model.patron import Hold, Patron
from core.monitor import SweepMonitor
from core.util.datetime_helpers import utc_now
from core.util.notifications import PushNotifications

if TYPE_CHECKING:
    from sqlalchemy.orm import Query

    from core.model.collection import Collection


class HoldsNotificationMonitor(SweepMonitor):
    """Sweep across all holds that are ready to be checked out by the user (position=0)"""

    MODEL_CLASS: type[Base] | None = Hold
    SERVICE_NAME: str | None = "Holds Notification"
    # Override the framework default (100). Notifications fan out to network
    # I/O (FCM), so smaller batches keep per-batch blocking and memory low.
    DEFAULT_BATCH_SIZE = 25

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._availability_checks: dict[int, set[int]] = {}
        self._odl_apis: dict[int, BaseODLAPI] = {}

    def run_once(self, *ignore):
        self._availability_checks.clear()
        self._odl_apis.clear()
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

    def scope_to_collection(self, qu: Query, collection: Collection) -> Query:
        """Do not scope to collection"""
        return qu

    def item_query(self) -> Query:
        # Correlated EXISTS subquery: only select holds whose patron has at
        # least one FCM-eligible device token. Using EXISTS instead of
        # JOIN+DISTINCT gives the planner a clear hint and avoids duplicate
        # rows that would otherwise need de-duplication.
        # `exists().where(...)` only accepts a single criterion in the
        # typed stubs we use, so combine via and_().
        has_fcm_token = exists().where(
            and_(
                DeviceToken.patron_id == Patron.id,
                DeviceToken.token_type.in_(
                    (DeviceTokenTypes.FCM_ANDROID, DeviceTokenTypes.FCM_IOS)
                ),
            )
        )
        query = (
            super()
            .item_query()
            .join(Hold.patron)
            .filter(
                # Only holds ready for checkout.
                Hold.position == 0,
                or_(Hold.end == None, Hold.end > utc_now()),
                # Cooldown: at most one notification per calendar day per hold.
                # We compare on the date portion only (func.date) so legacy
                # rows that stored a full timestamp in patron_last_notified
                # still get normalized to a calendar date. Using `<` (not
                # `!=`) guarantees we never re-notify a patron whose
                # last-notified date is today, even if a clock skew or a
                # legacy row produces an unexpected value.
                or_(
                    Hold.patron_last_notified == None,
                    func.date(Hold.patron_last_notified) < utc_now().date(),
                ),
                # Skip patrons with no FCM-eligible device tokens.
                has_fcm_token,
            )
            # Eager-load every relationship that PushNotifications dereferences
            # while building the message. Without this we'd issue ~4 extra
            # SELECTs per hold (classic N+1). Note: Hold.work is a Python
            # property that resolves to license_pool.work, which is already
            # lazy="joined" on the LicensePool side, so no joinedload is needed
            # for the work itself.
            .options(
                joinedload(Hold.patron).joinedload(Patron.device_tokens),
                joinedload(Hold.patron).joinedload(Patron.library),
                joinedload(Hold.license_pool).joinedload(LicensePool.identifier),
            )
        )
        return query

    def _refresh_availability(self, pool: LicensePool) -> set[int] | None:
        """Check each ODL pool once per sweep, including across batch boundaries."""
        collection = pool.collection
        api_class = {ODLAPI.label(): ODLAPI, ODL2API.label(): ODL2API}.get(
            collection.protocol
        )
        if api_class is None:
            # Other providers do not use the local ODL license/queue model.
            return None
        pool_id = pool.id
        collection_id = collection.id
        if pool_id is None or collection_id is None:
            return set()
        if pool_id not in self._availability_checks:
            try:
                if collection_id not in self._odl_apis:
                    self._odl_apis[collection_id] = api_class(self._db, collection)
                self._availability_checks[pool_id] = self._odl_apis[
                    collection_id
                ].refresh_hold_availability(pool)
            except Exception:
                self.log.exception(
                    "Unable to verify hold availability for license pool %s", pool.id
                )
                self._availability_checks[pool_id] = set()
            if not self._availability_checks[pool_id]:
                self.log.info(
                    "No ready holds covered by verified capacity for license pool %s",
                    pool.id,
                )
        return self._availability_checks[pool_id]

    def process_items(self, items: list[Hold]) -> None:
        today = utc_now().date()
        candidate_ids: list[int] = []
        for hold in items:
            hold_id = hold.id
            if hold_id is None:
                continue
            raw = hold.patron_last_notified
            last_notified: datetime.date | None
            if isinstance(raw, datetime.datetime):
                # Legacy DB rows may carry a datetime; normalize to date.
                last_notified = raw.date()
            else:
                last_notified = raw
            if last_notified is not None and last_notified >= today:
                continue
            verified_hold_ids = self._refresh_availability(hold.license_pool)
            if verified_hold_ids is None or hold_id in verified_hold_ids:
                candidate_ids.append(hold_id)

        if not candidate_ids:
            return

        # Re-query after refreshing the licenses and queue. A hold may have
        # moved back to a waiting position, expired, or been deleted since
        # fetch_batch(). populate_existing also replaces stale ORM state.
        eligible = (
            self.item_query()
            .filter(Hold.id.in_(candidate_ids))
            .populate_existing()
            .all()
        )
        if eligible:
            PushNotifications.send_holds_notifications(eligible)
