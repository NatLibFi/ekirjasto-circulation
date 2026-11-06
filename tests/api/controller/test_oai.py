from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest
from lxml import etree

from api.controller.oaipmh import OAIError, OAIPMHController
from core.oai_pmh import OAIHeader


@pytest.fixture
def controller() -> OAIPMHController:
    manager = MagicMock()
    manager._db = MagicMock()
    return OAIPMHController(manager)


def test_date_range_accepts_matching_precisions_and_includes_until_day(
    controller: OAIPMHController,
):
    since, until = controller.date_range({"from": "2026-11-01", "until": "2026-11-05"})

    assert since == datetime(2026, 11, 1, tzinfo=UTC)
    assert until == datetime(2026, 11, 5, 23, 59, 59, tzinfo=UTC)


@pytest.mark.parametrize(
    "arguments",
    [
        {"from": "2026-11-01", "until": "2026-11-01T00:00:00Z"},
        {"from": "2026-11-02T00:00:00Z", "until": "2026-11-01T00:00:00Z"},
        {"from": "not-a-date"},
    ],
)
def test_date_range_rejects_invalid_bounds(
    controller: OAIPMHController, arguments: dict[str, str]
):
    with pytest.raises(OAIError, match="Date|Invalid") as excinfo:
        controller.date_range(arguments)
    assert excinfo.value.code == "badArgument"


def test_identifier_round_trip_preserves_unicode_values():
    header = OAIHeader(
        work_id=1,
        identifier_id=2,
        identifier_type="URN",
        identifier_value="urn:isbn:978-951-0-12345-6/ä",
        pool_id=3,
        modified_at=datetime(2026, 11, 5, tzinfo=UTC),
        entered_at=datetime(2026, 11, 5, tzinfo=UTC),
    )

    identifier = OAIPMHController.identifier("example.org", header)

    assert OAIPMHController.decode_identifier("example.org", identifier) == (
        "URN",
        "urn:isbn:978-951-0-12345-6/ä",
    )


@pytest.mark.parametrize("identifier", ["oai:other.org:abc", "oai:example.org:%%%"])
def test_decode_identifier_rejects_invalid_identifiers(identifier: str):
    with pytest.raises(OAIError) as excinfo:
        OAIPMHController.decode_identifier("example.org", identifier)
    assert excinfo.value.code == "idDoesNotExist"


@pytest.mark.parametrize(
    "query,code",
    [
        ({"verb": "Unknown"}, "badVerb"),
        ({"verb": "GetRecord"}, "badArgument"),
        ({"verb": "ListRecords", "metadataPrefix": "bad"}, None),
        ({"verb": "Identify", "verb": ["Identify", "Identify"]}, "badVerb"),
    ],
)
def test_validate_arguments(query: dict[str, str], code: str | None):
    controller = OAIPMHController(MagicMock(_db=MagicMock()))
    with flask.Flask(__name__).test_request_context("/", query_string=query):
        if code:
            with pytest.raises(OAIError) as excinfo:
                controller.validate_arguments()
            assert excinfo.value.code == code
        else:
            verb, arguments = controller.validate_arguments()
            assert verb == "ListRecords"
            assert arguments["metadataPrefix"] == "bad"


def test_decode_identifier_rejects_noncanonical_base64():
    payload = json.dumps(["ISBN", "123"], separators=(",", ":")).encode()
    padded = base64.urlsafe_b64encode(payload).decode()

    with pytest.raises(OAIError) as excinfo:
        OAIPMHController.decode_identifier("example.org", f"oai:example.org:{padded}")
    assert excinfo.value.code == "idDoesNotExist"


def test_records_does_not_load_discovery_service_web_clients():
    manager = MagicMock()
    manager._db = MagicMock()
    controller = OAIPMHController(manager)
    library = SimpleNamespace(
        settings=SimpleNamespace(
            oai_pmh_catalogue_url="https://catalogue.example",
            oai_pmh_organization_code="org",
        ),
        short_name="library",
    )
    header = OAIHeader(
        work_id=1,
        identifier_id=2,
        identifier_type="ISBN",
        identifier_value="978-951-0-12345-6",
        pool_id=3,
        modified_at=datetime(2026, 11, 5, tzinfo=UTC),
        entered_at=datetime(2026, 11, 5, tzinfo=UTC),
    )
    pool = SimpleNamespace(
        work=MagicMock(),
        presentation_edition=MagicMock(),
        identifier=MagicMock(),
    )
    catalogue = MagicMock()
    catalogue.load_pools.return_value = {header.pool_id: pool}
    catalogue.isbn_identifiers.return_value = {}
    builder = MagicMock()

    with (
        patch("api.controller.oaipmh.OAICatalogue", return_value=catalogue),
        patch("api.controller.oaipmh.MARCRecordBuilder", return_value=builder) as build,
        patch("api.controller.oaipmh.record_to_xml", return_value=b"<record/>"),
    ):
        controller.records(
            etree.Element("root"),
            "example.org",
            [header],
            library,
            "https://circulation.example",
        )

    manager._db.scalars.assert_not_called()
    assert build.call_args.args[:4] == (
        "https://circulation.example",
        "library",
        ["https://catalogue.example"],
        "org",
    )
