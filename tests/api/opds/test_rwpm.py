import logging

from api.opds.rwpm import BelongsTo, Metadata, PresentationProperties


def test_publication_metadata_accepts_and_normalizes_new_fields():
    metadata = Metadata.model_validate(
        {
            "title": "Test",
            "abridged": "false",
            "numberOfPages": "208",
            "presentation": {"layout": "reflowable"},
            "belongsTo": {
                "collection": "A collection",
                "series": [
                    {
                        "name": "A series",
                        "position": "15",
                        "links": [{"href": "https://example.com"}],
                    }
                ],
            },
        }
    )

    assert metadata.abridged is False
    assert metadata.number_of_pages == 208
    assert metadata.presentation.layout == "reflowable"
    assert metadata.belongs_to.collection == "A collection"
    assert metadata.belongs_to.series[0].name == "A series"
    assert metadata.belongs_to.series[0].position == "15"


def test_publication_metadata_accepts_correct_types():
    metadata = Metadata.model_validate(
        {
            "title": "Test",
            "abridged": False,
            "numberOfPages": 208,
            "presentation": {"layout": "reflowable"},
            "belongsTo": {
                "collection": "A collection",
                "series": [
                    {
                        "name": "A series",
                        "position": 15,
                        "links": [{"href": "https://example.com"}],
                    },
                    {"name": "No position"},
                ],
            },
        }
    )

    assert metadata.abridged is False
    assert metadata.number_of_pages == 208
    assert metadata.presentation.layout == "reflowable"
    assert metadata.belongs_to.collection == "A collection"
    assert metadata.belongs_to.series[0].name == "A series"
    assert metadata.belongs_to.series[0].position == "15"
    assert metadata.belongs_to.series[1].name == "No position"
    assert metadata.belongs_to.series[1].position is None


def test_invalid_new_metadata_is_logged_and_dropped(caplog):
    caplog.set_level(logging.ERROR)

    metadata = Metadata.model_validate(
        {
            "title": "Test",
            "abridged": "maybe",
            "numberOfPages": "many",
            "presentation": 15,
            "belongsTo": 15,
        }
    )

    assert metadata.abridged is None
    assert metadata.number_of_pages is None
    assert metadata.presentation is None
    assert metadata.belongs_to is None
    assert "Invalid abridged value" in caplog.text
    assert "Invalid numberOfPages value" in caplog.text
    assert "Invalid presentation value" in caplog.text
    assert "Invalid belongsTo value" in caplog.text


def test_invalid_nested_metadata_fields_are_logged_and_dropped(caplog):
    caplog.set_level(logging.ERROR)

    metadata = Metadata.model_validate(
        {
            "title": "Test",
            "presentation": {"layout": 15},
            "belongsTo": {
                "collection": 15,
                "series": [
                    {"name": "Valid", "position": "1"},
                    {"name": "Invalid", "position": "unknown"},
                    "invalid",
                ],
            },
        }
    )

    assert metadata.presentation.layout is None
    assert metadata.belongs_to.collection is None
    assert len(metadata.belongs_to.series) == 1
    assert "Invalid belongsTo series entry" in caplog.text


def test_belongs_to_defaults_all_fields_to_none_or_empty():
    belongs_to = BelongsTo.model_validate({})

    assert belongs_to.collection is None
    assert belongs_to.series == ()


def test_presentation_layout_is_optional():
    assert PresentationProperties.model_validate({}).layout is None
