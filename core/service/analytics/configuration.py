from core.service.configuration import ServiceConfiguration


class AnalyticsConfiguration(ServiceConfiguration):
    s3_analytics_enabled: bool = False
    opensearch_analytics_enabled: bool = False
    opensearch_analytics_url: str = ""
    opensearch_analytics_index_prefix: str = ""
