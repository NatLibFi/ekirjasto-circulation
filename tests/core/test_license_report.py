from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO

from core.model import CirculationEvent
from core.model.licensing import LicensePoolStatus, LicenseType
from core.reports.license_report import LicenseReport
from tests.fixtures.database import DatabaseTransactionFixture


class TestLicenseReport:
    def test_write_includes_license_details_and_excludes_open_access(
        self, db: DatabaseTransactionFixture
    ):
        availability_time = datetime(2024, 1, 2, tzinfo=timezone.utc)
        last_seen = datetime(2024, 2, 3, tzinfo=timezone.utc)

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

        license_ = db.license(
            pool,
            identifier="license-1",
            checkouts_left=7,
            checkouts_available=4,
            terms_concurrency=2,
            last_checked=last_seen,
        )
        license_.created = availability_time
        license_.license_type = LicenseType.loan_limited
        license_.total_checkouts = 10
        license_.price_value = 12.5
        license_.price_currency = "EUR"
        license_.order_name = "Order 1"

        CirculationEvent.log(
            db.session,
            pool,
            CirculationEvent.DISTRIBUTOR_TITLE_REMOVE,
            old_value=1,
            new_value=0,
            start=last_seen,
        )

        open_access_work = db.work(
            title="Open Access Book",
            authors=["Open Author"],
            with_open_access_download=True,
        )

        output = StringIO()
        LicenseReport(db.session).write(output)

        rows = list(csv.DictReader(StringIO(output.getvalue())))
        identifiers = [row["Work/Licensepool Identifier"] for row in rows]

        assert len(rows) == 2
        assert identifiers.count(pool.identifier.identifier) == 2
        assert open_access_work.license_pools[0].identifier.identifier not in identifiers

        pool_row = next(row for row in rows if not row["License identifier"])
        assert pool_row["Title"] == "The Report Book"
        assert pool_row["Primary author"] == "Author, Report"
        assert pool_row["Title/Licensepool Created"] == "2024-01-02"
        assert pool_row["Last seen (best guess)"] == "2024-02-03"
        assert pool_row["Current copies owned"] == "2"
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
