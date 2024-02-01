from api.opensearch_analytics_provider import OpenSearchAnalyticsProvider
from core.analytics import Analytics
from core.local_analytics_provider import LocalAnalyticsProvider

# The test set is based on core/test_analytics.py

MOCK_PROTOCOL = "../core/mock_analytics_provider"


class TestOpenSearchAnalytics:
    def test_init_opensource_analytics(self):
        analytics = Analytics(
            opensearch_analytics_enabled=True,
            opensearch_analytics_index_prefix="circulation-events",
            opensearch_analytics_url="http://localhost:9200",
        )

        assert len(analytics.providers) == 2
        assert type(analytics.providers[0]) == LocalAnalyticsProvider
        assert type(analytics.providers[1]) == OpenSearchAnalyticsProvider
