import datetime

from opensearch_dsl import Search
from opensearchpy import OpenSearch

from core.local_analytics_provider import LocalAnalyticsProvider
from core.model.contributor import Contributor
from core.model.library import Library
from core.model.licensing import LicensePool
from core.util.http import HTTP


class OpenSearchAnalyticsProvider(LocalAnalyticsProvider):
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
        "duration",
    )

    # Fields that get indexed as booleans
    BOOLEAN_FIELDS = (
        "fiction",
        "open_access",
    )

    def __init__(
        self,
        opensearch_analytics_url=None,
        opensearch_analytics_index_prefix=None,
    ):
        self.url = opensearch_analytics_url
        self.index_prefix = opensearch_analytics_index_prefix

        use_ssl = self.url.startswith("https://")
        self.__client = OpenSearch(self.url, use_ssl=use_ssl, timeout=20, maxsize=25)
        self.indices = self.__client.indices
        self.index = self.__client.index
        self.search = Search(using=self.__client, index=self.index_prefix)

        # Version v1 is hardcoded here. Implement external_search-type
        # version system if needed in the future.
        self.index_name = self.index_prefix + "-" + "v1"
        self.setup_index(self.index_name)

    def setup_index(self, new_index=None, **index_settings):
        """Create the event index with appropriate mapping."""

        index_name = new_index
        if self.indices.exists(index_name):
            # A light-weight ad-hoc migration for putting the new duration field to mappings.
            # This can soon be removed.
            index = self.indices.get(index_name)
            duration_mapping = (
                index.get(index_name)
                .get("mappings", {})
                .get("properties", {})
                .get("duration")
            )
            if not duration_mapping:
                body = {"properties": {"duration": {"type": "float"}}}
                self.indices.put_mapping(index=index_name, body=body)

        else:
            properties = {}
            for field in self.KEYWORD_FIELDS:
                properties[field] = {"type": "keyword"}
            for field in self.DATETIME_FIELDS:
                properties[field] = {"type": "date"}
            for field in self.BOOLEAN_FIELDS:
                properties[field] = {"type": "boolean"}
            for field in self.NUMERIC_FIELDS:
                properties[field] = {"type": "float"}

            body = {"mappings": {"properties": properties}}
            self.indices.create(index=index_name, body=body)

    # Copied from s3_analytics_provider.py (with minor edits)
    @staticmethod
    def _create_event_object(
        library: Library,
        license_pool: LicensePool,
        event_type: str,
        time: datetime.datetime,
        old_value,
        new_value,
        neighborhood: str | None = None,
        duration: int | None = None,
    ) -> dict:
        """Create a Python dict containing required information about the event.

        :param library: Library associated with the event

        :param license_pool: License pool associated with the event

        :param event_type: Type of the event

        :param time: Event's timestamp

        :param old_value: Old value of the metric changed by the event

        :param new_value: New value of the metric changed by the event

        :param neighborhood: Geographic location of the event

        :duration: Duration of the event in seconds

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
            "duration": duration,
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
            "title": work.title if work else None,
            "author": work.author if work else None,
            "series": work.series if work else None,
            "series_position": work.series_position if work else None,
            "language": work.language if work else None,
            "open_access": license_pool.open_access if license_pool else None,
            "authors": [
                contribution.contributor.sort_name
                for contribution in edition.contributions
                if getattr(contribution.contributor, "role", None)
                == Contributor.AUTHOR_ROLE
            ]
            if edition
            else None,
            "contributions": [
                ": ".join(
                    [
                        getattr(contribution.contributor, "role", ""),
                        contribution.contributor.sort_name,
                    ]
                )
                for contribution in edition.contributions
                if (
                    not getattr(contribution.contributor, "role", None)
                    or contribution.contributor.role != Contributor.AUTHOR_ROLE
                )
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
        duration: int | None = None,
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

        :param duration: Duration of the event in seconds
        :type duration: int
        """

        if not library and not license_pool:
            raise ValueError("Either library or license_pool must be provided.")

        event = self._create_event_object(
            library,
            license_pool,
            event_type,
            time,
            old_value,
            new_value,
            None,
            duration,
        )

        self.index(
            index=self.index_name,
            body=event,
        )

    def post(self, url, params):
        HTTP.post_with_timeout(url, params)
