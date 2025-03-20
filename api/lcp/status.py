import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from functools import cached_property

log = logging.getLogger("License status doc")


class Link:
    """
    https://readium.org/lcp-specs/releases/lsd/latest#25-links
    """

    def __init__(
        self,
        href: str = None,
        rel: str = None,
        title: str = None,
        mime_type: str = None,  # Handling 'type' explicitly
        templated: str = None,
        profile: str = None
    ):
        self.href = href
        self.rel = rel
        self.title = title
        self.mime_type = mime_type
        self.templated = templated
        self.profile = profile

    @classmethod
    def from_dict(cls, data: dict):
        # Handle 'type' mapping to 'content_type'
        if 'type' in data:
            data['mime_type'] = data.pop('type')
        return cls(**data)

    def __repr__(self):
        return f"<Link(rel={self.rel}, href={self.href}, content_type={self.mime_type})>"
class Status:
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


class EventType:
    """
    https://readium.org/lcp-specs/releases/lsd/latest#27-events
    """

    REGISTER = "register"
    RENEW = "renew"
    RETURN = "renew"
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

    def get(self, *, rel: str, mime_type: str) -> Link | None:
        """Get the first link that matches the given rel and type."""
        return next(
            (link for link in self.items if link.rel == rel and link.mime_type == mime_type),
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
        # Ensure that links is a LinkCollection, even if a list is provided
        # self.links = LinkCollection(
        #     items=[Link(**{**link, 'mime_type': link.get('type', None)}) if isinstance(link, dict) else link for link in self.links]
        # )
        self.links = LinkCollection(
            items=[
                Link.from_dict(link) if isinstance(link, dict) else link
                for link in self.links
            ]
        )

    @staticmethod
    def content_type() -> str:
        return "application/vnd.readium.license.status.v1.0+json"

    @property
    def active(self) -> bool:
        return self.status in [Status.READY, Status.ACTIVE]

    @classmethod
    def from_json(cls, data: bytes):
        data_dict = json.loads(data.decode("utf-8"))
        
        # If 'type' exists, replace it with 'content_type'
        if isinstance(data_dict.get('links'), list):
            for link in data_dict['links']:
                if 'type' in link:
                    link['mime_type'] = link.pop('type')
                    
        return cls(**data_dict)