from sqlalchemy.orm import Session

from api.integration.demarque_webreader import DeMarqueWebReader
from api.odl import ODLAPI
from api.odl2 import ODL2API
from core.model.collection import Collection
from tests.api.mockapi.mock import MockHTTPClient


class MockOPDS2WithODLApi(ODLAPI):
    def __init__(
        self,
        _db: Session,
        collection: Collection,
        mock_http_client: MockHTTPClient,
        demarque_webreader: DeMarqueWebReader | None = None,
    ) -> None:
        super().__init__(_db, collection, demarque_webreader=demarque_webreader)

        self.mock_http_client = mock_http_client

    @staticmethod
    def _notification_url(
        short_name: str | None, patron_id: str, license_id: str
    ) -> str:
        return f"https://ekirjasto/{short_name}/odl/notify/{patron_id}/{license_id}"


class MockODL2Api(ODL2API):
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
        short_name: str | None, patron_id: str, license_id: str
    ) -> str:
        return f"https://ekirjasto/{short_name}/odl/notify/{patron_id}/{license_id}"
