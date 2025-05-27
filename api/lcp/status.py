from datetime import datetime
from enum import Enum

from dateutil import parser
from pydantic import BaseModel, Field, validator


class Status(str, Enum):
    """
    https://readium.org/lcp-specs/releases/lsd/latest.html#23-status-of-a-license
    """

    READY = "ready"
    ACTIVE = "active"
    REVOKED = "revoked"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Updated(BaseModel):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#24-timestamps
    """

    license: datetime | None = None
    status: datetime | None = None

    @validator("license", "status", pre=True)
    def parse_iso_timestamp(cls, value):
        if isinstance(value, str):
            return parser.isoparse(value)
        return value


class PotentialRights(BaseModel):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#26-potential-rights
    """

    end: datetime | None = None

    @validator("end", pre=True)
    def parse_iso_timestamp(cls, value):
        if isinstance(value, str):
            return parser.isoparse(value)
        return value


class EventType(str, Enum):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#27-events
    """

    REGISTER = "register"
    RENEW = "renew"
    RETURN = "return"
    REVOKE = "revoke"
    CANCEL = "cancel"


class Event(BaseModel):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#27-events
    """

    event_type: EventType | None = Field(None, alias="type")
    name: str | None = None
    timestamp: datetime | None = None
    id: str | None = None
    device: str | None = None

    @validator("timestamp", pre=True)
    def parse_iso_timestamp(cls, value):
        if isinstance(value, str):
            return parser.isoparse(value)
        return value


class Link(BaseModel):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#25-links
    """

    href: str
    rel: str | list[str]
    title: str | None = None
    content_type: str | None = Field(None, alias="type")
    templated: str | None = None
    profile: str | None = None

    def to_dict(self):
        return self.dict(by_alias=True)


class LinkCollection(BaseModel):
    """
    Creates a collection of Link classas.
    """

    __root__: list[Link]

    def get(self, rel: str, content_type: str) -> Link | None:
        return next(
            (
                link
                for link in self.__root__
                if link.rel == rel and link.content_type == content_type
            ),
            None,
        )


class LoanStatus(BaseModel):
    """
    This document is defined as part of the Readium LCP Specifications.

    Readium calls this the License Status Document (LSD), however, that
    name conflates the concept of License. In the context of ODL and library
    lends, it's really the loan status document, so we use that name here.

    The spec for it is located here:
    https://readium.org/lcp-specs/releases/lsd/latest.html

    Technically the spec says that there must be at lease one link
    with rel="license" but this is not always the case in practice,
    especially when the license is returned or revoked. So we don't
    enforce that here.
    """

    id: str
    status: Status
    message: str | None = None
    updated: Updated | None = None
    links: LinkCollection
    potential_rights: PotentialRights = Field(default_factory=PotentialRights)
    events: list[Event] | None = Field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.status in [Status.READY, Status.ACTIVE]

    @staticmethod
    def content_type() -> str:
        return "application/vnd.readium.license.status.v1.0+json"

    def to_serializable(self):
        """This is a temporary function that's only used in tests."""
        return {
            "id": self.id,
            "status": self.status.value,
            "message": self.message,
            "updated": {
                "license": self.updated.license.isoformat(),
                "status": self.updated.status.isoformat(),
            },
            "links": [link.to_dict() for link in self.links.__root__],
            "potential_rights": {
                "end": self.potential_rights.end.isoformat(),
            },
        }
