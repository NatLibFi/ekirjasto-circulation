from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime
from io import BytesIO, StringIO
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import webpub_manifest_parser.opds2.ast as opds2_ast
from flask_babel import lazy_gettext as _
from requests import Response
from sqlalchemy.orm import Session
from uritemplate import URITemplate
from webpub_manifest_parser.core import ManifestParserFactory, ManifestParserResult
from webpub_manifest_parser.core.analyzer import NodeFinder
from webpub_manifest_parser.core.ast import (
    ArrayOfCollectionsProperty,
    Link,
    Manifestlike,
)
from webpub_manifest_parser.core.properties import BooleanProperty
from webpub_manifest_parser.core.syntax import MissingPropertyError
from webpub_manifest_parser.errors import BaseError
from webpub_manifest_parser.opds2 import (
    ManifestParser,
    OPDS2CollectionRolesRegistry,
    OPDS2FeedParserFactory,
    OPDS2SemanticAnalyzer,
    OPDS2SyntaxAnalyzer,
)
from webpub_manifest_parser.opds2.registry import (
    OPDS2LinkRelationsRegistry,
    OPDS2MediaTypesRegistry,
)
from webpub_manifest_parser.utils import encode, first_or_default

from api.circulation import Fulfillment, FulfillmentInfo
from api.circulation_exceptions import CannotFulfill
from core.coverage import CoverageFailure
from core.integration.settings import (
    ConfigurationFormItem,
    ConfigurationFormItemType,
    FormField,
)
from core.metadata_layer import (
    CirculationData,
    ContributorData,
    FormatData,
    IdentifierData,
    LinkData,
    Metadata,
    SubjectData,
)
from core.model import (
    Collection,
    Contributor,
    DataSource,
    DeliveryMechanism,
    Edition,
    ExternalIntegration,
    Hyperlink,
    Identifier,
    LicensePool,
    LicensePoolDeliveryMechanism,
    LinkRelations,
    MediaTypes,
    Patron,
    Representation,
    RightsStatus,
    Subject,
)
from core.model.constants import IdentifierType
from core.opds_import import (
    BaseOPDSAPI,
    BaseOPDSImporter,
    OPDSImporterLibrarySettings,
    OPDSImporterSettings,
    OPDSImportMonitor,
)
from core.util.http import HTTP, BadResponseException
from core.util.opds_writer import OPDSFeed

if TYPE_CHECKING:
    from webpub_manifest_parser.core import ast as core_ast


class RWPMManifestParser:
    def __init__(self, manifest_parser_factory: ManifestParserFactory):
        """Initialize a new instance of RWPMManifestParser class.

        :param manifest_parser_factory: Factory creating a new instance
            of a RWPM-compatible parser (RWPM, OPDS 2.x, ODL 2.x, etc.)
        """
        if not isinstance(manifest_parser_factory, ManifestParserFactory):
            raise ValueError(
                "Argument 'manifest_parser_factory' must be an instance of {}".format(
                    ManifestParserFactory
                )
            )

        self._manifest_parser_factory = manifest_parser_factory

    def parse_manifest(
        self, manifest: str | dict[str, Any] | Manifestlike
    ) -> ManifestParserResult:
        """Parse the feed into an RPWM-like AST object.

        :param manifest: RWPM-like manifest
        :return: Parsed RWPM-like manifest
        """
        result = None
        input_stream: BytesIO | StringIO

        try:
            if isinstance(manifest, bytes):
                input_stream = BytesIO(manifest)
                parser = self._manifest_parser_factory.create()
                result = parser.parse_stream(input_stream)
            elif isinstance(manifest, str):
                input_stream = StringIO(manifest)
                parser = self._manifest_parser_factory.create()
                result = parser.parse_stream(input_stream)
            elif isinstance(manifest, dict):
                parser = self._manifest_parser_factory.create()
                result = parser.parse_json(manifest)
            elif isinstance(manifest, Manifestlike):
                result = ManifestParserResult(manifest)
            else:
                raise ValueError(
                    "Argument 'manifest' must be either a string, a dictionary, or an instance of {}".format(
                        Manifestlike
                    )
                )
        except BaseError:
            logging.exception("Failed to parse the RWPM-like manifest")

            raise

        return result


class PalaceOPDS2PresentationMetadata(opds2_ast.PresentationMetadata):  # type: ignore[misc]
    time_tracking = BooleanProperty(
        "http://palaceproject.io/terms/timeTracking", False, default_value=False
    )


class PalaceOPDS2Publication(opds2_ast.OPDS2Publication):  # type: ignore[misc]
    metadata = opds2_ast.TypeProperty(
        key="metadata", required=True, nested_type=PalaceOPDS2PresentationMetadata
    )


class PalaceOPDS2Feed(opds2_ast.OPDS2Feed):  # type: ignore[misc]
    publications = ArrayOfCollectionsProperty(
        "publications",
        required=False,
        role=OPDS2CollectionRolesRegistry.PUBLICATIONS,
        collection_type=PalaceOPDS2Publication,
    )


class PalaceOPDS2SyntaxAnalyzer(OPDS2SyntaxAnalyzer):  # type: ignore[misc]
    def _create_manifest(self) -> opds2_ast.OPDS2Feed:
        return PalaceOPDS2Feed()


class PalaceOPDS2FeedParserFactory(OPDS2FeedParserFactory):  # type: ignore[misc]
    def create(self) -> ManifestParser:
        """Create a new OPDS 2.0 parser.

        :return: OPDS 2.0 parser
        :rtype: Parser
        """
        media_types_registry = OPDS2MediaTypesRegistry()
        link_relations_registry = OPDS2LinkRelationsRegistry()
        collection_roles_registry = OPDS2CollectionRolesRegistry()
        syntax_analyzer = (
            PalaceOPDS2SyntaxAnalyzer()
        )  # This is the only change from the base class
        semantic_analyzer = OPDS2SemanticAnalyzer(
            media_types_registry, link_relations_registry, collection_roles_registry
        )
        parser = ManifestParser(syntax_analyzer, semantic_analyzer)

        return parser


class OPDS2ImporterSettings(OPDSImporterSettings):
    custom_accept_header: str = FormField(
        default="{}, {};q=0.9, */*;q=0.1".format(
            OPDS2MediaTypesRegistry.OPDS_FEED.key, "application/json"
        ),
        form=ConfigurationFormItem(
            label=_("Custom accept header"),
            description=_(
                "Some servers expect an accept header to decide which file to send. You can use */* if the server doesn't expect anything."
            ),
            type=ConfigurationFormItemType.TEXT,
            required=False,
        ),
    )

    ignored_identifier_types: list[str] = FormField(
        alias="IGNORED_IDENTIFIER_TYPE",
        default=[],
        form=ConfigurationFormItem(
            label=_("List of identifiers that will be skipped"),
            description=_(
                "Circulation Manager will not be importing publications with identifiers having one of the selected types."
            ),
            type=ConfigurationFormItemType.MENU,
            required=False,
            options={
                identifier_type.value: identifier_type.value
                for identifier_type in IdentifierType
            },
            format="narrow",
        ),
    )


class OPDS2ImporterLibrarySettings(OPDSImporterLibrarySettings):
    pass


class OPDS2API(BaseOPDSAPI):
    @classmethod
    def settings_class(cls) -> type[OPDS2ImporterSettings]:
        return OPDS2ImporterSettings

    @classmethod
    def library_settings_class(cls) -> type[OPDS2ImporterLibrarySettings]:
        return OPDS2ImporterLibrarySettings

    @classmethod
    def label(cls) -> str:
        return "OPDS 2.0 Import"

    @classmethod
    def description(cls) -> str:
        return "Import books from a publicly-accessible OPDS 2.0 feed."

    def __init__(self, _db: Session, collection: Collection):
        super().__init__(_db, collection)
        self.token_auth_configuration: str | None = (
            collection.integration_configuration.context.get(
                ExternalIntegration.TOKEN_AUTH
            )
        )

    @classmethod
    def get_authentication_token(
        cls, patron: Patron, datasource: DataSource, token_auth_url: str
    ) -> str:
        """Get the authentication token for a patron"""
        log = cls.logger()

        patron_id = patron.identifier_to_remote_service(datasource)
        url = URITemplate(token_auth_url).expand(patron_id=patron_id)
        response = HTTP.get_with_timeout(url)
        if response.status_code != 200:
            log.error(
                f"Could not authenticate the patron (authorization identifier: '{patron.authorization_identifier}' "
                f"external identifier: '{patron_id}'): {str(response.content)}"
            )
            raise CannotFulfill()

        # The response should be the JWT token, not wrapped in any format like JSON
        token = response.text
        if not token:
            log.error(
                f"Could not authenticate the patron({patron_id}): {str(response.content)}"
            )
            raise CannotFulfill()

        return token

    def fulfill_token_auth(
        self, patron: Patron, licensepool: LicensePool, fulfillment: FulfillmentInfo
    ) -> FulfillmentInfo:
        if not fulfillment.content_link:
            self.log.warning(
                "No content link found in fulfillment, unable to fulfill via OPDS2 token auth."
            )
            return fulfillment

        templated = URITemplate(fulfillment.content_link)
        if "authentication_token" not in templated.variable_names:
            self.log.warning(
                "No authentication_token variable found in content_link, unable to fulfill via OPDS2 token auth."
            )
            return fulfillment

        if not self.token_auth_configuration:
            self.log.warning(
                "No token auth configuration found, unable to fulfill via OPDS2 token auth."
            )
            return fulfillment

        token = self.get_authentication_token(
            patron, licensepool.data_source, self.token_auth_configuration
        )
        fulfillment.content_link = templated.expand(authentication_token=token)
        fulfillment.content_link_redirect = True
        return fulfillment

    def fulfill(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        delivery_mechanism: LicensePoolDeliveryMechanism,
    ) -> Fulfillment | FulfillmentInfo:
        fufillment_info = super().fulfill(patron, pin, licensepool, delivery_mechanism)
        if self.token_auth_configuration:
            fufillment_info = self.fulfill_token_auth(
                patron, licensepool, fufillment_info  # type: ignore
            )
        return fufillment_info


class OPDS2Importer(BaseOPDSImporter[OPDS2ImporterSettings]):
    """Imports editions and license pools from an OPDS 2.0 feed."""

    NAME: str = ExternalIntegration.OPDS2_IMPORT
    DESCRIPTION: str = _("Import books from a publicly-accessible OPDS 2.0 feed.")
    NEXT_LINK_RELATION: str = "next"

    @classmethod
    def settings_class(cls) -> type[OPDS2ImporterSettings]:
        return OPDS2ImporterSettings

    def __init__(
        self,
        db: Session,
        collection: Collection,
        parser: RWPMManifestParser,
        data_source_name: str | None = None,
        http_get: Callable[..., tuple[int, Any, bytes]] | None = None,
    ):
        """Initialize a new instance of OPDS2Importer class.

        :param db: Database session

        :param collection: Circulation Manager's collection.
            LicensePools created by this OPDS2Import class will be associated with the given Collection.
            If this is None, no LicensePools will be created -- only Editions.
        :param parser: Feed parser
        :param data_source_name: Name of the source of this OPDS feed.
            All Editions created by this import will be associated with this DataSource.
            If there is no DataSource with this name, one will be created.
            NOTE: If `collection` is provided, its .data_source will take precedence over any value provided here.
            This is only for use when you are importing OPDS metadata without any particular Collection in mind.
        """
        super().__init__(db, collection, data_source_name, http_get)
        self._parser = parser
        self.ignored_identifier_types = self.settings.ignored_identifier_types

    def _is_identifier_allowed(self, identifier: Identifier) -> bool:
        """Check the identifier and return a boolean value indicating whether CM can import it.

        :param identifier: Identifier object
        :return: Boolean value indicating whether CM can import the identifier
        """
        return identifier.type not in self.ignored_identifier_types

    def _extract_subjects(self, subjects: list[core_ast.Subject]) -> list[SubjectData]:
        """Extract a list of SubjectData objects from the webpub-manifest-parser's subject.

        :param subjects: Parsed subject object
        :return: List of subjects metadata
        """
        self.log.debug("Started extracting subjects metadata")

        subject_metadata_list = []

        for subject in subjects:
            self.log.debug(
                f"Started extracting subject metadata from {encode(subject)}"
            )

            scheme = subject.scheme

            subject_type = Subject.by_uri.get(scheme)
            if not subject_type:
                # We can't represent this subject because we don't
                # know its scheme. Just treat it as a tag.
                subject_type = Subject.TAG

            subject_metadata = SubjectData(
                type=subject_type, identifier=subject.code, name=subject.name, weight=1
            )

            subject_metadata_list.append(subject_metadata)

            self.log.debug(
                "Finished extracting subject metadata from {}: {}".format(
                    encode(subject), encode(subject_metadata)
                )
            )

        self.log.debug(
            "Finished extracting subjects metadata: {}".format(
                encode(subject_metadata_list)
            )
        )

        return subject_metadata_list

    def _extract_contributors(
        self,
        contributors: list[core_ast.Contributor],
        default_role: str | None = Contributor.AUTHOR_ROLE,
    ) -> list[ContributorData]:
        """Extract a list of ContributorData objects from the webpub-manifest-parser's contributor.

        :param contributors: Parsed contributor object
        :param default_role: Default role
        :return: List of contributors metadata
        """
        self.log.debug("Started extracting contributors metadata")

        contributor_metadata_list = []

        for contributor in contributors:
            self.log.debug(
                "Started extracting contributor metadata from {}".format(
                    encode(contributor)
                )
            )

            contributor_metadata = ContributorData(
                sort_name=contributor.sort_as,
                display_name=contributor.name,
                family_name=None,
                wikipedia_name=None,
                roles=contributor.roles if contributor.roles else default_role,
            )
            # If the feed is missing contributor name information, record the information to our metadata
            if not (
                contributor_metadata.sort_name or contributor_metadata.display_name
            ):
                contributor_metadata.sort_name = Edition.UNKNOWN_AUTHOR
                contributor_metadata.display_name = Edition.UNKNOWN_AUTHOR
                self.log.info(
                    "Extracted contributor metadata with missing name from {}: {}".format(
                        encode(contributor), encode(contributor_metadata)
                    )
                )

            self.log.debug(
                "Finished extracting contributor metadata from {}: {}".format(
                    encode(contributor), encode(contributor_metadata)
                )
            )

            contributor_metadata_list.append(contributor_metadata)

        self.log.debug(
            "Finished extracting contributors metadata: {}".format(
                encode(contributor_metadata_list)
            )
        )

        return contributor_metadata_list

    def _extract_link(
        self, link: Link, feed_self_url: str, default_link_rel: str | None = None
    ) -> LinkData:
        """Extract a LinkData object from webpub-manifest-parser's link.

        :param link: webpub-manifest-parser's link
        :param feed_self_url: Feed's self URL
        :param default_link_rel: Default link's relation

        :return: Link metadata
        """
        self.log.debug(f"Started extracting link metadata from {encode(link)}")

        # FIXME: It seems that OPDS 2.0 spec doesn't contain information about rights so we use the default one.
        rights_uri = RightsStatus.rights_uri_from_string("")
        rel = first_or_default(link.rels, default_link_rel)
        media_type = link.type
        href = link.href

        if feed_self_url and not urlparse(href).netloc:
            # This link is relative, so we need to get the absolute url
            href = urljoin(feed_self_url, href)

        link_metadata = LinkData(
            rel=rel,
            href=href,
            media_type=media_type,
            rights_uri=rights_uri,
            content=None,
        )

        self.log.debug(
            "Finished extracting link metadata from {}: {}".format(
                encode(link), encode(link_metadata)
            )
        )

        return link_metadata

    def _extract_description_link(
        self, publication: opds2_ast.OPDS2Publication
    ) -> LinkData | None:
        """Extract description from the publication object and create a Hyperlink.DESCRIPTION link containing it.

        :param publication: Publication object
        :return: LinkData object containing publication's description
        """
        self.log.debug(
            "Started extracting a description link from {}".format(
                encode(publication.metadata.description)
            )
        )

        description_link = None

        if publication.metadata.description:
            description_link = LinkData(
                rel=Hyperlink.DESCRIPTION,
                media_type=MediaTypes.TEXT_PLAIN,
                content=publication.metadata.description,
            )

        self.log.debug(
            "Finished extracting a description link from {}: {}".format(
                encode(publication.metadata.description), encode(description_link)
            )
        )

        return description_link

    def _extract_image_links(
        self, publication: opds2_ast.OPDS2Publication, feed_self_url: str
    ) -> list[LinkData]:
        """Extracts a list of LinkData objects containing information about artwork.

        :param publication: Publication object
        :param feed_self_url: Feed's self URL
        :return: List of links metadata
        """
        self.log.debug(
            f"Started extracting image links from {encode(publication.images)}"
        )

        if not publication.images:
            return []

        # FIXME: This code most likely will not work in general.
        # There's no guarantee that these images have the same media type,
        # or that the second-largest image isn't far too large to use as a thumbnail.
        # Instead of using the second-largest image as a thumbnail,
        # find the image that would make the best thumbnail
        # because of its dimensions, media type, and aspect ratio:
        #       IDEAL_COVER_ASPECT_RATIO = 2.0/3
        #       IDEAL_IMAGE_HEIGHT = 240
        #       IDEAL_IMAGE_WIDTH = 160

        sorted_raw_image_links = list(
            reversed(
                sorted(
                    publication.images.links,
                    key=lambda link: (link.width or 0, link.height or 0),
                )
            )
        )
        image_links = []

        if len(sorted_raw_image_links) > 0:
            cover_link = self._extract_link(
                sorted_raw_image_links[0],
                feed_self_url,
                default_link_rel=Hyperlink.IMAGE,
            )
            image_links.append(cover_link)

        if len(sorted_raw_image_links) > 1:
            cover_link = self._extract_link(
                sorted_raw_image_links[1],
                feed_self_url,
                default_link_rel=Hyperlink.THUMBNAIL_IMAGE,
            )
            image_links.append(cover_link)

        self.log.debug(
            "Finished extracting image links from {}: {}".format(
                encode(publication.images), encode(image_links)
            )
        )

        return image_links

    def _extract_links(
        self, publication: opds2_ast.OPDS2Publication, feed_self_url: str
    ) -> list[LinkData]:
        """Extract a list of LinkData objects from a list of webpub-manifest-parser links.

        :param publication: Publication object
        :param feed_self_url: Feed's self URL
        :return: List of links metadata
        """
        self.log.debug(f"Started extracting links from {encode(publication.links)}")

        links = []

        for link in publication.links:
            link_metadata = self._extract_link(link, feed_self_url)
            links.append(link_metadata)

        description_link = self._extract_description_link(publication)
        if description_link:
            links.append(description_link)

        image_links = self._extract_image_links(publication, feed_self_url)
        if image_links:
            links.extend(image_links)

        self.log.debug(
            "Finished extracting links from {}: {}".format(
                encode(publication.links), encode(links)
            )
        )

        return links

    def _extract_media_types_and_drm_scheme_from_link(
        self, link: core_ast.Link
    ) -> list[tuple[str, str]]:
        """Extract information about content's media type and used DRM schema from the link.

        :param link: Link object
        :return: 2-tuple containing information about the content's media type and its DRM schema
        """
        self.log.debug(
            "Started extracting media types and a DRM scheme from {}".format(
                encode(link)
            )
        )

        media_types_and_drm_scheme = []

        if (
            link.properties
            and link.properties.availability
            and link.properties.availability.state
            != opds2_ast.OPDS2AvailabilityType.AVAILABLE.value
        ):
            self.log.info(f"Link unavailable. Skipping. {encode(link)}")
            return []

        # We need to take into account indirect acquisition links
        if link.properties and link.properties.indirect_acquisition:
            # We make the assumption that when we have nested indirect acquisition links
            # that the most deeply nested link is the content type, and the link at the nesting
            # level above that is the DRM. We discard all other levels of indirection, assuming
            # that they don't matter for us.
            #
            # This may not cover all cases, but it lets us deal with CM style acquisition links
            # where the top level link is a OPDS feed and the common case of a single
            # indirect_acquisition link.
            for acquisition_object in link.properties.indirect_acquisition:
                nested_acquisition = acquisition_object
                nested_types = [link.type]
                while nested_acquisition:
                    nested_types.append(nested_acquisition.type)
                    nested_acquisition = first_or_default(nested_acquisition.child)
                [drm_type, media_type] = nested_types[-2:]

                # We then check this returned pair of content types to make sure they match known
                # book or audiobook and DRM types. If they do not match known types, then we skip
                # this link.
                if (
                    media_type in MediaTypes.BOOK_MEDIA_TYPES
                    or media_type in MediaTypes.AUDIOBOOK_MEDIA_TYPES
                ) and drm_type in DeliveryMechanism.KNOWN_DRM_TYPES:
                    media_types_and_drm_scheme.append((media_type, drm_type))

        # There are no indirect links, then the link type points to the media, and
        # there is no DRM for this link.
        else:
            if (
                link.type in MediaTypes.BOOK_MEDIA_TYPES
                or link.type in MediaTypes.AUDIOBOOK_MEDIA_TYPES
            ):
                media_types_and_drm_scheme.append((link.type, DeliveryMechanism.NO_DRM))

        self.log.debug(
            "Finished extracting media types and a DRM scheme from {}: {}".format(
                encode(link), encode(media_types_and_drm_scheme)
            )
        )

        return media_types_and_drm_scheme

    def _extract_medium_from_links(self, links: core_ast.LinkList) -> str | None:
        """Extract the publication's medium from its links.

        :param links: List of links
        :return: Publication's medium
        """
        derived = None

        for link in links:
            if not link.rels or not link.type or not self._is_acquisition_link(link):
                continue

            link_media_type, _ = first_or_default(
                self._extract_media_types_and_drm_scheme_from_link(link),
                default=(None, None),
            )
            derived = Edition.medium_from_media_type(link_media_type)

            if derived:
                break

        return derived

    @staticmethod
    def _extract_medium(
        publication: opds2_ast.OPDS2Publication,
        default_medium: str | None = Edition.BOOK_MEDIUM,
    ) -> str | None:
        """Extract the publication's medium from its metadata.

        :param publication: Publication object
        :return: Publication's medium
        """
        medium = default_medium

        if publication.metadata.type:
            medium = Edition.additional_type_to_medium.get(
                publication.metadata.type, default_medium
            )

        return medium

    def _extract_identifier(
        self, publication: opds2_ast.OPDS2Publication
    ) -> Identifier:
        """Extract the publication's identifier from its metadata.

        :param publication: Publication object
        :return: Identifier object
        """
        return self.parse_identifier(publication.metadata.identifier)  # type: ignore[no-any-return]

    def _extract_publication_metadata(
        self,
        feed: opds2_ast.OPDS2Feed,
        publication: opds2_ast.OPDS2Publication,
        data_source_name: str | None,
    ) -> Metadata:
        """Extract a Metadata object from webpub-manifest-parser's publication.

        :param publication: Feed object
        :param publication: Publication object
        :param data_source_name: Data source's name
        :return: Publication's metadata
        """
        self.log.debug(
            "Started extracting metadata from publication {}".format(
                encode(publication)
            )
        )

        title = publication.metadata.title

        if title == OPDSFeed.NO_TITLE:
            title = None

        subtitle = publication.metadata.subtitle

        languages = first_or_default(publication.metadata.languages)
        derived_medium = self._extract_medium_from_links(publication.links)
        medium = self._extract_medium(publication, derived_medium)

        publisher = first_or_default(publication.metadata.publishers)
        if publisher:
            publisher = publisher.name

        imprint = first_or_default(publication.metadata.imprints)
        if imprint:
            imprint = imprint.name

        published = publication.metadata.published
        subjects = self._extract_subjects(publication.metadata.subjects)
        contributors = (
            self._extract_contributors(
                publication.metadata.authors, Contributor.AUTHOR_ROLE
            )
            + self._extract_contributors(
                publication.metadata.translators, Contributor.TRANSLATOR_ROLE
            )
            + self._extract_contributors(
                publication.metadata.editors, Contributor.EDITOR_ROLE
            )
            + self._extract_contributors(
                publication.metadata.artists, Contributor.ARTIST_ROLE
            )
            + self._extract_contributors(
                publication.metadata.illustrators, Contributor.ILLUSTRATOR_ROLE
            )
            + self._extract_contributors(
                publication.metadata.letterers, Contributor.LETTERER_ROLE
            )
            + self._extract_contributors(
                publication.metadata.pencilers, Contributor.PENCILER_ROLE
            )
            + self._extract_contributors(
                publication.metadata.colorists, Contributor.COLORIST_ROLE
            )
            + self._extract_contributors(
                publication.metadata.inkers, Contributor.INKER_ROLE
            )
            + self._extract_contributors(
                publication.metadata.narrators, Contributor.NARRATOR_ROLE
            )
            + self._extract_contributors(
                publication.metadata.contributors, Contributor.CONTRIBUTOR_ROLE
            )
        )
        # Audiobook duration
        duration = publication.metadata.duration
        # Not all parsers support time_tracking
        time_tracking = getattr(publication.metadata, "time_tracking", False)
        if medium != Edition.AUDIO_MEDIUM and time_tracking is True:
            time_tracking = False
            self.log.warning(
                f"Ignoring the time tracking flag for entry {publication.metadata.identifier}"
            )

        feed_self_url = first_or_default(
            feed.links.get_by_rel(OPDS2LinkRelationsRegistry.SELF.key)
        ).href
        links = self._extract_links(publication, feed_self_url)

        last_opds_update = publication.metadata.modified

        identifier = self._extract_identifier(publication)
        identifier_data = IdentifierData(
            type=identifier.type, identifier=identifier.identifier
        )

        # FIXME: There are no measurements in OPDS 2.0
        measurements: list[Any] = []

        # FIXME: There is no series information in OPDS 2.0
        series = None
        series_position = None

        # FIXME: It seems that OPDS 2.0 spec doesn't contain information about rights so we use the default one
        rights_uri = RightsStatus.rights_uri_from_string("")

        circulation_data = CirculationData(
            default_rights_uri=rights_uri,
            data_source=data_source_name,
            primary_identifier=identifier_data,
            links=links,
            licenses_owned=LicensePool.UNLIMITED_ACCESS,
            licenses_available=LicensePool.UNLIMITED_ACCESS,
            licenses_reserved=0,
            patrons_in_hold_queue=0,
            formats=[],
            should_track_playtime=time_tracking,
        )

        formats = self._find_formats_in_non_open_access_acquisition_links(
            publication.links, links, rights_uri, circulation_data
        )
        circulation_data.formats.extend(formats)

        metadata = Metadata(
            data_source=data_source_name,
            title=title,
            subtitle=subtitle,
            language=languages,
            medium=medium,
            publisher=publisher,
            published=published,
            imprint=imprint,
            primary_identifier=identifier_data,
            subjects=subjects,
            contributors=contributors,
            measurements=measurements,
            series=series,
            series_position=series_position,
            links=links,
            data_source_last_updated=last_opds_update,
            duration=duration,
            circulation=circulation_data,
        )

        self.log.debug(
            "Finished extracting metadata from publication {}: {}".format(
                encode(publication), encode(metadata)
            )
        )

        return metadata

    def _find_formats_in_non_open_access_acquisition_links(
        self,
        ast_link_list: list[core_ast.Link],
        link_data_list: list[LinkData],
        rights_uri: str,
        circulation_data: CirculationData,
    ) -> list[FormatData]:
        """Find circulation formats in non open-access acquisition links.

        :param ast_link_list: List of Link objects
        :param link_data_list: List of LinkData objects
        :param rights_uri: Rights URI
        :param circulation_data: Circulation data
        :return: List of additional circulation formats found in non-open access links
        """
        formats = []

        for ast_link, parsed_link in zip(ast_link_list, link_data_list):
            if not self._is_acquisition_link(ast_link):
                continue
            if self._is_open_access_link_(parsed_link, circulation_data):
                continue

            for (
                content_type,
                drm_scheme,
            ) in self._extract_media_types_and_drm_scheme_from_link(ast_link):
                formats.append(
                    FormatData(
                        content_type=content_type,
                        drm_scheme=drm_scheme,
                        link=parsed_link,
                        rights_uri=rights_uri,
                    )
                )

        return formats

    @staticmethod
    def _get_publications(
        feed: opds2_ast.OPDS2Feed,
    ) -> Iterable[opds2_ast.OPDS2Publication]:
        """Return all the publications in the feed.
        :param feed: OPDS 2.0 feed
        :return: An iterable list of publications containing in the feed
        """
        if feed.publications:
            yield from feed.publications

        if feed.groups:
            for group in feed.groups:
                if group.publications:
                    yield from group.publications

    @staticmethod
    def _is_acquisition_link(link: core_ast.Link) -> bool:
        """Return a boolean value indicating whether a link can be considered an acquisition link.

        :param link: Link object
        :return: Boolean value indicating whether a link can be considered an acquisition link
        """
        return any(
            [rel for rel in link.rels if rel in LinkRelations.CIRCULATION_ALLOWED]
        )

    @staticmethod
    def _is_open_access_link_(
        link_data: LinkData, circulation_data: CirculationData
    ) -> bool:
        """Return a boolean value indicating whether the specified LinkData object describes an open-access link.

        :param link_data: LinkData object
        :param circulation_data: CirculationData object
        """
        open_access_link = (
            link_data.rel == Hyperlink.OPEN_ACCESS_DOWNLOAD and link_data.href
        )

        if open_access_link:
            return True

        # Try to deduce if the ast_link is open-access, even if it doesn't explicitly say it is
        rights_uri = link_data.rights_uri or circulation_data.default_rights_uri
        open_access_rights_link = (
            link_data.media_type in Representation.BOOK_MEDIA_TYPES
            and link_data.href
            and rights_uri in RightsStatus.OPEN_ACCESS
        )

        return open_access_rights_link

    def _record_coverage_failure(
        self,
        failures: dict[str, list[CoverageFailure]],
        identifier: Identifier,
        error_message: str,
        transient: bool = True,
    ) -> CoverageFailure:
        """Record a new coverage failure.

        :param failures: Dictionary mapping publication identifiers to corresponding CoverageFailure objects
        :param identifier: Publication's identifier
        :param error_message: Message describing the failure
        :param transient: Boolean value indicating whether the failure is final or it can go away in the future
        :return: CoverageFailure object describing the error
        """
        if identifier.identifier is None:
            raise ValueError

        if identifier not in failures:
            failures[identifier.identifier] = []

        failure = CoverageFailure(
            identifier,
            error_message,
            data_source=self.data_source,
            transient=transient,
            collection=self.collection,
        )
        failures[identifier.identifier].append(failure)

        return failure

    def _record_publication_unrecognizable_identifier(
        self, publication: opds2_ast.OPDS2Publication
    ) -> None:
        """Record a publication's unrecognizable identifier, i.e. identifier that has an unknown format
            and could not be parsed by CM.

        :param publication: OPDS 2.x publication object
        """
        original_identifier = publication.metadata.identifier
        title = publication.metadata.title

        if original_identifier is None:
            self.log.warning(f"Publication '{title}' does not have an identifier.")
        else:
            self.log.warning(
                f"Publication # {original_identifier} ('{title}') has an unrecognizable identifier."
            )

    def extract_next_links(self, feed: str | opds2_ast.OPDS2Feed) -> list[str]:
        """Extracts "next" links from the feed.

        :param feed: OPDS 2.0 feed
        :return: List of "next" links
        """
        parser_result = self._parser.parse_manifest(feed)
        parsed_feed = parser_result.root

        if not parsed_feed:
            return []

        next_links = parsed_feed.links.get_by_rel(self.NEXT_LINK_RELATION)
        next_links = [next_link.href for next_link in next_links]

        return next_links  # type: ignore[no-any-return]

    def extract_last_update_dates(
        self, feed: str | opds2_ast.OPDS2Feed
    ) -> list[tuple[str | None, datetime | None]]:
        """Extract last update date of the feed.

        :param feed: OPDS 2.0 feed
        :return: A list of 2-tuples containing publication's identifiers and their last modified dates
        """
        parser_result = self._parser.parse_manifest(feed)
        parsed_feed = parser_result.root

        if not parsed_feed:
            return []

        dates = [
            (publication.metadata.identifier, publication.metadata.modified)
            for publication in self._get_publications(parsed_feed)
            if publication.metadata.modified
        ]

        return dates

    def _parse_feed_links(self, links: list[core_ast.Link]) -> None:
        """Parse the global feed links. Currently only parses the token endpoint link"""
        for link in links:
            if first_or_default(link.rels) == Hyperlink.TOKEN_AUTH:
                # Save the collection-wide token authentication endpoint
                self.collection.integration_configuration.context_update(
                    {ExternalIntegration.TOKEN_AUTH: link.href}
                )

    def extract_feed_data(
        self, feed: str | opds2_ast.OPDS2Feed, feed_url: str | None = None
    ) -> tuple[dict[str, Metadata], dict[str, list[CoverageFailure]]]:
        """Turn an OPDS 2.0 feed into lists of Metadata and CirculationData objects.
        :param feed: OPDS 2.0 feed
        :param feed_url: Feed URL used to resolve relative links
        """
        from webpub_manifest_parser.core.ast import Contributor

        parser_result = self._parser.parse_manifest(feed)
        feed = parser_result.root
        publication_metadata_dictionary = {}
        failures: dict[str, list[CoverageFailure]] = {}

        if feed.links:
            self._parse_feed_links(feed.links)

        for publication in self._get_publications(feed):
            recognized_identifier = self._extract_identifier(publication)

            if not recognized_identifier or not self._is_identifier_allowed(
                recognized_identifier
            ):
                self._record_publication_unrecognizable_identifier(publication)
                continue

            publication_metadata = self._extract_publication_metadata(
                feed, publication, self.data_source_name
            )

            publication_metadata_dictionary[
                publication_metadata.primary_identifier.identifier
            ] = publication_metadata

        node_finder = NodeFinder()

        for error in parser_result.errors:
            publication = node_finder.find_parent_or_self(
                parser_result.root, error.node, opds2_ast.OPDS2Publication
            )

            if publication:
                recognized_identifier = self._extract_identifier(publication)

                if not recognized_identifier or not self._is_identifier_allowed(
                    recognized_identifier
                ):
                    self._record_publication_unrecognizable_identifier(publication)
                    # In the case of missing name properties of a Contributor, we proceed to not record them.
                if (
                    isinstance(error, MissingPropertyError)
                    and isinstance(error.node, Contributor)
                    and not error.node.name
                ):
                    self.log.info(
                        f"Publication # {recognized_identifier} ({publication.metadata.title}) Contributor was missing name property values but this error is skipped."
                    )
                else:
                    self._record_coverage_failure(
                        failures, recognized_identifier, error.error_message
                    )
            else:
                self.log.warning(f"{error.error_message}")

        return publication_metadata_dictionary, failures


class OPDS2ImportMonitor(OPDSImportMonitor):
    PROTOCOL = ExternalIntegration.OPDS2_IMPORT
    MEDIA_TYPE = OPDS2MediaTypesRegistry.OPDS_FEED.key, "application/json"

    def _verify_media_type(self, url: str, response: Response) -> None:
        # Make sure we got an OPDS feed, and not an error page that was
        # sent with a 200 status code.
        media_type = response.headers.get("Content-Type")
        if not media_type or not any(x in media_type for x in self.MEDIA_TYPE):
            message = "Expected {} OPDS 2.0 feed, got {}".format(
                self.MEDIA_TYPE, media_type
            )

            raise BadResponseException(url, message=message, response=response)

    def _get_accept_header(self) -> str:
        return "{}, {};q=0.9, */*;q=0.1".format(
            OPDS2MediaTypesRegistry.OPDS_FEED.key, "application/json"
        )
