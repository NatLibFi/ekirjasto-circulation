import datetime
import logging
from typing import Dict

from opensearchpy import OpenSearch
from core.model.contributor import Contributor
from opensearch_dsl import Search
from core.local_analytics_provider import LocalAnalyticsProvider
from core.model.library import Library
from core.model.licensing import LicensePool

from flask_babel import lazy_gettext as _

from core.model import ConfigurationSetting, ExternalIntegration, Session
from core.util.http import HTTP

from .config import CannotLoadConfiguration


class OpenSearchAnalyticsProvider(LocalAnalyticsProvider):
    NAME = _("OpenSearch")
    DESCRIPTION = _("How to Configure a OpenSearch Integration")
    INSTRUCTIONS = _("<p>Here be instructions</p>")

    EVENTS_INDEX_PREFIX_KEY = "events_index_prefix"
    DEFAULT_EVENTS_INDEX_PREFIX = "circulation-events"

    SEARCH_VERSION = "search_version"
    SEARCH_VERSION_OS1_X = "Opensearch 1.x"
    DEFAULT_SEARCH_VERSION = SEARCH_VERSION_OS1_X

    # Fields that get indexed as keyword for aggregating and building faceted search
    KEYWORD_FIELDS = (
        "type",
        "library_id",
        "library_name",
        "library_short_name",
        "location",
        "license_pool_id",
        "publisher",
        "genres",
        "imprint",
        "medium",
        "collection",
        "identifier_type",
        "identifier",
        "data_source",
        "distributor",
        "audience",
        "author",
        "series",
        "language",
    )

    # Fields that get indexed as datetime for aggregations and date range searches
    DATETIME_FIELDS = (
        "start",
        "end",
        "issued",
        "availability_time",
    )

    # Fields that get indexed as numbers for aggregations and value range searches
    NUMERIC_FIELDS = (
        "quality",
        "rating",
        "popularity",
        "licenses_owned",
        "licenses_available",
        "licenses_reserved",
        "patrons_in_hold_queue",
    )

    # Fields that get indexed as booleans
    BOOLEAN_FIELDS = (
        "fiction",
        "open_access",
        "self_hosted",
    )

    DEFAULT_URL = "http://localhost:9200"

    SETTINGS = [
        {
            "key": ExternalIntegration.URL,
            "label": _("URL"),
            "default": DEFAULT_URL,
            "required": True,
            "format": "url",
        },
        {
            "key": EVENTS_INDEX_PREFIX_KEY,
            "label": _("Index prefix"),
            "default": DEFAULT_EVENTS_INDEX_PREFIX,
            "required": True,
            "description": _(
                "Event index will be created with this unique prefix. In most cases, the default will work fine. You may need to change this if you have multiple application servers using a single OpenSearch server."
            ),
        },
        {
            "key": SEARCH_VERSION,
            "label": _("The search service version"),
            "default": DEFAULT_SEARCH_VERSION,
            "description": _(
                "Which version of the search engine is being used. Changing this value will require a CM restart."
            ),
            "required": True,
            "type": "select",
            "options": [
                {"key": SEARCH_VERSION_OS1_X, "label": SEARCH_VERSION_OS1_X},
            ],
        },
    ]

    def __init__(
        self,
        integration,
        library=None,
        version=None,
    ):
        self.log = logging.getLogger("OpenSearch analytics")

        if not library:
            raise CannotLoadConfiguration(
                "OpenSearch can't be configured without a library."
            )

        self.library_id = library.id

        url_setting = ConfigurationSetting.for_externalintegration(
            ExternalIntegration.URL, integration
        )
        self.url = url_setting.value or self.DEFAULT_URL

        prefix_setting = ConfigurationSetting.for_externalintegration(
            self.EVENTS_INDEX_PREFIX_KEY, integration
        )
        self.index_prefix = prefix_setting.value or self.DEFAULT_EVENTS_INDEX_PREFIX

        use_ssl = self.url.startswith("https://")
        self.__client = OpenSearch(self.url, use_ssl=use_ssl, timeout=20, maxsize=25)
        self.indices = self.__client.indices
        self.index = self.__client.index
        self.search = Search(using=self.__client, index=self.index_prefix)

        # Version v1 is hardcoded here. Implement external_search-type
        # version system if needed in the future.
        self.index_name = self.index_prefix + "-" + "v1"
        self.setup_index(self.index_name)

    @classmethod
    def opensearch_integration(cls, _db, library=None) -> ExternalIntegration:
        """Look up the ExternalIntegration for Opensearch analytics."""
        return ExternalIntegration.lookup(
            _db,
            protocol=cls.__module__,
            goal=ExternalIntegration.ANALYTICS_GOAL,
            library=library,
        )

    @classmethod
    def analytics_index_name(cls, _db, library=None):
        """Look up the name of the OpenSearch analytics index.

        (Copied and modified from external_search's
        works_prefixed and works_index_name methods)
        """
        integration = cls.opensearch_integration(_db, library)

        if not integration:
            return None
        setting = integration.setting(cls.EVENTS_INDEX_PREFIX_KEY)
        prefix = setting.value_or_default(cls.DEFAULT_EVENTS_INDEX_PREFIX)
        # Version v1 is hardcoded here. Implement external_search-type
        # version system if needed in the future.
        return prefix + "-" + "v1"

    def setup_index(self, new_index=None, **index_settings):
        """Create the event index with appropriate mapping."""

        index_name = new_index
        if self.indices.exists(index_name):
            self.log.info("Index %s exists already.", index_name)
            # self.log.info("Deleting index %s", index_name)
            # self.indices.delete(index_name)

        else:
            self.log.info("Creating index %s", index_name)

            properties = {}
            for field in self.KEYWORD_FIELDS:
                properties[field] = {"type": "keyword"}
            for field in self.DATETIME_FIELDS:
                properties[field] = {"type": "date"}
            for field in self.BOOLEAN_FIELDS:
                properties[field] = {"type": "boolean"}
            for field in self.NUMERIC_FIELDS:
                properties[field] = {"type": "float"}

            body = {
                "mappings": {"properties": properties}
            }  # TODO: add settings if necessary
            index = self.indices.create(index=index_name, body=body)

    # Copied from s3_analytics_provider.py (with minor edits)
    @staticmethod
    def _create_event_object(
        library: Library,
        license_pool: LicensePool,
        event_type: str,
        time: datetime.datetime,
        old_value,
        new_value,
        neighborhood: str,
    ) -> Dict:
        """Create a Python dict containing required information about the event.

        :param library: Library associated with the event

        :param license_pool: License pool associated with the event

        :param event_type: Type of the event

        :param time: Event's timestamp

        :param old_value: Old value of the metric changed by the event

        :param new_value: New value of the metric changed by the event

        :param neighborhood: Geographic location of the event

        :return: Python dict containing required information about the event
        """
        start = time
        if not start:
            start = datetime.datetime.utcnow()
        end = start

        if new_value is None or old_value is None:
            delta = None
        else:
            delta = new_value - old_value

        data_source = license_pool.data_source if license_pool else None
        identifier = license_pool.identifier if license_pool else None
        collection = license_pool.collection if license_pool else None
        work = license_pool.work if license_pool else None
        edition = work.presentation_edition if work else None
        if not edition and license_pool:
            edition = license_pool.presentation_edition

        event = {
            "type": event_type,
            "start": start,
            "end": end,
            "library_id": library.id,
            "library_name": library.name,
            "library_short_name": library.short_name,
            "old_value": old_value,
            "new_value": new_value,
            "delta": delta,
            "location": neighborhood,
            "license_pool_id": license_pool.id if license_pool else None,
            "publisher": edition.publisher if edition else None,
            "imprint": edition.imprint if edition else None,
            "issued": edition.issued if edition else None,
            "published": datetime.datetime.combine(
                edition.published, datetime.datetime.min.time()
            )
            if edition and edition.published
            else None,
            "medium": edition.medium if edition else None,
            "collection": collection.name if collection else None,
            "identifier_type": identifier.type if identifier else None,
            "identifier": identifier.identifier if identifier else None,
            "data_source": data_source.name if data_source else None,
            "distributor": data_source.name if data_source else None,
            "audience": work.audience if work else None,
            "fiction": work.fiction if work else None,
            "quality": work.quality if work else None,
            "rating": work.rating if work else None,
            "popularity": work.popularity if work else None,
            "genres": [genre.name for genre in work.genres] if work else None,
            "availability_time": license_pool.availability_time
            if license_pool
            else None,
            "licenses_owned": license_pool.licenses_owned if license_pool else None,
            "licenses_available": license_pool.licenses_available
            if license_pool
            else None,
            "licenses_reserved": license_pool.licenses_reserved
            if license_pool
            else None,
            "patrons_in_hold_queue": license_pool.patrons_in_hold_queue
            if license_pool
            else None,
            "self_hosted": license_pool.self_hosted if license_pool else None,
            "title": work.title if work else None,
            "author": work.author if work else None,
            "series": work.series if work else None,
            "series_position": work.series_position if work else None,
            "language": work.language if work else None,
            "open_access": license_pool.open_access if license_pool else None,
            "authors": list(
                [
                    contribution.contributor.sort_name
                    for contribution in edition.contributions
                    if contribution.role == Contributor.AUTHOR_ROLE
                ]
                if edition
                else None
            ),
            "contributions": [
                ": ".join(
                    contribution.contributor.role,
                    contribution.contributor.sort_name,
                )
                for contribution in edition.contributions
                if contribution.role != Contributor.AUTHOR_ROLE
            ]
            if edition
            else None,
        }

        return event

    def collect_event(
        self,
        library,
        license_pool,
        event_type,
        time,
        old_value=None,
        new_value=None,
        **kwargs,
    ):
        """Log the event using the appropriate for the specific provider's mechanism.

        :param db: Database session
        :type db: sqlalchemy.orm.session.Session

        :param library: Library associated with the event
        :type library: core.model.library.Library

        :param license_pool: License pool associated with the event
        :type license_pool: core.model.licensing.LicensePool

        :param event_type: Type of the event
        :type event_type: str

        :param time: Event's timestamp
        :type time: datetime.datetime

        :param neighborhood: Geographic location of the event
        :type neighborhood: str

        :param old_value: Old value of the metric changed by the event
        :type old_value: Any

        :param new_value: New value of the metric changed by the event
        :type new_value: Any
        """

        if not library and not license_pool:
            raise ValueError("Either library or license_pool must be provided.")
        if library:
            _db = Session.object_session(library)
        else:
            _db = Session.object_session(license_pool)
        if library and self.library_id and library.id != self.library_id:
            return

        neighborhood = None

        # TODO: Check if we can use locations like in local_analytics
        # if self.location_source == self.LOCATION_SOURCE_NEIGHBORHOOD:
        #     neighborhood = kwargs.pop("neighborhood", None)

        event = self._create_event_object(
            library, license_pool, event_type, time, old_value, new_value, neighborhood
        )

        self.index(
            index=self.index_name,
            body=event,
        )

    def post(self, url, params):
        response = HTTP.post_with_timeout(url, params)


# The Analytics class looks for the name "Provider".
Provider = OpenSearchAnalyticsProvider
