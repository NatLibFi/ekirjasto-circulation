"""Finna's MARC-only OAI-PMH 2.0 endpoint (full-harvest first release)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import quote, unquote, urlsplit

import flask
from flask import Response
from itsdangerous import BadData, URLSafeTimedSerializer
from lxml import etree
from pymarc import record_to_xml
from sqlalchemy import select, update

from api.config import Configuration
from core.configuration.library import LibrarySettings
from core.integration.base import integration_settings_load
from core.marc import MARCRecordBuilder
from core.model import ConfigurationSetting, Library
from core.oai_pmh import OAICatalogue, OAIHeader
from core.util.datetime_helpers import utc_now
from core.util.log import LoggerMixin

OAI_NS = "http://www.openarchives.org/OAI/2.0/"
MARC_NS = "http://www.loc.gov/MARC21/slim"
MARC_SCHEMA = "http://www.loc.gov/standards/marcxml/schema/MARC21slim.xsd"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
OAI_SCHEMA = "http://www.openarchives.org/OAI/2.0/OAI-PMH.xsd"

# These are probably the only types E-kirjasto will ever have.
OAI_IDENTIFIER_TYPE_TOKENS = {
    "ISBN": "isbn",
    "URN": "urn",
    "URI": "uri",
    "DOI": "doi",
    "ASIN": "asin",
    "UPC": "upc",
    "EAN": "ean",
}

OAI_IDENTIFIER_TYPES_BY_TOKEN = {
    token: identifier_type
    for identifier_type, token in OAI_IDENTIFIER_TYPE_TOKENS.items()
}


class OAIError(Exception):
    def __init__(self, code: str, message: str):
        """Store the OAI-PMH error code and human-readable response message."""
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class VerbSpec:
    required: frozenset[str]
    optional: frozenset[str]
    accepts_resumption_token: bool = False


@dataclass(frozen=True)
class ResumptionTokenState:
    version: int
    library: int
    namespace: str
    verb: str
    after: int
    upper: int
    size: int
    filters: dict[str, str]
    expires: int
    cursor: int

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-compatible token payload."""
        return {
            "version": self.version,
            "library": self.library,
            "namespace": self.namespace,
            "verb": self.verb,
            "after": self.after,
            "upper": self.upper,
            "size": self.size,
            "filters": self.filters,
            "expires": self.expires,
            "cursor": self.cursor,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Any,
        *,
        library: Library,
        namespace: str,
        verb: str,
    ) -> ResumptionTokenState:
        """Validate and construct a continuation state from a token payload."""
        if not isinstance(payload, dict):
            raise ValueError()
        state = cls(
            version=payload["version"],
            library=payload["library"],
            namespace=payload["namespace"],
            verb=payload["verb"],
            after=payload["after"],
            upper=payload["upper"],
            size=payload["size"],
            filters=payload["filters"],
            expires=payload["expires"],
            cursor=payload["cursor"],
        )
        if (
            state.version != 2
            or state.library != library.id
            or state.namespace != namespace
            or state.verb != verb
            or not isinstance(state.filters, dict)
            or state.expires <= utc_now().timestamp()
            or not 0 <= state.after <= state.upper
            or not 100 <= state.size <= 5000
        ):
            raise ValueError()
        return state


def element(
    parent: etree._Element,
    name: str,
    text: str | None = None,
    **attributes: str,
) -> etree._Element:
    """Append an OAI-PMH namespace element with optional text and attributes."""
    child = etree.SubElement(parent, f"{{{OAI_NS}}}{name}", **attributes)
    child.text = text
    return child


def timestamp(value: datetime) -> str:
    """Format a datetime as UTC with the second precision used by OAI-PMH."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class OAIPMHController(LoggerMixin):
    VERBS: dict[str, VerbSpec] = {
        "Identify": VerbSpec(frozenset(), frozenset()),
        "ListMetadataFormats": VerbSpec(frozenset(), frozenset({"identifier"})),
        "GetRecord": VerbSpec(frozenset({"identifier", "metadataPrefix"}), frozenset()),
        "ListIdentifiers": VerbSpec(
            frozenset({"metadataPrefix"}),
            frozenset({"from", "until", "set"}),
            accepts_resumption_token=True,
        ),
        "ListRecords": VerbSpec(
            frozenset({"metadataPrefix"}),
            frozenset({"from", "until", "set"}),
            accepts_resumption_token=True,
        ),
        "ListSets": VerbSpec(frozenset(), frozenset(), accepts_resumption_token=True),
    }

    def __init__(self, manager):
        """Use the circulation manager's database session for harvest requests."""
        self._db = manager._db

    @staticmethod
    def date_range(
        arguments: dict[str, str]
    ) -> tuple[datetime | None, datetime | None]:
        """Parse inclusive OAI-PMH date bounds into timezone-aware UTC datetimes.

        Accept day or second precision, expanding a day-only ``until`` to the
        last second of that day. Raise ``badArgument`` for invalid dates,
        mixed precision, or a start later than the end.
        """
        bounds: dict[str, tuple[datetime, str] | None] = {}
        for name in ("from", "until"):
            value = arguments.get(name)
            if value is None:
                bounds[name] = None
                continue
            date, precision = OAIPMHController.parse_date(value)
            if name == "until" and precision == "day":
                date += timedelta(hours=23, minutes=59, seconds=59)
            bounds[name] = date, precision
        since_bound = bounds["from"]
        until_bound = bounds["until"]
        if since_bound is not None and until_bound is not None:
            since, since_precision = since_bound
            until, until_precision = until_bound
            if since_precision != until_precision or since > until:
                raise OAIError(
                    "badArgument",
                    "Date bounds must have matching granularity and from <= until.",
                )
        return (
            since_bound[0] if since_bound else None,
            until_bound[0] if until_bound else None,
        )

    @staticmethod
    def parse_date(value: str) -> tuple[datetime, str]:
        """Parse one supported OAI-PMH date and return its precision."""
        formats = {
            10: ("%Y-%m-%d", "day"),
            20: ("%Y-%m-%dT%H:%M:%SZ", "second"),
        }
        try:
            fmt, precision = formats[len(value)]
        except KeyError as exc:
            raise OAIError(
                "badArgument", "Dates must use UTC seconds or YYYY-MM-DD."
            ) from exc
        if precision == "day" and not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value
        ):
            raise OAIError("badArgument", "Dates must use UTC seconds or YYYY-MM-DD.")
        if precision == "second" and not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
        ):
            raise OAIError("badArgument", "Dates must use UTC seconds or YYYY-MM-DD.")
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC), precision
        except ValueError as exc:
            raise OAIError("badArgument", "Invalid date.") from exc

    def validate_arguments(self) -> tuple[str, dict[str, str]]:
        """Validate the current request and return its verb and arguments.

        Enforce verb-specific required and optional arguments, reject empty or
        repeated values, and require resumption tokens to accompany only a verb.
        Raise OAIError with the appropriate protocol error code on failure.
        """
        values = flask.request.values
        verbs = values.getlist("verb")
        if len(verbs) != 1 or verbs[0] not in self.VERBS:
            raise OAIError("badVerb", "Supply exactly one supported OAI-PMH verb.")
        verb = verbs[0]
        if any(len(values.getlist(key)) != 1 or not values[key] for key in values):
            raise OAIError(
                "badArgument", "Arguments must be nonempty and may not be repeated."
            )
        arguments = values.to_dict()
        spec = self.VERBS[verb]
        if "resumptionToken" in arguments:
            if not spec.accepts_resumption_token or set(arguments) != {
                "verb",
                "resumptionToken",
            }:
                raise OAIError(
                    "badArgument",
                    "A resumption token must be used alone with its verb.",
                )
            if verb == "ListSets":
                raise OAIError(
                    "badResumptionToken", "This repository does not issue set tokens."
                )
            return verb, arguments
        keys = set(arguments) - {"verb"}
        if not spec.required <= keys or keys - spec.required - spec.optional:
            raise OAIError(
                "badArgument", "Missing or unsupported argument for this verb."
            )
        return verb, arguments

    @staticmethod
    def identifier(namespace: str, header: OAIHeader) -> str:
        """Encode a selected publication's type and value as a repository identifier.

        Use a readable, typed local identifier. Percent-encoding keeps values
        URI-safe while the type prefix prevents collisions between identifier
        systems that happen to use the same value.
        """
        type_token = OAI_IDENTIFIER_TYPE_TOKENS.get(
            header.identifier_type,
            header.identifier_type.lower().replace(" ", "-"),
        )
        value = quote(header.identifier_value, safe="-._~")
        return f"oai:{namespace}:{type_token}-{value}"

    @staticmethod
    def decode_identifier(namespace: str, identifier: str) -> tuple[str, str]:
        """Recover the publication type and value from a canonical repository identifier.

        Reject foreign namespaces and malformed typed identifiers with
        ``idDoesNotExist``.
        """
        prefix = f"oai:{namespace}:"
        try:
            if not identifier.startswith(prefix):
                raise ValueError()
            type_token, separator, encoded_value = identifier[len(prefix) :].partition(
                "-"
            )
            if not separator or not type_token or not encoded_value:
                raise ValueError()
            identifier_type = OAI_IDENTIFIER_TYPES_BY_TOKEN.get(
                type_token, type_token.replace("-", " ")
            )
            identifier_value = unquote(encoded_value)
            if not identifier_type or not identifier_value:
                raise ValueError()
            return identifier_type, identifier_value
        except (ValueError, UnicodeError) as exc:
            raise OAIError("idDoesNotExist", "Unknown publication identifier.") from exc

    def serializer(self) -> URLSafeTimedSerializer:
        """Create the signed token serializer, requiring an application signing secret."""
        secret = flask.current_app.secret_key
        if not secret:
            raise RuntimeError("OAI-PMH requires the application's signing secret")
        return URLSafeTimedSerializer(secret, salt="oai-pmh-v1")

    def token_state(
        self,
        token: str,
        library: Library,
        namespace: str,
        verb: str,
        settings: LibrarySettings,
    ) -> ResumptionTokenState:
        """Verify a continuation token and return its saved harvest state.

        Check signature age, the original harvest expiry, repository and verb
        identity, and paging bounds. Report invalid state as
        ``badResumptionToken``.
        """
        try:
            payload = self.serializer().loads(
                token, max_age=settings.oai_pmh_token_lifetime
            )
            return ResumptionTokenState.from_dict(
                payload, library=library, namespace=namespace, verb=verb
            )
        except (BadData, ValueError, KeyError, TypeError) as exc:
            raise OAIError(
                "badResumptionToken", "Invalid or expired resumption token."
            ) from exc

    @staticmethod
    def add_header(parent, namespace: str, header: OAIHeader):
        """Append a publication identifier and modification datestamp to an OAI header."""
        node = element(parent, "header")
        element(node, "identifier", OAIPMHController.identifier(namespace, header))
        element(node, "datestamp", timestamp(header.modified_at))

    def single_header(
        self, catalogue: OAICatalogue, namespace: str, identifier: str
    ) -> OAIHeader:
        """Find a currently harvestable publication by its repository identifier.

        Raise ``idDoesNotExist`` if decoding fails or the catalogue no longer
        exposes the selected publication.
        """
        identity = self.decode_identifier(namespace, identifier)
        headers = catalogue.headers(limit=1, identifier=identity)
        if not headers:
            raise OAIError("idDoesNotExist", "Unknown publication identifier.")
        return headers[0]

    def records(
        self,
        parent: etree._Element,
        namespace: str,
        headers: list[OAIHeader],
        library: Library,
        cm_url: str,
    ) -> None:
        """Append OAI records containing MARCXML metadata for the requested page.

        Batch-load selected pools and equivalent ISBNs, and use catalogue
        datestamps for MARC control fields. Include the optional configured
        catalogue URL in the generated MARC links.
        """
        settings = library.settings
        urls = (
            [settings.oai_pmh_catalogue_url] if settings.oai_pmh_catalogue_url else []
        )
        assert library.short_name is not None
        catalogue = OAICatalogue(self._db, library)
        pools = catalogue.load_pools(headers)
        isbns = catalogue.isbn_identifiers(headers)
        for header in headers:
            pool = pools[header.pool_id]
            annotator = MARCRecordBuilder(
                cm_url,
                library.short_name,
                urls,
                settings.oai_pmh_organization_code,
                modified_at=header.modified_at,
                entered_at=header.entered_at,
                isbn_identifiers=isbns,
            )
            record = annotator.annotate_work_record(
                True, pool.work, pool, pool.presentation_edition, pool.identifier
            )
            node = element(parent, "record")
            self.add_header(node, namespace, header)
            metadata = element(node, "metadata")
            marc_xml = etree.fromstring(record_to_xml(record, namespace=True))
            marc_xml.set(f"{{{XSI_NS}}}schemaLocation", f"{MARC_NS} {MARC_SCHEMA}")
            metadata.append(marc_xml)

    def dispatch(
        self,
        root: etree._Element,
        verb: str,
        arguments: dict[str, str],
        library: Library,
        settings: LibrarySettings,
        namespace: str,
        base_url: str,
        cm_url: str,
    ) -> None:
        """Append the requested verb's response or raise an OAI-PMH protocol error.

        Serve repository information, format discovery, individual records, or
        paginated headers and records. Continuations retain the initial work-ID
        ceiling, date filters, page size, and expiry while reading live data.
        """
        if "metadataPrefix" in arguments and arguments["metadataPrefix"] != "marc21":
            raise OAIError(
                "cannotDisseminateFormat", "Only marc21 metadata is available."
            )
        if verb == "Identify":
            self.identify(root, library, settings, base_url)
            return
        if verb == "ListSets":
            raise OAIError("noSetHierarchy", "This repository does not support sets.")
        if verb == "ListMetadataFormats":
            self.list_metadata_formats(root, arguments, library, namespace)
            return
        if verb == "GetRecord":
            self.get_record(root, arguments, library, namespace, cm_url)
            return
        self.list_records_or_identifiers(
            root, verb, arguments, library, settings, namespace, cm_url
        )

    def identify(
        self,
        root: etree._Element,
        library: Library,
        settings: LibrarySettings,
        base_url: str,
    ) -> None:
        """Append the repository's Identify response."""
        catalogue = OAICatalogue(self._db, library)
        node = element(root, "Identify")
        for name, value in (
            ("repositoryName", library.name),
            ("baseURL", base_url),
            ("protocolVersion", "2.0"),
            ("adminEmail", settings.oai_pmh_admin_contact),
            ("earliestDatestamp", timestamp(catalogue.earliest_datestamp())),
            ("deletedRecord", "no"),
            ("granularity", "YYYY-MM-DDThh:mm:ssZ"),
        ):
            element(node, name, value)

    def list_metadata_formats(
        self,
        root: etree._Element,
        arguments: dict[str, str],
        library: Library,
        namespace: str,
    ) -> None:
        """Append the supported metadata format description."""
        catalogue = OAICatalogue(self._db, library)
        if "identifier" in arguments:
            self.single_header(catalogue, namespace, arguments["identifier"])
        node = element(element(root, "ListMetadataFormats"), "metadataFormat")
        element(node, "metadataPrefix", "marc21")
        element(node, "schema", MARC_SCHEMA)
        element(node, "metadataNamespace", MARC_NS)

    def get_record(
        self,
        root: etree._Element,
        arguments: dict[str, str],
        library: Library,
        namespace: str,
        cm_url: str,
    ) -> None:
        """Append one currently harvestable MARC record."""
        catalogue = OAICatalogue(self._db, library)
        header = self.single_header(catalogue, namespace, arguments["identifier"])
        self.records(element(root, "GetRecord"), namespace, [header], library, cm_url)

    def list_records_or_identifiers(
        self,
        root: etree._Element,
        verb: str,
        arguments: dict[str, str],
        library: Library,
        settings: LibrarySettings,
        namespace: str,
        cm_url: str,
    ) -> None:
        """Append one page of headers or records and an optional token."""
        catalogue = OAICatalogue(self._db, library)
        state = self._listing_state(
            arguments, library, settings, namespace, verb, catalogue
        )
        since, until = self.date_range(state.filters)
        headers = catalogue.headers(
            after=state.after,
            upper=state.upper,
            # Fetch one extra header to detect whether a continuation is needed.
            limit=state.size + 1,
            since=since,
            until=until,
        )
        if not headers:
            if "resumptionToken" in arguments:
                raise OAIError(
                    "badResumptionToken",
                    "The live catalogue no longer contains this page; restart the harvest.",
                )
            raise OAIError("noRecordsMatch", "No publications match this request.")
        page = headers[: state.size]
        node = element(root, verb)
        if verb == "ListIdentifiers":
            for header in page:
                self.add_header(node, namespace, header)
        else:
            self.records(node, namespace, page, library, cm_url)
        if len(headers) > len(page):
            next_state = ResumptionTokenState(
                **{
                    **state.as_dict(),
                    "after": page[-1].work_id,
                    "cursor": state.cursor + len(page),
                }
            )
            element(
                node,
                "resumptionToken",
                self.serializer().dumps(next_state.as_dict()),
                cursor=str(state.cursor),
                expirationDate=timestamp(datetime.fromtimestamp(state.expires, UTC)),
            )

    def _listing_state(
        self,
        arguments: dict[str, str],
        library: Library,
        settings: LibrarySettings,
        namespace: str,
        verb: str,
        catalogue: OAICatalogue,
    ) -> ResumptionTokenState:
        """Load a continuation state or create the state for a new listing."""
        if "resumptionToken" in arguments:
            return self.token_state(
                arguments["resumptionToken"], library, namespace, verb, settings
            )
        self.date_range(arguments)
        if "set" in arguments:
            raise OAIError("noSetHierarchy", "This repository does not support sets.")
        now = int(utc_now().timestamp())
        assert library.id is not None
        return ResumptionTokenState(
            version=2,
            library=library.id,
            namespace=namespace,
            verb=verb,
            after=0,
            upper=catalogue.upper_bound(),
            size=settings.oai_pmh_page_size,
            filters={
                key: arguments[key] for key in ("from", "until") if key in arguments
            },
            expires=now + settings.oai_pmh_token_lifetime,
            cursor=0,
        )

    def _repository_context(
        self, library: Library
    ) -> tuple[LibrarySettings, str, str] | Response:
        """Load local OAI settings and construct the canonical endpoint URLs."""
        settings = integration_settings_load(LibrarySettings, library)
        cm_url = ConfigurationSetting.sitewide(
            self._db, Configuration.BASE_URL_KEY
        ).value
        if (
            not cm_url
            or urlsplit(cm_url).scheme not in {"https", "http"}
            or not urlsplit(cm_url).netloc
            or not library.uuid
            or not settings.oai_pmh_namespace
        ):
            return Response(
                "OAI-PMH repository configuration is incomplete.\n",
                status=503,
                mimetype="text/plain",
            )
        assert library.short_name is not None
        cm_url = cm_url.rstrip("/")
        base_url = f"{cm_url}/{quote(library.short_name, safe='')}/oaipmh"
        return settings, cm_url, base_url

    def _freeze_namespace(
        self, library: Library, configured_namespace: str | None
    ) -> str | Response:
        """Persist the first namespace and reject later configuration changes."""
        # The conditional update makes concurrent first requests agree on identity.
        self._db.execute(
            update(Library)
            .where(Library.id == library.id, Library.oai_pmh_namespace.is_(None))
            .values(oai_pmh_namespace=configured_namespace),
            execution_options={"synchronize_session": False},
        )
        stored_namespace = self._db.scalar(
            select(Library.oai_pmh_namespace).where(Library.id == library.id)
        )
        if stored_namespace != configured_namespace:
            self.log.error("OAI-PMH namespace changed for library %s", library.id)
            return Response(
                "The OAI-PMH repository namespace cannot change.\n",
                status=503,
                mimetype="text/plain",
            )
        return cast(str, stored_namespace)

    @staticmethod
    def _response_root() -> etree._Element:
        """Create the OAI-PMH response envelope."""
        root = etree.Element(
            f"{{{OAI_NS}}}OAI-PMH", nsmap={None: OAI_NS, "xsi": XSI_NS}
        )
        root.set(f"{{{XSI_NS}}}schemaLocation", f"{OAI_NS} {OAI_SCHEMA}")
        element(root, "responseDate", timestamp(utc_now()))
        return root

    @staticmethod
    def _render_error(
        root: etree._Element, request_node: etree._Element, error: OAIError
    ) -> None:
        """Replace any partial response with one protocol error element."""
        if error.code in {"badArgument", "badVerb"}:
            request_node.attrib.clear()
        for child in list(root)[2:]:
            root.remove(child)
        element(root, "error", str(error), code=error.code)

    def harvest(self) -> Response:
        """Handle a library-scoped GET or POST request and return OAI-PMH XML.

        Load local repository settings and freeze the namespace on first use.
        Return HTTP errors for incomplete configuration; encode protocol errors
        inside the XML response and disable response caching.
        """
        library = cast(Library, getattr(flask.request, "library"))
        context = self._repository_context(library)
        if isinstance(context, Response):
            return context
        settings, cm_url, base_url = context
        namespace = self._freeze_namespace(library, settings.oai_pmh_namespace)
        if isinstance(namespace, Response):
            return namespace
        root = self._response_root()
        request_node = element(root, "request", base_url)
        try:
            verb, arguments = self.validate_arguments()
            try:
                for name, value in arguments.items():
                    request_node.set(name, value)
            except ValueError as exc:
                raise OAIError(
                    "badArgument", "Arguments must contain valid XML characters."
                ) from exc
            self.dispatch(
                root, verb, arguments, library, settings, namespace, base_url, cm_url
            )
        except OAIError as exc:
            self._render_error(root, request_node, exc)
        response = Response(
            etree.tostring(
                root,
                encoding="UTF-8",
                xml_declaration=True,
                pretty_print=True,
            ),
            mimetype="text/xml",
        )
        response.headers["Cache-Control"] = "no-store"
        return response
