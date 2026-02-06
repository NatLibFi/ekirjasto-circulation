from contextlib import nullcontext

import pytest
from pydantic import ValidationError

from api.odl2 import ODL2Importer
from core.model.configuration import ExternalIntegration
from core.model.datasource import DataSource
from core.opds2_import import OPDS2Importer
from core.opds_schema import ODL2SchemaValidation, OPDS2SchemaValidation
from tests.fixtures.database import DatabaseTransactionFixture
from tests.fixtures.files import OPDS2FilesFixture, OPDS2WithODLFilesFixture


class TestOPDS2Validation:
    @pytest.mark.parametrize(
        "feed_name, fail",
        [
            ("feed.json", False),
            ("feed2.json", False),
            ("bad_feed.json", True),
            ("bad_feed2.json", True),
        ],
    )
    def test_opds2_schema(
        self,
        feed_name: str,
        fail: bool,
        db: DatabaseTransactionFixture,
        opds2_files_fixture: OPDS2FilesFixture,
    ):
        collection = db.collection(
            protocol=ExternalIntegration.OPDS2_IMPORT,
            data_source_name=DataSource.FEEDBOOKS,
            settings={
                "external_account_id": "http://example.com/feed",
            },
        )
        validator = OPDS2SchemaValidation(
            db.session,
            collection=collection,
            import_class=OPDS2Importer,
        )

        context = pytest.raises(ValidationError) if fail else nullcontext()

        feed = opds2_files_fixture.sample_text(feed_name)
        with context:
            validator.import_one_feed(feed)


class TestOPDS2WithODLValidation:
    @pytest.mark.parametrize(
        "feed_name, fail",
        [
            ("demarque_feed.json", True),
            ("feed2.json", False),
        ],
    )
    def test_opds2_with_odl_schema(
        self,
        feed_name: str,
        fail: bool,
        db: DatabaseTransactionFixture,
        opds2_with_odl_files_fixture: OPDS2WithODLFilesFixture,
    ):
        collection = db.collection(
            protocol=ExternalIntegration.ODL2,
            data_source_name=DataSource.FEEDBOOKS,
            settings={
                "username": "username",
                "password": "password",
                "external_account_id": "http://example.com/feed",
            },
        )
        validator = ODL2SchemaValidation(
            db.session,
            collection=collection,
            import_class=ODL2Importer,
        )

        context = pytest.raises(ValidationError) if fail else nullcontext()

        feed = opds2_with_odl_files_fixture.sample_text(feed_name)
        with context:
            imported, failures = validator.import_one_feed(feed)
            assert (len(imported), len(failures)) == (0, 0)
