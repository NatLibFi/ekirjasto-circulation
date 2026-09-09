from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from core.model.work import Work
from core.util.http import HTTP


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnnifSubjectSuggestion:
    uri: str
    label: str
    score: Decimal | None = None
    notation: str | None = None


class AnnifSubjectExtractor:
    """Extract subject suggestions from a Work summary using Finto AI."""

    API_URL = "https://ai.finto.fi/v1"
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

    def __init__(self, api_url: str | None = None, limit: int = 10):
        self.api_url = (api_url or self.API_URL).rstrip("/")
        self.limit = limit

    def suggestions_for(self, work: Work) -> list[AnnifSubjectSuggestion]:
        if not work.summary_text or not work.language:
            return []

        project = self.PROJECTS.get(work.language.lower())
        language = self.LANGUAGES.get(work.language.lower())
        if not project or not language:
            return []

        url = f"{self.api_url}/projects/{project}/suggest"
        response = HTTP.post_with_timeout(
            url,
            data={
                "text": work.summary_text,
                "limit": self.limit,
                "language": language,
            },
            headers={"Accept": "application/json"},
        )
        results: list[dict[str, Any]] = response.json().get("results", [])
        suggestions = [
            AnnifSubjectSuggestion(
                uri=result["uri"],
                label=result["label"],
                score=Decimal(str(result["score"]))
                if result.get("score") is not None
                else None,
                notation=result.get("notation"),
            )
            for result in results[: self.limit]
        ]
        log.info(
            "Annif suggestions for work %s: %s",
            work.id,
            [
                {
                    "uri": suggestion.uri,
                    "label": suggestion.label,
                    "score": suggestion.score,
                }
                for suggestion in suggestions
            ],
        )
        return suggestions
