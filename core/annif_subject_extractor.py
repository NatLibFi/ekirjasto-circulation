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
class AnnifSubjectSuggestion:
    uri: str
    label: str
    score: Decimal | None = None


class AnnifSubjectExtractor:
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
        self.api_url = (api_url or self.API_URL).rstrip("/")
        self.limit = limit
        self.threshold = threshold

    def suggestions_for(
        self, works: Sequence[Work]
    ) -> dict[int, list[AnnifSubjectSuggestion]]:
        """Return subject suggestions for a batch of works.

        Finto AI accepts at most 32 documents per batch. Works are grouped by
        language because each language uses a separate Annif project.
        """
        suggestions_by_work: dict[int, list[AnnifSubjectSuggestion]] = {
            work.id: [] for work in works
        }
        works_by_project: dict[tuple[str, str], list[Work]] = {}
        for work in works:
            if not work.summary_text or not work.language:
                continue

            language_key = work.language.lower()
            project = self.PROJECTS.get(language_key)
            language = self.LANGUAGES.get(language_key)
            if project and language:
                works_by_project.setdefault((project, language), []).append(work)

        for (project, language), project_works in works_by_project.items():
            for start in range(0, len(project_works), self.BATCH_SIZE):
                batch = project_works[start : start + self.BATCH_SIZE]
                suggestions_by_work.update(
                    self._suggestions_for_batch(project, language, batch)
                )

        return suggestions_by_work

    def _suggestions_for_batch(
        self, project: str, language: str, works: Sequence[Work]
    ) -> dict[int, list[AnnifSubjectSuggestion]]:
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
        results_by_work: dict[int, list[AnnifSubjectSuggestion]] = {
            work.id: [] for work in works
        }
        for result in response.json():
            work_id = int(result["document_id"])
            suggestions_by_uri: dict[str, AnnifSubjectSuggestion] = {}
            for suggestion in result.get("results", []):
                subject = AnnifSubjectSuggestion(
                    uri=suggestion["uri"],
                    label=suggestion["label"],
                    score=Decimal(str(suggestion["score"]))
                    if suggestion.get("score") is not None
                    else None,
                )
                previous = suggestions_by_uri.get(subject.uri)
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
                "Extracted %s Annif subjects for work %s (%s)",
                len(results_by_work[work.id]),
                identifier,
                work.title,
            )

        return results_by_work
