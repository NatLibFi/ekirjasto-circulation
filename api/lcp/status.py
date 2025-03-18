import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from functools import cached_property

log = logging.getLogger("License status doc")

# TODO: Remove this when we drop support for Python 3.10
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:

    class StrEnum(str, Enum):
        pass


@dataclass
class BaseLink:
    href: str
    rel: str


@dataclass
class Link(BaseLink):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#25-links
    """

    title: str | None = None
    profile: str | None = None


class Status(StrEnum):
    """
    https://readium.org/lcp-specs/releases/lsd/latest.html#23-status-of-a-license
    """

    READY = auto()
    ACTIVE = auto()
    REVOKED = auto()
    RETURNED = auto()
    CANCELLED = auto()
    EXPIRED = auto()


@dataclass
class Updated:
    """
    https://readium.org/lcp-specs/releases/lsd/latest#24-timestamps
    """

    license: datetime
    status: datetime


@dataclass
class PotentialRights:
    """
    https://readium.org/lcp-specs/releases/lsd/latest#26-potential-rights
    """

    end: datetime | None = None


class EventType(StrEnum):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#27-events
    """

    REGISTER = auto()
    RENEW = auto()
    RETURN = auto()
    REVOKE = auto()
    CANCEL = auto()


@dataclass
class Event:
    """
    https://readium.org/lcp-specs/releases/lsd/latest#27-events
    """

    type: EventType
    name: str
    timestamp: datetime
    id: str | None = None
    device: str | None = None


@dataclass
class CompactCollection:
    """
    Represents a collection of links.
    """

    items: list[Link] = field(default_factory=list)

    def __iter__(self):
        return iter(self.items)

    def append(self, item: Link):
        self.items.append(item)

    def __len__(self):
        return len(self.items)


@dataclass
class LoanStatus:
    """
    This document is defined as part of the Readium LCP Specifications.

    Readium calls this the License Status Document (LSD), however, that
    name conflates the concept of License. In the context of ODL and library
    lends, it's really the loan status document, so we use that name here.

    The spec for it is located here:
    https://readium.org/lcp-specs/releases/lsd/latest.html

    Technically the spec says that there must be at least one link
    with rel="license" but this is not always the case in practice,
    especially when the license is returned or revoked. So we don't
    enforce that here.
    """

    id: str
    status: Status
    # message: str
    # updated: Updated
    links: CompactCollection
    potential_rights: PotentialRights = field(default_factory=PotentialRights)
    events: list[Event] = field(default_factory=list)

    @staticmethod
    def content_type() -> str:
        return "application/vnd.readium.license.status.v1.0+json"

    @cached_property
    def active(self) -> bool:
        return self.status in [Status.READY, Status.ACTIVE]

    @classmethod
    def from_json(cls, data: bytes):
        try:
            decoded_data = data.decode("utf-8")
            log.info(f"Decoded data: {decoded_data}")
            parsed_data = json.loads(decoded_data)
            log.info(f"Parsed JSON data: {parsed_data}")
            return cls(**parsed_data)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            log.exception(f"Failed to decode or parse JSON: {e}")
            raise ValueError("Invalid JSON format") from e
        except TypeError as e:
            log.exception(f"Failed to create {cls.__name__} from parsed data: {e}")
            raise ValueError(f"Invalid LoanStatus structure: {e}") from e
