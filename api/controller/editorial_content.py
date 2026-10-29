from __future__ import annotations

import datetime
import uuid

import flask
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.admin.controller.base import AdminPermissionsControllerMixin
from api.admin.problem_details import MISSING_EDITORIAL_CONTENT
from core.config import Configuration
from core.model import (
    Admin,
    EditorialContent,
    EditorialContentStatus,
    EditorialContentType,
    Library,
)
from core.problem_details import INVALID_INPUT
from core.util.problem_detail import ProblemDetail


class EditorialContentController(AdminPermissionsControllerMixin):
    LANGUAGES = ("fi", "en", "sv")

    def __init__(self, db: Session):
        """Initialize the editorial-content controller."""
        self._db = db

    @staticmethod
    def _enum(value, enum_type):
        """Convert a value to an enum member, returning None if invalid."""
        try:
            return enum_type(value)
        except (TypeError, ValueError):
            return None

    def _library(self) -> Library:
        """Return the library attached to the current request."""
        return flask.request.library  # type: ignore[attr-defined]

    def _admin(self) -> Admin:
        """Return the administrator attached to the current request."""
        return flask.request.admin  # type: ignore[attr-defined]

    def _payload(self) -> dict:
        """Read the request payload from JSON or form data."""
        payload = flask.request.get_json(silent=True)
        if payload is not None:
            return payload
        return flask.request.form.to_dict()

    def _validate_payload(self, payload: dict, existing=None) -> dict | ProblemDetail:
        """Validate and normalize an editorial-content payload."""
        content_type = self._enum(
            payload.get("type", getattr(existing, "type", None)), EditorialContentType
        )
        status = self._enum(
            payload.get(
                "status", getattr(existing, "status", EditorialContentStatus.DRAFT)
            ),
            EditorialContentStatus,
        )
        language_value = payload.get("language", getattr(existing, "language", ""))
        title_value = payload.get("title", getattr(existing, "title", ""))
        language = language_value.strip() if isinstance(language_value, str) else ""
        title = title_value.strip() if isinstance(title_value, str) else ""
        intro_html = payload.get("intro_html", getattr(existing, "intro_html", ""))
        body_html = payload.get("body_html", getattr(existing, "body_html", ""))
        if (
            not content_type
            or not status
            or not language
            or not title
            or not isinstance(intro_html, str)
            or not isinstance(body_html, str)
        ):
            return INVALID_INPUT
        return {
            "type": content_type,
            "status": status,
            "language": language,
            "title": title,
            "intro_html": intro_html,
            "body_html": body_html,
            "group_id": payload.get("group_id"),
        }

    def _query_for_library(self, library: Library, public=False):
        """Build a query for content belonging to a library."""
        query = select(EditorialContent).where(
            EditorialContent.library_id == library.id
        )
        if public:
            query = query.where(
                EditorialContent.status == EditorialContentStatus.PUBLISHED
            )
        return query

    def public_list(self, content_type: str | None, language: str | None):
        """Return published editorial content for the public API."""
        library = self._library()
        type_value = (
            self._enum(content_type, EditorialContentType) if content_type else None
        )
        query = self._query_for_library(library, public=True)
        if language is not None:
            query = query.where(EditorialContent.language == language)
        if type_value:
            query = query.where(EditorialContent.type == type_value)
        items = (
            self._db.execute(
                query.order_by(
                    EditorialContent.published_at.desc(),
                    EditorialContent.updated_at.desc(),
                )
            )
            .scalars()
            .all()
        )
        return {"content": [item.to_dict() for item in items]}

    def public_item(self, identifier: str, language: str | None = None):
        """Return one published editorial-content group."""
        library = self._library()
        selected_language = language or Configuration.localization_languages()[0]
        query = self._query_for_library(library, public=True).where(
            EditorialContent.language == selected_language
        )
        try:
            identifier_uuid = uuid.UUID(identifier)
            query = query.where(
                (EditorialContent.group_id == identifier_uuid)
                | (EditorialContent.id == identifier_uuid)
            )
        except ValueError:
            slug_item = (
                self._db.execute(
                    self._query_for_library(library, public=True).where(
                        EditorialContent.slug == identifier
                    )
                )
                .scalars()
                .first()
            )
            if slug_item is None:
                return MISSING_EDITORIAL_CONTENT
            query = query.where(EditorialContent.group_id == slug_item.group_id)
        item = (
            self._db.execute(query.order_by(EditorialContent.published_at.desc()))
            .scalars()
            .first()
        )
        return item.to_dict() if item else MISSING_EDITORIAL_CONTENT

    def admin_list(self):
        """Return editorial content grouped for the admin interface."""
        library = self._library()
        self.require_librarian(library)
        items = (
            self._db.execute(
                self._query_for_library(library).order_by(
                    EditorialContent.created_at.desc(),
                    EditorialContent.updated_at.desc(),
                )
            )
            .scalars()
            .all()
        )
        groups = {}
        for item in items:
            group = groups.setdefault(item.group_id, [])
            group.append(item)
        return {"content": [self._group_summary(items) for items in groups.values()]}

    def admin_group(self, identifier: str):
        """Return all language variants in an editorial-content group."""
        library = self._library()
        self.require_librarian(library)
        try:
            group_id = uuid.UUID(identifier)
        except ValueError:
            return MISSING_EDITORIAL_CONTENT
        items = (
            self._db.execute(
                self._query_for_library(library).where(
                    EditorialContent.group_id == group_id
                )
            )
            .scalars()
            .all()
        )
        if not items:
            return MISSING_EDITORIAL_CONTENT
        return {
            "group_id": str(group_id),
            "type": items[0].type.value,
            "languages": [
                self._language_detail(group_id, items, language)
                for language in self.LANGUAGES
            ],
        }

    def admin_create(self):
        """Create an editorial-content item for the current library."""
        library = self._library()
        admin = self._admin()
        if not admin.has_content_writer_role(library):
            from api.admin.exceptions import AdminNotAuthorized

            raise AdminNotAuthorized()
        payload = self._validate_payload(self._payload())
        if isinstance(payload, ProblemDetail):
            return payload
        item = EditorialContent(
            library=library,
            group_id=self._group_id(payload),
            type=payload["type"],
            language=payload["language"],
            title=payload["title"],
            slug=self._slug(
                library, payload["type"], payload["language"], payload["title"]
            ),
            intro_html=self._sanitize(payload["intro_html"]),
            body_html=self._sanitize(payload["body_html"]),
            status=EditorialContentStatus.DRAFT,
            created_by=admin,
            updated_by=admin,
        )
        if payload["status"] == EditorialContentStatus.PUBLISHED:
            item.status = EditorialContentStatus.PUBLISHED
            item.published_at = datetime.datetime.now(datetime.UTC)
        self._db.add(item)
        self._db.flush()
        return item.to_dict()

    def _group_id(self, payload):
        """Resolve a valid existing group ID or create a new one."""
        value = payload.get("group_id")
        if not value:
            return uuid.uuid4()
        try:
            group_id = uuid.UUID(value)
        except (ValueError, TypeError, AttributeError):
            return uuid.uuid4()
        existing = self._db.execute(
            select(EditorialContent.id).where(
                EditorialContent.library_id == self._library().id,
                EditorialContent.group_id == group_id,
            )
        ).first()
        return group_id if existing else uuid.uuid4()

    @staticmethod
    def _group_summary(items):
        """Summarize a group of editorial-content language variants."""
        ordered = sorted(
            items,
            key=lambda item: ("fi", "en", "sv").index(item.language)
            if item.language in ("fi", "en", "sv")
            else 99,
        )
        return {
            "group_id": str(ordered[0].group_id),
            "type": ordered[0].type.value,
            "title": ordered[0].title,
            "languages": [item.language for item in ordered],
            "statuses": {item.language: item.status.value for item in ordered},
            "created_at": min(item.created_at for item in ordered).isoformat(),
        }

    def _language_detail(self, group_id, items, language):
        """Return the admin details for one language variant."""
        item = next((item for item in items if item.language == language), None)
        if item is None:
            return {
                "language": language,
                "available": False,
                "action": "create",
                "group_id": str(group_id),
            }
        data = item.to_dict()
        data.update({"available": True, "action": "edit"})
        return data

    def admin_update(self, identifier: str):
        """Update an editorial-content item."""
        item = self._find_admin_item(identifier)
        if isinstance(item, ProblemDetail):
            return item
        payload = self._validate_payload(self._payload(), item)
        if isinstance(payload, ProblemDetail):
            return payload
        item.type = payload["type"]
        item.language = payload["language"]
        item.title = payload["title"]
        item.intro_html = self._sanitize(payload["intro_html"])
        item.body_html = self._sanitize(payload["body_html"])
        item.updated_by = self._admin()
        if (
            payload["status"] == EditorialContentStatus.PUBLISHED
            and item.status != EditorialContentStatus.PUBLISHED
        ):
            if item.published_at is None:
                item.published_at = datetime.datetime.now(datetime.UTC)
        if payload["status"] == EditorialContentStatus.DRAFT:
            item.published_at = None
        item.status = payload["status"]
        self._db.flush()
        return item.to_dict()

    def admin_publish(self, identifier: str, publish: bool):
        """Publish or unpublish an editorial-content item."""
        item = self._find_admin_item(identifier)
        if isinstance(item, ProblemDetail):
            return item
        item.status = (
            EditorialContentStatus.PUBLISHED
            if publish
            else EditorialContentStatus.DRAFT
        )
        item.published_at = datetime.datetime.now(datetime.UTC) if publish else None
        item.updated_by = self._admin()
        self._db.flush()
        return item.to_dict()

    def admin_delete(self, identifier: str):
        """Delete an editorial-content item."""
        item = self._find_admin_item(identifier)
        if isinstance(item, ProblemDetail):
            return item
        self._db.delete(item)
        return {"success": True}

    def _find_admin_item(self, identifier: str):
        """Find an editable item in the current administrator's library."""
        library = self._library()
        if not self._admin().has_content_writer_role(library):
            from api.admin.exceptions import AdminNotAuthorized

            raise AdminNotAuthorized()
        try:
            item = (
                self._db.execute(
                    self._query_for_library(library).where(
                        EditorialContent.id == uuid.UUID(identifier)
                    )
                )
                .scalars()
                .first()
            )
        except ValueError:
            item = None
        return item or MISSING_EDITORIAL_CONTENT

    def _slug(self, library, content_type, language, title):
        """Generate a unique slug for an editorial-content item."""
        from core.model.editorial_content import editorial_slug

        base = editorial_slug(title)
        slug = base
        n = 2
        while self._db.execute(
            select(EditorialContent.id).where(
                EditorialContent.library_id == library.id,
                EditorialContent.type == content_type,
                EditorialContent.language == language,
                EditorialContent.slug == slug,
            )
        ).first():
            slug = f"{base}-{n}"
            n += 1
        return slug

    @staticmethod
    def _sanitize(value: str) -> str:
        """Sanitize HTML supplied for editorial content."""
        from core.model.editorial_content import sanitize_editorial_html

        return sanitize_editorial_html(value)
