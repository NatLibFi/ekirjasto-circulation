from collections.abc import Callable
from typing import Any

from pydantic import ValidationError
from requests import Response

from api.odl2 import ODL2ImportMonitor
from api.opds.odl.odl import Feed
from api.opds.opds2 import BasePublicationFeed, PublicationFeed
from core.coverage import CoverageFailure
from core.model.edition import Edition
from core.opds2_import import OPDS2ImportMonitor
from core.util.log import LoggerMixin


class OPDS2SchemaValidationMixin(LoggerMixin):
    @classmethod
    def validate_schema(
        cls, feed_cls: type[BasePublicationFeed[Any]], feed: bytes | str
    ) -> None:
        try:
            feed_cls.model_validate_json(feed)
        except ValidationError as e:
            print(str(e))
            raise


class OPDS2SchemaValidation(OPDS2ImportMonitor, OPDS2SchemaValidationMixin):
    def import_one_feed(
        self, feed: bytes | str
    ) -> tuple[list[Edition], dict[str, list[CoverageFailure]]]:
        self.validate_schema(PublicationFeed, feed)
        return [], {}

    def follow_one_link(
        self, url: str, do_get: Callable[..., Response] | None = None
    ) -> tuple[list[str], bytes | None]:
        """We don't need all pages, the first page should be fine for validation"""
        next_links, feed = super().follow_one_link(url, do_get)
        return [], feed

    def feed_contains_new_data(self, feed: bytes | str) -> bool:
        return True


class ODL2SchemaValidation(ODL2ImportMonitor, OPDS2SchemaValidationMixin):
    def import_one_feed(
        self, feed: bytes | str
    ) -> tuple[list[Edition], dict[str, list[CoverageFailure]]]:
        self.validate_schema(Feed, feed)
        return [], {}

    def follow_one_link(
        self, url: str, do_get: Callable[..., Response] | None = None
    ) -> tuple[list[str], bytes | None]:
        """We don't need all pages, the first page should be fine for validation"""
        next_links, feed = super().follow_one_link(url, do_get)
        return [], feed

    def feed_contains_new_data(self, feed: bytes | str) -> bool:
        return True
