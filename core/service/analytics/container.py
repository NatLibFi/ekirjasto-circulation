from dependency_injector import providers
from dependency_injector.containers import DeclarativeContainer

from core.analytics import Analytics


class AnalyticsContainer(DeclarativeContainer):
    config = providers.Configuration()
    storage = providers.DependenciesContainer()

    analytics: providers.Provider[Analytics] = providers.Singleton(
        Analytics,
        s3_analytics_enabled=config.s3_analytics_enabled,
        s3_service=storage.analytics,
        opensearch_analytics_enabled=config.opensearch_analytics_enabled,
        opensearch_analytics_url=config.opensearch_analytics_url,
        opensearch_analytics_index_prefix=config.opensearch_analytics_index_prefix,
    )
