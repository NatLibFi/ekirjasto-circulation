from __future__ import annotations

import csv
from collections.abc import Generator, Mapping
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
    def writerow(self, row: Mapping[str, object]) -> object:
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
        writer = csv.DictWriter(output, fieldnames=self.headers, lineterminator="\n")
        writer.writeheader()

        with closing(self._pools_with_loan_counts()) as pools:
            for pool, loan_counts in pools:
                self._write_pool(writer, pool, loan_counts)

    def stream(self) -> Generator[str, None, None]:
        """Yield the report in pool-sized chunks instead of buffering it all."""
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=self.headers, lineterminator="\n")
        writer.writeheader()
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
    ) -> Generator[tuple[LicensePool, dict[int, int]], None, None]:
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

        shared: dict[str, object] = {
            "Work/Licensepool Identifier": identifier.identifier,
            "Collection": pool.collection.name,
            "Licensepool Status": self._enum_value(pool.status),
        }
        if edition:
            shared.update(self._edition_fields(edition))
        writer.writerow(
            shared
            | {
                "Title/Licensepool Created": self._date(pool.availability_time),
                "Licensepool exhausted date": self._date(pool.exhausted_date),
                "Current copies owned": pool.licenses_owned,
                "Current copies available": pool.licenses_available,
                "Patrons in hold queue": pool.patrons_in_hold_queue,
            }
        )

        for license in pool.licenses:
            writer.writerow(
                shared
                | {
                    "License identifier": license.identifier,
                    "License created": self._date(license.created),
                    "License status": self._enum_value(license.status),
                    "License type": self._enum_value(license.license_type),
                    "License missing?": license.is_missing,
                    "License last checked": self._date(license.last_checked),
                    "License checkouts left": license.checkouts_left,
                    "License checkouts available": license.checkouts_available,
                    "License concurrency": license.terms_concurrency,
                    "Active loans": loan_counts.get(license.id, 0)
                    if license.id is not None
                    else 0,
                    "License expiration": self._date(license.expires),
                    "License status url": license.status_url,
                    "License exhausted date": self._date(license.availability_since),
                    "License availability reason": license.availability_reason,
                    "License price": license.price_value,
                    "License price currency": license.price_currency,
                    "License order name": license.order_name,
                    "License purchased checkouts": license.total_checkouts,
                }
            )

    def _edition_fields(self, edition: Edition) -> dict[str, object]:
        work = edition.work
        primary_author = edition.primary_author
        return {
            "Title": edition.title,
            "Original title": edition.original_title or "",
            "Primary author": primary_author.sort_name if primary_author else "",
            "Author": edition.author,
            "Language": edition.language,
            "Original language": edition.original_language or "",
            "Published": self._date(edition.published),
            "Original published": self._date(edition.original_published),
            "Format": edition.medium,
            "File size": edition.file_size,
            "Audiobook duration": edition.duration,
            "Ebook pages": edition.number_of_pages,
            "Series": self._series(edition),
            "Book Collection": edition.collection,
            "Abridged": "abridged" if edition.abridged is True else "not abridged",
            "Awards": "; ".join(str(award) for award in edition.awards or []),
            "Keywords": "; ".join(str(keyword) for keyword in work.keywords)
            if work is not None
            else "",
            "Data source modified": self._date(edition.data_source_modified),
            "Work last modified": self._date(work.last_update_time)
            if work is not None
            else "",
        }

    def _series(self, edition) -> str:
        series = edition.series or ""
        if edition.series_position is not None:
            series += f": {edition.series_position}"
        return series

    def _date(self, value: date | datetime | None) -> str:
        return value.strftime(self.format) if value else ""

    def _enum_value(self, value) -> str:
        return value.value if value else ""
