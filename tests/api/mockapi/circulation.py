from abc import ABC
from collections import defaultdict

from sqlalchemy.orm import Session

from api.circulation import (
    BaseCirculationAPI,
    CirculationAPI,
    HoldInfo,
    LoanInfo,
)
from api.circulation_manager import CirculationManager
from core.integration.settings import BaseSettings
from core.model import DataSource, Hold, Loan
from core.service.container import Services

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
            if source == DataSource.AXIS_360:
                set_delivery_mechanism_at = BaseCirculationAPI.BORROW_STEP
            if source == DataSource.THREEM:
                can_revoke_hold_when_reserved = False
            remote = MockRemoteAPI(
                set_delivery_mechanism_at, can_revoke_hold_when_reserved
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
