from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from core.model.identifier import Identifier
from core.model.work import Work
from core.util.http import HTTP


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class YSOSubjectSuggestion:
    uri: str
    label: str
    score: Decimal | None = None


class YSOSubjectExtractor:
    """Extract subject suggestions from a Work summary using Finto AI."""

    API_URL = "https://ai.finto.fi/v1"
    BATCH_SIZE = 32
    DEFAULT_THRESHOLD = 0.1
    PROJECTS = {
        "fin": "yso-fi",
        "fi": "yso-fi",
        "swe": "yso-sv",
        "sv": "yso-sv",
        "eng": "yso-en",
        "en": "yso-en",
    }
    LANGUAGES = {
        "fin": "fi",
        "fi": "fi",
        "swe": "sv",
        "sv": "sv",
        "eng": "en",
        "en": "en",
    }

    def __init__(
        self,
        api_url: str | None = None,
        limit: int = 10,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        """Create an extractor configured for the Finto AI suggestion API.

        Args:
            api_url: Base URL of the Finto AI API, or the default Finto AI URL.
            limit: Maximum number of suggestions to retain per work.
            threshold: Minimum score for a suggestion to be included.
        """
        self.api_url = (api_url or self.API_URL).rstrip("/")
        self.limit = limit
        self.threshold = threshold

    def suggestions_for(
        self, works: Sequence[Work]
    ) -> dict[int, list[YSOSubjectSuggestion]]:
        """Return subject suggestions for a batch of works.

        Finto AI accepts at most 32 documents per batch. Works are grouped by
        language because each language uses a separate Finto AI project.

        Works without summary text or a supported language are returned with
        an empty suggestion list.
        """
        suggestions_by_work: dict[int, list[YSOSubjectSuggestion]] = {
            work.id: [] for work in works
        }
        works_by_project: dict[tuple[str, str], list[Work]] = {}
        for work in works:
            # Finto AI cannot produce useful suggestions without both inputs.
            if not work.summary_text or not work.language:
                continue

            language_key = work.language.lower()
            project = self.PROJECTS.get(language_key)
            language = self.LANGUAGES.get(language_key)
            if project and language:
                works_by_project.setdefault((project, language), []).append(work)

        for (project, language), project_works in works_by_project.items():
            # Keep requests within Finto AI's maximum batch size.
            for start in range(0, len(project_works), self.BATCH_SIZE):
                batch = project_works[start : start + self.BATCH_SIZE]
                suggestions_by_work.update(
                    self._suggestions_for_batch(project, language, batch)
                )

        return suggestions_by_work

    def _suggestions_for_batch(
        self, project: str, language: str, works: Sequence[Work]
    ) -> dict[int, list[YSOSubjectSuggestion]]:
        """Request and normalize suggestions for one Finto AI project batch.

        Args:
            project: Finto AI project identifier, such as ``yso-fi``.
            language: Language code passed to Finto AI.
            works: Works whose summaries are sent in this request.

        Returns:
            A mapping from work IDs to at most ``self.limit`` suggestions.
        """
        url = f"{self.api_url}/projects/{project}/suggest-batch"
        response = HTTP.post_with_timeout(
            url,
            params={
                "limit": self.limit,
                "threshold": self.threshold,
                "language": language,
            },
            json={
                "documents": [
                    {"document_id": str(work.id), "text": work.summary_text}
                    for work in works
                ]
            },
            headers={"Accept": "application/json"},
        )
        results_by_work: dict[int, list[YSOSubjectSuggestion]] = {
            work.id: [] for work in works
        }
        for result in response.json():
            work_id = int(result["document_id"])
            suggestions_by_uri: dict[str, YSOSubjectSuggestion] = {}
            for suggestion in result.get("results", []):
                subject = YSOSubjectSuggestion(
                    uri=suggestion["uri"],
                    label=suggestion["label"],
                    score=Decimal(str(suggestion["score"]))
                    if suggestion.get("score") is not None
                    else None,
                )
                previous = suggestions_by_uri.get(subject.uri)
                # A URI can occur more than once; retain the highest score.
                if previous is None or (
                    subject.score is not None
                    and (previous.score is None or subject.score > previous.score)
                ):
                    suggestions_by_uri[subject.uri] = subject
            suggestions = list(suggestions_by_uri.values())[: self.limit]
            results_by_work[work_id] = suggestions

        for work in works:
            identifier = work.presentation_edition.primary_identifier.identifier
            log.info(
                "Extracted %s YSO subjects for work %s (%s)",
                len(results_by_work[work.id]),
                identifier,
                work.title,
            )

        return results_by_work
