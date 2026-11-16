from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from io import StringIO
from typing import Protocol, TextIO

from sqlalchemy.orm import Session, selectinload

from core.model import LicensePool
from core.model.edition import Edition
from core.model.identifier import Identifier
from core.model.licensing import License
from core.model.work import Work


class CSVWriter(Protocol):
    def writerow(self, row: Iterable[object]) -> object:
        ...


class LicenseReport:
    """Write a CSV report containing license pools and their licenses."""

    format = "%Y-%m-%d"

    headers = [
        "Work/Licensepool Identifier",
        "Title",
        "Original title",
        "Primary author",
        "Author",
        "Collection",
        "Language",
        "Original language",
        "Published",
        "Original published",
        "Format",
        "File size",
        "Audiobook duration",
        "Ebook pages",
        "Series",
        "Book Collection",
        "Abridged",
        "Awards",
        "Keywords",
        "Data source modified",
        "Work last modified",
        "Title/Licensepool Created",
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

        for pool in self._pools():
            self._write_pool(writer, pool)

    def stream(self) -> Iterator[str]:
        """Yield the report in pool-sized chunks instead of buffering it all."""
        output = StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(self.headers)
        # Send the header as its own chunk, then clear it before writing data.
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        for pool in self._pools():
            self._write_pool(writer, pool)
            yield output.getvalue()
            # Keep each yielded chunk limited to the next pool's rows.
            output.seek(0)
            output.truncate(0)

    def _pools(self):
        return (
            self.db.query(LicensePool)
            .options(
                selectinload(LicensePool.identifier),
                selectinload(LicensePool.collection),
                selectinload(LicensePool.presentation_edition)
                .selectinload(Edition.work)
                .selectinload(Work.license_pools)
                .selectinload(LicensePool.identifier)
                .selectinload(Identifier.primarily_identifies)
                .selectinload(Edition.data_source),
                selectinload(LicensePool.licenses).selectinload(License.loans),
                selectinload(LicensePool.loans),
                selectinload(LicensePool.holds),
            )
            .filter(LicensePool.open_access == False)
            .order_by(LicensePool.availability_time.desc())
            .yield_per(100)
        )

    def _write_pool(self, writer: CSVWriter, pool: LicensePool) -> None:
        edition = pool.presentation_edition
        identifier = pool.identifier

        row: list[object] = [""] * len(self.headers)
        row[0] = identifier.identifier
        if edition:
            self._populate_edition(row, edition)
        row[5] = pool.collection.name
        row[21] = self._date(pool.availability_time)
        row[22] = self._enum_value(pool.status)
        row[23] = self._date(pool.exhausted_date)
        row[24:27] = [
            pool.licenses_owned,
            pool.licenses_available,
            pool.patrons_in_hold_queue,
        ]
        writer.writerow(row)

        for license in pool.licenses:
            license_row: list[object] = [""] * len(self.headers)
            license_row[0] = identifier.identifier
            if edition:
                self._populate_edition(license_row, edition)
                license_row[5] = pool.collection.name
            license_row[22] = self._enum_value(pool.status)
            license_row[27:45] = [
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

    def _populate_edition(self, row: list[object], edition) -> None:
        row[1:5] = [
            edition.title,
            edition.original_title or "",
            edition.primary_author.sort_name if edition.primary_author else "",
            edition.author,
        ]
        row[6:21] = [
            edition.language,
            edition.original_language or "",
            self._date(edition.published),
            self._date(edition.original_published),
            edition.medium,
            edition.file_size,
            edition.duration,
            edition.number_of_pages,
            self._series(edition),
            edition.collection,
            "abridged" if edition.abridged is True else "not abridged",
            "; ".join(edition.awards or []),
            "; ".join(edition.work.keywords),
            self._date(edition.data_source_modified),
            self._date(edition.work.last_update_time),
        ]

    def _series(self, edition) -> str:
        series = edition.series or ""
        if edition.series_position is not None:
            series += f": {edition.series_position}"
        return series

    def _date(self, value: date | datetime | None) -> str:
        return value.strftime(self.format) if value else ""

    def _enum_value(self, value) -> str:
        return value.value if value else ""
