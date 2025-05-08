from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from requests import Response
from sqlalchemy.orm import Session

from api.odl import ODLAPI
from core.model.collection import Collection
from core.util.datetime_helpers import utc_now
from tests.api.mockapi.mock import MockHTTPClient


class MockOPDS2WithODLApi(ODLAPI):
    def __init__(
        self,
        _db: Session,
        collection: Collection,
        mock_http_client: MockHTTPClient,
    ) -> None:
        super().__init__(_db, collection)

        self.mock_http_client = mock_http_client


    @staticmethod
    def _notification_url(
        short_name: str | None, license_id: str
    ) -> str:
        return f"https://ekirjasto/{short_name}/odl_notify/{license_id}"
