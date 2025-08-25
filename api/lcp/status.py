from enum import Enum
from functools import cached_property

from pydantic import AwareDatetime, Field

from api.opds.base import BaseOpdsModel
from api.opds.types.link import BaseLink, CompactCollection


class Link(BaseLink):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#25-links
    """

    title: str | None = None
    profile: str | None = None


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


class Updated(BaseOpdsModel):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#24-timestamps
    """

    license: AwareDatetime
    status: AwareDatetime


class PotentialRights(BaseOpdsModel):
    """ "
    https://readium.org/lcp-specs/releases/lsd/latest#26-potential-rights
    """

    end: AwareDatetime | None = None


class EventType(str, Enum):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#27-events
    """

    REGISTER = "register"
    RENEW = "renew"
    RETURN = "return"
    REVOKE = "revoke"
    CANCEL = "cancel"


class Event(BaseOpdsModel):
    """
    https://readium.org/lcp-specs/releases/lsd/latest#27-events
    """

    type: EventType
    name: str
    timestamp: AwareDatetime

    # The spec isn't clear if these fields are required, but DeMarque does not
    # provide id in their event data.
    id: str | None = None
    device: str | None = None


class LoanStatus(BaseOpdsModel):
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

    @staticmethod
    def content_type() -> str:
        return "application/vnd.readium.license.status.v1.0+json"

    id: str
    status: Status
    message: str | None = None  # Ellibs does not provide this in their data
    updated: Updated | None = None  # Ellibs does not provide this in their data
    links: CompactCollection[
        Link
    ] | None = None  # Ellibs does not provide this in their data when circulation gets an ODL notification request
    potential_rights: PotentialRights = Field(default_factory=PotentialRights)
    events: list[Event] = Field(default_factory=list)

    @cached_property
    def active(self) -> bool:
        return self.status in [Status.READY, Status.ACTIVE]
