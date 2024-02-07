import unittest
from unittest.mock import patch

import flask
from flask import Flask

from api.opensearch_analytics_search import OpenSearchAnalyticsSearch


class MockLibrary:
    def short_name():
        return "testlib"


class TestOpenSearchAnalyticsSearch(unittest.TestCase):
    # Patch environmental variables
    @patch.dict(
        "os.environ",
        {
            "PALACE_OPENSEARCH_ANALYTICS_URL": "http://localhost:9300",
            "PALACE_OPENSEARCH_ANALYTICS_INDEX_PREFIX": "circulation-events",
        },
    )
    # Patch OpenSearch client in OpeSearchAnalyticsSearch
    @patch("api.opensearch_analytics_search.OpenSearch")
    def test_events(self, mock_opensearch):
        mock_response = {
            "aggregations": {
                "field_1": {"buckets": [{"key": "value1"}]},
                "field_2": {"buckets": [{"key": "value2"}]},
            }
        }
        # Mock the response for the search method of the mocked OpenSearch client
        mock_opensearch.return_value.search.return_value = mock_response

        # Create a Flask app context for the test
        app = Flask(__name__)
        with app.test_request_context():
            # Set up the Flask request context variables
            setattr(flask.request, "library", MockLibrary())

            search_instance = OpenSearchAnalyticsSearch()

            test_params = {
                "from": "2023-01-01",
                "to": "2023-01-31",
            }
            result = search_instance.events(params=test_params)

            expected_result = {
                "data": {"field_1": [{"key": "value1"}], "field_2": [{"key": "value2"}]}
            }
            self.assertEqual(result, expected_result)

    # Patch environmental variables
    @patch.dict(
        "os.environ",
        {
            "PALACE_OPENSEARCH_ANALYTICS_URL": "http://localhost:9300",
            "PALACE_OPENSEARCH_ANALYTICS_INDEX_PREFIX": "circulation-events",
        },
    )
    # Patch OpenSearch client in OpeSearchAnalyticsSearch
    @patch("api.opensearch_analytics_search.OpenSearch")
    def test_histogram(self, mock_opensearch):
        # Prepare mock data for OpenSearch response
        mock_response = {
            "aggregations": {
                "events_per_interval": {
                    "buckets": [
                        {
                            "key": 1696107600000,
                            "key_as_string": "2023-10-01T00:00:00.000+03:00",
                            "type": {
                                "buckets": [
                                    {
                                        "doc_count": 24,
                                        "key": "circulation_manager_check_out",
                                    },
                                    {
                                        "doc_count": 17,
                                        "key": "circulation_manager_check_in",
                                    },
                                ]
                            },
                        }
                    ]
                }
            }
        }
        # Create a MagicMock for the search method of the mocked OpenSearch client
        mock_opensearch.return_value.search.return_value = mock_response
        # Create a Flask app context for the test
        app = Flask(__name__)
        with app.test_request_context():
            # Set up the Flask request context variables
            setattr(flask.request, "library", MockLibrary())

            # Initialize the OpenSearchAnalyticsSearch class
            search_instance = OpenSearchAnalyticsSearch()

            # Call the events method with the test parameters
            test_params = {
                "from": "2023-01-01",
                "to": "2023-01-31",
                "interval": "month",
            }
            result = search_instance.events_histogram(params=test_params)

            expected_result = {
                "data": {
                    "events_per_interval": {
                        "buckets": [
                            {
                                "key": 1696107600000,
                                "key_as_string": "2023-10-01T00:00:00.000+03:00",
                                "type": {
                                    "buckets": [
                                        {
                                            "doc_count": 24,
                                            "key": "circulation_manager_check_out",
                                        },
                                        {
                                            "doc_count": 17,
                                            "key": "circulation_manager_check_in",
                                        },
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
            self.assertEqual(result, expected_result)

    # Patch environmental variables
    @patch.dict(
        "os.environ",
        {
            "PALACE_OPENSEARCH_ANALYTICS_URL": "http://localhost:9300",
            "PALACE_OPENSEARCH_ANALYTICS_INDEX_PREFIX": "circulation-events",
        },
    )
    # Patch OpenSearch client in OpeSearchAnalyticsSearch
    @patch("api.opensearch_analytics_search.OpenSearch")
    def test_facets(self, mock_opensearch):
        # Prepare mock data for OpenSearch response
        mock_response = {
            "aggregations": {
                "audience": {
                    "buckets": [
                        {"doc_count": 72, "key": "Adult"},
                        {"doc_count": 4, "key": "Young Adult"},
                    ]
                },
                "collection": {
                    "buckets": [
                        {
                            "doc_count": 76,
                            "key": "Library Simplified Content Server Crawlable",
                        }
                    ]
                },
            }
        }
        # Create a MagicMock for the search method of the mocked OpenSearch client
        mock_opensearch.return_value.search.return_value = mock_response
        # Create a Flask app context for the test
        app = Flask(__name__)
        with app.test_request_context():
            # Set up the Flask request context variables
            setattr(flask.request, "library", MockLibrary())

            # Initialize the OpenSearchAnalyticsSearch class
            search_instance = OpenSearchAnalyticsSearch()

            # Call the events method with the test parameters
            result = search_instance.get_facets()

            expected_result = {
                "facets": {
                    "audience": {
                        "buckets": [
                            {"doc_count": 72, "key": "Adult"},
                            {"doc_count": 4, "key": "Young Adult"},
                        ]
                    },
                    "collection": {
                        "buckets": [
                            {
                                "doc_count": 76,
                                "key": "Library Simplified Content Server Crawlable",
                            }
                        ]
                    },
                }
            }
            self.assertEqual(result, expected_result)


if __name__ == "__main__":
    unittest.main()
