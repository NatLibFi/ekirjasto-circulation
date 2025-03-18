from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


class PalaceValueError(ValueError):
    pass


@dataclass
class Encryption:
    algorithm: str


@dataclass
class Rights:
    copy: int | None = None
    print: int | None = None


@dataclass
class Signature:
    algorithm: str
    value: str


@dataclass
class Link:
    href: str
    rel: str | None = None
    templated: bool = False
    type: str | None = None


@dataclass
class CompactCollection:
    links: list[Link] = field(default_factory=list)

    def get(self, rel: str, type: str | None = None) -> Link | None:
        for link in self.links:
            if link.rel == rel and (type is None or link.type == type):
                return link
        return None


@dataclass
class LicenseDocument:
    id: str
    issued: datetime
    provider: str
    encryption: Encryption
    signature: Signature
    links: CompactCollection
    updated: datetime | None = None
    rights: Rights | None = None

    @staticmethod
    def content_type() -> str:
        return "application/vnd.readium.lcp.license.v1.0+json"

    def __post_init__(self):
        if self.links.get(rel="hint") is None:
            raise PalaceValueError("links must contain a link with rel 'hint'")
        if self.links.get(rel="publication") is None:
            raise PalaceValueError("links must contain a link with rel 'publication'")
