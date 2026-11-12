from __future__ import annotations

import datetime
import functools
import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from _pytest.logging import LogCaptureFixture
from pymarc import MARCReader

from core.classifier import ClassifierConstants
from core.marc import Annotator, MARCExporter
from core.model import DataSource, MarcFile
from core.util.datetime_helpers import utc_now
from core.util.uuid import uuid_encode

if TYPE_CHECKING:
    from tests.fixtures.database import DatabaseTransactionFixture
    from tests.fixtures.s3 import MockS3Service, S3ServiceFixture


class MarcExporterFixture:
    def __init__(self, db: DatabaseTransactionFixture, s3: MockS3Service):
        self.db = db

        self.now = utc_now()
        self.library = db.default_library()
        self.s3_service = s3
        self.exporter = MARCExporter(self.db.session, s3)
        self.mock_annotator = MagicMock(spec=Annotator)
        assert self.library.short_name is not None
        self.annotator = Annotator(
            "http://cm.url",
            self.library.short_name,
            ["http://webclient.url"],
            "org",
        )

        self.library = db.library()
        self.collection = db.collection()
        self.collection.libraries.append(self.library)

        self.now = utc_now()
        self.yesterday = self.now - datetime.timedelta(days=1)
        self.last_week = self.now - datetime.timedelta(days=7)

        self.w1 = db.work(
            genre="Mystery", with_open_access_download=True, collection=self.collection
        )
        self.w1.last_update_time = self.yesterday
        self.w2 = db.work(
            genre="Mystery", with_open_access_download=True, collection=self.collection
        )
        self.w2.last_update_time = self.last_week

        self.records = functools.partial(
            self.exporter.records,
            self.library,
            self.collection,
            annotator=self.annotator,
            creation_time=self.now,
        )


@pytest.fixture
def marc_exporter_fixture(
    db: DatabaseTransactionFixture,
    s3_service_fixture: S3ServiceFixture,
) -> MarcExporterFixture:
    return MarcExporterFixture(db, s3_service_fixture.mock_service())


class TestMARCExporter:
    def test_create_record(
        self, db: DatabaseTransactionFixture, marc_exporter_fixture: MarcExporterFixture
    ):
        work = db.work(
            with_license_pool=True,
            title="old title",
            authors=["old author"],
            data_source_name=DataSource.OVERDRIVE,
            audience=ClassifierConstants.AUDIENCE_ADULT,
        )

        mock_revised = MagicMock()

        create_record = functools.partial(
            MARCExporter.create_record,
            revised=mock_revised,
            work=work,
            annotator=marc_exporter_fixture.mock_annotator,
        )

        record = create_record()
        assert record is not None

        # Make sure we pass the expected arguments to Annotator.annotate_work_record
        marc_exporter_fixture.mock_annotator.annotate_work_record.assert_called_once_with(
            mock_revised,
            work,
            work.license_pools[0],
            work.license_pools[0].presentation_edition,
            work.license_pools[0].identifier,
        )

    def test_records(
        self,
        db: DatabaseTransactionFixture,
        marc_exporter_fixture: MarcExporterFixture,
    ):
        storage_service = marc_exporter_fixture.s3_service
        creation_time = marc_exporter_fixture.now

        marc_exporter_fixture.records()

        # The file was mirrored and a MarcFile was created to track the mirrored file.
        assert len(storage_service.uploads) == 1
        [cache] = db.session.query(MarcFile).all()
        assert cache.library == marc_exporter_fixture.library
        assert cache.collection == marc_exporter_fixture.collection

        short_name = marc_exporter_fixture.library.short_name
        collection_name = marc_exporter_fixture.collection.name
        date_str = creation_time.strftime("%Y-%m-%d")
        uuid_str = uuid_encode(cache.id)

        assert (
            cache.key
            == f"marc/{short_name}/{collection_name}.full.{date_str}.{uuid_str}.mrc"
        )
        assert cache.created == creation_time
        assert cache.since is None

        records = list(MARCReader(storage_service.uploads[0].content))
        assert len(records) == 2

        title_fields = [record.get_fields("245") for record in records]
        titles = {fields[0].get_subfields("a")[0] for fields in title_fields}
        assert titles == {
            marc_exporter_fixture.w1.title,
            marc_exporter_fixture.w2.title,
        }

    def test_records_since_time(
        self,
        db: DatabaseTransactionFixture,
        marc_exporter_fixture: MarcExporterFixture,
    ):
        # If the `since` parameter is set, only works updated since that time
        # are included in the export and the filename reflects that we created
        # a partial export.
        since = marc_exporter_fixture.now - datetime.timedelta(days=3)
        storage_service = marc_exporter_fixture.s3_service
        creation_time = marc_exporter_fixture.now

        marc_exporter_fixture.records(
            since_time=since,
        )
        [cache] = db.session.query(MarcFile).all()
        assert cache.library == marc_exporter_fixture.library
        assert cache.collection == marc_exporter_fixture.collection

        short_name = marc_exporter_fixture.library.short_name
        collection_name = marc_exporter_fixture.collection.name
        from_date = since.strftime("%Y-%m-%d")
        to_date = creation_time.strftime("%Y-%m-%d")
        uuid_str = uuid_encode(cache.id)

        assert (
            cache.key
            == f"marc/{short_name}/{collection_name}.delta.{from_date}.{to_date}.{uuid_str}.mrc"
        )
        assert cache.created == creation_time
        assert cache.since == since

        # Only the work updated since the `since` time is included in the export.
        [record] = list(MARCReader(storage_service.uploads[0].content))
        [title_field] = record.get_fields("245")
        assert title_field.get_subfields("a")[0] == marc_exporter_fixture.w1.title

    def test_records_none(
        self,
        db: DatabaseTransactionFixture,
        marc_exporter_fixture: MarcExporterFixture,
        caplog: LogCaptureFixture,
    ):
        # If there are no works to export, no file is created and a log message is generated.
        caplog.set_level(logging.INFO)

        storage_service = marc_exporter_fixture.s3_service

        # Remove the works from the database.
        db.session.delete(marc_exporter_fixture.w1)
        db.session.delete(marc_exporter_fixture.w2)

        marc_exporter_fixture.records()

        assert [] == storage_service.uploads
        assert db.session.query(MarcFile).count() == 0
        assert len(caplog.records) == 1
        assert "No MARC records to upload" in caplog.text

    def test_records_exception(
        self,
        db: DatabaseTransactionFixture,
        marc_exporter_fixture: MarcExporterFixture,
        caplog: LogCaptureFixture,
    ):
        # If an exception occurs while exporting, no file is created and a log message is generated.
        caplog.set_level(logging.ERROR)

        exporter = marc_exporter_fixture.exporter
        storage_service = marc_exporter_fixture.s3_service

        # Mock our query function to raise an exception.
        exporter.query_works = MagicMock(side_effect=Exception("Boom!"))

        marc_exporter_fixture.records()

        assert [] == storage_service.uploads
        assert db.session.query(MarcFile).count() == 0
        assert len(caplog.records) == 1
        assert "Failed to upload MARC file" in caplog.text
        assert "Boom!" in caplog.text

    def test_records_minimum_size(
        self,
        marc_exporter_fixture: MarcExporterFixture,
    ):
        exporter = marc_exporter_fixture.exporter
        storage_service = marc_exporter_fixture.s3_service

        exporter.MINIMUM_UPLOAD_BATCH_SIZE_BYTES = 100

        # Mock the "records" generated, and force the response to be of certain sizes
        created_record_mock = MagicMock()
        created_record_mock.as_marc = MagicMock(
            side_effect=[b"1" * 600, b"2" * 20, b"3" * 500, b"4" * 10]
        )
        exporter.create_record = lambda *args: created_record_mock

        # Mock the query_works to return 4 works
        exporter.query_works = MagicMock(
            return_value=[MagicMock(), MagicMock(), MagicMock(), MagicMock()]
        )

        marc_exporter_fixture.records()

        assert storage_service.mocked_multipart_upload is not None
        # Even though there are 4 parts, we upload in 3 batches due to minimum size limitations
        # The "4"th part gets uploaded due it being the tail piece
        assert len(storage_service.mocked_multipart_upload.content_parts) == 3
        assert storage_service.mocked_multipart_upload.content_parts == [
            b"1" * 600,
            b"2" * 20 + b"3" * 500,
            b"4" * 10,
        ]
