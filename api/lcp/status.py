from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, HttpUrl, validator
from typing import List, Optional, Union
from dateutil import parser

class Status(str, Enum):
    READY = "ready"
    ACTIVE = "active"
    REVOKED = "revoked"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Updated(BaseModel):
    license: Optional[datetime] = None
    status: Optional[datetime] = None

    @validator('license', 'status', pre=True)
    def parse_iso_timestamp(cls, value):
        if isinstance(value, str):
            return parser.isoparse(value)
        return value

class PotentialRights(BaseModel):
    end: Optional[datetime] = None

    @validator('end', pre=True)
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
    event_type: EventType = Field(None, alias='type')
    name: Optional[str] = None
    timestamp: Optional[datetime] = None
    id: Optional[str] = None
    device: Optional[str] = None

    @validator('timestamp', pre=True)
    def parse_iso_timestamp(cls, value):
        if isinstance(value, str):
            return parser.isoparse(value)
        return value

class Link(BaseModel):
    href: str
    rel: Union[str, List[str]]
    title: Optional[str] = None
    content_type: str = Field(None, alias='type')
    templated: Optional[str] = None
    profile: Optional[str] = None

    def to_dict(self):
        return self.dict(by_alias=True)

class LinkCollection(BaseModel):
    __root__: List[Link]

    def get(self, rel: str, content_type: str) -> Optional[Link]:
        """Get a link by its 'rel' attribute."""
        return next(
            (link for link in self.__root__ if link.rel == rel and link.content_type == content_type),
            None
        )


class LoanStatus(BaseModel):
    id: str
    status: Status
    message: Optional[str] = None
    updated: Optional[Updated] = None
    links: LinkCollection
    potential_rights: PotentialRights = Field(default_factory=PotentialRights)
    events: Optional[List[Event]] = Field(default_factory=list) 

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
            }
        }