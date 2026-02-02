from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from api.circulation import (
    BaseCirculationAPI,
    CirculationAPI,
    Fulfillment,
    HoldInfo,
    LoanInfo,
)
from api.circulation_manager import CirculationManager
from core.integration.settings import BaseSettings
from core.model.collection import Collection
from core.model.datasource import DataSource
from core.model.licensing import LicensePool, LicensePoolDeliveryMechanism
from core.model.patron import Patron
from core.service.container import Services


class MockBaseCirculationAPI(BaseCirculationAPI):
    def __init__(
        self,
        _db: Session,
        collection: Collection,
        set_delivery_mechanism_at: str | None = BaseCirculationAPI.FULFILL_STEP,
        can_revoke_hold_when_reserved: bool = True,
        data_source_name: str = "Test Data Source",
    ):
        old_protocol = collection.integration_configuration.protocol
        collection.integration_configuration.protocol = self.label()
        super().__init__(_db, collection)
        collection.integration_configuration.protocol = old_protocol
        self.SET_DELIVERY_MECHANISM_AT = set_delivery_mechanism_at
        self.CAN_REVOKE_HOLD_WHEN_RESERVED = can_revoke_hold_when_reserved
        self.responses: dict[str, list[Any]] = defaultdict(list)
        self.availability_updated_for: list[LicensePool] = []
        self.data_source_name = data_source_name

    @classmethod
    def label(cls) -> str:
        return ""

    @classmethod
    def description(cls) -> str:
        return ""

    @classmethod
    def settings_class(cls) -> type[BaseSettings]:
        return BaseSettings

    @classmethod
    def library_settings_class(cls) -> type[BaseSettings]:
        return BaseSettings

    @property
    def data_source(self) -> DataSource:
        return DataSource.lookup(self._db, self.data_source_name, autocreate=True)

    def checkout(
        self,
        patron: Patron,
        pin: str | None,
        licensepool: LicensePool,
        delivery_mechanism: LicensePoolDeliveryMechanism | None,
    ) -> LoanInfo | HoldInfo:
        # Should be a LoanInfo.
        return self._return_or_raise("checkout")

    def update_availability(self, licensepool: LicensePool) -> None:
        """Simply record the fact that update_availability was called."""
        self.availability_updated_for.append(licensepool)

    def place_hold(
        self,
        patron: Patron,
        pin: str | None,
        licensepool: LicensePool,
        notification_email_address: str | None,
    ) -> HoldInfo:
        # Should be a HoldInfo.
        return self._return_or_raise("hold")

    def fulfill(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        delivery_mechanism: LicensePoolDeliveryMechanism,
    ) -> Fulfillment:
        # Should be a Fulfillment.
        return self._return_or_raise("fulfill")

    def recalculate_holds_in_license_pool(self, licensepool: LicensePool) -> None:
        pass

    def checkin(self, patron: Patron, pin: str, licensepool: LicensePool) -> None:
        # Return value is not checked.
        return self._return_or_raise("checkin")

    def release_hold(self, patron: Patron, pin: str, licensepool: LicensePool) -> None:
        # Return value is not checked.
        return self._return_or_raise("release_hold")

    def queue_checkout(self, response: LoanInfo | HoldInfo | Exception) -> None:
        self._queue("checkout", response)

    def queue_hold(self, response: HoldInfo | Exception) -> None:
        self._queue("hold", response)

    def queue_fulfill(self, response: Fulfillment | Exception) -> None:
        self._queue("fulfill", response)

    def queue_checkin(self, response: None | Exception = None) -> None:
        self._queue("checkin", response)

    def queue_release_hold(self, response: None | Exception = None) -> None:
        self._queue("release_hold", response)

    def _queue(self, k: str, v: Any) -> None:
        self.responses[k].append(v)

    def _return_or_raise(self, key: str) -> Any:
        self.log.debug(key)
        response = self.responses[key].pop()
        if isinstance(response, Exception):
            raise response
        return response

    # Only called TestODLNotificationController
    def delete_expired_loan(self, loan):
        self.availability_updated_for.append(loan.license_pool)


class MockCirculationAPI(CirculationAPI):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.responses = defaultdict(list)
        self.remotes = {}

    def queue_checkout(self, licensepool, response):
        self._queue("checkout", licensepool, response)

    def queue_hold(self, licensepool, response):
        self._queue("hold", licensepool, response)

    def queue_fulfill(self, licensepool, response):
        self._queue("fulfill", licensepool, response)

    def queue_checkin(self, licensepool, response):
        self._queue("checkin", licensepool, response)

    def queue_release_hold(self, licensepool, response):
        self._queue("release_hold", licensepool, response)

    def _queue(self, method, licensepool, response):
        mock = self.api_for_license_pool(licensepool)
        return mock._queue(method, response)

    def api_for_license_pool(self, licensepool):
        source = licensepool.data_source.name
        if source not in self.remotes:
            set_delivery_mechanism_at = BaseCirculationAPI.FULFILL_STEP
            can_revoke_hold_when_reserved = True
            remote = MockBaseCirculationAPI(
                self._db,
                licensepool.collection,
                set_delivery_mechanism_at,
                can_revoke_hold_when_reserved,
            )
            self.remotes[source] = remote
        return self.remotes[source]


class MockCirculationManager(CirculationManager):
    d_circulation: MockCirculationAPI

    def __init__(self, db: Session, services: Services):
        super().__init__(db, services)

    def setup_circulation(self, library, analytics):
        """Set up the Circulation object."""
        return MockCirculationAPI(self._db, library, analytics=analytics)
