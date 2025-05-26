from datetime import datetime
from enum import Enum

from dateutil import parser
from pydantic import BaseModel, Field, validator


class Status(str, Enum):
    READY = "ready"
    ACTIVE = "active"
    REVOKED = "revoked"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Updated(BaseModel):
    license: datetime | None = None
    status: datetime | None = None

    @validator("license", "status", pre=True)
    def parse_iso_timestamp(cls, value):
        if isinstance(value, str):
            return parser.isoparse(value)
        return value


class PotentialRights(BaseModel):
    end: datetime | None = None

    @validator("end", pre=True)
    def parse_iso_timestamp(cls, value):
        if isinstance(value, str):
            return parser.isoparse(value)
        return value


class EventType(str, Enum):
    REGISTER = "register"
    RENEW = "renew"
    RETURN = "return"
    REVOKE = "revoke"
    CANCEL = "cancel"


class Event(BaseModel):
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
    href: str
    rel: str | list[str]
    title: str | None = None
    content_type: str | None = Field(None, alias="type")
    templated: str | None = None
    profile: str | None = None

    def to_dict(self):
        return self.dict(by_alias=True)


class LinkCollection(BaseModel):
    __root__: list[Link]

    def get(self, rel: str, content_type: str) -> Link | None:
        """Get a link by its 'rel' attribute."""
        return next(
            (
                link
                for link in self.__root__
                if link.rel == rel and link.content_type == content_type
            ),
            None,
        )


class LoanStatus(BaseModel):
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
