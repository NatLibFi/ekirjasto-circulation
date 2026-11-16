from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from io import StringIO
from unittest.mock import Mock, patch

import pytest

from core.model.licensing import LicensePoolStatus, LicenseType
from core.reports.license_report import LicenseReport
from tests.fixtures.database import DatabaseTransactionFixture


class TestLicenseReport:
    @pytest.mark.parametrize("stream", [False, True])
    def test_write_includes_license_details_and_excludes_open_access(
        self, db: DatabaseTransactionFixture, stream: bool
    ):
        availability_time = datetime(2024, 1, 2, tzinfo=timezone.utc)

        work = db.work(
            title="The Report Book",
            authors=["Report Author"],
            with_license_pool=True,
        )
        [pool] = work.license_pools
        pool.open_access = False
        pool.availability_time = availability_time
        pool.status = LicensePoolStatus.active
        pool.licenses_owned = 2
        pool.licenses_available = 1
        pool.patrons_in_hold_queue = 3

        edition = pool.presentation_edition
        edition.original_title = "Original Report Book"
        edition.original_language = "fin"
        edition.published = date(2024, 1, 1)
        edition.original_published = date(2023, 6, 1)
        edition.file_size = 1024.0
        edition.duration = 3600.0
        edition.number_of_pages = 200
        edition.series = "Report Series"
        edition.series_position = 2
        edition.collection = "Report Collection"
        edition.abridged = True
        edition.awards = ["Award One", "Award Two"]
        edition.keywords = ["reports", "books"]
        edition.data_source_modified = availability_time
        work.last_update_time = availability_time

        license_ = db.license(
            pool,
            identifier="license-1",
            checkouts_left=7,
            checkouts_available=4,
            terms_concurrency=2,
        )
        license_.created = availability_time
        license_.license_type = LicenseType.loan_limited
        license_.total_checkouts = 10
        license_.price_value = 12.5
        license_.price_currency = "EUR"
        license_.order_name = "Order 1"

        open_access_work = db.work(
            title="Open Access Book",
            authors=["Open Author"],
            with_open_access_download=True,
        )

        output = StringIO()
        report = LicenseReport(db.session)
        if stream:
            output.write("".join(report.stream()))
        else:
            report.write(output)

        reader = csv.DictReader(StringIO(output.getvalue()))
        rows = list(reader)
        assert reader.fieldnames == LicenseReport.headers
        identifiers = [row["Work/Licensepool Identifier"] for row in rows]

        assert len(rows) == 2
        assert identifiers.count(pool.identifier.identifier) == 2
        assert (
            open_access_work.license_pools[0].identifier.identifier not in identifiers
        )

        pool_row = next(row for row in rows if not row["License identifier"])
        assert pool_row["Title"] == "The Report Book"
        assert pool_row["Primary author"] == "Author, Report"
        assert pool_row["Title/Licensepool Created"] == "2024-01-02"
        assert pool_row["Current copies owned"] == "2"
        assert pool_row["Current copies available"] == "1"
        assert pool_row["Patrons in hold queue"] == "3"

        license_row = next(row for row in rows if row["License identifier"])
        assert license_row["License identifier"] == "license-1"
        assert license_row["License created"] == "2024-01-02"
        assert license_row["License type"] == "loan_limited"
        assert license_row["License checkouts left"] == "7"
        assert license_row["License concurrency"] == "2"
        assert license_row["License price"] == "12.5"
        assert license_row["License price currency"] == "EUR"
        assert license_row["License purchased checkouts"] == "10"
        assert license_row["Active loans"] == "0"
        assert license_row["Title/Licensepool Created"] == ""
        assert license_row["Current copies owned"] == ""
        assert pool_row["Active loans"] == ""

        shared = {
            "Title": "The Report Book",
            "Original title": "Original Report Book",
            "Primary author": "Author, Report",
            "Author": edition.author,
            "Collection": pool.collection.name,
            "Language": "eng",
            "Original language": "fin",
            "Published": "2024-01-01",
            "Original published": "2023-06-01",
            "Format": edition.medium,
            "File size": "1024.0",
            "Audiobook duration": "3600.0",
            "Ebook pages": "200",
            "Series": "Report Series: 2",
            "Book Collection": "Report Collection",
            "Abridged": "abridged",
            "Awards": "Award One; Award Two",
            "Keywords": "reports; books",
            "Data source modified": "2024-01-02",
            "Work last modified": "2024-01-02",
            "Licensepool Status": "active",
        }
        for row in rows:
            assert {key: row[key] for key in shared} == shared

    def test_loan_counts_are_specific_to_each_license(
        self, db: DatabaseTransactionFixture
    ):
        work = db.work(with_license_pool=True)
        [pool] = work.license_pools
        pool.open_access = False
        first = db.license(pool, identifier="first")
        second = db.license(pool, identifier="second")
        db.license(pool, identifier="unused")
        first.loan_to(db.patron())
        first.loan_to(db.patron())
        second.loan_to(db.patron())
        # A pool loan without an individual license must not be counted.
        pool.loan_to(db.patron())

        output = StringIO()
        LicenseReport(db.session).write(output)
        rows = csv.DictReader(StringIO(output.getvalue()))
        assert {
            row["License identifier"]: row["Active loans"]
            for row in rows
            if row["License identifier"]
        } == {"first": "2", "second": "1", "unused": "0"}

    def test_stream_yields_header_then_one_chunk_per_pool(
        self, db: DatabaseTransactionFixture
    ):
        pools = []
        for day in (1, 2):
            work = db.work(title=f"Book {day}, with comma", with_license_pool=True)
            [pool] = work.license_pools
            pool.open_access = False
            pool.availability_time = datetime(2024, 1, day, tzinfo=timezone.utc)
            db.license(pool)
            pools.append(pool)

        report = LicenseReport(db.session)
        chunks = list(report.stream())
        assert len(chunks) == 3
        assert list(csv.reader(StringIO(chunks[0]))) == [report.headers]
        for chunk, pool in zip(chunks[1:], reversed(pools)):
            rows = list(csv.DictReader(StringIO(chunks[0] + chunk)))
            assert len(rows) == 2
            assert all(
                row["Work/Licensepool Identifier"] == pool.identifier.identifier
                for row in rows
            )
            assert all(row["Title"] == pool.presentation_edition.title for row in rows)

        output = StringIO()
        report.write(output)
        assert "".join(chunks) == output.getvalue()

    def test_empty_report(self, db: DatabaseTransactionFixture):
        report = LicenseReport(db.session)
        output = StringIO()
        report.write(output)
        assert list(csv.reader(StringIO(output.getvalue()))) == [report.headers]
        assert list(report.stream()) == [output.getvalue()]

    def test_closing_stream_closes_pool_iterator(self, db: DatabaseTransactionFixture):
        work = db.work(with_license_pool=True)
        [pool] = work.license_pools
        pool.open_access = False
        closed = Mock()

        def pools():
            try:
                # Leave another batch unread when the client disconnects.
                for _ in range(101):
                    yield pool
            finally:
                closed()

        report = LicenseReport(db.session)
        with patch.object(report, "_pools", return_value=pools()):
            stream = report.stream()
            next(stream)
            next(stream)
            closed.assert_not_called()
            stream.close()
        closed.assert_called_once_with()

    def test_missing_optional_edition_fields_and_work(
        self, db: DatabaseTransactionFixture
    ):
        edition, pool = db.edition(authors=[], with_license_pool=True)
        pool.open_access = False
        db.license(pool)
        assert edition.work is None

        output = StringIO()
        LicenseReport(db.session).write(output)
        rows = list(csv.DictReader(StringIO(output.getvalue())))
        assert len(rows) == 2
        for row in rows:
            for field in (
                "Original title",
                "Original language",
                "Primary author",
                "Published",
                "Original published",
                "Awards",
                "Keywords",
                "Work last modified",
            ):
                assert row[field] == ""

    def test_missing_presentation_edition(self, db: DatabaseTransactionFixture):
        work = db.work(with_license_pool=True)
        [pool] = work.license_pools
        pool.open_access = False
        pool.presentation_edition = None
        db.license(pool)

        output = StringIO()
        LicenseReport(db.session).write(output)
        rows = list(csv.DictReader(StringIO(output.getvalue())))
        assert len(rows) == 2
        for row in rows:
            assert row["Title"] == ""
            assert row["Collection"] == pool.collection.name
            assert row["Work/Licensepool Identifier"] == pool.identifier.identifier

    @pytest.mark.parametrize("identifier", [None, Mock(identifier="")])
    def test_missing_identifier_raises(self, identifier):
        pool = Mock(id=123, identifier=identifier)
        writer = Mock()

        with pytest.raises(ValueError, match="pool 123 has no identifier"):
            LicenseReport(Mock())._write_pool(writer, pool, {})
        writer.writerow.assert_not_called()
