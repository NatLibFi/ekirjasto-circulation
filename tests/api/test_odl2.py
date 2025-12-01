import copy
import datetime
import functools
import json
import uuid

import pytest
from freezegun import freeze_time

from api.circulation_exceptions import PatronHoldLimitReached, PatronLoanLimitReached
from api.odl2 import ODL2HoldReaper, ODL2Importer, ODL2LoanReaper
from core.coverage import CoverageFailure
from core.model import (
    Collection,
    Contribution,
    Contributor,
    DataSource,
    DeliveryMechanism,
    Edition,
    EditionConstants,
    Hold,
    LicensePool,
    LicensePoolDeliveryMechanism,
    Loan,
    MediaTypes,
    Work,
)
from core.model.constants import IdentifierConstants
from core.model.licensing import DeliveryMechanism, LicensePool, LicenseStatus
from core.model.resource import Hyperlink
from core.util.datetime_helpers import utc_now
from tests.fixtures.api_odl import (
    LicenseHelper,
    LicenseInfoHelper,
    MockGet,
    ODLAPIFilesFixture,
)
from tests.fixtures.database import DatabaseTransactionFixture
from tests.fixtures.odl import ODL2ApiFixture


class TestODL2Importer:
    @staticmethod
    def _get_delivery_mechanism_by_drm_scheme_and_content_type(
        delivery_mechanisms: list[LicensePoolDeliveryMechanism],
        content_type: str,
        drm_scheme: str | None,
    ) -> DeliveryMechanism | None:
        """Find a license pool in the list by its identifier.

        :param delivery_mechanisms: List of delivery mechanisms
        :param content_type: Content type
        :param drm_scheme: DRM scheme

        :return: Delivery mechanism with the specified DRM scheme and content type (if any)
        """
        for delivery_mechanism in delivery_mechanisms:
            mechanism = delivery_mechanism.delivery_mechanism

            if (
                mechanism.drm_scheme == drm_scheme
                and mechanism.content_type == content_type
            ):
                return mechanism

        return None

    @freeze_time("2016-01-01T00:00:00+00:00")
    def test_import(
        self,
        odl2_importer: ODL2Importer,
        odl_mock_get: MockGet,
        api_odl_files_fixture: ODLAPIFilesFixture,
    ) -> None:
        """Ensure that ODL2Importer2 correctly processes and imports the ODL feed encoded using OPDS 2.x.

        NOTE: `freeze_time` decorator is required to treat the licenses in the ODL feed as non-expired.
        """
        # Arrange
        moby_dick_license = LicenseInfoHelper(
            license=LicenseHelper(
                identifier="urn:uuid:f7847120-fc6f-11e3-8158-56847afe9799",
                concurrency=10,
                checkouts=30,
                expires="2016-04-25T12:25:21+02:00",
            ),
            left=30,
            available=10,
        )

        odl_mock_get.add(moby_dick_license)
        feed = api_odl_files_fixture.sample_text("feed.json")

        config = odl2_importer.collection.integration_configuration
        odl2_importer.ignored_identifier_types = [IdentifierConstants.URI]
        DatabaseTransactionFixture.set_settings(
            config, odl2_skipped_license_formats=["text/html"]
        )

        # Act
        imported_editions, pools, works, failures = odl2_importer.import_from_feed(feed)

        # Assert

        # 1. Make sure that there is a single edition only
        assert isinstance(imported_editions, list)
        assert 1 == len(imported_editions)

        [moby_dick_edition] = imported_editions
        assert isinstance(moby_dick_edition, Edition)
        assert moby_dick_edition.primary_identifier.identifier == "978-3-16-148410-0"
        assert moby_dick_edition.primary_identifier.type == "ISBN"
        assert Hyperlink.SAMPLE in {
            l.rel for l in moby_dick_edition.primary_identifier.links
        }

        assert "Moby-Dick" == moby_dick_edition.title
        assert not moby_dick_edition.subtitle
        assert "eng" == moby_dick_edition.language
        assert "eng" == moby_dick_edition.language
        assert EditionConstants.BOOK_MEDIUM == moby_dick_edition.medium
        assert "Herman Melville" == moby_dick_edition.author

        assert 1 == len(moby_dick_edition.author_contributors)
        [moby_dick_author] = moby_dick_edition.author_contributors
        assert isinstance(moby_dick_author, Contributor)
        assert "Herman Melville" == moby_dick_author.display_name
        assert "Melville, Herman" == moby_dick_author.sort_name

        assert 1 == len(moby_dick_author.contributions)
        [moby_dick_author_author_contribution] = moby_dick_author.contributions
        assert isinstance(moby_dick_author_author_contribution, Contribution)
        assert moby_dick_author == moby_dick_author_author_contribution.contributor
        assert moby_dick_edition == moby_dick_author_author_contribution.edition
        assert Contributor.Role.AUTHOR == moby_dick_author_author_contribution.role

        assert "Feedbooks" == moby_dick_edition.data_source.name

        assert "Test Publisher" == moby_dick_edition.publisher
        assert datetime.date(2015, 9, 29) == moby_dick_edition.published

        assert "http://example.org/cover.jpg" == moby_dick_edition.cover_full_url
        assert (
            "http://example.org/cover-small.jpg"
            == moby_dick_edition.cover_thumbnail_url
        )

        # 2. Make sure that license pools have correct configuration
        assert isinstance(pools, list)
        assert 1 == len(pools)

        [moby_dick_license_pool] = pools
        assert isinstance(moby_dick_license_pool, LicensePool)
        assert moby_dick_license_pool.identifier.identifier == "978-3-16-148410-0"
        assert moby_dick_license_pool.identifier.type == "ISBN"
        assert not moby_dick_license_pool.open_access
        assert (
            10 == moby_dick_license_pool.licenses_owned
        )  # There may be 30 left but 10 concurrent users
        assert 10 == moby_dick_license_pool.licenses_available

        assert 2 == len(moby_dick_license_pool.delivery_mechanisms)

        moby_dick_epub_adobe_drm_delivery_mechanism = (
            self._get_delivery_mechanism_by_drm_scheme_and_content_type(
                moby_dick_license_pool.delivery_mechanisms,
                MediaTypes.EPUB_MEDIA_TYPE,
                DeliveryMechanism.ADOBE_DRM,
            )
        )
        assert moby_dick_epub_adobe_drm_delivery_mechanism is not None

        moby_dick_epub_lcp_drm_delivery_mechanism = (
            self._get_delivery_mechanism_by_drm_scheme_and_content_type(
                moby_dick_license_pool.delivery_mechanisms,
                MediaTypes.EPUB_MEDIA_TYPE,
                DeliveryMechanism.LCP_DRM,
            )
        )
        assert moby_dick_epub_lcp_drm_delivery_mechanism is not None

        assert 1 == len(moby_dick_license_pool.licenses)
        [moby_dick_license] = moby_dick_license_pool.licenses  # type: ignore
        assert (
            "urn:uuid:f7847120-fc6f-11e3-8158-56847afe9799"
            == moby_dick_license.identifier  # type: ignore
        )
        assert (
            "http://www.example.com/get{?id,checkout_id,expires,patron_id,passphrase,hint,hint_url,notification_url}"
            == moby_dick_license.checkout_url  # type: ignore
        )
        assert "http://www.example.com/status/294024" == moby_dick_license.status_url  # type: ignore
        assert (
            datetime.datetime(2016, 4, 25, 10, 25, 21, tzinfo=datetime.timezone.utc)
            == moby_dick_license.expires  # type: ignore
        )
        assert 30 == moby_dick_license.checkouts_left  # type: ignore
        assert 10 == moby_dick_license.checkouts_available  # type: ignore

        # 3. Make sure that work objects contain all the required metadata
        assert isinstance(works, list)
        assert 1 == len(works)

        [moby_dick_work] = works
        assert isinstance(moby_dick_work, Work)
        assert moby_dick_edition == moby_dick_work.presentation_edition
        assert 1 == len(moby_dick_work.license_pools)
        assert moby_dick_license_pool == moby_dick_work.license_pools[0]

        # The entry contained one subject that maps to a genre
        assert 1 == len(moby_dick_work.genres)
        assert "Children" == moby_dick_work.audience
        assert True == moby_dick_work.fiction

        # 4. Make sure that the failure is covered
        assert 1 == len(failures)
        huck_finn_failures = failures["9781234567897"]

        assert isinstance(huck_finn_failures, list)
        assert 1 == len(huck_finn_failures)
        [huck_finn_failure] = huck_finn_failures
        assert isinstance(huck_finn_failure, CoverageFailure)
        assert "9781234567897" == huck_finn_failure.obj.identifier

        assert "2 validation errors" in huck_finn_failure.exception

    @freeze_time("2016-01-01T00:00:00+00:00")
    def test_import_from_feed_ellibs(
        self,
        odl2_importer: ODL2Importer,
        odl_mock_get: MockGet,
        api_odl_files_fixture: ODLAPIFilesFixture,
    ) -> None:
        """Ensure that ODL2Importer2 correctly processes and imports the ODL feed rom Ellibs encoded using OPDS 2.x.

        NOTE: `freeze_time` decorator is required to treat the licenses in the ODL feed as non-expired.
        """
        # Arrange
        maahan_katketty_license = LicenseInfoHelper(
            license=LicenseHelper(
                identifier="urn:license:222",
                concurrency=1,
            ),
            available=1,
        )

        odl_mock_get.add(maahan_katketty_license)
        feed = api_odl_files_fixture.sample_text("ellibs_feed_single_publication.json")

        config = odl2_importer.collection.integration_configuration
        odl2_importer.ignored_identifier_types = [IdentifierConstants.URI]
        DatabaseTransactionFixture.set_settings(
            config, odl2_skipped_license_formats=["text/html"]
        )

        # Act
        imported_editions, pools, works, failures = odl2_importer.import_from_feed(feed)

        # Assert

        # 1. Make sure that there is a single edition only
        assert isinstance(imported_editions, list)
        assert 1 == len(imported_editions)

        [maahan_katketty_edition] = imported_editions
        assert isinstance(maahan_katketty_edition, Edition)
        assert maahan_katketty_edition.primary_identifier.identifier == "9789512445448"
        assert maahan_katketty_edition.primary_identifier.type == "ISBN"

        assert "Maahan kätketty" == maahan_katketty_edition.title
        assert not maahan_katketty_edition.subtitle
        assert "fin" == maahan_katketty_edition.language
        assert EditionConstants.BOOK_MEDIUM == maahan_katketty_edition.medium
        assert "John Ajvide Lindqvist" == maahan_katketty_edition.author

        assert 1 == len(maahan_katketty_edition.author_contributors)
        [maahan_katketty_author] = maahan_katketty_edition.author_contributors
        assert isinstance(maahan_katketty_author, Contributor)
        assert "John Ajvide Lindqvist" == maahan_katketty_author.display_name
        assert "Lindqvist, John Ajvide" == maahan_katketty_author.sort_name

        assert 1 == len(maahan_katketty_author.contributions)
        [
            maahan_katketty_author_author_contribution
        ] = maahan_katketty_author.contributions
        assert isinstance(maahan_katketty_author_author_contribution, Contribution)
        assert (
            maahan_katketty_author
            == maahan_katketty_author_author_contribution.contributor
        )
        assert (
            maahan_katketty_edition
            == maahan_katketty_author_author_contribution.edition
        )
        assert (
            Contributor.Role.AUTHOR == maahan_katketty_author_author_contribution.role
        )

        assert (
            "Feedbooks" == maahan_katketty_edition.data_source.name
        )  # Based on some default value in tests. It's based on what's set as the collection's datasource in the admin UI.

        assert "Gummerus" == maahan_katketty_edition.publisher
        assert (
            datetime.date(2025, 1, 1) == maahan_katketty_edition.published
        )  # The data constains only year

        assert (
            "https://www.library.com/sites/default/files/imagecache/product_full/bookcover_9789512445448.jpg"
            == maahan_katketty_edition.cover_full_url
        )
        assert (
            "https://www.library.com/sites/default/files/imagecache/product/bookcover_9789512445448.jpg"
            == maahan_katketty_edition.cover_thumbnail_url
        )

        # 2. Make sure that license pools and licenses have correct configuration
        assert isinstance(pools, list)
        assert 1 == len(pools)

        [maahan_katketty_licensepool] = pools
        assert isinstance(maahan_katketty_licensepool, LicensePool)
        assert (
            maahan_katketty_licensepool.identifier.identifier == "9789512445448"
        )  # Same as the book's ISBN
        assert maahan_katketty_licensepool.identifier.type == "ISBN"
        assert not maahan_katketty_licensepool.open_access
        assert 1 == maahan_katketty_licensepool.licenses_owned
        assert 1 == maahan_katketty_licensepool.licenses_available

        assert 1 == len(maahan_katketty_licensepool.licenses)
        [maahan_katketty_license] = maahan_katketty_licensepool.licenses  # type: ignore
        assert "urn:license:222" == maahan_katketty_license.identifier  # type: ignore
        assert (
            "https://example.com/odl-test/get.php{?id,checkout_id,expires,patron_id,notification_url,passphrase}"
            == maahan_katketty_license.checkout_url  # type: ignore
        )
        assert "https://example.com/odl-test/status.php?license_id=222" == maahan_katketty_license.status_url  # type: ignore
        assert 1 == maahan_katketty_license.checkouts_available  # type: ignore

        # 3. Make our delivery mechanisms are set
        assert 1 == len(maahan_katketty_licensepool.delivery_mechanisms)

        maahan_katketty_epub_lcp_drm_delivery_mechanism = (
            self._get_delivery_mechanism_by_drm_scheme_and_content_type(
                maahan_katketty_licensepool.delivery_mechanisms,
                MediaTypes.EPUB_MEDIA_TYPE,
                DeliveryMechanism.LCP_DRM,
            )
        )
        assert maahan_katketty_epub_lcp_drm_delivery_mechanism is not None

        # 4. Make sure that work objects contain all the required metadata
        assert isinstance(works, list)
        assert 1 == len(works)

        [maahan_katketty_work] = works
        assert isinstance(maahan_katketty_work, Work)
        assert maahan_katketty_edition == maahan_katketty_work.presentation_edition
        assert 1 == len(maahan_katketty_work.license_pools)
        assert maahan_katketty_licensepool == maahan_katketty_work.license_pools[0]

        # The entry contained two subjects that maps to a genre
        assert 2 == len(maahan_katketty_work.genres)
        assert "Adult" == maahan_katketty_work.audience
        assert True == maahan_katketty_work.fiction

    @freeze_time("2016-01-01T00:00:00+00:00")
    def test_import_audiobook_with_streaming(
        self,
        db: DatabaseTransactionFixture,
        odl2_importer: ODL2Importer,
        odl_mock_get: MockGet,
        api_odl_files_fixture: ODLAPIFilesFixture,
    ) -> None:
        """Ensure that ODL2Importer2 correctly processes and imports a feed with an audiobook."""
        license = api_odl_files_fixture.sample_text("license-audiobook.json")
        feed = api_odl_files_fixture.sample_text("feed-audiobook-streaming.json")
        odl_mock_get.add(license)

        db.set_settings(
            odl2_importer.collection.integration_configuration,
            odl2_skipped_license_formats=["text/html"],
        )

        imported_editions, pools, works, failures = odl2_importer.import_from_feed(feed)

        # Make sure we imported one edition and it is an audiobook
        assert isinstance(imported_editions, list)
        assert 1 == len(imported_editions)

        [edition] = imported_editions
        assert isinstance(edition, Edition)
        assert edition.primary_identifier.identifier == "9780792766919"
        assert edition.primary_identifier.type == "ISBN"
        assert EditionConstants.AUDIO_MEDIUM == edition.medium

        # Make sure that license pools have correct configuration
        assert isinstance(pools, list)
        assert 1 == len(pools)

        [license_pool] = pools
        assert not license_pool.open_access
        assert 1 == license_pool.licenses_owned
        assert 1 == license_pool.licenses_available

        assert 2 == len(license_pool.delivery_mechanisms)

        lcp_delivery_mechanism = (
            self._get_delivery_mechanism_by_drm_scheme_and_content_type(
                license_pool.delivery_mechanisms,
                MediaTypes.AUDIOBOOK_PACKAGE_LCP_MEDIA_TYPE,
                DeliveryMechanism.LCP_DRM,
            )
        )
        assert lcp_delivery_mechanism is not None

        feedbooks_delivery_mechanism = (
            self._get_delivery_mechanism_by_drm_scheme_and_content_type(
                license_pool.delivery_mechanisms,
                MediaTypes.AUDIOBOOK_MANIFEST_MEDIA_TYPE,
                DeliveryMechanism.FEEDBOOKS_AUDIOBOOK_DRM,
            )
        )
        assert feedbooks_delivery_mechanism is not None

    @freeze_time("2016-01-01T00:00:00+00:00")
    def test_import_audiobook_no_streaming(
        self,
        odl2_importer: ODL2Importer,
        odl_mock_get: MockGet,
        api_odl_files_fixture: ODLAPIFilesFixture,
    ) -> None:
        """
        Ensure that ODL2Importer2 correctly processes and imports a feed with an audiobook
        that is not available for streaming.
        """
        license = api_odl_files_fixture.sample_text("license-audiobook.json")
        feed = api_odl_files_fixture.sample_text("feed-audiobook-no-streaming.json")
        odl_mock_get.add(license)

        imported_editions, pools, works, failures = odl2_importer.import_from_feed(feed)

        # Make sure we imported one edition and it is an audiobook
        assert isinstance(imported_editions, list)
        assert 1 == len(imported_editions)

        [edition] = imported_editions
        assert isinstance(edition, Edition)
        assert edition.primary_identifier.identifier == "9781603937221"
        assert edition.primary_identifier.type == "ISBN"
        assert EditionConstants.AUDIO_MEDIUM == edition.medium

        # Make sure that license pools have correct configuration
        assert isinstance(pools, list)
        assert 1 == len(pools)

        [license_pool] = pools
        assert not license_pool.open_access
        assert 1 == license_pool.licenses_owned
        assert 1 == license_pool.licenses_available

        assert 1 == len(license_pool.delivery_mechanisms)

        lcp_delivery_mechanism = (
            self._get_delivery_mechanism_by_drm_scheme_and_content_type(
                license_pool.delivery_mechanisms,
                MediaTypes.AUDIOBOOK_PACKAGE_LCP_MEDIA_TYPE,
                DeliveryMechanism.LCP_DRM,
            )
        )
        assert lcp_delivery_mechanism is not None

    @freeze_time("2016-01-01T00:00:00+00:00")
    def test_import_open_access(
        self,
        odl2_importer: ODL2Importer,
        api_odl_files_fixture: ODLAPIFilesFixture,
    ) -> None:
        """
        Ensure that ODL2Importer2 correctly processes and imports a feed with an
        open access book.
        """
        feed = api_odl_files_fixture.sample_text("open-access-title.json")
        imported_editions, pools, works, failures = odl2_importer.import_from_feed(feed)

        assert isinstance(imported_editions, list)
        assert 1 == len(imported_editions)

        [edition] = imported_editions
        assert isinstance(edition, Edition)
        assert (
            edition.primary_identifier.identifier
            == "https://www.feedbooks.com/book/7256"
        )
        assert edition.primary_identifier.type == "URI"
        assert edition.medium == EditionConstants.BOOK_MEDIUM

        # Make sure that license pools have correct configuration
        assert isinstance(pools, list)
        assert 1 == len(pools)

        [license_pool] = pools
        assert license_pool.open_access is True

        assert 1 == len(license_pool.delivery_mechanisms)

        oa_ebook_delivery_mechanism = (
            self._get_delivery_mechanism_by_drm_scheme_and_content_type(
                license_pool.delivery_mechanisms,
                MediaTypes.EPUB_MEDIA_TYPE,
                None,
            )
        )
        assert oa_ebook_delivery_mechanism is not None

    @freeze_time("2016-01-01T00:00:00+00:00")
    def test_import_availability(
        self,
        odl2_importer: ODL2Importer,
        odl_mock_get: MockGet,
        api_odl_files_fixture: ODLAPIFilesFixture,
    ) -> None:
        feed_json = json.loads(api_odl_files_fixture.sample_text("feed.json"))

        moby_dick_license_dict = feed_json["publications"][0]["licenses"][0]
        test_book_license_dict = feed_json["publications"][2]["licenses"][0]

        huck_finn_publication_dict = feed_json["publications"][1]
        huck_finn_publication_dict["licenses"] = copy.deepcopy(
            feed_json["publications"][0]["licenses"]
        )
        huck_finn_publication_dict["images"] = copy.deepcopy(
            feed_json["publications"][0]["images"]
        )
        huck_finn_license_dict = huck_finn_publication_dict["licenses"][0]

        MOBY_DICK_LICENSE_ID = "urn:uuid:f7847120-fc6f-11e3-8158-56847afe9799"
        TEST_BOOK_LICENSE_ID = "urn:uuid:f7847120-fc6f-11e3-8158-56847afe9798"
        HUCK_FINN_LICENSE_ID = f"urn:uuid:{uuid.uuid4()}"

        test_book_license_dict["metadata"]["availability"] = {
            "state": "unavailable",
            "reason": "https://registry.opds.io/reason#preordered",
            "until": "2016-01-20T00:00:00Z",
        }
        huck_finn_license_dict["metadata"]["identifier"] = HUCK_FINN_LICENSE_ID
        huck_finn_publication_dict["metadata"][
            "title"
        ] = "Adventures of Huckleberry Finn"

        # Mock responses from license status server
        def license_status_reply(
            license_id: str,
            concurrency: int = 10,
            checkouts: int | None = 30,
            expires: str | None = "2016-04-25T12:25:21+02:00",
        ) -> LicenseInfoHelper:
            return LicenseInfoHelper(
                license=LicenseHelper(
                    identifier=license_id,
                    concurrency=concurrency,
                    checkouts=checkouts,
                    expires=expires,
                ),
                left=checkouts,
                available=concurrency,
            )

        odl_mock_get.add(license_status_reply(MOBY_DICK_LICENSE_ID))
        odl_mock_get.add(license_status_reply(HUCK_FINN_LICENSE_ID))

        (
            imported_editions,
            pools,
            works,
            failures,
        ) = odl2_importer.import_from_feed(json.dumps(feed_json))

        assert isinstance(pools, list)
        assert 3 == len(pools)

        [moby_dick_pool, huck_finn_pool, test_book_pool] = pools

        def assert_pool(
            pool: LicensePool,
            identifier: str,
            identifier_type: str,
            licenses_owned: int,
            licenses_available: int,
            license_id: str,
            available_for_borrowing: bool,
            license_status: LicenseStatus,
        ) -> None:
            assert pool.identifier.identifier == identifier
            assert pool.identifier.type == identifier_type
            assert pool.licenses_owned == licenses_owned
            assert pool.licenses_available == licenses_available
            assert len(pool.licenses) == 1
            [license_info] = pool.licenses
            assert license_info.identifier == license_id
            assert license_info.is_available_for_borrowing is available_for_borrowing
            assert license_info.status == license_status

        assert_moby_dick_pool = functools.partial(
            assert_pool,
            identifier="978-3-16-148410-0",
            identifier_type="ISBN",
            license_id=MOBY_DICK_LICENSE_ID,
        )
        assert_test_book_pool = functools.partial(
            assert_pool,
            identifier="http://example.org/test-book",
            identifier_type="URI",
            license_id=TEST_BOOK_LICENSE_ID,
        )
        assert_huck_finn_pool = functools.partial(
            assert_pool,
            identifier="9781234567897",
            identifier_type="ISBN",
            license_id=HUCK_FINN_LICENSE_ID,
        )

        assert_moby_dick_pool(
            moby_dick_pool,
            licenses_owned=10,
            licenses_available=10,
            available_for_borrowing=True,
            license_status=LicenseStatus.available,
        )

        assert_test_book_pool(
            test_book_pool,
            licenses_owned=0,
            licenses_available=0,
            available_for_borrowing=False,
            license_status=LicenseStatus.unavailable,
        )

        assert_huck_finn_pool(
            huck_finn_pool,
            licenses_owned=10,
            licenses_available=10,
            available_for_borrowing=True,
            license_status=LicenseStatus.available,
        )

        # Harvest the feed again, but this time the status has changed
        moby_dick_license_dict["metadata"]["availability"] = {
            "state": "unavailable",
        }
        del test_book_license_dict["metadata"]["availability"]
        huck_finn_publication_dict["metadata"]["availability"] = {
            "state": "unavailable",
        }

        # Mock responses from license status server
        odl_mock_get.add(license_status_reply(TEST_BOOK_LICENSE_ID))

        # Harvest the feed again
        (
            imported_editions,
            pools,
            works,
            failures,
        ) = odl2_importer.import_from_feed(json.dumps(feed_json))

        assert isinstance(pools, list)
        assert 3 == len(pools)

        [moby_dick_pool, huck_finn_pool, test_book_pool] = pools

        assert_moby_dick_pool(
            moby_dick_pool,
            licenses_owned=0,
            licenses_available=0,
            available_for_borrowing=False,
            license_status=LicenseStatus.unavailable,
        )

        assert_test_book_pool(
            test_book_pool,
            licenses_owned=10,
            licenses_available=10,
            available_for_borrowing=True,
            license_status=LicenseStatus.available,
        )

        assert_huck_finn_pool(
            huck_finn_pool,
            licenses_owned=0,
            licenses_available=0,
            available_for_borrowing=False,
            license_status=LicenseStatus.unavailable,
        )

    def test_import_accessibility_success(
        self,
        odl2_importer: ODL2Importer,
        odl_mock_get: MockGet,
        api_odl_files_fixture: ODLAPIFilesFixture,
    ) -> None:
        license = LicenseInfoHelper(
            license=LicenseHelper(
                identifier="urn:uuid:111",
                concurrency=1,
                expires="2027-01-15",
            ),
            available=1,
        )
        odl_mock_get.add(license)
        feed = api_odl_files_fixture.sample_text("dm_feed_accessibility.json")
        config = odl2_importer.collection.integration_configuration

        # Act
        imported_editions, pools, works, failures = odl2_importer.import_from_feed(feed)

        [book] = imported_editions

        # Check that the relevant fields have mappings.
        assert book.accessibility.conforms_to == [
            "This publication meets minimum accessibility standards"
        ]
        assert book.accessibility.ways_of_reading == [
            "Has alternative text",
            "Readable in read aloud or dynamic braille",
        ]


class TestODL2API:
    def test_loan_limit(self, odl2_api_fixture: ODL2ApiFixture):
        """Test the loan limit collection setting"""
        # Set the loan limit
        odl2_api_fixture.api.loan_limit = 1

        with odl2_api_fixture.mock_http.patch():
            response = odl2_api_fixture.checkout(
                patron=odl2_api_fixture.patron,
                pool=odl2_api_fixture.work.active_license_pool(),
                create_loan=True,
            )
        # Did the loan take place correctly?
        assert (
            response.identifier
            == odl2_api_fixture.work.presentation_edition.primary_identifier.identifier
        )

        # Second loan for the patron should fail due to the loan limit
        work2: Work = odl2_api_fixture.create_work(odl2_api_fixture.collection)
        with pytest.raises(PatronLoanLimitReached) as exc:
            with odl2_api_fixture.mock_http.patch():
                odl2_api_fixture.checkout(
                    patron=odl2_api_fixture.patron, pool=work2.active_license_pool()
                )
        assert exc.value.limit == 1

    def test_hold_limit(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2ApiFixture
    ):
        """Test the hold limit collection setting"""
        # Set the hold limit
        odl2_api_fixture.api.hold_limit = 1

        patron1 = db.patron()

        # First checkout with patron1, then place a hold with the test patron
        pool = odl2_api_fixture.work.active_license_pool()
        with odl2_api_fixture.mock_http.patch():
            response = odl2_api_fixture.checkout(patron=patron1, pool=pool)
        assert (
            response.identifier
            == odl2_api_fixture.work.presentation_edition.primary_identifier.identifier
        )

        hold_response = odl2_api_fixture.place_hold(
            odl2_api_fixture.patron, pool, create_hold=True
        )
        # Hold was successful
        assert hold_response.hold_position == 1

        # Second work should fail for the test patron due to the hold limit
        work2: Work = odl2_api_fixture.create_work(odl2_api_fixture.collection)
        # Generate a license
        odl2_api_fixture.setup_license(work2)

        # Do the same, patron1 checkout and test patron hold
        pool = work2.active_license_pool()
        with odl2_api_fixture.mock_http.patch():
            response = odl2_api_fixture.checkout(patron=patron1, pool=pool)
        assert (
            response.identifier
            == work2.presentation_edition.primary_identifier.identifier
        )

        # Hold should fail
        with pytest.raises(PatronHoldLimitReached) as exc:
            odl2_api_fixture.place_hold(odl2_api_fixture.patron, pool)
        assert exc.value.limit == 1


class TestODL2LoanReaper:
    def test_run_once(
        self, odl2_api_fixture: ODL2ApiFixture, db: DatabaseTransactionFixture
    ):
        data_source = DataSource.lookup(db.session, "Feedbooks", autocreate=True)
        DatabaseTransactionFixture.set_settings(
            odl2_api_fixture.collection.integration_configuration,
            **{Collection.DATA_SOURCE_NAME_SETTING: data_source.name},
        )
        reaper = ODL2LoanReaper(
            db.session, odl2_api_fixture.collection, api=odl2_api_fixture.api
        )

        now = utc_now()
        yesterday = now - datetime.timedelta(days=1)
        tomorrow = now + datetime.timedelta(days=1)

        # License with all its checkouts on loan to 4 patrons.
        odl2_api_fixture.setup_license(concurrency=4, available=0)
        expired_loan1, ignore = odl2_api_fixture.license.loan_to(
            db.patron(), end=yesterday
        )
        expired_loan2, ignore = odl2_api_fixture.license.loan_to(
            db.patron(), end=yesterday
        )
        current_loan, ignore = odl2_api_fixture.license.loan_to(
            db.patron(), end=tomorrow
        )
        very_old_loan, ignore = odl2_api_fixture.license.loan_to(
            db.patron(), start=now - datetime.timedelta(days=90), end=None
        )
        assert 4 == db.session.query(Loan).count()
        assert 0 == odl2_api_fixture.pool.licenses_available

        progress = reaper.run_once(reaper.timestamp().to_data())

        # The expired loans have been deleted and the current loan remains.
        assert 1 == db.session.query(Loan).count()

        assert 3 == odl2_api_fixture.pool.licenses_available

        # The TimestampData returned reflects what work was done.
        assert "Loans deleted: 3. License pools updated: 1" == progress.achievements

        # The TimestampData does not include any timing information --
        # that will be applied by run().
        assert None == progress.start
        assert None == progress.finish


class TestODL2HoldReaper:
    def test_run_once(
        self, odl2_api_fixture: ODL2ApiFixture, db: DatabaseTransactionFixture
    ):
        pool = odl2_api_fixture.work.active_license_pool()

        data_source = DataSource.lookup(db.session, "Feedbooks", autocreate=True)
        DatabaseTransactionFixture.set_settings(
            odl2_api_fixture.collection.integration_configuration,
            **{Collection.DATA_SOURCE_NAME_SETTING: data_source.name},
        )
        reaper = ODL2HoldReaper(
            db.session, odl2_api_fixture.collection, api=odl2_api_fixture.api
        )

        now = utc_now()
        yesterday = now - datetime.timedelta(days=1)

        odl2_api_fixture.setup_license(concurrency=3, available=3)
        expired_hold1, ignore = pool.on_hold_to(  # type:ignore
            db.patron(), end=yesterday, position=0
        )
        expired_hold2, ignore = pool.on_hold_to(  # type:ignore
            db.patron(), end=yesterday, position=0
        )
        current_hold, ignore = pool.on_hold_to(db.patron(), position=3)  # type:ignore
        # This hold has an end date in the past, but its position is greater than 0
        # so the end date is not reliable.
        bad_end_date, ignore = pool.on_hold_to(  # type:ignore
            db.patron(), end=yesterday, position=4
        )
        # This hold has no end date.
        no_end_date, ignore = pool.on_hold_to(  # type:ignore
            db.patron(), end=None, position=0
        )

        progress = reaper.run_once(reaper.timestamp().to_data())

        # The expired holds have been deleted and the other holds have been updated to be reserved.
        assert 2 == db.session.query(Hold).count()
        assert [current_hold, bad_end_date] == db.session.query(Hold).order_by(
            Hold.start
        ).all()
        assert 0 == current_hold.position
        assert 0 == bad_end_date.position
        assert current_hold.end > now
        assert bad_end_date.end > now
        assert 1 == pool.licenses_available  # type:ignore
        assert 2 == pool.licenses_reserved  # type:ignore

        # The TimestampData returned reflects what work was done.
        assert "Holds deleted: 3. License pools updated: 1" == progress.achievements

        # The TimestampData does not include any timing information --
        # that will be applied by run().
        assert None == progress.start
        assert None == progress.finish
