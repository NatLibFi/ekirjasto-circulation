from __future__ import annotations

from typing import Any

from sqlalchemy import asc, case, desc, func, literal, nullslast
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Query, Session, selectinload

from api.admin.model.work_inventory import (
    WorkInventoryPage,
    WorkInventoryRequest,
    WorkInventoryRow,
)
from core.model import (
    DataSource,
    Edition,
    Genre,
    Identifier,
    Library,
    LicensePool,
    Work,
)
from core.model.licensing import LicensePoolStatus
from core.model.work import WorkGenre
from core.util import LanguageCodes


class WorkInventoryQuery:
    PAGE_SIZE = 50

    def __init__(
        self, session: Session, library: Library
    ) -> None:
        self._db = session
        # Parent collections are part of the library's inventory too.
        self._collection_ids = sorted(
            {
                collection.id
                for collection in library.all_collections
                if collection.id is not None
            }
        )
        # The index contains bibliographic data, but circulation values are
        # authoritative in PostgreSQL and change independently of indexing.
        pool_status_rank: Any = case(
            (LicensePool.status == LicensePoolStatus.active, 3),
            (LicensePool.status == LicensePoolStatus.exhausted, 2),
            (LicensePool.status == LicensePoolStatus.no_licenses, 1),
            else_=0,
        )
        self.pool_totals = (
            self._db.query(
                LicensePool.work_id.label("work_id"),
                func.coalesce(func.sum(LicensePool.licenses_owned), 0).label(
                    "licenses_owned"
                ),
                func.coalesce(func.sum(LicensePool.patrons_in_hold_queue), 0).label(
                    "patrons_in_hold_queue"
                ),
                func.max(pool_status_rank).label("status_rank"),
                func.min(LicensePool.availability_time).label("title_created_date"),
            )
            .filter(LicensePool.collection_id.in_(self._collection_ids))
            .group_by(LicensePool.work_id)
            .subquery()
        )

        self.status: Any = case(
            (self.pool_totals.c.status_rank == 3, LicensePoolStatus.active.value),
            (self.pool_totals.c.status_rank == 2, LicensePoolStatus.exhausted.value),
            else_=LicensePoolStatus.no_licenses.value,
        ).label("license_pool_status")
        self.licenses_owned = func.coalesce(self.pool_totals.c.licenses_owned, 0).label(
            "licenses_owned"
        )
        self.patrons_in_hold_queue = func.coalesce(
            self.pool_totals.c.patrons_in_hold_queue, 0
        ).label("patrons_in_hold_queue")

        keyword_names = func.array_to_string(Edition.keywords, ", ").collate("C")
        self.source_editions = (
            self._db.query(
                LicensePool.work_id.label("work_id"),
                func.string_agg(
                    func.distinct(keyword_names),
                    aggregate_order_by(literal(", "), keyword_names),
                ).label("names"),
            )
            .join(Identifier, Identifier.id == LicensePool.identifier_id)
            .join(Edition, Edition.primary_identifier_id == Identifier.id)
            .join(DataSource, DataSource.id == Edition.data_source_id)
            .filter(DataSource.name != DataSource.PRESENTATION_EDITION)
            .group_by(LicensePool.work_id)
            .subquery()
        )

    def fetch(self, options: WorkInventoryRequest) -> WorkInventoryPage:
        query = self._base_query()
        available_languages = query.with_entities(Edition.language).distinct().all()
        language_options = {
            language: LanguageCodes.english_names[language]
            for (language,) in available_languages
            if language in LanguageCodes.english_names
        }
        query = self._apply_filters(query, options)
        total = query.count() if options.include_total else None
        query = self._apply_sort(query, options)
        rows = (
            query.options(
                selectinload(Work.work_genres).joinedload(WorkGenre.genre),  # type: ignore[attr-defined]
                # Work.keywords traverses source editions through pool identifiers.
                selectinload(Work.license_pools)
                .joinedload(LicensePool.identifier)
                .selectinload(Identifier.primarily_identifies)
                .joinedload(Edition.data_source),
            )
            .offset(options.offset)
            .limit(self.PAGE_SIZE + 1)
            .all()
        )
        has_more = len(rows) > self.PAGE_SIZE
        rows = rows[: self.PAGE_SIZE]

        return WorkInventoryPage(
            rows=[WorkInventoryRow(*row) for row in rows],
            language_options=language_options,
            genre_names=self._available_genre_names(),
            total=total,
            offset=options.offset,
            limit=self.PAGE_SIZE,
            has_more=has_more,
        )

    def _base_query(self) -> Query:
        query = (
            self._db.query(
                Work,
                Edition,
                Identifier,
                self.status,
                self.licenses_owned,
                self.patrons_in_hold_queue,
                self.pool_totals.c.title_created_date,
            )
            .join(Edition, Work.presentation_edition_id == Edition.id)
            .outerjoin(self.pool_totals, self.pool_totals.c.work_id == Work.id)
            .outerjoin(Identifier, Edition.primary_identifier_id == Identifier.id)
            .filter(
                Work.id.in_(
                    self._db.query(LicensePool.work_id).filter(
                        LicensePool.collection_id.in_(self._collection_ids)
                    )
                )
            )
        )

        return query

    def _apply_filters(self, query: Query, options: WorkInventoryRequest) -> Query:
        if options.text_filters.get("keywords"):
            query = query.outerjoin(
                self.source_editions, self.source_editions.c.work_id == Work.id
            )

        title = options.text_filters.get("title")
        author = options.text_filters.get("author")
        if title:
            query = query.filter(Edition.title.ilike(f"%{title}%"))
        if author:
            query = query.filter(Edition.author.ilike(f"%{author}%"))

        for argument, column in (
            ("language", Edition.language),
            ("format", Edition.medium),
        ):
            value = options.exact_filters.get(argument)
            if value:
                query = query.filter(column == value)

        statuses = options.statuses
        if statuses:
            query = query.filter(self.status.in_(statuses))

        numeric_filters = (
            ("licenses_owned", self.licenses_owned),
            ("patrons_in_hold_queue", self.patrons_in_hold_queue),
        )
        for name, count_column in numeric_filters:
            exact_count = options.count_filters.get(name)
            minimum_count = options.count_filters.get(f"{name}_min")
            maximum_count = options.count_filters.get(f"{name}_max")
            if exact_count is not None:
                query = query.filter(count_column == exact_count)
            if minimum_count is not None:
                query = query.filter(count_column >= minimum_count)
            if maximum_count is not None:
                query = query.filter(count_column <= maximum_count)

        text_filters: dict[str, Any] = {
            "series": Edition.series,
            "awards": func.array_to_string(Edition.awards, ", "),
            "original_language": Edition.original_language,
            "original_title": Edition.original_title,
            "keywords": self.source_editions.c.names,
        }
        for name, text_column in text_filters.items():
            value = options.text_filters.get(name)
            if value:
                query = query.filter(text_column.ilike(f"%{value}%"))

        genre_name = options.genre
        if genre_name:
            query = query.filter(
                Work.id.in_(
                    self._db.query(WorkGenre.work_id)
                    .join(Genre, Genre.id == WorkGenre.genre_id)
                    .filter(Genre.name == genre_name)
                )
            )

        audience = options.exact_filters.get("audience")
        if audience:
            query = query.filter(Work.audience == audience)

        fiction = options.fiction
        if fiction is not None:
            query = query.filter(Work.fiction.is_(fiction))
        number_column: Any
        for name, number_column in (
            ("published_year", func.extract("year", Edition.published)),
            ("duration", Edition.duration),
            ("number_of_pages", Edition.number_of_pages),
            (
                "original_published_year",
                func.extract("year", Edition.original_published),
            ),
        ):
            for suffix, comparison in (("_min", "min"), ("_max", "max")):
                number_value = options.number_filters.get(name + suffix)
                if number_value is not None:
                    query = query.filter(
                        number_column >= number_value
                        if comparison == "min"
                        else number_column <= number_value
                    )
        target_age = options.target_age
        if target_age is not None:
            query = query.filter(Work.target_age.contains(target_age))
        for name, date_column in (
            ("title_created_date", self.pool_totals.c.title_created_date),
            ("data_source_modified", Edition.data_source_modified),
        ):
            minimum = options.date_filters.get(name + "_min")
            maximum = options.date_filters.get(name + "_max")
            if minimum:
                query = query.filter(func.date(date_column) >= minimum)
            if maximum:
                query = query.filter(func.date(date_column) <= maximum)

        return query

    def _apply_sort(self, query: Query, options: WorkInventoryRequest) -> Query:
        sort_columns = {
            "title": Edition.sort_title,
            "author": Edition.sort_author,
            "published_year": Edition.published,
            "language": Edition.language,
            "format": Edition.medium,
            "license_pool_status": self.status,
            "licenses_owned": self.licenses_owned,
            "patrons_in_hold_queue": self.patrons_in_hold_queue,
            "series": Edition.series,
            "duration": Edition.duration,
            "number_of_pages": Edition.number_of_pages,
            "awards": func.array_to_string(Edition.awards, ", "),
            "title_created_date": self.pool_totals.c.title_created_date,
            "data_source_modified": Edition.data_source_modified,
            "original_published_year": Edition.original_published,
            "original_language": Edition.original_language,
            "original_title": Edition.original_title,
            "fiction": Work.fiction,
            "audience": Work.audience,
            "target_age": func.lower(Work.target_age),
        }
        sort_name = options.sort
        sort_column = sort_columns.get(sort_name, Edition.sort_title)
        descending = options.descending
        query = query.order_by(
            nullslast(desc(sort_column) if descending else asc(sort_column)),
            Work.id.asc(),
        )

        return query

    def _available_genre_names(self) -> list[str]:
        available_genre_names = (
            self._db.query(Genre.name)
            .join(WorkGenre, WorkGenre.genre_id == Genre.id)
            .join(LicensePool, LicensePool.work_id == WorkGenre.work_id)
            .filter(LicensePool.collection_id.in_(self._collection_ids))
            .distinct()
            .all()
        )
        return [name for (name,) in available_genre_names]
