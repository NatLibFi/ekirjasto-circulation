import json
import uuid
import types
from collections.abc import Callable
from typing import Any, Literal
from functools import partial
from unittest.mock import MagicMock
import datetime
import pytest
from _pytest.monkeypatch import MonkeyPatch
from core.util.datetime_helpers import utc_now

# from _pytest.monkeypatch import MonkeyPatch
from requests import Response
from api.odl import ODLAPI, BaseODLAPI
from api.odl2 import ODL2API
from core.model import (
    Collection,
    Library,
    License,
    LicensePool,
    Loan,
    Patron,
    Representation,
    Work,
)
from api.odl import BaseODLAPI
from api.circulation import HoldInfo, LoanInfo
from api.lcp.status import LoanStatus, Link, LinkCollection
from core.model.configuration import ExternalIntegration
from core.util.http import HTTP
from tests.api.mockapi.mock import MockHTTPClient
from tests.core.mock import MockRequestsResponse
from tests.fixtures.api_odl import ODL2APIFilesFixture, ODLAPIFilesFixture
from tests.fixtures.database import DatabaseTransactionFixture
from tests.fixtures.files import OPDS2WithODLFilesFixture, APIFilesFixture
from tests.mocks.odl import MockOPDS2WithODLApi

class OPDS2WithODLApiFixture:
    def __init__(
        self,
        db: DatabaseTransactionFixture,
        files: OPDS2WithODLFilesFixture,
    ):
        self.db = db
        self.files = files

        self.library = db.default_library()
        self.collection = self.create_collection(self.library)
        self.work = self.create_work(self.collection)
        self.license = self.setup_license()
        self.mock_http = MockHTTPClient()
        self.api = MockOPDS2WithODLApi(self.db.session, self.collection, self.mock_http)
        self.patron = db.patron()
        self.pool = self.license.license_pool
        self.api_checkout = partial(
            self.api.checkout,
            patron=self.patron,
            pin="pin",
            licensepool=self.pool,
            delivery_mechanism=MagicMock(),
        )

    def create_work(self, collection: Collection) -> Work:
        return self.db.work(with_license_pool=True, collection=collection)


    def create_collection(self, library, api_class=ODLAPI):
        """Create a mock ODL collection to use in tests."""
        integration_protocol = api_class.label()
        collection, _ = Collection.by_name_and_protocol(
            self.db.session,
            f"Test {api_class.__name__} Collection",
            integration_protocol,
        )
        collection.integration_configuration.settings_dict = {
            "username": "a",
            "password": "b",
            "external_account_id": "http://odl",
            Collection.DATA_SOURCE_NAME_SETTING: "Feedbooks",
        }
        collection.libraries.append(library)
        return collection

    def setup_license(
        self,
        work: Work | None = None,
        available: int = 1,
        concurrency: int = 1,
        left: int | None = None,
        expires: datetime.datetime | None = None,
    ) -> License:
        work = work or self.work
        pool = work.license_pools[0]

        if len(pool.licenses) == 0:
            self.db.license(pool)

        license_ = pool.licenses[0]
        license_.checkout_url = "https://loan.feedbooks.net/loan/get/{?id,checkout_id,expires,patron_id,notification_url,hint,hint_url}"
        license_.checkouts_available = available
        license_.terms_concurrency = concurrency
        license_.expires = expires
        license_.checkouts_left = left
        pool.update_availability_from_licenses()
        return license_

    @staticmethod
    def loan_status_document(
        status: str = "ready",
        self_link: str | Literal[False] = "http://status",
        return_link: str | Literal[False] = "http://return",
        license_link: str | Literal[False] = "http://license",
        links: list[dict[str, str]] | None = None,
    ) -> LoanStatus:
        if links is None:
            links = []

        if license_link:
            links.append(
                {
                    "rel": "license",
                    "href": license_link,
                    "type": "application/vnd.readium.license.status.v1.0+json",
                },
            )

        if self_link:
            links.append(
                {
                    "rel": "self",
                    "href": self_link,
                    "type": LoanStatus.content_type(),
                }
            )

        if return_link:
            links.append(
                {
                    "rel": "return",
                    "href": return_link,
                    "type": LoanStatus.content_type(),
                }
            )
        return LoanStatus(
                id=str(uuid.uuid4()),
                status=status,
                message="This is a message",
                updated={
                    "license": "2025-04-25T11:12:13Z",
                    "status": "2025-04-25T11:12:13Z",
                },
                links=links,
                potential_rights={"end": "3017-10-21T11:12:13Z"},
            )

    def checkin(
        self, patron: Patron | None = None, pool: LicensePool | None = None
    ) -> None:
        patron = patron or self.patron
        pool = pool or self.pool

        self.mock_http.queue_response(
            200, content=self.loan_status_document().to_serializable()
        )
        self.mock_http.queue_response(
            200, content=self.loan_status_document("returned").to_serializable()
        )
        self.api.checkin(patron, "pin", pool)

    def checkout(
        self,
        loan_url: str | None = None,
        patron: Patron | None = None,
        pool: LicensePool | None = None,
        create_loan: bool = False,
    ) -> LoanInfo:
        patron = patron or self.patron
        pool = pool or self.pool
        loan_url = loan_url or self.db.fresh_url()

        self.mock_http.queue_response(
            201, content=self.loan_status_document(self_link=loan_url).to_serializable()
        )
        loan_info = self.api_checkout(patron=patron, licensepool=pool)
        if create_loan:
            loan_info.create_or_update(patron, pool)
        return loan_info

    def place_hold(
        self,
        patron: Patron | None = None,
        pool: LicensePool | None = None,
        create_hold: bool = False,
    ) -> HoldInfo:
        patron = patron or self.patron
        pool = pool or self.pool

        hold_info = self.api.place_hold(patron, "pin", pool, "dummy@email.com")
        if create_hold:
            hold_info.create_or_update(patron, pool)
        return hold_info


@pytest.fixture(scope="function")
def opds2_with_odl_api_fixture(
    db: DatabaseTransactionFixture,
    opds2_with_odl_files_fixture: OPDS2WithODLFilesFixture,
) -> OPDS2WithODLApiFixture:
    return OPDS2WithODLApiFixture(db, opds2_with_odl_files_fixture)


class LicenseHelper:
    """Represents an ODL license."""

    def __init__(
        self,
        identifier: str | None = "",
        checkouts: int | None = None,
        concurrency: int | None = None,
        expires: datetime.datetime | str | None = None,
    ) -> None:
        """Initialize a new instance of LicenseHelper class.

        :param identifier: License's identifier
        :param checkouts: Total number of checkouts before a license expires
        :param concurrency: Number of concurrent checkouts allowed
        :param expires: Date & time when a license expires
        """
        self.identifier = identifier if identifier else f"urn:uuid:{uuid.uuid1()}"
        self.checkouts = checkouts
        self.concurrency = concurrency
        self.expires = (
            expires.isoformat() if isinstance(expires, datetime.datetime) else expires
        )


class LicenseInfoHelper:
    """Represents information about the current state of a license stored in the License Info Document."""

    def __init__(
        self,
        license: LicenseHelper,
        available: int,
        status: str = "available",
        left: int | None = None,
    ) -> None:
        """Initialize a new instance of LicenseInfoHelper class."""
        self.license: LicenseHelper = license
        self.status: str = status
        self.left: int | None = left
        self.available: int = available

    def __str__(self) -> str:
        """Return a JSON representation of the License Info Document."""
        return self.json

    @property
    def json(self) -> str:
        """Return a JSON representation of the License Info Document."""
        return json.dumps(self.dict)

    @property
    def dict(self) -> dict[str, Any]:
        """Return a dictionary representation of the License Info Document."""
        output: dict[str, Any] = {
            "identifier": self.license.identifier,
            "status": self.status,
            "terms": {
                "concurrency": self.license.concurrency,
            },
            "checkouts": {
                "available": self.available,
            },
        }
        if self.license.expires is not None:
            output["terms"]["expires"] = self.license.expires
        if self.left is not None:
            output["checkouts"]["left"] = self.left
        return output

# OLD FIXTURES
class MonkeyPatchedODLFixture:
    """A fixture that patches the ODLAPI to make it possible to intercept HTTP requests for testing."""

    def __init__(self, monkeypatch: MonkeyPatch):
        self.monkeypatch = monkeypatch

    @staticmethod
    def _queue_response(patched_self, status_code, headers={}, content=None):
        patched_self.responses.insert(
            0, MockRequestsResponse(status_code, headers, content)
        )

    @staticmethod
    def _get(patched_self, url, headers=None, *args, **kwargs):
        patched_self.requests.append([url, headers])
        response = patched_self.responses.pop()
        return HTTP._process_response(url, response, *args, **kwargs)

    @staticmethod
    def _url_for(patched_self, *args, **kwargs):
        del kwargs["_external"]
        return "http://{}?{}".format(
            "/".join(args),
            "&".join([f"{key}={val}" for key, val in list(kwargs.items())]),
        )

    def __call__(self, api: type[BaseODLAPI]):
        # We monkeypatch the ODLAPI class to intercept HTTP requests and responses
        # these monkeypatched methods are staticmethods on this class. They take
        # a patched_self argument, which is the instance of the ODLAPI class that
        # they have been monkeypatched onto.
        self.monkeypatch.setattr(api, "_get", self._get)
        self.monkeypatch.setattr(api, "_url_for", self._url_for)
        self.monkeypatch.setattr(
            api, "queue_response", self._queue_response, raising=False
        )


@pytest.fixture(scope="function")
def monkey_patch_odl(monkeypatch) -> MonkeyPatchedODLFixture:
    """A fixture that patches the ODLAPI to make it possible to intercept HTTP requests for testing."""
    return MonkeyPatchedODLFixture(monkeypatch)


class ODLTestFixture:
    """A basic ODL fixture that collects various bits of information shared by all tests."""

    def __init__(
        self,
        db: DatabaseTransactionFixture,
        files: APIFilesFixture,
        patched: MonkeyPatchedODLFixture,
    ):
        self.db = db
        self.files = files
        self.patched = patched
        patched(ODLAPI)

    def library(self):
        return self.db.default_library()

    def collection(self, library, api_class=ODLAPI):
        """Create a mock ODL collection to use in tests."""
        integration_protocol = api_class.label()
        collection, _ = Collection.by_name_and_protocol(
            self.db.session,
            f"Test {api_class.__name__} Collection",
            integration_protocol,
        )
        collection.integration_configuration.settings_dict = {
            "username": "a",
            "password": "b",
            "external_account_id": "http://odl",
            Collection.DATA_SOURCE_NAME_SETTING: "Feedbooks",
        }
        collection.libraries.append(library)
        return collection

    def work(self, collection):
        return self.db.work(with_license_pool=True, collection=collection)

    def pool(self, license):
        return license.license_pool

    def license(self, work):
        def setup(self, available, concurrency, left=None, expires=None):
            self.checkouts_available = available
            self.checkouts_left = left
            self.terms_concurrency = concurrency
            self.expires = expires
            self.license_pool.update_availability_from_licenses()

        pool = work.license_pools[0]
        l = self.db.license(
            pool,
            checkout_url="https://loan.feedbooks.net/loan/get/{?id,checkout_id,expires,patron_id,notification_url,hint,hint_url}",
            checkouts_available=1,
            terms_concurrency=1,
        )
        l.setup = types.MethodType(setup, l)
        pool.update_availability_from_licenses()
        return l

    def api(self, collection):
        api = ODLAPI(self.db.session, collection)
        api.requests = []
        api.responses = []
        return api

    def checkin(self, api, patron: Patron, pool: LicensePool) -> Callable[[], None]:
        """Create a function that, when evaluated, performs a checkin."""

        lsd = json.dumps(
            {
                "status": "ready",
                "links": [
                    {
                        "rel": "return",
                        "href": "http://return",
                    }
                ],
            }
        )
        returned_lsd = json.dumps(
            {
                "status": "returned",
            }
        )

        def c():
            api.queue_response(200, content=lsd)
            api.queue_response(200)
            api.queue_response(200, content=returned_lsd)
            api.checkin(patron, "pin", pool)

        return c

    def checkout(
        self,
        api,
        patron: Patron,
        pool: LicensePool,
        db: DatabaseTransactionFixture,
        loan_url: str,
    ) -> Callable[[], tuple[LoanInfo, Any]]:
        """Create a function that, when evaluated, performs a checkout."""

        def c():
            lsd = json.dumps(
                {
                    "status": "ready",
                    "potential_rights": {"end": "3017-10-21T11:12:13Z"},
                    "links": [
                        {
                            "rel": "self",
                            "href": loan_url,
                        }
                    ],
                }
            )
            api.queue_response(200, content=lsd)
            loan = api.checkout(patron, "pin", pool, Representation.EPUB_MEDIA_TYPE)
            loan_db = (
                db.session.query(Loan)
                .filter(Loan.license_pool == pool, Loan.patron == patron)
                .one()
            )
            return loan, loan_db

        return c


@pytest.fixture(scope="function")
def odl_test_fixture(
    db: DatabaseTransactionFixture,
    api_odl_files_fixture: ODLAPIFilesFixture,
    monkey_patch_odl: MonkeyPatchedODLFixture,
) -> ODLTestFixture:
    return ODLTestFixture(db, api_odl_files_fixture, monkey_patch_odl)


class ODLAPITestFixture:
    """An ODL fixture that sets up extra information for API testing on top of the base ODL fixture."""

    def __init__(
        self,
        odl_fixture: ODLTestFixture,
        library: Library,
        collection: Collection,
        work: Work,
        license: License,
        api,
        patron: Patron,
    ):
        self.fixture = odl_fixture
        self.db = odl_fixture.db
        self.files = odl_fixture.files
        self.library = library
        self.collection = collection
        self.work = work
        self.license = license
        self.api = api
        self.patron = patron
        self.pool = license.license_pool

    def checkin(self, patron: Patron | None = None, pool: LicensePool | None = None):
        patron = patron or self.patron
        pool = pool or self.pool
        return self.fixture.checkin(self.api, patron=patron, pool=pool)()

    def checkout(
        self,
        loan_url: str | None = None,
        patron: Patron | None = None,
        pool: LicensePool | None = None,
    ) -> tuple[LoanInfo, Any]:
        patron = patron or self.patron
        pool = pool or self.pool
        loan_url = loan_url or self.db.fresh_url()
        return self.fixture.checkout(
            self.api, patron=patron, pool=pool, db=self.db, loan_url=loan_url
        )()


@pytest.fixture(scope="function")
def odl_api_test_fixture(odl_test_fixture: ODLTestFixture) -> ODLAPITestFixture:
    library = odl_test_fixture.library()
    collection = odl_test_fixture.collection(library)
    work = odl_test_fixture.work(collection)
    license = odl_test_fixture.license(work)
    api = odl_test_fixture.api(collection)
    patron = odl_test_fixture.db.patron()
    return ODLAPITestFixture(
        odl_test_fixture, library, collection, work, license, api, patron
    )


class ODL2TestFixture(ODLTestFixture):
    """An ODL2 test fixture that mirrors the ODL test fixture except for the API class being used"""

    def __init__(
        self,
        db: DatabaseTransactionFixture,
        files: APIFilesFixture,
        patched: MonkeyPatchedODLFixture,
    ):
        super().__init__(db, files, patched)
        patched(ODL2API)

    def collection(
        self, library: Library, api_class: type[ODL2API] = ODL2API
    ) -> Collection:
        collection = super().collection(library, api_class)
        collection.integration_configuration.name = "Test ODL2 Collection"
        collection.integration_configuration.protocol = ExternalIntegration.ODL2
        return collection

    def api(self, collection) -> ODL2API:
        api = ODL2API(self.db.session, collection)
        api.requests = []  # type: ignore
        api.responses = []  # type: ignore
        return api


class ODL2APITestFixture(ODLAPITestFixture):
    """The ODL2 API fixture has no changes in terms of data, from the ODL API fixture"""


@pytest.fixture(scope="function")
def odl2_test_fixture(
    db: DatabaseTransactionFixture,
    api_odl2_files_fixture: ODL2APIFilesFixture,
    monkey_patch_odl: MonkeyPatchedODLFixture,
) -> ODL2TestFixture:
    """The ODL2 API uses the ODL API in the background, so the mockeypatching is the same"""
    return ODL2TestFixture(db, api_odl2_files_fixture, monkey_patch_odl)


@pytest.fixture(scope="function")
def odl2_api_test_fixture(odl2_test_fixture: ODL2TestFixture) -> ODL2APITestFixture:
    library = odl2_test_fixture.library()
    collection = odl2_test_fixture.collection(library)
    work = odl2_test_fixture.work(collection)
    license = odl2_test_fixture.license(work)
    api = odl2_test_fixture.api(collection)
    patron = odl2_test_fixture.db.patron()
    return ODL2APITestFixture(
        odl2_test_fixture, library, collection, work, license, api, patron
    )
