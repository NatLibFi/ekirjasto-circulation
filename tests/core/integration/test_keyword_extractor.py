from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.integration.keyword_extractor import KeywordExtractor, KeywordSuggestion


def make_work(work_id, summary_text="A summary.", language="eng"):
    return SimpleNamespace(
        id=work_id,
        summary_text=summary_text,
        language=language,
        title=f"Work {work_id}",
        presentation_edition=SimpleNamespace(
            primary_identifier=SimpleNamespace(identifier=f"identifier-{work_id}")
        ),
    )


def extractor(limit=10, threshold=0.1):
    return KeywordExtractor(
        api_url="https://example.test/v1/",
        limit=limit,
        threshold=threshold,
    )


class TestKeywordExtractor:
    @pytest.mark.parametrize(
        ("language", "project"),
        [("eng", "yso-en"), ("FIN", "yso-fi"), ("swe", "yso-sv")],
    )
    def test_supported_language_projects(self, language, project):
        """Use the matching Finto project and language for each supported language."""
        a_work = make_work(1, language=language)
        language_code = {"eng": "en", "FIN": "fi", "swe": "sv"}[language]

        with patch.object(
            KeywordExtractor, "_suggestions_for_batch", return_value={1: []}
        ) as request:
            extractor().suggestions_for([a_work])

        request.assert_called_once_with(project, language_code, [a_work])

    def test_keyword_suggestion_is_immutable(self):
        """Prevent keyword suggestions from being modified after creation."""
        suggestion = KeywordSuggestion("uri", "label", Decimal("0.5"))

        with pytest.raises(AttributeError):
            suggestion.label = "changed"

    def test_suggestions_for_skips_works_without_required_metadata(self):
        """Return empty suggestions without requesting works lacking usable metadata."""
        works = [
            make_work(1, summary_text=None),
            make_work(2, language=None),
            make_work(3, language="nor"),
        ]

        with patch.object(KeywordExtractor, "_suggestions_for_batch") as request:
            result = extractor().suggestions_for(works)

        assert {1: [], 2: [], 3: []} == result
        request.assert_not_called()

    def test_suggestions_for_filters_configured_work_languages(self):
        """Avoid sending summaries for work languages disabled in the settings."""
        works = [
            make_work(1, language="fin"),
            make_work(2, language="eng"),
        ]

        with patch.object(
            KeywordExtractor, "_suggestions_for_batch", return_value={1: []}
        ) as request:
            result = KeywordExtractor(
                api_url="https://example.test/v1",
                limit=10,
                threshold=0.1,
                work_languages=["fin"],
            ).suggestions_for(works)

        assert result == {1: [], 2: []}
        request.assert_called_once_with("yso-fi", "fi", [works[0]])

    @pytest.mark.parametrize(
        ("keyword_language", "expected_label_language"),
        [("fin", "fi"), ("book", "en")],
    )
    def test_suggestions_for_batch_requests_configured_keyword_language(
        self, keyword_language, expected_label_language
    ):
        """Request Finnish or book-language labels according to configuration."""
        response = SimpleNamespace(json=lambda: [])

        with patch(
            "core.integration.keyword_extractor.HTTP.post_with_timeout",
            return_value=response,
        ) as post:
            extractor = KeywordExtractor(
                api_url="https://example.test/v1",
                limit=10,
                threshold=0.1,
                keyword_language=keyword_language,
            )
            extractor._suggestions_for_batch("yso-en", "en", [make_work(1)])

        assert (
            post.call_args.kwargs["params"]["language"] == expected_label_language
        )

    def test_suggestions_for_groups_by_language_and_batches_requests(self):
        """Group works by language and split each group into API-sized batches."""
        works = [
            make_work(i, language="eng" if i <= 34 else "fin") for i in range(1, 36)
        ]
        expected = {a_work.id: [] for a_work in works}

        with patch.object(
            KeywordExtractor,
            "_suggestions_for_batch",
            side_effect=lambda project, language, batch: {
                a_work.id: [KeywordSuggestion(project, str(a_work.id))]
                for a_work in batch
            },
        ) as request:
            result = extractor().suggestions_for(works)

        assert expected.keys() == result.keys()
        assert [suggestion.label for suggestion in result[1]] == ["1"]
        assert [suggestion.label for suggestion in result[34]] == ["34"]
        assert [call.args[0] for call in request.call_args_list] == [
            "yso-en",
            "yso-en",
            "yso-fi",
        ]
        assert [call.args[1] for call in request.call_args_list] == ["en", "en", "fi"]
        assert [len(call.args[2]) for call in request.call_args_list] == [32, 2, 1]

    def test_suggestions_for_batch_sends_documents_and_normalizes_results(self):
        """Send summaries to Finto and normalize, deduplicate, and limit its results."""
        # These are the two documents that will be sent to Finto AI. The
        # second document has no response below, so it should keep [] in the
        # returned mapping.
        works = [
            make_work(1, summary_text="A story about forests."),
            make_work(2, summary_text="A history of music."),
        ]

        # The response deliberately covers the normalization cases:
        # - the same URI is returned twice, so the highest score wins;
        # - a keyword may have no score;
        # - the third unique keyword is dropped because limit=2;
        # - work 2 is absent, so it gets an empty list.
        response = SimpleNamespace(
            json=lambda: [
                {
                    "document_id": "1",
                    "results": [
                        {
                            "uri": "uri-forest",
                            "label": "Forests",
                            "score": 0.2,
                        },
                        {
                            "uri": "uri-forest",
                            "label": "Forests",
                            "score": 0.8,
                        },
                        {"uri": "uri-nature", "label": "Nature", "score": None},
                        {"uri": "uri-trees", "label": "Trees", "score": 0.7},
                    ],
                }
            ]
        )

        with patch(
            "core.integration.keyword_extractor.HTTP.post_with_timeout",
            return_value=response,
        ) as post:
            result = extractor(limit=2, threshold=0.25)._suggestions_for_batch(
                "yso-en", "en", works
            )

        post.assert_called_once_with(
            "https://example.test/v1/projects/yso-en/suggest-batch",
            params={"limit": 2, "threshold": 0.25, "language": "en"},
            json={
                "documents": [
                    {"document_id": "1", "text": "A story about forests."},
                    {"document_id": "2", "text": "A history of music."},
                ]
            },
            headers={"Accept": "application/json"},
        )
        assert [
            (suggestion.uri, suggestion.label, suggestion.score)
            for suggestion in result[1]
        ] == [
            ("uri-forest", "Forests", Decimal("0.8")),
            ("uri-nature", "Nature", None),
        ]
        assert result[2] == []
