from pydantic_settings import SettingsConfigDict

from core.service.configuration import ServiceConfiguration
from core.util.pydantic import HttpUrl

class SearchConfiguration(ServiceConfiguration):
    url: HttpUrl
    index_prefix: str = "circulation-works"
    timeout: int = 20
    maxsize: int = 25

    model_config = SettingsConfigDict(env_prefix="PALACE_SEARCH_")

