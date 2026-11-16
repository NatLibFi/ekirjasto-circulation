from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from contextlib import closing
from datetime import date, datetime
from io import StringIO
from itertools import batched
from typing import Protocol, TextIO

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from core.model import LicensePool
from core.model.contributor import Contribution
from core.model.edition import Edition
from core.model.identifier import Identifier
from core.model.licensing import License
from core.model.patron import Loan
from core.model.work import Work
from core.util.log import LoggerMixin


class CSVWriter(Protocol):
    def writerow(self, row: Iterable[object]) -> object:
        ...


class LicenseReport(LoggerMixin):
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

        with closing(self._pools_with_loan_counts()) as pools:
            for pool, loan_counts in pools:
                self._write_pool(writer, pool, loan_counts)

    def stream(self) -> Iterator[str]:
        """Yield the report in pool-sized chunks instead of buffering it all."""
        output = StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(self.headers)
        # Send the header as its own chunk, then clear it before writing data.
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        # Close the query iterator before request teardown ends its transaction,
        # including when a disconnected client closes this generator early.
        with closing(self._pools_with_loan_counts()) as pools:
            for pool, loan_counts in pools:
                self._write_pool(writer, pool, loan_counts)
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
                selectinload(LicensePool.presentation_edition)
                .selectinload(Edition.contributions)
                .selectinload(Contribution.contributor),
                selectinload(LicensePool.licenses),
            )
            .filter(LicensePool.open_access == False)
            .order_by(LicensePool.availability_time.desc())
            .yield_per(100)
        )

    def _pools_with_loan_counts(
        self,
    ) -> Iterator[tuple[LicensePool, dict[int, int]]]:
        with closing(iter(self._pools())) as pools:
            for batch in batched(pools, 100):
                # Count in SQL without loading loan or patron objects. Restrict
                # each query to this batch to keep memory and query size bounded.
                loan_counts = dict(
                    self.db.query(Loan.license_id, func.count(Loan.id))
                    .join(License, Loan.license_id == License.id)
                    .filter(License.license_pool_id.in_([pool.id for pool in batch]))
                    .group_by(Loan.license_id)
                    .all()
                )
                for pool in batch:
                    yield pool, loan_counts

    def _write_pool(
        self, writer: CSVWriter, pool: LicensePool, loan_counts: dict[int, int]
    ) -> None:
        edition = pool.presentation_edition
        identifier = pool.identifier
        if identifier is None or not identifier.identifier:
            message = (
                f"Cannot generate license report: pool {pool.id} has no identifier."
            )
            self.log.error(message)
            raise ValueError(message)

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
                loan_counts.get(license.id, 0),
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

    def _populate_edition(self, row: list[object], edition: Edition) -> None:
        work = edition.work
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
            "; ".join(work.keywords) if work is not None else "",
            self._date(edition.data_source_modified),
            self._date(work.last_update_time) if work is not None else "",
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
