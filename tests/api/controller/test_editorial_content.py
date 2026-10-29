import datetime
import uuid

import pytest

from api.admin.exceptions import AdminNotAuthorized
from api.admin.problem_details import MISSING_EDITORIAL_CONTENT
from api.controller.editorial_content import EditorialContentController
from core.model import (
    Admin,
    AdminRole,
    EditorialContent,
    EditorialContentStatus,
    EditorialContentType,
    create,
)
from core.problem_details import INVALID_INPUT
from core.util.problem_detail import ProblemDetail
from tests.fixtures.database import DatabaseTransactionFixture
from tests.fixtures.flask import FlaskAppFixture


def make_controller(db: DatabaseTransactionFixture) -> EditorialContentController:
    """Create an editorial-content controller for a test database."""
    return EditorialContentController(db.session)


def make_admin(db: DatabaseTransactionFixture, role: str, library=None) -> Admin:
    """Create an administrator with the requested role."""
    admin, _ = create(db.session, Admin, email=f"{uuid.uuid4()}@example.com")
    admin.add_role(role, library)
    return admin


def make_content(
    db: DatabaseTransactionFixture,
    library,
    *,
    title="Title",
    language="en",
    content_type=EditorialContentType.NEWS,
    status=EditorialContentStatus.PUBLISHED,
    group_id=None,
    slug=None,
    body_html="<p>Body</p>",
):
    """Create editorial content with convenient test defaults."""
    now = datetime.datetime.now(datetime.UTC)
    item, _ = create(
        db.session,
        EditorialContent,
        library=library,
        group_id=group_id or uuid.uuid4(),
        type=content_type,
        language=language,
        title=title,
        slug=slug or title.lower().replace(" ", "-"),
        intro_html="<p>Intro</p>",
        body_html=body_html,
        status=status,
        published_at=now if status == EditorialContentStatus.PUBLISHED else None,
        created_at=now,
        updated_at=now,
    )
    return item


class TestEditorialContentController:
    def test_public_list_filters_published_type_and_language(
        self, db: DatabaseTransactionFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Filter public listings by publication state, type, and language."""
        library = db.default_library()
        make_content(db, library, title="Published", language="en")
        make_content(
            db,
            library,
            title="Draft",
            language="en",
            status=EditorialContentStatus.DRAFT,
        )
        make_content(db, library, title="Finnish", language="fi")
        make_content(db, library, title="FAQ", content_type=EditorialContentType.FAQ)
        controller = make_controller(db)

        with flask_app_fixture.test_request_context("/", library=library):
            response = controller.public_list("news", "en")

        assert [item["title"] for item in response["content"]] == ["Published"]

    def test_public_list_without_filters_returns_all_published_content(
        self, db: DatabaseTransactionFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Return published content in all languages and types without filters."""
        library = db.default_library()
        make_content(db, library, title="English news", language="en")
        make_content(
            db,
            library,
            title="Finnish FAQ",
            language="fi",
            content_type=EditorialContentType.FAQ,
        )
        make_content(
            db,
            library,
            title="Draft",
            language="fi",
            status=EditorialContentStatus.DRAFT,
        )
        controller = make_controller(db)

        with flask_app_fixture.test_request_context("/", library=library):
            response = controller.public_list(None, None)

        assert {item["title"] for item in response["content"]} == {
            "English news",
            "Finnish FAQ",
        }

    def test_public_banner_list_without_language_returns_all_published_languages(
        self, db: DatabaseTransactionFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Return banners in every language when no language is requested."""
        library = db.default_library()
        make_content(
            db,
            library,
            title="Finnish banner",
            language="fi",
            content_type=EditorialContentType.BANNER,
        )
        make_content(
            db,
            library,
            title="English banner",
            language="en",
            content_type=EditorialContentType.BANNER,
        )
        controller = make_controller(db)

        with flask_app_fixture.test_request_context("/", library=library):
            response = controller.public_list("banner", None)

        assert {item["language"] for item in response["content"]} == {"fi", "en"}

    def test_public_item_finds_by_id_group_id_and_slug(
        self, db: DatabaseTransactionFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Resolve public content by item ID, group ID, or slug."""
        library = db.default_library()
        group_id = uuid.uuid4()
        item = make_content(
            db, library, title="A Story", group_id=group_id, slug="a-story"
        )
        translation = make_content(
            db,
            library,
            title="En berättelse",
            language="sv",
            group_id=group_id,
            slug="en-berattelse",
        )
        controller = make_controller(db)

        with flask_app_fixture.test_request_context("/", library=library):
            assert controller.public_item(str(item.id))["id"] == str(item.id)
            assert controller.public_item(str(group_id))["id"] == str(item.id)
            assert controller.public_item("a-story")["id"] == str(item.id)
            assert controller.public_item(str(group_id), "sv")["id"] == str(
                translation.id
            )
            assert controller.public_item("missing") == MISSING_EDITORIAL_CONTENT

    def test_admin_create_sanitizes_html_and_generates_unique_slug(
        self, db: DatabaseTransactionFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Sanitize created content and disambiguate duplicate slugs."""
        library = db.default_library()
        admin = make_admin(db, AdminRole.CONTENT_WRITER, library)
        make_content(
            db,
            library,
            title="Hello World",
            status=EditorialContentStatus.DRAFT,
            slug="hello-world",
        )
        controller = make_controller(db)

        payload = {
            "type": "news",
            "language": "en",
            "title": "Hello World",
            "intro_html": "<script>bad()</script><p>Intro</p>",
            "body_html": '<a href="javascript:bad">Body</a>',
            "status": "published",
        }
        with flask_app_fixture.test_request_context(
            "/", method="POST", json=payload, library=library, admin=admin
        ):
            result = controller.admin_create()

        assert result["slug"] == "hello-world-2"
        assert result["status"] == "published"
        assert result["published_at"] is not None
        assert "script" not in result["intro_html"]
        assert "javascript:" not in result["body_html"]

    def test_admin_create_rejects_invalid_payload_and_requires_writer(
        self, db: DatabaseTransactionFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Reject invalid payloads and unauthorized administrators."""
        library = db.default_library()
        librarian = make_admin(db, AdminRole.LIBRARIAN, library)
        controller = make_controller(db)
        payload = {"type": "news", "language": "en", "title": "", "body_html": ""}

        with flask_app_fixture.test_request_context(
            "/", method="POST", json=payload, library=library, admin=librarian
        ):
            with pytest.raises(AdminNotAuthorized):
                controller.admin_create()

        writer = make_admin(db, AdminRole.CONTENT_WRITER, library)
        with flask_app_fixture.test_request_context(
            "/", method="POST", json=payload, library=library, admin=writer
        ):
            assert controller.admin_create() == INVALID_INPUT

    def test_admin_group_lists_language_details_and_admin_list_groups_items(
        self, db: DatabaseTransactionFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Group admin listings and show missing language variants."""
        library = db.default_library()
        admin = make_admin(db, AdminRole.LIBRARIAN, library)
        group_id = uuid.uuid4()
        make_content(db, library, title="Finnish", language="fi", group_id=group_id)
        make_content(db, library, title="English", language="en", group_id=group_id)
        controller = make_controller(db)

        with flask_app_fixture.test_request_context("/", library=library, admin=admin):
            group = controller.admin_group(str(group_id))
            listing = controller.admin_list()

        assert [item["language"] for item in group["languages"]] == ["fi", "en", "sv"]
        assert group["languages"][2]["available"] is False
        assert len(listing["content"]) == 1
        assert listing["content"][0]["languages"] == ["fi", "en"]

    def test_admin_update_publish_unpublish_and_delete(
        self, db: DatabaseTransactionFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Exercise update, publication, unpublication, and deletion flows."""
        library = db.default_library()
        admin = make_admin(db, AdminRole.CONTENT_WRITER, library)
        item = make_content(db, library, status=EditorialContentStatus.DRAFT)
        controller = make_controller(db)

        with flask_app_fixture.test_request_context(
            "/",
            method="PUT",
            json={"title": "Updated", "status": "published"},
            library=library,
            admin=admin,
        ):
            updated = controller.admin_update(str(item.id))
        assert updated["title"] == "Updated"
        assert updated["status"] == "published"
        assert updated["published_at"] is not None

        with flask_app_fixture.test_request_context("/", library=library, admin=admin):
            unpublished = controller.admin_publish(str(item.id), False)
        assert unpublished["status"] == "draft"
        assert unpublished["published_at"] is None

        with flask_app_fixture.test_request_context("/", library=library, admin=admin):
            assert controller.admin_delete(str(item.id)) == {"success": True}
            assert controller.admin_update(str(item.id)) == MISSING_EDITORIAL_CONTENT

    def test_admin_operations_are_isolated_by_library(
        self, db: DatabaseTransactionFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Prevent administrators from modifying another library's content."""
        library = db.default_library()
        other_library = db.library()
        admin = make_admin(db, AdminRole.CONTENT_WRITER, library)
        item = make_content(db, other_library)
        controller = make_controller(db)

        with flask_app_fixture.test_request_context("/", library=library, admin=admin):
            assert controller.admin_update(str(item.id)) == MISSING_EDITORIAL_CONTENT
            assert controller.admin_delete(str(item.id)) == MISSING_EDITORIAL_CONTENT

    def test_invalid_non_string_title_returns_invalid_input(
        self, db: DatabaseTransactionFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Reject non-string titles in editorial-content payloads."""
        library = db.default_library()
        admin = make_admin(db, AdminRole.CONTENT_WRITER, library)
        controller = make_controller(db)

        with flask_app_fixture.test_request_context(
            "/",
            method="POST",
            json={"type": "news", "language": "en", "title": 1, "body_html": ""},
            library=library,
            admin=admin,
        ):
            result = controller.admin_create()

        assert isinstance(result, ProblemDetail)
        assert result == INVALID_INPUT
