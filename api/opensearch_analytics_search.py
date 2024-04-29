import logging
import os

import flask
from opensearch_dsl import Search
from opensearchpy import OpenSearch

from api.opensearch_analytics_provider import OpenSearchAnalyticsProvider


class OpenSearchAnalyticsSearch:
    TIME_INTERVALS = ("hour", "day", "month")
    DEFAULT_TIME_INTERVAL = "day"
    FACET_FIELDS = (
        "type",
        "library_name",
        "location",
        "publisher",
        "genres",
        "imprint",
        "medium",
        "collection",
        "data_source",
        "distributor",
        "audience",
        "language",
    )

    DURATION_CLAUSES = {
        "under_2h": {"lt": 2 * 60 * 60},
        "over_2h": {"gte": 2 * 60 * 60},
    }

    def __init__(self):
        self.log = logging.getLogger("OpenSearch analytics")
        self.url = os.environ.get("PALACE_OPENSEARCH_ANALYTICS_URL", "")
        index_prefix = os.environ.get("PALACE_OPENSEARCH_ANALYTICS_INDEX_PREFIX", "")
        # Version v1 is hardcoded here. Implement external_search-type
        # version system if needed in the future.
        self.index_name = index_prefix + "-" + "v1"

        use_ssl = self.url.startswith("https://")
        self.__client = OpenSearch(self.url, use_ssl=use_ssl, timeout=20, maxsize=25)
        self.search = Search(using=self.__client, index=self.index_name)

    def events(self, params=None, pdebug=False):
        """Run a search query on events.

        :return: An aggregated list of facet buckets
        """

        # Filter by library
        library = getattr(flask.request, "library", None)
        library_short_name = library.short_name if library else None
        must = (
            [{"match": {"library_short_name": library_short_name}}]
            if library_short_name
            else []
        )
        should = []

        # Add filter per provided keyword parameters
        must += [
            {"match": {key: value}}
            for key, value in params.items()
            if key in OpenSearchAnalyticsProvider.KEYWORD_FIELDS
        ]

        # Use time range query if "from" and/or "to" parameters given
        from_time = params.get("from")
        to_time = params.get("to")
        if from_time or to_time:
            range = {}
            if from_time:
                range["gte"] = from_time
            if to_time:
                range["lte"] = to_time
            must.append({"range": {"start": range}})

        # Add keyword aggregation buckets
        aggs = {}
        for field in OpenSearchAnalyticsProvider.KEYWORD_FIELDS:
            aggs[field] = {"terms": {"field": field, "size": 100}}

        # Add duration filter if given
        duration_param = params.get("duration")
        duration_clause = self.DURATION_CLAUSES.get(duration_param, None)

        if duration_clause is not None:
            should = [
                {"range": {"duration": duration_clause}},
                {"bool": {"must_not": {"exists": {"field": "duration"}}}},
            ]

        # Prepare and run the query
        query = {
            "size": 0,
            "query": {
                "bool": {
                    "must": must,
                    "should": should,
                    "minimum_should_match": 1 if should else 0,
                },
            },
            "aggs": aggs,
        }
        result = self.__client.search(index=self.index_name, body=query)

        # Simplify the result object for client
        data = {}
        for key, value in result["aggregations"].items():
            data[key] = value["buckets"]

        return {"data": data}

    def events_histogram(self, params=None, pdebug=False):
        """Run a search query on events.

        :return: A nested aggregated list of event type buckets
        inside date histogram buckets
        """

        # Filter by library
        library = getattr(flask.request, "library", None)
        library_short_name = library.short_name if library else None
        should = []
        must = (
            [{"match": {"library_short_name": library_short_name}}]
            if library_short_name
            else []
        )

        # Add filter per provided keyword parameters
        must += [
            {"match": {key: value}}
            for key, value in params.items()
            if key in OpenSearchAnalyticsProvider.KEYWORD_FIELDS
        ]

        # Use time range query if "from" and/or "to" parameters given
        from_param = params.get("from")
        to_param = params.get("to")
        if from_param or to_param:
            range = {}
            if from_param:
                range["gte"] = from_param
            if to_param:
                range["lte"] = to_param
            must.append({"range": {"start": range}})

        # Add duration filter if given
        duration_param = params.get("duration")
        duration_clause = self.DURATION_CLAUSES.get(duration_param, None)

        if duration_clause is not None:
            should = [
                {"range": {"duration": duration_clause}},
                {"bool": {"must_not": {"exists": {"field": "duration"}}}},
            ]

        # Add time interval aggregation buckets
        interval_param = params.get("interval")
        interval = (
            interval_param
            if interval_param in self.TIME_INTERVALS
            else self.DEFAULT_TIME_INTERVAL
        )
        aggs = {}
        aggs["events_per_interval"] = {
            "date_histogram": {
                "field": "start",
                "interval": interval,
                "min_doc_count": 0,
                "missing": 0,
                "time_zone": "Europe/Helsinki",
                "extended_bounds": {
                    "min": from_param if from_param else None,
                    "max": f"{to_param}T23:59" if to_param else None,
                },
            },
            "aggs": {"type": {"terms": {"field": "type", "size": 100}}},
        }

        # Prepare and run the query
        query = {
            "size": 0,
            "query": {
                "bool": {
                    "must": must,
                    "should": should,
                    "minimum_should_match": 1 if should else 0,
                }
            },
            "aggs": aggs,
        }
        result = self.__client.search(index=self.index_name, body=query)

        # Simplify the result object for client
        data = {
            "events_per_interval": {
                "buckets": [
                    {
                        "key": item["key"],
                        "key_as_string": item["key_as_string"],
                        "type": {
                            "buckets": item["type"]["buckets"],
                        },
                    }
                    for item in result["aggregations"]["events_per_interval"]["buckets"]
                ]
            }
        }

        return {"data": data}

    def get_facets(self, pdebug=False):
        """Run a search query to get all the available facets.

        :return: An aggregated list of facet buckets
        """

        # Filter by library
        library = getattr(flask.request, "library", None)
        library_short_name = library.short_name if library else None
        filters = (
            [{"match": {"library_short_name": library_short_name}}]
            if library_short_name
            else []
        )

        # Add all term fields to aggregations
        aggs = {}
        for field in self.FACET_FIELDS:
            aggs[field] = {"terms": {"field": field, "size": 1000}}

        # Prepare and run the query (with 0 size)
        query = {"size": 0, "query": {"bool": {"must": filters}}, "aggs": aggs}
        result = self.__client.search(index=self.index_name, body=query)

        # Simplify the result object for client
        data = {}
        for key, value in result["aggregations"].items():
            data[key] = {"buckets": value.get("buckets", [])}

        return {"facets": data}
