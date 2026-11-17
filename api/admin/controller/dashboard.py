from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import date, datetime, timedelta
from typing import Any

import flask
from sqlalchemy import asc, case, desc, func, nullslast
from sqlalchemy.orm import Session

from api.admin.controller.base import AdminPermissionsControllerMixin
from api.admin.model.dashboard_statistics import StatisticsResponse
from api.controller.circulation_manager import CirculationManagerController
from api.local_analytics_exporter import LocalAnalyticsExporter
from core.feed.annotator.admin import AdminAnnotator
from core.model import (
    Admin,
    CirculationEvent,
    DataSource,
    Identifier,
    LicensePool,
    Work,
)
from core.model.edition import Edition
from core.model.licensing import LicensePoolStatus
from core.reports.license_report import LicenseReport


class DashboardController(
    CirculationManagerController, AdminPermissionsControllerMixin
):
    def license_report(self) -> Generator[str, None, None]:
        self.require_system_admin()
        return LicenseReport(self._db).stream()

    def all_works(self) -> dict:
        """Return the selected library's complete work inventory."""
        library = getattr(flask.request, "library")
        self.require_librarian(library)

        def integer_argument(name: str, default: int | None = None) -> int | None:
            value = flask.request.args.get(name)
            if value is None or value == "":
                return default
            try:
                return max(0, int(value))
            except ValueError:
                return default

        def text_argument(name: str) -> str | None:
            value = flask.request.args.get(name, "").strip()
            return value or None

        limit = 20
        offset = integer_argument("offset", 0) or 0

        # The index contains bibliographic data, but circulation values are
        # authoritative in PostgreSQL and change independently of indexing.
        # Keeping the complete query here also means works with no pool are
        # retained by the outer join.
        pool_status_rank: Any = case(
            (LicensePool.status == LicensePoolStatus.active, 3),
            (LicensePool.status == LicensePoolStatus.exhausted, 2),
            (LicensePool.status == LicensePoolStatus.no_licenses, 1),
            else_=0,
        )
        pool_totals = (
            self._db.query(
                LicensePool.work_id.label("work_id"),
                func.coalesce(func.sum(LicensePool.licenses_owned), 0).label(
                    "licenses_owned"
                ),
                func.coalesce(func.sum(LicensePool.patrons_in_hold_queue), 0).label(
                    "patrons_in_hold_queue"
                ),
                func.max(pool_status_rank).label("status_rank"),
            )
            .filter(
                LicensePool.collection_id.in_(
                    collection.id for collection in library.collections
                )
            )
            .group_by(LicensePool.work_id)
            .subquery()
        )

        status: Any = case(
            (pool_totals.c.status_rank == 3, LicensePoolStatus.active.value),
            (pool_totals.c.status_rank == 2, LicensePoolStatus.exhausted.value),
            else_=LicensePoolStatus.no_licenses.value,
        ).label("license_pool_status")
        licenses_owned = func.coalesce(pool_totals.c.licenses_owned, 0).label(
            "licenses_owned"
        )
        patrons_in_hold_queue = func.coalesce(
            pool_totals.c.patrons_in_hold_queue, 0
        ).label("patrons_in_hold_queue")

        query = (
            self._db.query(
                Work,
                Edition,
                Identifier,
                status,
                licenses_owned,
                patrons_in_hold_queue,
            )
            .join(Edition, Work.presentation_edition_id == Edition.id)
            .outerjoin(pool_totals, pool_totals.c.work_id == Work.id)
            .outerjoin(Identifier, Edition.primary_identifier_id == Identifier.id)
            .filter(
                Work.id.in_(
                    self._db.query(LicensePool.work_id).filter(
                        LicensePool.collection_id.in_(
                            collection.id for collection in library.collections
                        )
                    )
                )
            )
        )

        title = text_argument("title")
        author = text_argument("author")
        if title:
            query = query.filter(Edition.title.ilike(f"%{title}%"))
        if author:
            query = query.filter(Edition.author.ilike(f"%{author}%"))

        for argument, column in (
            ("language", Edition.language),
            ("format", Edition.medium),
        ):
            value = text_argument(argument)
            if value:
                query = query.filter(column == value)

        statuses = [
            value for value in flask.request.args.get("status", "").split(",") if value
        ]
        if statuses:
            query = query.filter(status.in_(statuses))

        numeric_filters = (
            ("licenses_owned", licenses_owned),
            ("patrons_in_hold_queue", patrons_in_hold_queue),
        )
        for name, column in numeric_filters:
            exact = integer_argument(name)
            minimum = integer_argument(f"{name}_min")
            maximum = integer_argument(f"{name}_max")
            if exact is not None:
                query = query.filter(column == exact)
            if minimum is not None:
                query = query.filter(column >= minimum)
            if maximum is not None:
                query = query.filter(column <= maximum)

        include_total = flask.request.args.get("include_total", "true").lower() not in (
            "false",
            "0",
        )
        total = query.count() if include_total else None
        sort_columns = {
            "title": Edition.sort_title,
            "author": Edition.sort_author,
            "published_year": Edition.published,
            "language": Edition.language,
            "format": Edition.medium,
            "license_pool_status": status,
            "licenses_owned": licenses_owned,
            "patrons_in_hold_queue": patrons_in_hold_queue,
        }
        sort_name = text_argument("sort") or "title"
        sort_column = sort_columns.get(sort_name, Edition.sort_title)
        descending = flask.request.args.get("direction", "asc").lower() == "desc"
        query = query.order_by(
            nullslast(desc(sort_column) if descending else asc(sort_column)),
            Work.id.asc(),
        )

        rows = query.offset(offset).limit(limit + 1).all()
        has_more = len(rows) > limit
        rows = rows[:limit]

        annotator = AdminAnnotator(self.circulation, library)
        works = []
        for work, edition, identifier, pool_status, owned, holds in rows:
            works.append(
                {
                    "id": work.id,
                    "admin_url": annotator.permalink_for(identifier)[0].replace(
                        "/works/", "/admin/works/", 1
                    )
                    if identifier
                    else None,
                    "title": edition.title,
                    "author": edition.author,
                    "published_year": edition.published.year
                    if edition.published
                    else None,
                    "language": edition.language,
                    "format": edition.medium,
                    "license_pool_status": pool_status,
                    "licenses_owned": owned,
                    "patrons_in_hold_queue": holds,
                    "identifier": {
                        "type": identifier.type if identifier else None,
                        "identifier": identifier.identifier if identifier else None,
                    },
                }
            )

        return {
            "works": works,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    def stats(
        self, stats_function: Callable[[Admin, Session], StatisticsResponse]
    ) -> StatisticsResponse:
        admin: Admin = getattr(flask.request, "admin")
        return stats_function(admin, self._db)

    def circulation_events(self):
        annotator = AdminAnnotator(self.circulation, flask.request.library)
        num = min(int(flask.request.args.get("num", "100")), 500)

        results = (
            self._db.query(CirculationEvent)
            .join(LicensePool)
            .join(Work)
            .join(DataSource)
            .join(Identifier)
            .order_by(nullslast(desc(CirculationEvent.start)))
            .limit(num)
            .all()
        )

        events = [
            {
                "id": result.id,
                "type": result.type,
                "time": result.start,
                "book": {
                    "title": result.license_pool.work.title,
                    "url": annotator.permalink_for(
                        result.license_pool.identifier,
                    ),
                },
            }
            for result in results
        ]

        return dict({"circulation_events": events})

    def bulk_circulation_events(self, analytics_exporter=None):
        date_format = "%Y-%m-%d"

        def get_date(field):
            # Return a date or datetime object representing the
            # _beginning_ of the asked-for day, local time.
            #
            # Unlike most places in this application we do not
            # use UTC since the time was selected by a human user.
            today = date.today()
            value = flask.request.args.get(field, None)
            if not value:
                return today
            try:
                return datetime.strptime(value, date_format).date()
            except ValueError as e:
                # This won't happen in real life since the format is
                # controlled by the calendar widget. There's no need
                # to send an error message -- just use the default
                # date.
                return today

        # For the start date we should use the _beginning_ of the day,
        # which is what get_date returns.
        date_start = get_date("date")

        # When running the search, the cutoff is the first moment of
        # the day _after_ the end date. When generating the filename,
        # though, we should use the date provided by the user.
        date_end_label = get_date("dateEnd")
        date_end = date_end_label + timedelta(days=1)
        locations = flask.request.args.get("locations", None)
        library = getattr(flask.request, "library", None)
        library_short_name = library.short_name if library else None

        analytics_exporter = analytics_exporter or LocalAnalyticsExporter()
        data = analytics_exporter.export(
            self._db, date_start, date_end, locations, library
        )
        return (
            data,
            date_start.strftime(date_format),
            date_end_label.strftime(date_format),
            library_short_name,
        )

    def _get_date_range_and_params(self):
        """Helper function to get date ranges and other parameters."""
        date_format = "%Y-%m-%d"

        def get_date(field):
            today = date.today()
            value = flask.request.args.get(field, None)
            if not value:
                return today
            try:
                return datetime.strptime(value, date_format).date()
            except ValueError:
                return today

        date_start = get_date("date")
        date_end_label = get_date("dateEnd")
        date_end = date_end_label + timedelta(days=1)
        locations = flask.request.args.get("locations", None)
        library = getattr(flask.request, "library", None)
        library_short_name = library.short_name if library else None

        return (
            date_start,
            date_end,
            date_end_label,
            locations,
            library,
            library_short_name,
            date_format,
        )

    def circulation_loan_statistics_excel(self):
        """Exports circulation loan statistics to Excel."""
        (
            date_start,
            date_end,
            date_end_label,
            locations,
            library,
            library_short_name,
            date_format,
        ) = self._get_date_range_and_params()

        local_analytics_exporter = LocalAnalyticsExporter()
        data = local_analytics_exporter.export_excel(
            self._db, date_start, date_end, locations, library
        )
        return (
            data,
            date_start.strftime(date_format),
            date_end_label.strftime(date_format),
            library_short_name,
        )

    def circulation_loan_statistics_csv(self):
        """Exports circulation loan statistics to CSV."""
        (
            date_start,
            date_end,
            date_end_label,
            locations,
            library,
            library_short_name,
            date_format,
        ) = self._get_date_range_and_params()

        local_analytics_exporter = LocalAnalyticsExporter()
        data = local_analytics_exporter.export_csv(
            self._db, date_start, date_end, locations, library
        )
        return (
            data,
            date_start.strftime(date_format),
            date_end_label.strftime(date_format),
            library_short_name,
        )
