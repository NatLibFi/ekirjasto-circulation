from __future__ import annotations

import re
import uuid
from enum import Enum
from typing import TYPE_CHECKING

from html_sanitizer.sanitizer import Sanitizer
from sqlalchemy import Column, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, Unicode, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session, relationship
from sqlalchemy.sql import func

from core.model import Base

if TYPE_CHECKING:
    from core.model.admin import Admin
    from core.model.library import Library


class EditorialContentType(str, Enum):
    NEWS = "news"
    FAQ = "faq"
    BANNER = "banner"
    GUIDE = "guide"


class EditorialContentStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


_SANITIZER = Sanitizer(
    settings={
        "tags": {
            "p",
            "br",
            "strong",
            "em",
            "h1",
            "h2",
            "h3",
            "h4",
            "ul",
            "ol",
            "li",
            "a",
        },
        "attributes": {"a": {"href", "title"}},
        "empty": {"br"},
        "protocols": {"http", "https", "mailto"},
    }
)


def sanitize_editorial_html(value: str) -> str:
    """Remove disallowed markup from editorial HTML."""
    return _SANITIZER.sanitize(value)


def editorial_slug(value: str) -> str:
    """Convert a title into a URL-safe editorial-content slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "content"


class EditorialContent(Base):
    __tablename__ = "editorial_content"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4, index=True
    )
    library_id = Column(Integer, ForeignKey("libraries.id"), nullable=True, index=True)
    type = Column(
        SAEnum(EditorialContentType, native_enum=False), nullable=False, index=True
    )
    language = Column(Unicode, nullable=False, index=True)
    title = Column(Unicode, nullable=False)
    slug = Column(Unicode, nullable=False)
    intro_html = Column(Unicode, nullable=False)
    body_html = Column(Unicode, nullable=False)
    status = Column(
        SAEnum(EditorialContentStatus, native_enum=False), nullable=False, index=True
    )
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    created_by_id = Column(Integer, ForeignKey("admins.id"), nullable=True)
    updated_by_id = Column(Integer, ForeignKey("admins.id"), nullable=True)

    library: Mapped[Library] = relationship(
        "Library", back_populates="editorial_content"
    )
    created_by: Mapped[Admin] = relationship("Admin", foreign_keys=[created_by_id])
    updated_by: Mapped[Admin] = relationship("Admin", foreign_keys=[updated_by_id])

    __table_args__ = (
        UniqueConstraint(
            "library_id",
            "type",
            "group_id",
            "language",
            name="uq_editorial_content_group_language",
        ),
        Index(
            "ix_editorial_content_public_lookup",
            "library_id",
            "type",
            "language",
            "status",
        ),
        Index("ix_editorial_content_slug", "library_id", "type", "language", "slug"),
    )

    @classmethod
    def authentication_document_banners(cls, library: Library) -> list[dict]:
        """Return published banner content grouped by editorial content group."""
        db = Session.object_session(library)
        query = (
            select(cls.group_id, cls.language, cls.body_html)
            .where(cls.library_id == library.id)
            .where(cls.type == EditorialContentType.BANNER)
            .where(cls.status == EditorialContentStatus.PUBLISHED)
            .order_by(cls.group_id, cls.language)
        )

        banners: dict[uuid.UUID, dict[str, str]] = {}
        for group_id, language, body_html in db.execute(query):
            banners.setdefault(group_id, {})[language] = body_html

        return [
            {"id": str(group_id), "content": content}
            for group_id, content in banners.items()
        ]

    def to_dict(self) -> dict:
        """Serialize this editorial-content item for an API response."""
        assert self.type is not None
        assert self.status is not None
        return {
            "id": str(self.id),
            "group_id": str(self.group_id),
            "library": self.library.short_name if self.library else None,
            "type": self.type.value,
            "language": self.language,
            "title": self.title,
            "slug": self.slug,
            "intro_html": self.intro_html or "",
            "body_html": self.body_html,
            "status": self.status.value,
            "published_at": self.published_at.isoformat()
            if self.published_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
