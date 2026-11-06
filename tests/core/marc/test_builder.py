from __future__ import annotations

import datetime
import functools
import logging
import urllib
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from _pytest.logging import LogCaptureFixture
from pymarc import MARCReader, Record

from core.classifier import ClassifierConstants
from core.marc import Annotator
from core.model import (
    Contributor,
    DataSource,
    DeliveryMechanism,
    Edition,
    Genre,
    Identifier,
    LicensePoolDeliveryMechanism,
    MarcFile,
    Representation,
    RightsStatus,
)
from core.util.datetime_helpers import datetime_utc, utc_now
from core.util.uuid import uuid_encode

if TYPE_CHECKING:
    from tests.fixtures.database import DatabaseTransactionFixture
    from tests.fixtures.s3 import MockS3Service, S3ServiceFixture


class AnnotateWorkRecordFixture:
    def __init__(self):
        self.cm_url = "http://cm.url"
        self.short_name = "short_name"
        self.web_client_urls = ["http://webclient.url"]
        self.organization_name = "org"

        self.annotator = Annotator(
            self.cm_url,
            self.short_name,
            self.web_client_urls,
            self.organization_name,
        )

        self.revised = MagicMock()
        self.work = MagicMock()
        self.pool = MagicMock()
        self.edition = MagicMock()
        self.identifier = MagicMock()

        self.mock_leader = create_autospec(self.annotator.leader, return_value=" " * 24)
        self.mock_add_control_fields = create_autospec(
            self.annotator.add_control_fields
        )
        self.mock_add_marc_organization_code = create_autospec(
            self.annotator.add_marc_organization_code
        )
        self.mock_add_isbn = create_autospec(self.annotator.add_isbn)
        self.mock_add_title = create_autospec(self.annotator.add_title)
        self.mock_add_contributors = create_autospec(self.annotator.add_contributors)
        self.mock_add_publisher = create_autospec(self.annotator.add_publisher)
        self.mock_add_physical_description = create_autospec(
            self.annotator.add_physical_description
        )
        self.mock_add_audience = create_autospec(self.annotator.add_audience)
        self.mock_add_series = create_autospec(self.annotator.add_series)
        self.mock_add_formats = create_autospec(self.annotator.add_formats)
        self.mock_add_summary = create_autospec(self.annotator.add_summary)
        self.mock_add_genres = create_autospec(self.annotator.add_genres)
        self.mock_add_keywords = create_autospec(self.annotator.add_keywords)
        self.mock_add_web_client_urls = create_autospec(
            self.annotator.add_web_client_urls
        )

        self.annotator.leader = self.mock_leader
        self.annotator.add_control_fields = self.mock_add_control_fields
        self.annotator.add_marc_organization_code = self.mock_add_marc_organization_code
        self.annotator.add_isbn = self.mock_add_isbn
        self.annotator.add_title = self.mock_add_title
        self.annotator.add_contributors = self.mock_add_contributors
        self.annotator.add_publisher = self.mock_add_publisher
        self.annotator.add_physical_description = self.mock_add_physical_description
        self.annotator.add_audience = self.mock_add_audience
        self.annotator.add_series = self.mock_add_series
        self.annotator.add_formats = self.mock_add_formats
        self.annotator.add_summary = self.mock_add_summary
        self.annotator.add_genres = self.mock_add_genres
        self.annotator.add_keywords = self.mock_add_keywords
        self.annotator.add_web_client_urls = self.mock_add_web_client_urls

        self.annotate_work_record = functools.partial(
            self.annotator.annotate_work_record,
            self.revised,
            self.work,
            self.pool,
            self.edition,
            self.identifier,
        )


@pytest.fixture
def annotate_work_record_fixture() -> AnnotateWorkRecordFixture:
    return AnnotateWorkRecordFixture()


class TestAnnotator:
    def test_annotate_work_record(
        self, annotate_work_record_fixture: AnnotateWorkRecordFixture
    ) -> None:
        fixture = annotate_work_record_fixture
        with patch("core.marc.builder.Record") as mock_record:
            d = fixture.annotate_work_record()
            print(d)

        mock_record.assert_called_once_with(
            force_utf8=True, leader=fixture.mock_leader.return_value
        )
        fixture.mock_leader.assert_called_once_with(fixture.revised)
        record = mock_record()
        fixture.mock_add_control_fields.assert_called_once_with(
            record, fixture.identifier, fixture.pool, fixture.edition
        )
        fixture.mock_add_marc_organization_code.assert_called_once_with(
            record, fixture.organization_name
        )
        fixture.mock_add_isbn.assert_called_once_with(record, fixture.identifier)
        fixture.mock_add_title.assert_called_once_with(record, fixture.edition)
        fixture.mock_add_contributors.assert_called_once_with(record, fixture.edition)
        fixture.mock_add_publisher.assert_called_once_with(record, fixture.edition)
        fixture.mock_add_physical_description.assert_called_once_with(
            record, fixture.edition
        )
        fixture.mock_add_audience.assert_called_once_with(record, fixture.work)
        fixture.mock_add_series.assert_called_once_with(record, fixture.edition)
        fixture.mock_add_formats.assert_called_once_with(record, fixture.pool)
        fixture.mock_add_summary.assert_called_once_with(record, fixture.work)
        fixture.mock_add_genres.assert_called_once_with(record, fixture.work)
        fixture.mock_add_keywords.assert_called_once_with(record, fixture.edition)
        fixture.mock_add_web_client_urls.assert_called_once_with(
            record,
            fixture.identifier,
            fixture.short_name,
            fixture.cm_url,
            fixture.web_client_urls,
        )

    def test_annotate_work_record_no_organization_code(
        self, annotate_work_record_fixture: AnnotateWorkRecordFixture
    ) -> None:
        fixture = annotate_work_record_fixture
        fixture.annotator.organization_code = None
        fixture.annotate_work_record()

        assert fixture.mock_add_marc_organization_code.call_count == 0

    def test_leader(self):
        leader = Annotator.leader(False)
        assert leader == "00000nam  2200000   4500"

        # If the record is revised, the leader is different.
        leader = Annotator.leader(True)
        assert leader == "00000cam  2200000   4500"

    @staticmethod
    def _check_control_field(record, tag, expected):
        [field] = record.get_fields(tag)
        assert field.value() == expected

    @staticmethod
    def _check_field(record, tag, expected_subfields, expected_indicators=None):
        if not expected_indicators:
            expected_indicators = [" ", " "]
        [field] = record.get_fields(tag)
        assert field.indicators == expected_indicators
        for subfield, value in expected_subfields.items():
            assert field.get_subfields(subfield)[0] == value

    def test_add_control_fields(self, db: DatabaseTransactionFixture):
        # This edition has one format and was published before 1900.
        edition, pool = db.edition(with_license_pool=True)
        identifier = pool.identifier
        edition.issued = datetime_utc(956, 1, 1)

        now = utc_now()
        record = Record()

        Annotator.add_control_fields(record, identifier, pool, edition)
        self._check_control_field(record, "001", identifier.urn)
        assert now.strftime("%Y%m%d") in record.get_fields("005")[0].value()
        self._check_control_field(record, "006", "m        d        ")
        self._check_control_field(record, "007", "cr cn ---anuuu")
        self._check_control_field(
            record, "008", now.strftime("%y%m%d") + "s0956    xxu                 eng  "
        )

        # This French edition has two formats and was published in 2018.
        edition2, pool2 = db.edition(with_license_pool=True)
        identifier2 = pool2.identifier
        edition2.issued = datetime_utc(2018, 2, 3)
        edition2.language = "fre"
        LicensePoolDeliveryMechanism.set(
            pool2.data_source,
            identifier2,
            Representation.PDF_MEDIA_TYPE,
            DeliveryMechanism.ADOBE_DRM,
            RightsStatus.IN_COPYRIGHT,
        )

        record = Record()
        Annotator.add_control_fields(record, identifier2, pool2, edition2)
        self._check_control_field(record, "001", identifier2.urn)
        assert now.strftime("%Y%m%d") in record.get_fields("005")[0].value()
        self._check_control_field(record, "006", "m        d        ")
        self._check_control_field(record, "007", "cr cn ---mnuuu")
        self._check_control_field(
            record, "008", now.strftime("%y%m%d") + "s2018    xxu                 fre  "
        )

    def test_add_control_fields_audiobook(self, db: DatabaseTransactionFixture):
        edition, pool = db.edition(with_license_pool=True)
        edition.medium = Edition.AUDIO_MEDIUM
        record = Record(leader=Annotator.leader(False))

        Annotator.add_control_fields(record, pool.identifier, pool, edition)

        assert record.leader == "00000nim  2200000   4500"
        # pymarc normalizes 007 to MARC's fixed 14-character length.
        self._check_control_field(record, "007", "sr" + "|" * 12)

    def test_add_marc_organization_code(self):
        record = Record()
        Annotator.add_marc_organization_code(record, "US-MaBoDPL")
        self._check_control_field(record, "003", "US-MaBoDPL")

    def test_add_isbn(self, db: DatabaseTransactionFixture):
        isbn = db.identifier(identifier_type=Identifier.ISBN)
        record = Record()
        Annotator.add_isbn(record, isbn)
        self._check_field(record, "020", {"a": isbn.identifier})

        # If the identifier isn't an ISBN, but has an equivalent that is, it still
        # works.
        equivalent = db.identifier()
        data_source = DataSource.lookup(db.session, DataSource.OCLC)
        equivalent.equivalent_to(data_source, isbn, 1)
        record = Record()
        Annotator.add_isbn(record, equivalent)
        self._check_field(record, "020", {"a": isbn.identifier})

        # If there is no ISBN, the field is left out.
        non_isbn = db.identifier()
        record = Record()
        Annotator.add_isbn(record, non_isbn)
        assert [] == record.get_fields("020")

    def test_add_title(self, db: DatabaseTransactionFixture):
        edition = db.edition()
        edition.title = "The Good Soldier"
        edition.sort_title = "Good Soldier, The"
        edition.subtitle = "A Tale of Passion"

        record = Record()
        Annotator.add_title(record, edition)
        [field] = record.get_fields("245")
        self._check_field(
            record,
            "245",
            {
                "a": edition.title,
                "b": edition.subtitle,
                "c": edition.author,
            },
            ["0", "4"],
        )

        # If there's no subtitle or no author, those subfields are left out.
        edition.subtitle = None
        edition.author = None

        record = Record()
        Annotator.add_title(record, edition)
        [field] = record.get_fields("245")
        self._check_field(
            record,
            "245",
            {
                "a": edition.title,
            },
            ["0", "4"],
        )
        assert [] == field.get_subfields("b")
        assert [] == field.get_subfields("c")

    def test_add_contributors(self, db: DatabaseTransactionFixture):
        author = "a"
        author2 = "b"
        translator = "c"

        # Edition with one author gets a 100 field and no 700 fields.
        edition = db.edition(authors=[author])
        edition.sort_author = "sorted"

        record = Record()
        Annotator.add_contributors(record, edition)
        assert [] == record.get_fields("700")
        self._check_field(
            record, "100", {"a": edition.primary_author.sort_name}, ["1", " "]
        )

        # Edition with two authors and a translator gets a 100 field for the
        # primary author and 700 fields for the other contributors.
        edition = db.edition(authors=[author, author2])
        edition.add_contributor(translator, Contributor.Role.TRANSLATOR)

        record = Record()
        Annotator.add_contributors(record, edition)
        assert [] != record.get_fields("100")
        self._check_field(
            record, "100", {"a": edition.primary_author.sort_name}, ["1", " "]
        )
        fields = record.get_fields("700")
        for field in fields:
            assert ["1", " "] == field.indicators
        [author2_field, translator_field] = sorted(
            fields, key=lambda x: x.get_subfields("a")[0]
        )
        assert author2 == author2_field.get_subfields("a")[0]
        assert author2_field.get_subfields("e")[0] in {
            "Tekijä",
            "Author",
        }
        assert translator == translator_field.get_subfields("a")[0]
        assert translator_field.get_subfields("e")[0] in {
            "Kääntäjä",
            "Translator",
        }

    def test_add_publisher(self, db: DatabaseTransactionFixture):
        edition = db.edition()
        edition.publisher = db.fresh_str()
        edition.issued = datetime_utc(1894, 4, 5)

        record = Record()
        Annotator.add_publisher(record, edition)
        self._check_field(
            record,
            "264",
            {
                "a": "[Kustannuspaikka tuntematon]",
                "b": edition.publisher,
                "c": "1894",
            },
            [" ", "1"],
        )

        # If there's no publisher, the field is left out.
        record = Record()
        edition.publisher = None
        Annotator.add_publisher(record, edition)
        assert [] == record.get_fields("264")

    def test_add_physical_description(self, db: DatabaseTransactionFixture):
        book = db.edition()
        book.medium = Edition.BOOK_MEDIUM
        audio = db.edition()
        audio.medium = Edition.AUDIO_MEDIUM

        record = Record()
        Annotator.add_physical_description(record, book)
        self._check_field(record, "300", {"a": "verkkoaineisto"})
        self._check_field(
            record,
            "336",
            {
                "a": "teksti",
                "b": "txt",
                "2": "rdacontent",
            },
        )
        self._check_field(
            record,
            "337",
            {
                "a": "tietokonekäyttöinen",
                "b": "c",
                "2": "rdamedia",
            },
        )
        self._check_field(
            record,
            "338",
            {
                "a": "verkkoaineisto",
                "b": "cr",
                "2": "rdacarrier",
            },
        )
        self._check_field(
            record,
            "347",
            {
                "a": "tekstitiedosto",
                "2": "rda",
            },
        )
        self._check_field(
            record,
            "380",
            {
                "a": "e-kirja",
                "2": "tlcgt",
            },
        )

        record = Record()
        Annotator.add_physical_description(record, audio)
        self._check_field(
            record,
            "300",
            {
                "a": "verkkoaineisto",
            },
        )
        self._check_field(
            record,
            "336",
            {
                "a": "puhe",
                "b": "spw",
                "2": "rdacontent",
            },
        )
        self._check_field(
            record,
            "337",
            {
                "a": "tietokonekäyttöinen",
                "b": "c",
                "2": "rdamedia",
            },
        )
        self._check_field(
            record,
            "338",
            {
                "a": "verkkoaineisto",
                "b": "cr",
                "2": "rdacarrier",
            },
        )
        self._check_field(
            record,
            "347",
            {
                "a": "äänitiedosto",
                "2": "rda",
            },
        )
        assert [] == record.get_fields("380")

    def test_add_physical_description_audiobook_duration(
        self, db: DatabaseTransactionFixture
    ):
        audiobook = db.edition()
        audiobook.medium = Edition.AUDIO_MEDIUM
        audiobook.duration = 3723  # 1 hour, 2 minutes, and 3 seconds

        record = Record()
        Annotator.add_physical_description(record, audiobook)

        self._check_field(record, "300", {"a": "verkkoaineisto (1h 2min 3s)"})

    def test_add_audience(self, db: DatabaseTransactionFixture):
        for audience, term in list(Annotator.AUDIENCE_TERMS.items()):
            work = db.work(audience=audience)
            record = Record()
            Annotator.add_audience(record, work)
            self._check_field(
                record,
                "385",
                {
                    "a": term,
                    "2": "tlctarget",
                },
            )

    def test_add_series(self, db: DatabaseTransactionFixture):
        edition = db.edition()
        edition.series = db.fresh_str()
        edition.series_position = 5
        record = Record()
        Annotator.add_series(record, edition)
        self._check_field(
            record,
            "490",
            {
                "a": edition.series,
                "v": str(edition.series_position),
            },
            ["0", " "],
        )

        # If there's no series position, the same field is used without
        # the v subfield.
        edition.series_position = None
        record = Record()
        Annotator.add_series(record, edition)
        self._check_field(
            record,
            "490",
            {
                "a": edition.series,
            },
            ["0", " "],
        )
        [field] = record.get_fields("490")
        assert [] == field.get_subfields("v")

        # If there's no series, the field is left out.
        edition.series = None
        record = Record()
        Annotator.add_series(record, edition)
        assert [] == record.get_fields("490")

    def test_add_formats(self, db: DatabaseTransactionFixture):
        edition, pool = db.edition(with_license_pool=True)
        epub_lcp_drm, ignore = DeliveryMechanism.lookup(
            db.session, Representation.EPUB_MEDIA_TYPE, DeliveryMechanism.LCP_DRM
        )
        pool.delivery_mechanisms[0].delivery_mechanism = epub_lcp_drm
        LicensePoolDeliveryMechanism.set(
            pool.data_source,
            pool.identifier,
            Representation.PDF_MEDIA_TYPE,
            DeliveryMechanism.LCP_DRM,
            RightsStatus.IN_COPYRIGHT,
        )

        record = Record()
        Annotator.add_formats(record, pool)
        fields = record.get_fields("538")
        assert 2 == len(fields)
        [epub, pdf] = sorted(fields, key=lambda x: x.get_subfields("a")[0])
        assert "LCP-suojattu EPUB e-kirja" == epub.get_subfields("a")[0]
        assert [" ", " "] == pdf.indicators
        assert "LCP-suojattu PDF e-kirja" == pdf.get_subfields("a")[0]
        assert [" ", " "] == epub.indicators

    def test_add_summary(self, db: DatabaseTransactionFixture):
        work = db.work(with_license_pool=True)
        work.summary_text = "<p>Summary</p>"

        # Build and validate a record with a `520|a` summary.
        record = Record()
        Annotator.add_summary(record, work)
        self._check_field(record, "520", {"a": " Summary "})
        exported_record = record.as_marc()

        # Round trip the exported record to validate it.
        marc_reader = MARCReader(exported_record)
        round_tripped_record = next(marc_reader)
        self._check_field(round_tripped_record, "520", {"a": " Summary "})

    def test_add_simplified_genres(self, db: DatabaseTransactionFixture):
        work = db.work(with_license_pool=True)
        fantasy, ignore = Genre.lookup(db.session, "Fantasy", autocreate=True)
        romance, ignore = Genre.lookup(db.session, "Romance", autocreate=True)
        work.genres = [fantasy, romance]

        record = Record()
        Annotator.add_genres(record, work)
        fields = record.get_fields("653")
        [fantasy_field, romance_field] = sorted(
            fields, key=lambda x: x.get_subfields("a")[0]
        )
        assert [" ", "0"] == fantasy_field.indicators
        assert fantasy_field.get_subfields("a")[0] in {"Fantasia", "Fantasy"}
        assert [" ", "0"] == romance_field.indicators
        assert romance_field.get_subfields("a")[0] in {"Romantiikka", "Romance"}

    def test_add_keywords_audiobook_and_ebook(self, db: DatabaseTransactionFixture):
        audiobook, ignore = db.edition(with_license_pool=True)
        audiobook.medium = Edition.AUDIO_MEDIUM
        record = Record()

        Annotator.add_keywords(record, audiobook)

        [field] = record.get_fields("655")
        assert field.indicators == ["1", "7"]
        assert field.get_subfields("a") == ["äänikirja"]
        assert field.get_subfields("2") == ["slm/fin"]
        assert field.get_subfields("0") == ["https://urn.fi/URN:NBN:fi:au:slm:s579"]

        ebook, ignore = db.edition(with_license_pool=True)
        ebook.medium = Edition.BOOK_MEDIUM
        record = Record()

        Annotator.add_keywords(record, ebook)

        [field] = record.get_fields("655")
        assert field.indicators == ["1", "7"]
        assert field.get_subfields("a") == ["e-kirja"]
        assert field.get_subfields("2") == ["slm/fin"]
        assert field.get_subfields("0") == []

    def test_add_web_client_urls_empty(self):
        record = MagicMock(spec=Record)
        identifier = MagicMock()
        Annotator.add_web_client_urls(record, identifier, "", "", [])
        assert record.add_field.call_count == 0

    def test_add_web_client_urls(self, db: DatabaseTransactionFixture):
        record = Record()
        identifier = db.identifier()
        short_name = "short_name"
        cm_url = "http://cm.url"
        web_client_urls = ["http://webclient1.url", "http://webclient2.url"]
        Annotator.add_web_client_urls(
            record, identifier, short_name, cm_url, web_client_urls
        )
        fields = record.get_fields("856")
        assert len(fields) == 2
        [field1, field2] = fields
        assert field1.indicators == ["4", "0"]
        assert field2.indicators == ["4", "0"]

        # The URL for a work is constructed as:
        # - <cm-base>/<lib-short-name>/works/<qualified-identifier>
        work_link_template = "{cm_base}/{lib}/works/{qid}"
        # It is then encoded and the web client URL is constructed in this form:
        # - <web-client-base>/book/<encoded-work-url>
        client_url_template = "{client_base}/book/{work_link}"

        qualified_identifier = urllib.parse.quote(
            identifier.type + "/" + identifier.identifier, safe=""
        )

        expected_work_link = work_link_template.format(
            cm_base=cm_url, lib=short_name, qid=qualified_identifier
        )
        encoded_work_link = urllib.parse.quote(expected_work_link, safe="")

        expected_client_url_1 = client_url_template.format(
            client_base=web_client_urls[0], work_link=encoded_work_link
        )
        expected_client_url_2 = client_url_template.format(
            client_base=web_client_urls[1], work_link=encoded_work_link
        )

        # A few checks to ensure that our setup is useful.
        assert web_client_urls[0] != web_client_urls[1]
        assert expected_client_url_1 != expected_client_url_2
        assert expected_client_url_1.startswith(web_client_urls[0])
        assert expected_client_url_2.startswith(web_client_urls[1])

        assert field1.get_subfields("u")[0] == expected_client_url_1
        assert field2.get_subfields("u")[0] == expected_client_url_2
