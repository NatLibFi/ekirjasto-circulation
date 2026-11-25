from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, NamedTuple

from flask_babel import force_locale

from core.classifier.localized_names import genres as localized_genres
from core.feed.annotator.admin import AdminAnnotator
from core.model import Edition, Identifier, Work


def inventory_genre_labels(
    names: Iterable[str] = localized_genres,
) -> dict[str, str]:
    """Use the same Finnish genre labels for display and database sorting."""
    with force_locale("fi"):
        return {name: str(localized_genres.get(name, name)) for name in names}


@dataclass(frozen=True)
class WorkInventoryRequest:
    offset: int
    sort: str
    descending: bool
    include_total: bool
    text_filters: dict[str, str]
    exact_filters: dict[str, str]
    count_filters: dict[str, int]
    number_filters: dict[str, float]
    date_filters: dict[str, date]
    statuses: tuple[str, ...]
    genre: str | None
    fiction: bool | None
    target_age: int | None

    @classmethod
    def from_query(cls, args: Mapping[str, str]) -> WorkInventoryRequest:
        """Parse inventory options with the endpoint's existing lenient defaults."""

        def text(name: str) -> str | None:
            return args.get(name, "").strip() or None

        def integer(name: str, default: int | None = None) -> int | None:
            value = args.get(name)
            if value is None or value == "":
                return default
            try:
                return max(0, int(value))
            except ValueError:
                return default

        def number(name: str) -> float | None:
            value = text(name)
            try:
                return float(value) if value is not None else None
            except ValueError:
                return None

        def day(name: str) -> date | None:
            value = text(name)
            try:
                return date.fromisoformat(value) if value else None
            except ValueError:
                return None

        fiction = text("fiction")
        return cls(
            offset=integer("offset", 0) or 0,
            sort=text("sort") or "title",
            descending=args.get("direction", "asc").lower() == "desc",
            include_total=args.get("include_total", "true").lower()
            not in ("false", "0"),
            text_filters={
                name: text_value
                for name in (
                    "title",
                    "author",
                    "series",
                    "awards",
                    "original_language",
                    "original_title",
                    "keywords",
                )
                if (text_value := text(name)) is not None
            },
            exact_filters={
                name: exact_value
                for name in ("language", "format", "audience")
                if (exact_value := text(name)) is not None
            },
            count_filters={
                name + suffix: count_value
                for name in ("licenses_owned", "patrons_in_hold_queue")
                for suffix in ("", "_min", "_max")
                if (count_value := integer(name + suffix)) is not None
            },
            number_filters={
                name + suffix: number_value
                for name in (
                    "published_year",
                    "duration",
                    "number_of_pages",
                    "original_published_year",
                )
                for suffix in ("_min", "_max")
                if (number_value := number(name + suffix)) is not None
            },
            date_filters={
                name + suffix: date_value
                for name in ("title_created_date", "data_source_modified")
                for suffix in ("_min", "_max")
                if (date_value := day(name + suffix)) is not None
            },
            statuses=tuple(
                value for value in args.get("status", "").split(",") if value
            ),
            genre=text("genres"),
            fiction=fiction == "true" if fiction in ("true", "false") else None,
            target_age=integer("target_age"),
        )


class WorkInventoryRow(NamedTuple):
    work: Work
    edition: Edition
    identifier: Identifier | None
    pool_status: str
    owned: int
    holds: int
    created_date: datetime | None


@dataclass
class WorkInventoryPage:
    rows: list[WorkInventoryRow]
    language_options: dict[str, list[str]]
    genre_names: list[str]
    total: int | None
    offset: int
    limit: int
    has_more: bool

    def as_dict(self, annotator: AdminAnnotator) -> dict[str, Any]:
        with force_locale("fi"):
            genre_labels = inventory_genre_labels(self.genre_names)
            works = [
                serialize_inventory_row(row, genre_labels, annotator)
                for row in self.rows
            ]
        return {
            "works": works,
            "language_options": self.language_options,
            "genre_options": [
                {"value": name, "label": label}
                for name, label in sorted(
                    genre_labels.items(), key=lambda item: item[1].casefold()
                )
            ],
            "total": self.total,
            "offset": self.offset,
            "limit": self.limit,
            "has_more": self.has_more,
        }


def serialize_inventory_row(
    row: WorkInventoryRow,
    genre_labels: Mapping[str, str],
    annotator: AdminAnnotator,
) -> dict[str, Any]:
    work, edition, identifier, pool_status, owned, holds, created_date = row
    return {
        "id": work.id,
        "admin_url": annotator.url_for(
            "work_details",
            library_short_name=annotator.library.short_name,
            identifier_type=identifier.type,
            identifier=identifier.identifier,
            _external=True,
        )
        if identifier
        else None,
        "title": edition.title,
        "author": edition.author,
        "published_year": edition.published.year if edition.published else None,
        "language": edition.language,
        "format": edition.medium,
        "license_pool_status": pool_status,
        "licenses_owned": owned,
        "patrons_in_hold_queue": holds,
        "series": edition.series,
        "series_position": edition.series_position,
        "duration": edition.duration,
        "number_of_pages": edition.number_of_pages,
        "awards": edition.awards or [],
        "title_created_date": created_date.isoformat() if created_date else None,
        "data_source_modified": edition.data_source_modified.isoformat()
        if edition.data_source_modified
        else None,
        "original_published_year": edition.original_published.year
        if edition.original_published
        else None,
        "original_language": edition.original_language,
        "original_title": edition.original_title,
        "keywords": work.keywords,
        "genres": sorted(
            (genre_labels.get(genre.name, genre.name) for genre in work.genres),
            key=str.casefold,
        ),
        "fiction": work.fiction,
        "audience": work.audience,
        "target_age": work.target_age_string if work.target_age else None,
        "identifier": {
            "type": identifier.type if identifier else None,
            "identifier": identifier.identifier if identifier else None,
        },
    }
