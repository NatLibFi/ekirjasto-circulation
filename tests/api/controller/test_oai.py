from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest
from itsdangerous import URLSafeTimedSerializer
from lxml import etree

from api.controller.oaipmh import (
    MARC_NS,
    MARC_SCHEMA,
    OAI_NS,
    OAIError,
    OAIPMHController,
    ResumptionTokenState,
)
from core.oai_pmh import OAIHeader


@pytest.fixture
def controller() -> OAIPMHController:
    manager = MagicMock()
    manager._db = MagicMock()
    return OAIPMHController(manager)


@pytest.fixture
def library() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        name="Example Library",
        short_name="library",
        uuid="library-uuid",
        settings=SimpleNamespace(
            oai_pmh_namespace="example.org",
            oai_pmh_catalogue_url="https://catalogue.example",
            oai_pmh_organization_code="org",
            oai_pmh_admin_contact="mailto:admin@example.org",
            oai_pmh_page_size=2,
            oai_pmh_token_lifetime=3600,
        ),
    )


@pytest.fixture
def header() -> OAIHeader:
    return OAIHeader(
        work_id=1,
        identifier_id=2,
        identifier_type="ISBN",
        identifier_value="978-951-0-12345-6",
        pool_id=3,
        modified_at=datetime(2026, 11, 5, 12, 34, 56, tzinfo=UTC),
        entered_at=datetime(2026, 11, 5, tzinfo=UTC),
    )


def test_date_range_accepts_day_precision_and_includes_until_day(controller):
    """Day-precision harvesting must include every record on the until date."""
    since, until = controller.date_range({"from": "2026-11-01", "until": "2026-11-05"})

    assert since == datetime(2026, 11, 1, tzinfo=UTC)
    assert until == datetime(2026, 11, 5, 23, 59, 59, tzinfo=UTC)


def test_date_range_accepts_second_precision_and_equal_bounds(controller):
    """Second-precision harvests need exact inclusive boundaries."""
    value = "2026-11-05T12:34:56Z"

    assert controller.date_range({"from": value, "until": value}) == (
        datetime(2026, 11, 5, 12, 34, 56, tzinfo=UTC),
        datetime(2026, 11, 5, 12, 34, 56, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ({}, (None, None)),
        ({"from": "2026-11-01"}, (datetime(2026, 11, 1, tzinfo=UTC), None)),
        (
            {"until": "2026-11-05"},
            (None, datetime(2026, 11, 5, 23, 59, 59, tzinfo=UTC)),
        ),
    ],
)
def test_date_range_accepts_missing_bounds(controller, arguments, expected):
    """Open-ended harvests must remain valid when either date bound is absent."""
    assert controller.date_range(arguments) == expected


@pytest.mark.parametrize(
    "arguments",
    [
        {"from": "2026-11-01", "until": "2026-11-01T00:00:00Z"},
        {"from": "2026-11-02T00:00:00Z", "until": "2026-11-01T00:00:00Z"},
        {"from": "2026-02-30"},
        {"from": "2026-11-31T00:00:00Z"},
        {"from": "not-a-date"},
    ],
)
def test_date_range_rejects_invalid_bounds(controller, arguments):
    """Mixed precision, reversed, malformed, and impossible dates must fail early."""
    with pytest.raises(OAIError, match="Date|Invalid") as excinfo:
        controller.date_range(arguments)
    assert excinfo.value.code == "badArgument"


@pytest.mark.parametrize(
    "identifier_type,identifier_value",
    [
        ("ISBN", "978-951-0-12345-6"),
        ("URN", "urn:isbn:978-951-0-12345-6/ä"),
        ("DOI", "10.1234/value-with-hyphens/%"),
    ],
)
def test_identifier_round_trip_preserves_identifier_values(
    identifier_type, identifier_value
):
    """Repository identifiers must preserve supported types and escaped values."""
    header = OAIHeader(
        work_id=1,
        identifier_id=2,
        identifier_type=identifier_type,
        identifier_value=identifier_value,
        pool_id=3,
        modified_at=datetime(2026, 11, 5, tzinfo=UTC),
        entered_at=datetime(2026, 11, 5, tzinfo=UTC),
    )

    identifier = OAIPMHController.identifier("example.org", header)

    assert OAIPMHController.decode_identifier("example.org", identifier) == (
        identifier_type,
        identifier_value,
    )


@pytest.mark.parametrize(
    "identifier",
    [
        "oai:other.org:abc",
        "oai:example.org:-value",
        "oai:example.org:type-",
        "oai:example.org:%%%",
    ],
)
def test_decode_identifier_rejects_invalid_identifiers(identifier):
    """Malformed or foreign identifiers must not expose another repository's records."""
    with pytest.raises(OAIError) as excinfo:
        OAIPMHController.decode_identifier("example.org", identifier)
    assert excinfo.value.code == "idDoesNotExist"


def test_decode_identifier_accepts_unknown_identifier_type_token():
    """Future identifier types must remain decodable through their textual token."""
    assert OAIPMHController.decode_identifier(
        "example.org", "oai:example.org:custom-value"
    ) == ("custom", "value")


@pytest.mark.parametrize(
    "query,code",
    [
        ({"verb": "Unknown"}, "badVerb"),
        ({"verb": "GetRecord"}, "badArgument"),
        ({"verb": "GetRecord", "identifier": "id"}, "badArgument"),
        ({"verb": "ListRecords", "metadataPrefix": "marc21", "set": "all"}, "ok"),
        ({"verb": "Identify", "verb": ["Identify", "Identify"]}, "badVerb"),
        ({"verb": "Identify", "foo": "bar"}, "badArgument"),
        ({"verb": "Identify", "foo": ""}, "badArgument"),
        (
            {"verb": "ListRecords", "resumptionToken": "token", "from": "2026-11-01"},
            "badArgument",
        ),
        ({"verb": "ListSets", "resumptionToken": "token"}, "badResumptionToken"),
    ],
)
def test_validate_arguments(query, code):
    """Protocol argument errors must use the OAI error code expected by harvesters."""
    controller = OAIPMHController(MagicMock(_db=MagicMock()))
    with flask.Flask(__name__).test_request_context("/", query_string=query):
        if code != "ok":
            with pytest.raises(OAIError) as excinfo:
                controller.validate_arguments()
            assert excinfo.value.code == code
        else:
            verb, arguments = controller.validate_arguments()
            assert verb == "ListRecords"
            assert arguments["metadataPrefix"] == "marc21"


@pytest.mark.parametrize(
    "query,verb",
    [
        ({"verb": "Identify"}, "Identify"),
        ({"verb": "ListMetadataFormats"}, "ListMetadataFormats"),
        ({"verb": "ListMetadataFormats", "identifier": "id"}, "ListMetadataFormats"),
        (
            {"verb": "GetRecord", "identifier": "id", "metadataPrefix": "marc21"},
            "GetRecord",
        ),
        ({"verb": "ListIdentifiers", "metadataPrefix": "marc21"}, "ListIdentifiers"),
        ({"verb": "ListRecords", "metadataPrefix": "marc21"}, "ListRecords"),
        ({"verb": "ListSets"}, "ListSets"),
    ],
)
def test_validate_arguments_accepts_supported_verb_shapes(query, verb):
    """Each verb must accept exactly the required and documented optional arguments."""
    controller = OAIPMHController(MagicMock(_db=MagicMock()))
    with flask.Flask(__name__).test_request_context("/", query_string=query):
        actual_verb, arguments = controller.validate_arguments()

    assert actual_verb == verb
    assert arguments["verb"] == verb


def make_state(library_id=7, namespace="example.org", verb="ListRecords", **changes):
    values = dict(
        version=2,
        library=library_id,
        namespace=namespace,
        verb=verb,
        after=0,
        upper=10,
        size=100,
        filters={"from": "2026-11-01"},
        expires=2_000_000_000,
        cursor=0,
    )
    values.update(changes)
    return ResumptionTokenState(**values)


def test_token_state_round_trip(controller, library):
    """A valid signed token must preserve the state needed to continue a harvest."""
    app = flask.Flask(__name__)
    app.secret_key = "test-secret"
    state = make_state()

    with app.app_context():
        token = controller.serializer().dumps(state.as_dict())
        assert (
            controller.token_state(
                token, library, "example.org", "ListRecords", library.settings
            )
            == state
        )


@pytest.mark.parametrize(
    "token_state_kwargs,namespace,verb",
    [
        ({"expires": 1}, "example.org", "ListRecords"),
        ({}, "other.org", "ListRecords"),
        ({}, "example.org", "ListIdentifiers"),
        ({"library": 8}, "example.org", "ListRecords"),
        ({"after": 20, "upper": 10}, "example.org", "ListRecords"),
        ({"size": 99}, "example.org", "ListRecords"),
        ({"size": 5001}, "example.org", "ListRecords"),
    ],
)
def test_token_state_rejects_invalid_state(
    controller, library, token_state_kwargs, namespace, verb
):
    """Expired, mismatched, and unsafe paging state must not continue a harvest."""
    app = flask.Flask(__name__)
    app.secret_key = "test-secret"
    state = make_state(**token_state_kwargs)

    with app.app_context():
        token = controller.serializer().dumps(state.as_dict())
        with pytest.raises(OAIError) as excinfo:
            controller.token_state(token, library, namespace, verb, library.settings)

    assert excinfo.value.code == "badResumptionToken"


def test_token_state_rejects_invalid_signature(controller, library):
    """Tokens signed by another application must be rejected as unusable."""
    app = flask.Flask(__name__)
    app.secret_key = "test-secret"
    other_serializer = URLSafeTimedSerializer("other-secret", salt="oai-pmh-v1")

    with app.app_context():
        token = other_serializer.dumps(make_state().as_dict())
        with pytest.raises(OAIError, match="Invalid or expired"):
            controller.token_state(
                token, library, "example.org", "ListRecords", library.settings
            )


def test_serializer_requires_application_secret(controller):
    """Without an application secret, issuing tokens would make continuation insecure."""
    app = flask.Flask(__name__)

    with app.app_context(), pytest.raises(RuntimeError, match="signing secret"):
        controller.serializer()


def test_identify_dispatch_writes_repository_metadata(controller, library):
    """Identify must publish the repository identity and harvest capabilities."""
    root = controller._response_root()
    catalogue = MagicMock()
    catalogue.earliest_datestamp.return_value = datetime(2026, 1, 1, tzinfo=UTC)

    with patch("api.controller.oaipmh.OAICatalogue", return_value=catalogue):
        controller.dispatch(
            root,
            "Identify",
            {},
            library,
            library.settings,
            "example.org",
            "https://example.org/library/oaipmh",
            "https://example.org",
        )

    identify = root.find(f"{{{OAI_NS}}}Identify")
    assert identify is not None
    assert identify.findtext(f"{{{OAI_NS}}}repositoryName") == library.name
    assert identify.findtext(f"{{{OAI_NS}}}protocolVersion") == "2.0"
    assert identify.findtext(f"{{{OAI_NS}}}earliestDatestamp") == "2026-01-01T00:00:00Z"


def test_list_metadata_formats_dispatch_writes_marc21(controller, library):
    """Metadata discovery must advertise the MARC21 format actually served."""
    root = controller._response_root()

    with patch("api.controller.oaipmh.OAICatalogue"):
        controller.dispatch(
            root,
            "ListMetadataFormats",
            {},
            library,
            library.settings,
            "example.org",
            "https://example.org/oaipmh",
            "https://example.org",
        )

    node = root.find(f"{{{OAI_NS}}}ListMetadataFormats/{{{OAI_NS}}}metadataFormat")
    assert node.findtext(f"{{{OAI_NS}}}metadataPrefix") == "marc21"
    assert node.findtext(f"{{{OAI_NS}}}schema") == MARC_SCHEMA
    assert node.findtext(f"{{{OAI_NS}}}metadataNamespace") == MARC_NS


def test_list_metadata_formats_with_unknown_identifier_raises(controller, library):
    """Format discovery for an unknown record must return idDoesNotExist."""
    catalogue = MagicMock()
    catalogue.headers.return_value = []
    with patch("api.controller.oaipmh.OAICatalogue", return_value=catalogue):
        with pytest.raises(OAIError) as excinfo:
            controller.list_metadata_formats(
                controller._response_root(),
                {"identifier": "oai:example.org:nope"},
                library,
                "example.org",
            )
    assert excinfo.value.code == "idDoesNotExist"


def test_get_record_dispatch_uses_single_header_and_records(
    controller, library, header
):
    """GetRecord must resolve one current header before producing its MARC record."""
    catalogue = MagicMock()
    catalogue.headers.return_value = [header]
    controller.records = MagicMock()
    root = controller._response_root()
    identifier = controller.identifier("example.org", header)

    with patch("api.controller.oaipmh.OAICatalogue", return_value=catalogue):
        controller.dispatch(
            root,
            "GetRecord",
            {"identifier": identifier, "metadataPrefix": "marc21"},
            library,
            library.settings,
            "example.org",
            "https://example.org/oaipmh",
            "https://example.org",
        )

    controller.records.assert_called_once()
    assert controller.records.call_args.args[2] == [header]


@pytest.mark.parametrize("verb", ["ListIdentifiers", "ListRecords"])
def test_list_dispatch_paginates_and_emits_resumption_token(
    controller, library, header, verb
):
    """Large listings must return a bounded page and a signed continuation token."""
    headers = [
        header,
        header.__class__(
            work_id=2,
            identifier_id=3,
            identifier_type="ISBN",
            identifier_value="978-951-0-12345-7",
            pool_id=4,
            modified_at=header.modified_at,
            entered_at=header.entered_at,
        ),
        header.__class__(
            work_id=3,
            identifier_id=4,
            identifier_type="ISBN",
            identifier_value="978-951-0-12345-8",
            pool_id=5,
            modified_at=header.modified_at,
            entered_at=header.entered_at,
        ),
    ]
    catalogue = MagicMock()
    catalogue.headers.return_value = headers
    catalogue.upper_bound.return_value = 3
    controller.records = MagicMock()
    root = controller._response_root()
    app = flask.Flask(__name__)
    app.secret_key = "test-secret"

    with app.app_context(), patch(
        "api.controller.oaipmh.OAICatalogue", return_value=catalogue
    ):
        controller.dispatch(
            root,
            verb,
            {"metadataPrefix": "marc21"},
            library,
            library.settings,
            "example.org",
            "https://example.org/oaipmh",
            "https://example.org",
        )

    node = root.find(f"{{{OAI_NS}}}{verb}")
    token = node.find(f"{{{OAI_NS}}}resumptionToken")
    assert token is not None
    assert token.get("cursor") == "0"
    assert token.get("expirationDate") is not None
    if verb == "ListIdentifiers":
        assert len(node.findall(f"{{{OAI_NS}}}header")) == 2
    else:
        controller.records.assert_called_once()
        assert controller.records.call_args.args[2] == headers[:2]


def test_list_dispatch_consumes_resumption_token(controller, library, header):
    """Continuation requests must resume from the token's saved cursor and verb."""
    catalogue = MagicMock()
    catalogue.headers.return_value = [header]
    state = make_state(verb="ListIdentifiers", after=1, upper=3, cursor=2)
    root = controller._response_root()
    app = flask.Flask(__name__)
    app.secret_key = "test-secret"

    with app.app_context():
        token = controller.serializer().dumps(state.as_dict())
        with patch("api.controller.oaipmh.OAICatalogue", return_value=catalogue):
            controller.dispatch(
                root,
                "ListIdentifiers",
                {"resumptionToken": token},
                library,
                library.settings,
                "example.org",
                "https://example.org/oaipmh",
                "https://example.org",
            )

    catalogue.headers.assert_called_once()
    assert catalogue.headers.call_args.kwargs["after"] == 1


def test_dispatch_rejects_unknown_metadata_format(controller, library):
    """Requests for unsupported metadata must fail instead of returning misleading data."""
    with pytest.raises(OAIError) as excinfo:
        controller.dispatch(
            controller._response_root(),
            "Identify",
            {"metadataPrefix": "bad"},
            library,
            library.settings,
            "example.org",
            "https://example.org/oaipmh",
            "https://example.org",
        )
    assert excinfo.value.code == "cannotDisseminateFormat"


def test_dispatch_list_sets_rejects_set_hierarchy(controller, library):
    """The provider must explicitly report that it has no set hierarchy."""
    with pytest.raises(OAIError) as excinfo:
        controller.dispatch(
            controller._response_root(),
            "ListSets",
            {},
            library,
            library.settings,
            "example.org",
            "https://example.org/oaipmh",
            "https://example.org",
        )
    assert excinfo.value.code == "noSetHierarchy"


def test_list_dispatch_reports_no_records(controller, library):
    """An empty initial listing must use noRecordsMatch rather than an empty success page."""
    catalogue = MagicMock()
    catalogue.headers.return_value = []
    catalogue.upper_bound.return_value = 10

    with patch("api.controller.oaipmh.OAICatalogue", return_value=catalogue):
        with pytest.raises(OAIError) as excinfo:
            controller.list_records_or_identifiers(
                controller._response_root(),
                "ListRecords",
                {"metadataPrefix": "marc21"},
                library,
                library.settings,
                "example.org",
                "https://example.org",
            )

    assert excinfo.value.code == "noRecordsMatch"


def test_render_error_clears_partial_response_and_bad_arguments(controller):
    """Protocol errors must remove partial payloads and sanitize bad requests."""
    root = controller._response_root()
    request = etree.SubElement(root, f"{{{OAI_NS}}}request", verb="BadVerb")
    etree.SubElement(root, f"{{{OAI_NS}}}Identify")

    controller._render_error(root, request, OAIError("badArgument", "Invalid input"))

    assert request.attrib == {}
    assert root.find(f"{{{OAI_NS}}}Identify") is None
    error = root.find(f"{{{OAI_NS}}}error")
    assert error.get("code") == "badArgument"
    assert error.text == "Invalid input"


def test_response_root_has_oai_namespaces_and_schema_location(controller):
    """Every response needs the OAI namespace and schema declaration for consumers."""
    root = controller._response_root()
    xml = etree.tostring(root)

    assert root.tag == f"{{{OAI_NS}}}OAI-PMH"
    assert b"OAI-PMH.xsd" in xml
    assert root.find(f"{{{OAI_NS}}}responseDate") is not None


def test_harvest_renders_xml_errors_without_caching(controller, library):
    """The public endpoint must return parseable protocol errors and disable caching."""
    app = flask.Flask(__name__)
    app.secret_key = "test-secret"
    controller._repository_context = MagicMock(
        return_value=(
            library.settings,
            "https://example.org",
            "https://example.org/oaipmh",
        )
    )
    controller._freeze_namespace = MagicMock(return_value="example.org")

    with app.test_request_context("/", query_string={"verb": "Unknown"}):
        flask.request.library = library
        response = controller.harvest()

    document = etree.fromstring(response.data)
    error = document.find(f"{{{OAI_NS}}}error")
    request = document.find(f"{{{OAI_NS}}}request")
    assert response.status_code == 200
    assert response.mimetype == "text/xml"
    assert response.headers["Cache-Control"] == "no-store"
    assert error.get("code") == "badVerb"
    assert request.attrib == {}


def test_records_builds_marc_record_and_oai_header(controller, library, header):
    """Record responses must combine MARC output with a correct OAI header."""
    pool = SimpleNamespace(
        work=MagicMock(),
        presentation_edition=MagicMock(),
        identifier=MagicMock(),
    )
    catalogue = MagicMock()
    catalogue.load_pools.return_value = {header.pool_id: pool}
    catalogue.isbn_identifiers.return_value = {header.identifier_id: None}
    builder = MagicMock()
    builder.annotate_work_record.return_value = MagicMock()
    parent = etree.Element("root")

    with (
        patch("api.controller.oaipmh.OAICatalogue", return_value=catalogue),
        patch("api.controller.oaipmh.MARCRecordBuilder", return_value=builder) as build,
        patch(
            "api.controller.oaipmh.record_to_xml", return_value=b"<record/>"
        ) as to_xml,
    ):
        controller.records(
            parent, "example.org", [header], library, "https://circulation.example"
        )

    build.assert_called_once_with(
        "https://circulation.example",
        "library",
        ["https://catalogue.example"],
        "org",
        modified_at=header.modified_at,
        entered_at=header.entered_at,
        isbn_identifiers={header.identifier_id: None},
    )
    builder.annotate_work_record.assert_called_once_with(
        True, pool.work, pool, pool.presentation_edition, pool.identifier
    )
    to_xml.assert_called_once_with(
        builder.annotate_work_record.return_value, namespace=True
    )
    record = parent.find(f"{{{OAI_NS}}}record")
    assert record is not None
    assert record.find(
        f"{{{OAI_NS}}}header/{{{OAI_NS}}}identifier"
    ).text == controller.identifier("example.org", header)
    assert (
        record.find(f"{{{OAI_NS}}}header/{{{OAI_NS}}}datestamp").text
        == "2026-11-05T12:34:56Z"
    )
    metadata = record.find(f"{{{OAI_NS}}}metadata")
    assert metadata[0].get(
        f"{{http://www.w3.org/2001/XMLSchema-instance}}schemaLocation"
    ) == (f"{MARC_NS} {MARC_SCHEMA}")
