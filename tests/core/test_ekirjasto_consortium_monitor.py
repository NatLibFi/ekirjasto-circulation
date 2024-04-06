import re

from requests_mock import Mocker

from api.ekirjasto_consortium import EkirjastoConsortiumMonitor
from core.configuration.library import LibrarySettings
from tests.fixtures.database import DatabaseTransactionFixture


class TestEkirjastoConsortiumMonitor:
    def test_library_sync_happy_case(self, db: DatabaseTransactionFixture):
        _ = db.default_library()
        library = db.library(name="my_library")
        library.update_settings(
            LibrarySettings.construct(kirkanta_consortium_slug="vaski-kirjastot")
        )

        with Mocker() as mocker:
            self._mock_kirkanta_api(mocker)
            self._mock_koodisto_api(mocker)
            EkirjastoConsortiumMonitor(db.session).run()

        assert ["100"] == library.settings.municipalities

    def _mock_koodisto_api(self, mocker: Mocker) -> None:
        mocker.get(
            re.compile(r"https://koodistopalvelu.kanta.fi/.*"),
            status_code=200,
            json={
                "conceptCodes": [
                    {
                        "attributes": [
                            {
                                "attributeName": "ShortName",
                                "attributeValue": ["Turku"],
                            }
                        ],
                        "status": "ACTIVE",
                        "conceptCodeId": 100,
                    },
                    {
                        "attributes": [
                            {
                                "attributeName": "ShortName",
                                "attributeValue": ["Kangasniemi"],
                            }
                        ],
                        "status": "ACTIVE",
                        "conceptCodeId": 200,
                    },
                ],
                "totalItems": 2,
                "totalPages": 1,
                "page": 1,
            },
        )

    def _mock_kirkanta_api(self, mocker: Mocker) -> None:
        mocker.get(
            re.compile(r"https://api.kirjastot.fi/v4/city.*"),
            status_code=200,
            json={
                "type": "city",
                "items": [
                    {"id": "1", "name": "Turku", "consortium": "10"},
                    {"id": "2", "name": "Helsinki", "consortium": "20"},
                    {"id": "3", "name": "Kangasniemi", "consortium": None},
                ],
            },
        )

        mocker.get(
            re.compile(r"https://api.kirjastot.fi/v4/consortium.*"),
            status_code=200,
            json={
                "type": "consortium",
                "items": [
                    {
                        "id": "10",
                        "name": "Vaski-kirjastot",
                        "slug": "vaski-kirjastot",
                    },
                    {
                        "id": "20",
                        "name": "Helmet",
                        "slug": "helmet",
                    },
                ],
            },
        )
