import datetime
import uuid

from core.model import EditorialContent, EditorialContentStatus, EditorialContentType
from core.model.editorial_content import editorial_slug, sanitize_editorial_html


def test_editorial_slug_normalizes_titles():
    """Normalize editorial titles into URL-safe slugs."""
    assert editorial_slug("  New: Finnish News! ") == "new-finnish-news"
    assert editorial_slug("!!!") == "content"


def test_sanitize_editorial_html_keeps_allowed_markup_and_removes_unsafe_markup():
    """Keep allowed HTML while removing unsafe markup and attributes."""
    value = '<p>Hello <strong>world</strong></p><script>alert(1)</script><a href="javascript:bad" onclick="bad()">link</a>'

    sanitized = sanitize_editorial_html(value)

    assert "<strong>world</strong>" in sanitized
    assert "script" not in sanitized
    assert "javascript:" not in sanitized
    assert "onclick" not in sanitized


def test_to_dict_serializes_content_fields(db):
    """Serialize editorial-content fields into the API representation."""
    library = db.default_library()
    now = datetime.datetime.now(datetime.UTC)
    item = EditorialContent(
        id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        library=library,
        type=EditorialContentType.NEWS,
        language="en",
        title="News",
        slug="news",
        intro_html=None,
        body_html="<p>Body</p>",
        status=EditorialContentStatus.PUBLISHED,
        published_at=now,
        created_at=now,
        updated_at=now,
    )

    result = item.to_dict()

    assert result["id"] == str(item.id)
    assert result["group_id"] == str(item.group_id)
    assert result["library"] == library.short_name
    assert result["type"] == "news"
    assert result["status"] == "published"
    assert result["intro_html"] == ""
    assert result["published_at"] == now.isoformat()
