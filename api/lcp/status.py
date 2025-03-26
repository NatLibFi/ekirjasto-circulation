import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from dateutil import parser

log = logging.getLogger("License status doc")


@dataclass
class Link:
    """
    https://readium.org/lcp-specs/releases/lsd/latest#25-links
    """

    href: str | None = None
    rel: str | None = None
    title: str | None = None
    content_type: str | None = None
    templated: str | None = None
    profile: str | None = None

class Status(Enum):
    """
    https://readium.org/lcp-specs/releases/lsd/latest.html#23-status-of-a-license
    """

    READY = "ready"
    ACTIVE = "active"
    REVOKED = "revoked"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


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

    def __post_init__(self):
        if isinstance(self.end, str):
            try:
                self.end = parser.isoparse(self.end)
            except ValueError:
                self.end = None


class EventType(Enum):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#27-events
    """

    REGISTER = "register"
    RENEW = "renew"
    RETURN = "return"
    REVOKE = "revoke"
    CANCEL = "cancel"


@dataclass
class Event:
    """
    https://readium.org/lcp-specs/releases/lsd/latest#27-events
    """

    event_type: EventType
    name: str
    timestamp: datetime
    id: str | None = None
    device: str | None = None

    def __post_init__(self):
        if isinstance(self.timestamp, str):
            try:
                self.timestamp = parser.isoparse(self.timestamp)
            except ValueError:
                self.timestamp = None



@dataclass
class LinkCollection:
    """
    Represents a collection of links.
    """

    items: list[Link] = field(default_factory=list)

    def __iter__(self):
        return iter(self.items)

    def append(self, item: Link):
        self.items.append(item)

    def get(self, *, rel: str, content_type: str) -> Link | None:
        """Get the first link that matches the given rel and type."""
        return next(
            (link for link in self.items if link.rel == rel and link.content_type == content_type),
            None
        )

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
    links: LinkCollection
    potential_rights: PotentialRights = field(default_factory=PotentialRights)
    events: list[Event] = field(default_factory=list)

    def __post_init__(self):
        self.links = LinkCollection(
            items=[Link(**link) if isinstance(link, dict) else link for link in self.links]
        )
        
    @staticmethod
    def content_type() -> str:
        return "application/vnd.readium.license.status.v1.0+json"

    @property
    def active(self) -> bool:
        return self.status in [Status.READY, Status.ACTIVE]

    @classmethod
    def from_json(cls, data: str):
        parsed_data = json.loads(data)

        # Convert `links` dictionaries to `Link` objects within a `LinkCollection`
        links = LinkCollection(
            items=[
                Link(
                    href=link.get('href'),
                    rel=link.get('rel'),
                    title=link.get('title'),
                    content_type=link.get('type'),  # Map `type` to `content_type`
                    templated=link.get('templated'),
                    profile=link.get('profile')
                )
                for link in parsed_data.get('links', [])
            ]
        )

        # Convert `potential_rights` from dict to object
        potential_rights = PotentialRights(**parsed_data.get('potential_rights', {}))

        # Convert `events` from list of dicts to list of `Event` objects
        events = [
            Event(
                event_type=EventType(event['type']),  # Convert string to EventType
                name=event['name'],
                timestamp=event['timestamp'],
                id=event.get('id'),
                device=event.get('device')
            )
            for event in parsed_data.get('events', [])
        ]

        status = Status(parsed_data.get('status'))

        return cls(
            id=parsed_data['id'],
            status=status,
            links=links,
            potential_rights=potential_rights,
            events=events
        )