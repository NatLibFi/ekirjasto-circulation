from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import date, datetime
from typing import Protocol, TextIO

from sqlalchemy.orm import Session

from core.model import CirculationEvent, LicensePool


class CSVWriter(Protocol):
    def writerow(self, row: Iterable[object]) -> object:
        ...


class LicenseReport:
    """Write a CSV report containing license pools and their licenses."""

    format = "%Y-%m-%d"

    headers = [
        "Work/Licensepool Identifier",
        "Title",
        "Primary author",
        "Author",
        "Collection",
        "Language",
        "Format",
        "Audiobook duration",
        "Ebook pages",
        "Series",
        "Book Collection",
        "Abridged",
        "Awards",
        "Data source modified",
        "Work last modified",
        "Title/Licensepool Created",
        "Last seen (best guess)",
        "Licensepool Status",
        "Licensepool exhausted date",
        "Current copies owned",
        "Current copies available",
        "Patrons in hold queue",
        "License identifier",
        "License created",
        "License status",
        "License type",
        "License missing?",
        "License last checked",
        "License checkouts left",
        "License checkouts available",
        "License concurrency",
        "Active loans",
        "License expiration",
        "License status url",
        "License exhausted date",
        "License availability reason",
        "License price",
        "License price currency",
        "License order name",
        "License purchased checkouts",
    ]

    def __init__(self, db: Session):
        self.db = db

    def write(self, output: TextIO) -> None:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(self.headers)

        pools = (
            self.db.query(LicensePool)
            .filter(LicensePool.open_access == False)
            .order_by(LicensePool.availability_time.desc())
        )
        for pool in pools:
            self._write_pool(writer, pool)

    def _write_pool(self, writer: CSVWriter, pool: LicensePool) -> None:
        edition = pool.presentation_edition
        identifier = pool.identifier
        last_seen = self._last_seen(pool)

        row: list[object] = [""] * len(self.headers)
        row[0] = identifier.identifier
        if edition:
            row[1:4] = [edition.title, edition.primary_author, edition.author]
            row[5:13] = [
                edition.language,
                edition.medium,
                edition.duration,
                edition.number_of_pages,
                self._series(edition),
                edition.collection,
                "abridged" if edition.abridged is True else "not abridged",
                "; ".join(edition.awards or []),
            ]
            row[13] = self._date(edition.data_source_modified)
            row[14] = self._date(edition.work.last_update_time)
        row[4] = pool.collection.name
        row[15] = self._date(pool.availability_time)
        row[16] = self._date(last_seen)
        row[17] = self._enum_value(pool.status)
        row[18] = self._date(pool.exhausted_date)
        row[19:22] = [
            pool.licenses_owned,
            pool.licenses_available,
            pool.patrons_in_hold_queue,
        ]
        writer.writerow(row)

        for license in pool.licenses:
            license_row: list[object] = [""] * len(self.headers)
            license_row[0] = identifier.identifier
            if edition:
                license_row[1:4] = [
                    edition.title,
                    edition.primary_author,
                    edition.author,
                ]
                license_row[4] = pool.collection.name
                license_row[5] = edition.language
            license_row[17] = self._enum_value(pool.status)
            license_row[22:40] = [
                license.identifier,
                self._date(license.created),
                self._enum_value(license.status),
                license.license_type.value if license.license_type else "",
                license.is_missing,
                self._date(license.last_checked),
                license.checkouts_left,
                license.checkouts_available,
                license.terms_concurrency,
                len(license.loans),
                self._date(license.expires),
                license.status_url,
                self._date(license.availability_since),
                license.availability_reason,
                license.price_value,
                license.price_currency,
                license.order_name,
                license.total_checkouts,
            ]
            writer.writerow(license_row)

    def _last_seen(self, pool: LicensePool) -> datetime | None:
        last_seen = pool.availability_time
        for loan in pool.loans:
            if loan.start and (not last_seen or loan.start > last_seen):
                last_seen = loan.start
        for hold in pool.holds:
            if hold.start and (not last_seen or hold.start > last_seen):
                last_seen = hold.start

        events = (
            self.db.query(CirculationEvent)
            .filter(CirculationEvent.license_pool_id == pool.id)
            .order_by(CirculationEvent.start.desc())
        )
        title_removals = events.filter(
            CirculationEvent.type == CirculationEvent.DISTRIBUTOR_TITLE_REMOVE
        )
        if title_removals.count():
            candidate = title_removals[-1].start
            if not last_seen or candidate > last_seen:
                last_seen = candidate

        license_removals = (
            events.filter(
                CirculationEvent.type == CirculationEvent.DISTRIBUTOR_LICENSE_REMOVE
            )
            .filter(CirculationEvent.old_value > 0)
            .filter(CirculationEvent.new_value <= 0)
        )
        if license_removals.count():
            candidate = license_removals[-1].start
            if not last_seen or candidate > last_seen:
                last_seen = candidate
        return last_seen

    def _series(self, edition) -> str:
        series = edition.series or ""
        if edition.series_position is not None:
            series += f": {edition.series_position}"
        return series

    def _date(self, value: date | datetime | None) -> str:
        return value.strftime(self.format) if value else ""

    def _enum_value(self, value) -> str:
        return value.value if value else ""
