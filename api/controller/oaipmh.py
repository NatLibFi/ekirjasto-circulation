"""Finna's MARC-only OAI-PMH 2.0 endpoint (full-harvest first release)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import cast
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


def element(parent, name: str, text: str | None = None, **attributes):
    """Append an OAI-PMH namespace element with optional text and attributes."""
    child = etree.SubElement(parent, f"{{{OAI_NS}}}{name}", **attributes)
    child.text = text
    return child


def timestamp(value: datetime) -> str:
    """Format a datetime as UTC with the second precision used by OAI-PMH."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class OAIPMHController(LoggerMixin):
    VERBS: dict[str, tuple[set[str], set[str]]] = {
        "Identify": (set(), set()),
        "ListMetadataFormats": (set(), {"identifier"}),
        "GetRecord": ({"identifier", "metadataPrefix"}, set()),
        "ListIdentifiers": ({"metadataPrefix"}, {"from", "until", "set"}),
        "ListRecords": ({"metadataPrefix"}, {"from", "until", "set"}),
        "ListSets": (set(), set()),
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
        bounds: list[datetime | None] = []
        for name in ("from", "until"):
            value = arguments.get(name)
            if value is None:
                bounds.append(None)
                continue
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                fmt = "%Y-%m-%d"
            elif re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
            ):
                fmt = "%Y-%m-%dT%H:%M:%SZ"
            else:
                raise OAIError(
                    "badArgument", "Dates must use UTC seconds or YYYY-MM-DD."
                )
            try:
                bound = datetime.strptime(value, fmt).replace(tzinfo=UTC)
                # Day-granularity end bounds include every second of that day.
                if name == "until" and len(value) == 10:
                    bound += timedelta(hours=23, minutes=59, seconds=59)
                bounds.append(bound)
            except ValueError as exc:
                raise OAIError("badArgument", "Invalid date.") from exc
        since, until = bounds
        if since is not None and until is not None:
            if len(arguments["from"]) != len(arguments["until"]) or since > until:
                raise OAIError(
                    "badArgument",
                    "Date bounds must have matching granularity and from <= until.",
                )
        return since, until

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
        if "resumptionToken" in arguments:
            if verb not in {"ListIdentifiers", "ListRecords", "ListSets"} or set(
                arguments
            ) != {"verb", "resumptionToken"}:
                raise OAIError(
                    "badArgument",
                    "A resumption token must be used alone with its verb.",
                )
            if verb == "ListSets":
                raise OAIError(
                    "badResumptionToken", "This repository does not issue set tokens."
                )
            return verb, arguments
        required, optional = self.VERBS[verb]
        keys = set(arguments) - {"verb"}
        if not required <= keys or keys - required - optional:
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
    ) -> dict:
        """Verify a continuation token and return its saved harvest state.

        Check signature age, the original harvest expiry, repository and verb
        identity, and paging bounds. Report invalid state as
        ``badResumptionToken``.
        """
        try:
            state = self.serializer().loads(
                token, max_age=settings.oai_pmh_token_lifetime
            )
            if (
                not isinstance(state, dict)
                or state["version"] != 2
                or state["library"] != library.id
                or state["namespace"] != namespace
                or state["verb"] != verb
                or state["expires"] <= utc_now().timestamp()
                or not 0 <= state["after"] <= state["upper"]
                or not 100 <= state["size"] <= 5000
            ):
                raise ValueError()
            return state
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
        parent,
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
        urls = []
        if settings.oai_pmh_catalogue_url:
            urls.append(settings.oai_pmh_catalogue_url)
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
        root,
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
        catalogue = OAICatalogue(self._db, library)
        if "metadataPrefix" in arguments and arguments["metadataPrefix"] != "marc21":
            raise OAIError(
                "cannotDisseminateFormat", "Only marc21 metadata is available."
            )
        if verb == "Identify":
            node = element(root, verb)
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
            return
        if verb == "ListSets":
            raise OAIError("noSetHierarchy", "This repository does not support sets.")
        if verb == "ListMetadataFormats":
            if "identifier" in arguments:
                self.single_header(catalogue, namespace, arguments["identifier"])
            node = element(element(root, verb), "metadataFormat")
            element(node, "metadataPrefix", "marc21")
            element(node, "schema", MARC_SCHEMA)
            element(node, "metadataNamespace", MARC_NS)
            return
        if verb == "GetRecord":
            header = self.single_header(catalogue, namespace, arguments["identifier"])
            self.records(element(root, verb), namespace, [header], library, cm_url)
            return
        if "resumptionToken" in arguments:
            state = self.token_state(
                arguments["resumptionToken"], library, namespace, verb, settings
            )
        else:
            self.date_range(arguments)
            if "set" in arguments:
                raise OAIError(
                    "noSetHierarchy", "This repository does not support sets."
                )
            # Bound later pages by work ID without taking a catalogue snapshot.
            # Carry the original expiry forward so paging cannot extend it.
            state = {
                "version": 2,
                "library": library.id,
                "namespace": namespace,
                "verb": verb,
                "after": 0,
                "upper": catalogue.upper_bound(),
                "size": settings.oai_pmh_page_size,
                "filters": {
                    key: arguments[key] for key in ("from", "until") if key in arguments
                },
                "expires": int(utc_now().timestamp()) + settings.oai_pmh_token_lifetime,
                "cursor": 0,
            }
        since, until = self.date_range(state["filters"])
        headers = catalogue.headers(
            after=state["after"],
            upper=state["upper"],
            # Fetch one extra header to detect whether a continuation is needed.
            limit=state["size"] + 1,
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
        page = headers[: state["size"]]
        node = element(root, verb)
        if verb == "ListIdentifiers":
            for header in page:
                self.add_header(node, namespace, header)
        else:
            self.records(node, namespace, page, library, cm_url)
        if len(headers) > len(page):
            next_state = dict(
                state, after=page[-1].work_id, cursor=state["cursor"] + len(page)
            )
            element(
                node,
                "resumptionToken",
                self.serializer().dumps(next_state),
                cursor=str(state["cursor"]),
                expirationDate=timestamp(datetime.fromtimestamp(state["expires"], UTC)),
            )

    def harvest(self) -> Response:
        """Handle a library-scoped GET or POST request and return OAI-PMH XML.

        Load local repository settings and freeze the namespace on first use.
        Return HTTP errors for incomplete configuration; encode protocol errors
        inside the XML response and disable response caching.
        """
        library = cast(Library, getattr(flask.request, "library"))
        # Use local settings: repository identity must never be inherited.
        settings = integration_settings_load(LibrarySettings, library)
        cm_url = ConfigurationSetting.sitewide(
            self._db, Configuration.BASE_URL_KEY
        ).value
        if (
            not cm_url
            or urlsplit(cm_url).scheme not in {"https", "http"}
            or not urlsplit(cm_url).netloc
            or not library.uuid
        ):
            return Response(
                "OAI-PMH repository configuration is incomplete.\n",
                status=503,
                mimetype="text/plain",
            )
        assert library.short_name is not None
        cm_url = cm_url.rstrip("/")
        base_url = f"{cm_url}/{quote(library.short_name, safe='')}/oaipmh"
        # The conditional update makes concurrent first requests agree on identity.
        self._db.execute(
            update(Library)
            .where(Library.id == library.id, Library.oai_pmh_namespace == None)
            .values(oai_pmh_namespace=settings.oai_pmh_namespace),
            execution_options={"synchronize_session": False},
        )
        stored_namespace = self._db.scalar(
            select(Library.oai_pmh_namespace).where(Library.id == library.id)
        )
        if stored_namespace != settings.oai_pmh_namespace:
            self.log.error("OAI-PMH namespace changed for library %s", library.id)
            return Response(
                "The OAI-PMH repository namespace cannot change.\n",
                status=503,
                mimetype="text/plain",
            )
        namespace = stored_namespace
        root = etree.Element(
            f"{{{OAI_NS}}}OAI-PMH", nsmap={None: OAI_NS, "xsi": XSI_NS}
        )
        root.set(f"{{{XSI_NS}}}schemaLocation", f"{OAI_NS} {OAI_SCHEMA}")
        element(root, "responseDate", timestamp(utc_now()))
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
            if exc.code in {"badArgument", "badVerb"}:
                request_node.attrib.clear()
            # Discard partial verb output before emitting the protocol error.
            for child in list(root)[2:]:
                root.remove(child)
            element(root, "error", str(exc), code=exc.code)
        response = Response(
            etree.tostring(root, encoding="UTF-8", xml_declaration=True),
            mimetype="text/xml",
        )
        response.headers["Cache-Control"] = "no-store"
        return response
