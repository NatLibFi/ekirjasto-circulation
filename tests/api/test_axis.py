from __future__ import annotations

import datetime
import json
import socket
import ssl
import urllib
from functools import partial
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, Mock, PropertyMock

import pytest

from api.axis import (
    AudiobookMetadataParser,
    Axis360AcsFulfillmentInfo,
    Axis360API,
    Axis360APIConstants,
    Axis360BibliographicCoverageProvider,
    Axis360CirculationMonitor,
    Axis360FulfillmentInfo,
    Axis360FulfillmentInfoResponseParser,
    Axis360Settings,
    AxisCollectionReaper,
    AxisNowManifest,
    BibliographicParser,
    CheckinResponseParser,
    CheckoutResponseParser,
    HoldReleaseResponseParser,
    HoldResponseParser,
    JSONResponseParser,
)
from api.circulation_exceptions import *
from api.web_publication_manifest import FindawayManifest, SpineItem
from core.analytics import Analytics
from core.coverage import CoverageFailure
from core.integration.base import integration_settings_update
from core.metadata_layer import (
    CirculationData,
    ContributorData,
    IdentifierData,
    Metadata,
    SubjectData,
    TimestampData,
)
from core.mock_analytics_provider import MockAnalyticsProvider
from core.model import (
    Collection,
    Contributor,
    DataSource,
    DeliveryMechanism,
    Edition,
    ExternalIntegration,
    Hyperlink,
    Identifier,
    LinkRelations,
    MediaTypes,
    Representation,
    Subject,
    create,
)
from core.scripts import RunCollectionCoverageProviderScript
from core.util.datetime_helpers import datetime_utc, utc_now
from core.util.flask_util import Response
from core.util.http import RemoteIntegrationException
from core.util.problem_detail import ProblemDetail, ProblemError
from tests.api.mockapi.axis import MockAxis360API

if TYPE_CHECKING:
    from tests.fixtures.api_axis_files import AxisFilesFixture
    from tests.fixtures.authenticator import SimpleAuthIntegrationFixture
    from tests.fixtures.database import DatabaseTransactionFixture


class Axis360Fixture:
    # Sample bibliographic and availability data you can use in a test
    # without having to parse it from an XML file.
    BIBLIOGRAPHIC_DATA = Metadata(
        DataSource.AXIS_360,
        publisher="Random House Inc",
        language="eng",
        title="Faith of My Fathers : A Family Memoir",
        imprint="Random House Inc2",
        published=datetime_utc(2000, 3, 7, 0, 0),
        primary_identifier=IdentifierData(
            type=Identifier.AXIS_360_ID, identifier="0003642860"
        ),
        identifiers=[IdentifierData(type=Identifier.ISBN, identifier="9780375504587")],
        contributors=[
            ContributorData(
                sort_name="McCain, John", roles=[Contributor.PRIMARY_AUTHOR_ROLE]
            ),
            ContributorData(sort_name="Salter, Mark", roles=[Contributor.AUTHOR_ROLE]),
        ],
        subjects=[
            SubjectData(
                type=Subject.BISAC, identifier="BIOGRAPHY & AUTOBIOGRAPHY / Political"
            ),
            SubjectData(type=Subject.FREEFORM_AUDIENCE, identifier="Adult"),
        ],
    )

    AVAILABILITY_DATA = CirculationData(
        data_source=DataSource.AXIS_360,
        primary_identifier=BIBLIOGRAPHIC_DATA.primary_identifier,
        licenses_owned=9,
        licenses_available=8,
        licenses_reserved=0,
        patrons_in_hold_queue=0,
        last_checked=datetime_utc(2015, 5, 20, 2, 9, 8),
    )

    def __init__(self, db: DatabaseTransactionFixture, files: AxisFilesFixture):
        self.db = db
        self.files = files
        self.collection = MockAxis360API.mock_collection(
            db.session, db.default_library()
        )
        self.api = MockAxis360API(db.session, self.collection)

    def sample_data(self, filename):
        return self.files.sample_data(filename)


@pytest.fixture(scope="function")
def axis360(
    db: DatabaseTransactionFixture, api_axis_files_fixture: AxisFilesFixture
) -> Axis360Fixture:
    return Axis360Fixture(db, api_axis_files_fixture)


class TestAxis360API:
    def test__run_self_tests(
        self,
        axis360: Axis360Fixture,
        create_simple_auth_integration: SimpleAuthIntegrationFixture,
    ):
        # Verify that Axis360API._run_self_tests() calls the right
        # methods.

        class Mock(MockAxis360API):
            "Mock every method used by Axis360API._run_self_tests."

            # First we will refresh the bearer token.
            def refresh_bearer_token(self):
                return "the new token"

            # Then we will count the number of events in the past
            # give minutes.
            def recent_activity(self, since):
                self.recent_activity_called_with = since
                return [(1, "a"), (2, "b"), (3, "c")]

            # Then we will count the loans and holds for the default
            # patron.
            def patron_activity(self, patron, pin):
                self.patron_activity_called_with = (patron, pin)
                return ["loan", "hold"]

        # Now let's make sure two Libraries have access to this
        # Collection -- one library with a default patron and one
        # without.
        no_default_patron = axis360.db.library()
        axis360.collection.libraries.append(no_default_patron)

        with_default_patron = axis360.db.default_library()
        create_simple_auth_integration(with_default_patron)

        # Now that everything is set up, run the self-test.
        api = Mock(axis360.db.session, axis360.collection)
        now = utc_now()
        [
            no_patron_credential,
            recent_circulation_events,
            patron_activity,
            pools_without_delivery,
            refresh_bearer_token,
        ] = sorted(api._run_self_tests(axis360.db.session), key=lambda x: str(x.name))
        assert "Refreshing bearer token" == refresh_bearer_token.name
        assert True == refresh_bearer_token.success
        assert "the new token" == refresh_bearer_token.result

        assert (
            "Acquiring test patron credentials for library %s" % no_default_patron.name
            == no_patron_credential.name
        )
        assert False == no_patron_credential.success
        assert "Library has no test patron configured." == str(
            no_patron_credential.exception
        )

        assert (
            "Asking for circulation events for the last five minutes"
            == recent_circulation_events.name
        )
        assert True == recent_circulation_events.success
        assert "Found 3 event(s)" == recent_circulation_events.result
        since = api.recent_activity_called_with
        five_minutes_ago = utc_now() - datetime.timedelta(minutes=5)
        assert (five_minutes_ago - since).total_seconds() < 5

        assert (
            "Checking activity for test patron for library %s"
            % with_default_patron.name
            == patron_activity.name
        )
        assert True == patron_activity.success
        assert "Found 2 loans/holds" == patron_activity.result
        patron, pin = api.patron_activity_called_with
        assert "username1" == patron.authorization_identifier
        assert "password1" == pin

        assert (
            "Checking for titles that have no delivery mechanisms."
            == pools_without_delivery.name
        )
        assert True == pools_without_delivery.success
        assert (
            "All titles in this collection have delivery mechanisms."
            == pools_without_delivery.result
        )

    def test__run_self_tests_short_circuit(self, axis360: Axis360Fixture):
        # If we can't refresh the bearer token, the rest of the
        # self-tests aren't even run.

        class Mock(MockAxis360API):
            def refresh_bearer_token(self):
                raise Exception("no way")

        # Now that everything is set up, run the self-test. Only one
        # test will be run.
        api = Mock(axis360.db.session, axis360.collection)
        [failure] = api._run_self_tests(axis360.db.session)
        assert "Refreshing bearer token" == failure.name
        assert failure.success is False
        assert failure.exception is not None
        assert "no way" == failure.exception.args[0]

    def test_create_identifier_strings(self, axis360: Axis360Fixture):
        identifier = axis360.db.identifier()
        values = Axis360API.create_identifier_strings(["foo", identifier])
        assert ["foo", identifier.identifier] == values

    def test_availability_no_timeout(self, axis360: Axis360Fixture):
        # The availability API request has no timeout set, because it
        # may take time proportinate to the total size of the
        # collection.
        axis360.api.queue_response(200)
        axis360.api.availability()
        request = axis360.api.requests.pop()
        kwargs = request[-1]
        assert None == kwargs["timeout"]

    def test_availability_exception(self, axis360: Axis360Fixture):
        axis360.api.queue_response(500)

        with pytest.raises(RemoteIntegrationException) as excinfo:
            axis360.api.availability()
        assert (
            "Bad response from http://axis.test/availability/v2: Got status code 500 from external server, cannot continue."
            in str(excinfo.value)
        )

    def test_refresh_bearer_token_after_401(self, axis360: Axis360Fixture):
        # If we get a 401, we will fetch a new bearer token and try the
        # request again.

        axis360.api.queue_response(401)
        axis360.api.queue_response(200, content=json.dumps(dict(access_token="foo")))
        axis360.api.queue_response(200, content="The data")
        response = axis360.api.request("http://url/")
        assert b"The data" == response.content

    def test_refresh_bearer_token_error(self, axis360: Axis360Fixture):
        # Raise an exception if we don't get a 200 status code when
        # refreshing the bearer token.

        api = MockAxis360API(axis360.db.session, axis360.collection, with_token=False)
        api.queue_response(412)
        with pytest.raises(RemoteIntegrationException) as excinfo:
            api.refresh_bearer_token()
        assert (
            "Bad response from http://axis.test/accesstoken: Got status code 412 from external server, but can only continue on: 200."
            in str(excinfo.value)
        )

    def test_exception_after_401_with_fresh_token(self, axis360: Axis360Fixture):
        # If we get a 401 immediately after refreshing the token, we will
        # raise an exception.

        axis360.api.queue_response(401)
        axis360.api.queue_response(200, content=json.dumps(dict(access_token="foo")))
        axis360.api.queue_response(401)

        axis360.api.queue_response(301)

        with pytest.raises(RemoteIntegrationException) as excinfo:
            axis360.api.request("http://url/")
        assert "Got status code 401 from external server, cannot continue." in str(
            excinfo.value
        )

        # The fourth request never got made.
        assert [301] == [x.status_code for x in axis360.api.responses]

    def test_update_availability(self, axis360: Axis360Fixture):
        # Test the Axis 360 implementation of the update_availability method
        # defined by the CirculationAPI interface.

        # Create a LicensePool that needs updating.
        edition, pool = axis360.db.edition(
            identifier_type=Identifier.AXIS_360_ID,
            data_source_name=DataSource.AXIS_360,
            with_license_pool=True,
            collection=axis360.collection,
        )

        # We have never checked the circulation information for this
        # LicensePool. Put some random junk in the pool to verify
        # that it gets changed.
        pool.licenses_owned = 10
        pool.licenses_available = 5
        pool.patrons_in_hold_queue = 3
        assert None == pool.last_checked

        # Prepare availability information.
        data = axis360.sample_data("availability_with_loans.xml")

        # Modify the data so that it appears to be talking about the
        # book we just created.
        new_identifier = pool.identifier.identifier
        data = data.replace(b"0012533119", new_identifier.encode("utf8"))

        axis360.api.queue_response(200, content=data)

        axis360.api.update_availability(pool)

        # The availability information has been udpated, as has the
        # date the availability information was last checked.
        assert 2 == pool.licenses_owned
        assert 1 == pool.licenses_available
        assert 0 == pool.patrons_in_hold_queue
        assert pool.last_checked is not None

    def test_checkin_success(self, axis360: Axis360Fixture):
        # Verify that we can make a request to the EarlyCheckInTitle
        # endpoint and get a good response.
        edition, pool = axis360.db.edition(
            identifier_type=Identifier.AXIS_360_ID,
            data_source_name=DataSource.AXIS_360,
            with_license_pool=True,
        )
        data = axis360.sample_data("checkin_success.xml")
        axis360.api.queue_response(200, content=data)
        patron = axis360.db.patron()
        barcode = axis360.db.fresh_str()
        patron.authorization_identifier = barcode
        axis360.api.checkin(patron, "pin", pool)

        # Verify the format of the HTTP request that was made.
        [request] = axis360.api.requests
        [url, args, kwargs] = request
        data = kwargs.pop("data")
        assert kwargs["method"] == "GET"
        expect = "/EarlyCheckInTitle/v3?itemID={}&patronID={}".format(
            pool.identifier.identifier,
            barcode,
        )
        assert expect in url

    def test_checkin_failure(self, axis360: Axis360Fixture):
        # Verify that we correctly handle failure conditions sent from
        # the EarlyCheckInTitle endpoint.
        edition, pool = axis360.db.edition(
            identifier_type=Identifier.AXIS_360_ID,
            data_source_name=DataSource.AXIS_360,
            with_license_pool=True,
        )
        data = axis360.sample_data("checkin_failure.xml")
        axis360.api.queue_response(200, content=data)
        patron = axis360.db.patron()
        patron.authorization_identifier = axis360.db.fresh_str()
        pytest.raises(NotFoundOnRemote, axis360.api.checkin, patron, "pin", pool)

    def test_update_licensepools_for_identifiers(self, axis360: Axis360Fixture):
        class Mock(MockAxis360API):
            """Simulates an Axis 360 API that knows about some
            books but not others.
            """

            updated = []  # type: ignore
            reaped = []

            def _fetch_remote_availability(self, identifiers):
                for i, identifier in enumerate(identifiers):
                    # The first identifer in the list is still
                    # available.
                    identifier_data = IdentifierData(
                        type=identifier.type, identifier=identifier.identifier
                    )
                    metadata = Metadata(
                        data_source=DataSource.AXIS_360,
                        primary_identifier=identifier_data,
                    )
                    availability = CirculationData(
                        data_source=DataSource.AXIS_360,
                        primary_identifier=identifier_data,
                        licenses_owned=7,
                        licenses_available=6,
                    )
                    yield metadata, availability

                    # The rest have been 'forgotten' by Axis 360.
                    break

            def _reap(self, identifier):
                self.reaped.append(identifier)

        api = Mock(axis360.db.session, axis360.collection)
        still_in_collection = axis360.db.identifier(
            identifier_type=Identifier.AXIS_360_ID
        )
        no_longer_in_collection = axis360.db.identifier(
            identifier_type=Identifier.AXIS_360_ID
        )
        api.update_licensepools_for_identifiers(
            [still_in_collection, no_longer_in_collection]
        )

        # The LicensePool for the first identifier was updated.
        [lp] = still_in_collection.licensed_through
        assert 7 == lp.licenses_owned
        assert 6 == lp.licenses_available

        # The second was reaped.
        assert [no_longer_in_collection] == api.reaped

    def test_fetch_remote_availability(self, axis360: Axis360Fixture):
        # Test the _fetch_remote_availability method, as
        # used by update_licensepools_for_identifiers.

        id1 = axis360.db.identifier(identifier_type=Identifier.AXIS_360_ID)
        id2 = axis360.db.identifier(identifier_type=Identifier.AXIS_360_ID)
        data = axis360.sample_data("availability_with_loans.xml")
        # Modify the sample data so that it appears to be talking
        # about one of the books we're going to request.
        data = data.replace(b"0012533119", id1.identifier.encode("utf8"))
        axis360.api.queue_response(200, {}, data)
        results = [x for x in axis360.api._fetch_remote_availability([id1, id2])]

        # We asked for information on two identifiers.
        [request] = axis360.api.requests
        kwargs = request[-1]
        assert {"titleIds": "2001,2002"} == kwargs["params"]

        # We got information on only one.
        [(metadata, circulation)] = results
        assert (id1, False) == metadata.primary_identifier.load(axis360.db.session)
        assert (
            "El caso de la gracia : Un periodista explora las evidencias de unas vidas transformadas"
            == metadata.title
        )
        assert 2 == circulation.licenses_owned

    def test_reap(self, axis360: Axis360Fixture):
        # Test the _reap method, as used by
        # update_licensepools_for_identifiers.

        id1 = axis360.db.identifier(identifier_type=Identifier.AXIS_360_ID)
        assert [] == id1.licensed_through

        # If there is no LicensePool to reap, nothing happens.
        axis360.api._reap(id1)
        assert [] == id1.licensed_through

        # If there is a LicensePool but it has no owned licenses,
        # it's already been reaped, so nothing happens.
        (
            edition,
            pool,
        ) = axis360.db.edition(
            data_source_name=DataSource.AXIS_360,
            identifier_type=id1.type,
            identifier_id=id1.identifier,
            with_license_pool=True,
            collection=axis360.collection,
        )

        # This LicensePool has licenses, but it's not in a different
        # collection from the collection associated with this
        # Axis360API object, so it's not affected.
        collection2 = axis360.db.collection()
        (
            edition2,
            pool2,
        ) = axis360.db.edition(
            data_source_name=DataSource.AXIS_360,
            identifier_type=id1.type,
            identifier_id=id1.identifier,
            with_license_pool=True,
            collection=collection2,
        )

        pool.licenses_owned = 0
        pool2.licenses_owned = 10
        axis360.db.session.commit()
        updated = pool.last_checked
        updated2 = pool2.last_checked
        axis360.api._reap(id1)

        assert updated == pool.last_checked
        assert 0 == pool.licenses_owned
        assert updated2 == pool2.last_checked
        assert 10 == pool2.licenses_owned

        # If the LicensePool did have licenses, then reaping it
        # reflects the fact that the licenses are no longer owned.
        pool.licenses_owned = 10
        pool.licenses_available = 9
        pool.licenses_reserved = 8
        pool.patrons_in_hold_queue = 7
        axis360.api._reap(id1)
        assert 0 == pool.licenses_owned
        assert 0 == pool.licenses_available
        assert 0 == pool.licenses_reserved
        assert 0 == pool.patrons_in_hold_queue

    def test_get_fulfillment_info(self, axis360: Axis360Fixture):
        # Test the get_fulfillment_info method, which makes an API request.

        api = MockAxis360API(axis360.db.session, axis360.collection)
        api.queue_response(200, {}, "the response")

        # Make a request and check the response.
        response = api.get_fulfillment_info("transaction ID")
        assert b"the response" == response.content

        # Verify that the 'HTTP request' was made to the right URL
        # with the right keyword arguments and the right HTTP method.
        url, args, kwargs = api.requests.pop()
        assert url.endswith(api.fulfillment_endpoint)
        assert "POST" == kwargs["method"]
        assert "transaction ID" == kwargs["params"]["TransactionID"]

    def test_get_audiobook_metadata(self, axis360: Axis360Fixture):
        # Test the get_audiobook_metadata method, which makes an API request.

        api = MockAxis360API(axis360.db.session, axis360.collection)
        api.queue_response(200, {}, "the response")

        # Make a request and check the response.
        response = api.get_audiobook_metadata("Findaway content ID")
        assert b"the response" == response.content

        # Verify that the 'HTTP request' was made to the right URL
        # with the right keyword arguments and the right HTTP method.
        url, args, kwargs = api.requests.pop()
        assert url.endswith(api.audiobook_metadata_endpoint)
        assert "POST" == kwargs["method"]
        assert "Findaway content ID" == kwargs["params"]["fndcontentid"]

    def test_update_book(self, axis360: Axis360Fixture):
        # Verify that the update_book method takes a Metadata and a
        # CirculationData object, and creates appropriate data model
        # objects.

        analytics = MockAnalyticsProvider()
        api = MockAxis360API(axis360.db.session, axis360.collection)
        e, e_new, lp, lp_new = api.update_book(
            axis360.BIBLIOGRAPHIC_DATA,
            axis360.AVAILABILITY_DATA,
            analytics=cast(Analytics, analytics),
        )
        # A new LicensePool and Edition were created.
        assert True == lp_new
        assert True == e_new

        # The LicensePool reflects what it said in AVAILABILITY_DATA
        assert 9 == lp.licenses_owned

        # There's a presentation-ready Work created for the
        # LicensePool.
        assert True == lp.work.presentation_ready
        assert e == lp.work.presentation_edition

        # The Edition reflects what it said in BIBLIOGRAPHIC_DATA
        assert "Faith of My Fathers : A Family Memoir" == e.title

        # Three analytics events were sent out.
        #
        # It's not super important to test which ones, but they are:
        # 1. The creation of the LicensePool
        # 2. The setting of licenses_owned to 9
        # 3. The setting of licenses_available to 8
        #
        # No more DISTRIBUTOR events
        assert 0 == analytics.count

        # Now change a bit of the data and call the method again.
        new_circulation = CirculationData(
            data_source=DataSource.AXIS_360,
            primary_identifier=axis360.BIBLIOGRAPHIC_DATA.primary_identifier,
            licenses_owned=8,
            licenses_available=7,
        )

        e2, e_new, lp2, lp_new = api.update_book(
            axis360.BIBLIOGRAPHIC_DATA,
            new_circulation,
            analytics=cast(Analytics, analytics),
        )

        # The same LicensePool and Edition are returned -- no new ones
        # are created.
        assert e2 == e
        assert False == e_new
        assert lp2 == lp
        assert False == lp_new

        # The LicensePool has been updated to reflect the new
        # CirculationData
        assert 8 == lp.licenses_owned
        assert 7 == lp.licenses_available

        # Two more circulation events have been sent out -- one for
        # the licenses_owned change and one for the licenses_available
        # change.
        #
        # No more DISTRIBUTOR events
        assert 0 == analytics.count

    @pytest.mark.parametrize(
        ("setting", "setting_value", "attribute", "attribute_value"),
        [
            (Axis360API.VERIFY_SSL, None, "verify_certificate", True),
            (Axis360API.VERIFY_SSL, True, "verify_certificate", True),
            (Axis360API.VERIFY_SSL, False, "verify_certificate", False),
        ],
    )
    def test_integration_settings(
        self,
        setting,
        setting_value,
        attribute,
        attribute_value,
        axis360: Axis360Fixture,
    ):
        config = axis360.collection.integration_configuration
        settings = config.settings_dict.copy()
        if setting_value is not None:
            settings[setting] = setting_value
            config.settings_dict = settings
        api = MockAxis360API(axis360.db.session, axis360.collection)
        assert getattr(api, attribute) == attribute_value

    @pytest.mark.parametrize(
        ("setting", "setting_value", "is_valid", "expected"),
        [
            (
                "url",
                "production",
                True,
                Axis360APIConstants.SERVER_NICKNAMES["production"],
            ),
            ("url", "qa", True, Axis360APIConstants.SERVER_NICKNAMES["qa"]),
            ("url", "not-production", False, None),
            ("url", "http://any.url.will.do", True, "http://any.url.will.do/"),
        ],
    )
    def test_integration_settings_url(
        self, setting, setting_value, is_valid, expected, axis360: Axis360Fixture
    ):
        config = axis360.collection.integration_configuration
        config.settings_dict[setting] = setting_value

        if is_valid:
            integration_settings_update(
                Axis360Settings, config, {setting: setting_value}, merge=True
            )
            api = MockAxis360API(axis360.db.session, axis360.collection)
            assert api.base_url == expected
        else:
            pytest.raises(
                ProblemError,
                integration_settings_update,
                Axis360Settings,
                config,
                {setting: setting_value},
                merge=True,
            )


class TestCirculationMonitor:
    def test_run(self, axis360: Axis360Fixture):
        class Mock(Axis360CirculationMonitor):
            def catch_up_from(self, start, cutoff, progress):
                self.called_with = (start, cutoff, progress)

        monitor = Mock(axis360.db.session, axis360.collection, api_class=MockAxis360API)

        # The first time run() is called, catch_up_from() is asked to
        # find events between DEFAULT_START_TIME and the current time.
        monitor.run()
        start, cutoff, progress = monitor.called_with
        now = utc_now()
        assert monitor.DEFAULT_START_TIME == start
        assert (now - cutoff).total_seconds() < 2

        # The second time run() is called, catch_up_from() is asked
        # to find events between five minutes before the last cutoff,
        # and what is now the current time.
        monitor.run()
        new_start, new_cutoff, new_progress = monitor.called_with
        now = utc_now()
        before_old_cutoff = cutoff - monitor.OVERLAP
        assert before_old_cutoff == new_start
        assert (now - new_cutoff).total_seconds() < 2

    def test_catch_up_from(self, axis360: Axis360Fixture):
        class MockAPI(MockAxis360API):
            def recent_activity(self, since):
                self.recent_activity_called_with = since
                return [(1, "a"), (2, "b")]

        class MockMonitor(Axis360CirculationMonitor):
            processed = []

            def process_book(self, bibliographic, circulation):
                self.processed.append((bibliographic, circulation))

        mock_api = MockAPI(axis360.db.session, axis360.collection)
        monitor = MockMonitor(
            axis360.db.session, axis360.collection, api_class=mock_api
        )
        data = axis360.sample_data("single_item.xml")
        axis360.api.queue_response(200, content=data)
        progress = TimestampData()
        start_mock = MagicMock()
        monitor.catch_up_from(start_mock, MagicMock(), progress)

        # The start time was passed into recent_activity.
        assert start_mock == mock_api.recent_activity_called_with

        # process_book was called on each item returned by recent_activity.
        assert [(1, "a"), (2, "b")] == monitor.processed

        # The number of books processed was stored in
        # TimestampData.achievements.
        assert "Modified titles: 2." == progress.achievements

    def test_process_book(self, axis360: Axis360Fixture):
        integration, ignore = create(
            axis360.db.session,
            ExternalIntegration,
            goal=ExternalIntegration.ANALYTICS_GOAL,
            protocol="core.local_analytics_provider",
        )

        monitor = Axis360CirculationMonitor(
            axis360.db.session,
            axis360.collection,
            api_class=MockAxis360API,
        )
        edition, license_pool = monitor.process_book(
            axis360.BIBLIOGRAPHIC_DATA, axis360.AVAILABILITY_DATA
        )
        assert "Faith of My Fathers : A Family Memoir" == edition.title
        assert "eng" == edition.language
        assert "Random House Inc" == edition.publisher
        assert "Random House Inc2" == edition.imprint

        assert Identifier.AXIS_360_ID == edition.primary_identifier.type
        assert "0003642860" == edition.primary_identifier.identifier

        [isbn] = [
            x
            for x in edition.equivalent_identifiers()
            if x is not edition.primary_identifier
        ]
        assert Identifier.ISBN == isbn.type
        assert "9780375504587" == isbn.identifier

        assert ["McCain, John", "Salter, Mark"] == sorted(
            x.sort_name for x in edition.contributors
        )

        subs = sorted(
            (x.subject.type, x.subject.identifier)
            for x in edition.primary_identifier.classifications
        )
        assert [
            (Subject.BISAC, "BIOGRAPHY & AUTOBIOGRAPHY / Political"),
            (Subject.FREEFORM_AUDIENCE, "Adult"),
        ] == subs

        assert 9 == license_pool.licenses_owned
        assert 8 == license_pool.licenses_available
        assert 0 == license_pool.patrons_in_hold_queue
        assert datetime_utc(2015, 5, 20, 2, 9, 8) == license_pool.last_checked

        # Three circulation events were created, backdated to the
        # last_checked date of the license pool.
        events = license_pool.circulation_events

        for e in events:
            assert e.start == license_pool.last_checked

        # A presentation-ready work has been created for the LicensePool.
        work = license_pool.work
        assert True == work.presentation_ready
        assert "Faith of My Fathers : A Family Memoir" == work.title

        # A CoverageRecord has been provided for this book in the Axis
        # 360 bibliographic coverage provider, so that in the future
        # it doesn't have to make a separate API request to ask about
        # this book.
        records = [
            x
            for x in license_pool.identifier.coverage_records
            if x.data_source.name == DataSource.AXIS_360 and x.operation is None
        ]
        assert 1 == len(records)

        # Now, another collection with the same book shows up.
        collection2 = MockAxis360API.mock_collection(
            axis360.db.session, axis360.db.default_library(), "coll2"
        )
        monitor = Axis360CirculationMonitor(
            axis360.db.session,
            collection2,
            api_class=MockAxis360API,
        )
        edition2, license_pool2 = monitor.process_book(
            axis360.BIBLIOGRAPHIC_DATA, axis360.AVAILABILITY_DATA
        )

        # Both license pools have the same Work and the same presentation
        # edition.
        assert license_pool.work == license_pool2.work
        assert license_pool.presentation_edition == license_pool2.presentation_edition

    def test_process_book_updates_old_licensepool(self, axis360: Axis360Fixture):
        """If the LicensePool already exists, the circulation monitor
        updates it.
        """
        edition, licensepool = axis360.db.edition(
            with_license_pool=True,
            identifier_type=Identifier.AXIS_360_ID,
            identifier_id="0003642860",
        )
        # We start off with availability information based on the
        # default for test data.
        assert 1 == licensepool.licenses_owned

        identifier = IdentifierData(
            type=licensepool.identifier.type,
            identifier=licensepool.identifier.identifier,
        )
        metadata = Metadata(DataSource.AXIS_360, primary_identifier=identifier)
        monitor = Axis360CirculationMonitor(
            axis360.db.session,
            axis360.collection,
            api_class=MockAxis360API,
        )
        edition, licensepool = monitor.process_book(metadata, axis360.AVAILABILITY_DATA)

        # Now we have information based on the CirculationData.
        assert 9 == licensepool.licenses_owned


class TestReaper:
    def test_instantiate(self, axis360: Axis360Fixture):
        # Validate the standard CollectionMonitor interface.
        monitor = AxisCollectionReaper(
            axis360.db.session, axis360.collection, api_class=MockAxis360API
        )


class TestParsers:
    def test_bibliographic_parser(self, axis360: Axis360Fixture):
        # Make sure the bibliographic information gets properly
        # collated in preparation for creating Edition objects.

        data = axis360.sample_data("tiny_collection.xml")

        [bib1, av1], [bib2, av2] = BibliographicParser().process_all(data)

        # We test for availability information in a separate test.
        # Here we just make sure it is present.
        assert av1 is not None
        assert av2 is not None

        # But we did get bibliographic information.
        assert bib1 is not None
        assert bib2 is not None

        assert "Faith of My Fathers : A Family Memoir" == bib1.title
        assert "eng" == bib1.language
        assert datetime_utc(2000, 3, 7, 0, 0) == bib1.published

        assert "Simon & Schuster" == bib2.publisher
        assert "Pocket Books" == bib2.imprint

        assert Edition.BOOK_MEDIUM == bib1.medium

        # TODO: Would be nicer if we could test getting a real value
        # for this.
        assert None == bib2.series

        # Book #1 has two links -- a description and a cover image.
        [description, cover] = bib1.links
        assert Hyperlink.DESCRIPTION == description.rel
        assert Representation.TEXT_PLAIN == description.media_type
        assert description.content.startswith("John McCain's deeply moving memoir")

        # The cover image simulates the current state of the B&T cover
        # service, where we get a thumbnail-sized image URL in the
        # Axis 360 API response and we can hack the URL to get the
        # full-sized image URL.
        assert LinkRelations.IMAGE == cover.rel
        assert (
            "http://contentcafecloud.baker-taylor.com/Jacket.svc/D65D0665-050A-487B-9908-16E6D8FF5C3E/9780375504587/Large/Empty"
            == cover.href
        )
        assert MediaTypes.JPEG_MEDIA_TYPE == cover.media_type

        assert LinkRelations.THUMBNAIL_IMAGE == cover.thumbnail.rel
        assert (
            "http://contentcafecloud.baker-taylor.com/Jacket.svc/D65D0665-050A-487B-9908-16E6D8FF5C3E/9780375504587/Medium/Empty"
            == cover.thumbnail.href
        )
        assert MediaTypes.JPEG_MEDIA_TYPE == cover.thumbnail.media_type

        # Book #1 has a primary author, another author and a narrator.
        #
        # TODO: The narrator data is simulated. we haven't actually
        # verified that Axis 360 sends narrator information in the
        # same format as author information.
        [cont1, cont2, narrator] = bib1.contributors
        assert "McCain, John" == cont1.sort_name
        assert [Contributor.PRIMARY_AUTHOR_ROLE] == cont1.roles

        assert "Salter, Mark" == cont2.sort_name
        assert [Contributor.AUTHOR_ROLE] == cont2.roles

        assert "McCain, John S. III" == narrator.sort_name
        assert [Contributor.NARRATOR_ROLE] == narrator.roles

        # Book #2 only has a primary author.
        [cont] = bib2.contributors
        assert "Pollero, Rhonda" == cont.sort_name
        assert [Contributor.PRIMARY_AUTHOR_ROLE] == cont.roles

        axis_id, isbn = sorted(bib1.identifiers, key=lambda x: x.identifier)
        assert "0003642860" == axis_id.identifier
        assert "9780375504587" == isbn.identifier

        # Check the subjects for #2 because it includes an audience,
        # unlike #1.
        subjects = sorted(bib2.subjects, key=lambda x: x.identifier or "")
        assert [
            Subject.BISAC,
            Subject.BISAC,
            Subject.BISAC,
            Subject.AXIS_360_AUDIENCE,
        ] == [x.type for x in subjects]
        general_fiction, women_sleuths, romantic_suspense = sorted(
            x.name for x in subjects if x.type == Subject.BISAC
        )
        assert "FICTION / General" == general_fiction
        assert "FICTION / Mystery & Detective / Women Sleuths" == women_sleuths
        assert "FICTION / Romance / Suspense" == romantic_suspense

        [adult] = [
            x.identifier for x in subjects if x.type == Subject.AXIS_360_AUDIENCE
        ]
        assert "General Adult" == adult

        # The second book has a cover image simulating some possible
        # future case, where B&T change their cover service so that
        # the size URL hack no longer works. In this case, we treat
        # the image URL as both the full-sized image and the
        # thumbnail.
        [cover] = bib2.links
        assert LinkRelations.IMAGE == cover.rel
        assert "http://some-other-server/image.jpg" == cover.href
        assert MediaTypes.JPEG_MEDIA_TYPE == cover.media_type

        assert LinkRelations.THUMBNAIL_IMAGE == cover.thumbnail.rel
        assert "http://some-other-server/image.jpg" == cover.thumbnail.href
        assert MediaTypes.JPEG_MEDIA_TYPE == cover.thumbnail.media_type

        # The first book is available in two formats -- "ePub" and "AxisNow"
        [adobe, axisnow] = bib1.circulation.formats
        assert Representation.EPUB_MEDIA_TYPE == adobe.content_type
        assert DeliveryMechanism.ADOBE_DRM == adobe.drm_scheme

        assert None == axisnow.content_type
        assert DeliveryMechanism.AXISNOW_DRM == axisnow.drm_scheme

        # The second book is available in 'Blio' format, which
        # is treated as an alternate name for 'AxisNow'
        [axisnow] = bib2.circulation.formats
        assert None == axisnow.content_type
        assert DeliveryMechanism.AXISNOW_DRM == axisnow.drm_scheme

    def test_bibliographic_parser_audiobook(self, axis360: Axis360Fixture):
        # TODO - we need a real example to test from. The example we were
        # given is a hacked-up ebook. Ideally we would be able to check
        # narrator information here.
        data = axis360.sample_data("availability_with_audiobook_fulfillment.xml")

        [[bib, av]] = BibliographicParser().process_all(data)
        assert av is not None
        assert bib is not None

        assert "Back Spin" == bib.title
        assert Edition.AUDIO_MEDIUM == bib.medium

        # The audiobook has one DeliveryMechanism, in which the Findaway licensing document
        # acts as both the content type and the DRM scheme.
        [findaway] = bib.circulation.formats
        assert None == findaway.content_type
        assert DeliveryMechanism.FINDAWAY_DRM == findaway.drm_scheme

        # Although the audiobook is also available in the "AxisNow"
        # format, no second delivery mechanism was created for it, the
        # way it would have been for an ebook.
        assert b"<formatName>AxisNow</formatName>" in data

    def test_bibliographic_parser_blio_format(self, axis360: Axis360Fixture):
        # This book is available as 'Blio' but not 'AxisNow'.
        data = axis360.sample_data("availability_with_audiobook_fulfillment.xml")
        data = data.replace(b"Acoustik", b"Blio")
        data = data.replace(b"AxisNow", b"No Such Format")

        [[bib, av]] = BibliographicParser().process_all(data)
        assert av is not None
        assert bib is not None

        # A book in Blio format is treated as an AxisNow ebook.
        assert Edition.BOOK_MEDIUM == bib.medium
        [axisnow] = bib.circulation.formats
        assert None == axisnow.content_type
        assert DeliveryMechanism.AXISNOW_DRM == axisnow.drm_scheme

    def test_bibliographic_parser_blio_and_axisnow_format(
        self, axis360: Axis360Fixture
    ):
        # This book is available as both 'Blio' and 'AxisNow'.
        data = axis360.sample_data("availability_with_audiobook_fulfillment.xml")
        data = data.replace(b"Acoustik", b"Blio")

        [[bib, av]] = BibliographicParser().process_all(data)
        assert av is not None
        assert bib is not None

        # There is only one FormatData -- 'Blio' and 'AxisNow' mean the same thing.
        assert Edition.BOOK_MEDIUM == bib.medium
        [axisnow] = bib.circulation.formats
        assert None == axisnow.content_type
        assert DeliveryMechanism.AXISNOW_DRM == axisnow.drm_scheme

    def test_bibliographic_parser_unsupported_format(self, axis360: Axis360Fixture):
        data = axis360.sample_data("availability_with_audiobook_fulfillment.xml")
        data = data.replace(b"Acoustik", b"No Such Format 1")
        data = data.replace(b"AxisNow", b"No Such Format 2")

        [[bib, av]] = BibliographicParser().process_all(data)
        assert av is not None
        assert bib is not None

        # We don't support any of the formats, so no FormatData objects were created.
        assert [] == bib.circulation.formats

    def test_parse_author_role(self, axis360: Axis360Fixture):
        """Suffixes on author names are turned into roles."""
        author = "Dyssegaard, Elisabeth Kallick (TRN)"
        parse = BibliographicParser.parse_contributor
        c = parse(author)
        assert "Dyssegaard, Elisabeth Kallick" == c.sort_name
        assert [Contributor.TRANSLATOR_ROLE] == c.roles

        # A corporate author is given a normal author role.
        author = "Bob, Inc. (COR)"
        c = parse(author, primary_author_found=False)
        assert "Bob, Inc." == c.sort_name
        assert [Contributor.PRIMARY_AUTHOR_ROLE] == c.roles

        c = parse(author, primary_author_found=True)
        assert "Bob, Inc." == c.sort_name
        assert [Contributor.AUTHOR_ROLE] == c.roles

        # An unknown author type is given an unknown role
        author = "Eve, Mallory (ZZZ)"
        c = parse(author, primary_author_found=False)
        assert "Eve, Mallory" == c.sort_name
        assert [Contributor.UNKNOWN_ROLE] == c.roles

        # force_role overwrites whatever other role might be
        # assigned.
        author = "Bob, Inc. (COR)"
        c = parse(
            author, primary_author_found=False, force_role=Contributor.NARRATOR_ROLE
        )
        assert [Contributor.NARRATOR_ROLE] == c.roles

    def test_availability_parser(self, axis360: Axis360Fixture):
        """Make sure the availability information gets properly
        collated in preparation for updating a LicensePool.
        """

        data = axis360.sample_data("tiny_collection.xml")

        [bib1, av1], [bib2, av2] = BibliographicParser().process_all(data)

        # We already tested the bibliographic information, so we just make sure
        # it is present.
        assert bib1 is not None
        assert bib2 is not None

        # But we did get availability information.
        assert av1 is not None
        assert av2 is not None

        assert "0003642860" == av1.primary_identifier(axis360.db.session).identifier
        assert 9 == av1.licenses_owned
        assert 9 == av1.licenses_available
        assert 0 == av1.patrons_in_hold_queue


class Axis360FixturePlusParsers(Axis360Fixture):
    def __init__(self, db: DatabaseTransactionFixture, files: AxisFilesFixture):
        super().__init__(db, files)

        # We don't need an actual Collection object to test most of
        # these classes, but we do need to test that whatever object
        # we _claim_ is a Collection will have its id put into the
        # right spot of HoldInfo and LoanInfo objects.

        self.default_collection = MagicMock(spec=Collection)
        type(self.default_collection).id = PropertyMock(return_value=1337)


@pytest.fixture(scope="function")
def axis360parsers(
    db: DatabaseTransactionFixture, api_axis_files_fixture: AxisFilesFixture
) -> Axis360FixturePlusParsers:
    return Axis360FixturePlusParsers(db, api_axis_files_fixture)


class TestRaiseExceptionOnError:
    def test_internal_server_error(self, axis360parsers: Axis360FixturePlusParsers):
        data = axis360parsers.sample_data("internal_server_error.xml")
        parser = HoldReleaseResponseParser(MagicMock())
        with pytest.raises(RemoteInitiatedServerError) as excinfo:
            parser.process_first(data)
        assert "Internal Server Error" in str(excinfo.value)

    def test_ignore_error_codes(self, axis360parsers: Axis360FixturePlusParsers):
        # A parser subclass can decide not to raise exceptions
        # when encountering specific error codes.
        data = axis360parsers.sample_data("internal_server_error.xml")
        retval = object()

        class IgnoreISE(HoldReleaseResponseParser):
            def process_one(self, e, namespaces):
                self.raise_exception_on_error(e, namespaces, ignore_error_codes=[5000])
                return retval

        # Unlike in test_internal_server_error, no exception is
        # raised, because we told the parser to ignore this particular
        # error code.
        parser = IgnoreISE(MagicMock())
        assert retval == parser.process_first(data)

    def test_internal_server_error2(self, axis360parsers: Axis360FixturePlusParsers):
        data = axis360parsers.sample_data("invalid_error_code.xml")
        parser = HoldReleaseResponseParser(MagicMock())
        with pytest.raises(RemoteInitiatedServerError) as excinfo:
            parser.process_first(data)
        assert "Invalid response code from Axis 360: abcd" in str(excinfo.value)

    def test_missing_error_code(self, axis360parsers: Axis360FixturePlusParsers):
        data = axis360parsers.sample_data("missing_error_code.xml")
        parser = HoldReleaseResponseParser(MagicMock())
        with pytest.raises(RemoteInitiatedServerError) as excinfo:
            parser.process_first(data)
        assert "No status code!" in str(excinfo.value)


class TestCheckinResponseParser:
    def test_parse_checkin_success(self, axis360parsers: Axis360FixturePlusParsers):
        # The response parser raises an exception if there's a problem,
        # and returne True otherwise.
        #
        # "Book is not on loan" is not treated as a problem.
        for filename in ("checkin_success.xml", "checkin_not_checked_out.xml"):
            data = axis360parsers.sample_data(filename)
            parser = CheckinResponseParser(axis360parsers.default_collection)
            parsed = parser.process_first(data)
            assert parsed is True

    def test_parse_checkin_failure(self, axis360parsers: Axis360FixturePlusParsers):
        data = axis360parsers.sample_data("checkin_failure.xml")
        parser = CheckinResponseParser(axis360parsers.default_collection)
        pytest.raises(NotFoundOnRemote, parser.process_first, data)


class TestCheckoutResponseParser:
    def test_parse_already_checked_out(self, axis360parsers: Axis360FixturePlusParsers):
        data = axis360parsers.sample_data("already_checked_out.xml")
        parser = CheckoutResponseParser(MagicMock())
        pytest.raises(AlreadyCheckedOut, parser.process_first, data)

    def test_parse_not_found_on_remote(self, axis360parsers: Axis360FixturePlusParsers):
        data = axis360parsers.sample_data("not_found_on_remote.xml")
        parser = CheckoutResponseParser(MagicMock())
        pytest.raises(NotFoundOnRemote, parser.process_first, data)


class TestHoldResponseParser:
    def test_parse_already_on_hold(self, axis360parsers: Axis360FixturePlusParsers):
        data = axis360parsers.sample_data("already_on_hold.xml")
        parser = HoldResponseParser(MagicMock())
        pytest.raises(AlreadyOnHold, parser.process_first, data)


class TestHoldReleaseResponseParser:
    def test_success(self, axis360parsers: Axis360FixturePlusParsers):
        data = axis360parsers.sample_data("release_hold_success.xml")
        parser = HoldReleaseResponseParser(MagicMock())
        assert True == parser.process_first(data)

    def test_failure(self, axis360parsers: Axis360FixturePlusParsers):
        data = axis360parsers.sample_data("release_hold_failure.xml")
        parser = HoldReleaseResponseParser(MagicMock())
        pytest.raises(NotOnHold, parser.process_first, data)


class TestJSONResponseParser:
    def test__required_key(self):
        m = JSONResponseParser._required_key
        parsed = dict(key="value")

        # If the value is present, _required_key acts just like get().
        assert "value" == m("key", parsed)

        # If not, it raises a RemoteInitiatedServerError.
        with pytest.raises(RemoteInitiatedServerError) as excinfo:
            m("absent", parsed)
        assert (
            "Required key absent not present in Axis 360 fulfillment document: {'key': 'value'}"
            in str(excinfo.value)
        )

    def test_verify_status_code(self):
        success = dict(Status=dict(Code=0000))
        failure = dict(Status=dict(Code=1000, Message="A message"))
        missing = dict()

        m = JSONResponseParser.verify_status_code

        # If the document's Status object indicates success, nothing
        # happens.
        m(success)

        # If it indicates failure, an appropriate exception is raised.
        with pytest.raises(PatronAuthorizationFailedException) as excinfo:
            m(failure)
        assert "A message" in str(excinfo.value)

        # If the Status object is missing, a more generic exception is
        # raised.
        with pytest.raises(RemoteInitiatedServerError) as excinfo:
            m(missing)
        assert (
            "Required key Status not present in Axis 360 fulfillment document"
            in str(excinfo.value)
        )

    def test_parse(self):
        class Mock(JSONResponseParser):
            def _parse(self, parsed, **kwargs):
                self.called_with = parsed, kwargs
                return "success"

        parser = Mock()

        # Test success.
        doc = dict(Status=dict(Code=0000))

        # The JSON will be parsed and passed in to _parse(); all other
        # keyword arguments to parse() will be passed through to _parse().
        result = parser.parse(json.dumps(doc), arg2="value2")
        assert "success" == result
        assert (doc, dict(arg2="value2")) == parser.called_with

        # It also works if the JSON was already parsed.
        result = parser.parse(doc, foo="bar")
        assert (doc, {"foo": "bar"}) == parser.called_with

        # Non-JSON input causes an error.
        with pytest.raises(RemoteInitiatedServerError) as excinfo:
            parser.parse("I'm not JSON")
        assert (
            'Invalid response from Axis 360 (was expecting JSON): "I\'m not JSON"'
            in str(excinfo.value)
        )


class TestAxis360FulfillmentInfoResponseParser:
    def test__parse_findaway(self, axis360parsers: Axis360FixturePlusParsers) -> None:
        # _parse will create a valid FindawayManifest given a
        # complete document.

        parser = Axis360FulfillmentInfoResponseParser(api=axis360parsers.api)
        m = parser._parse

        edition, pool = axis360parsers.db.edition(with_license_pool=True)

        def get_data():
            # We'll be modifying this document to simulate failures,
            # so make it easy to load a fresh copy.
            return json.loads(
                axis360parsers.sample_data("audiobook_fulfillment_info.json")
            )

        # This is the data we just got from a call to Axis 360's
        # getfulfillmentInfo endpoint.
        data = get_data()

        # When we call _parse, the API is going to fire off an
        # additional request to the getaudiobookmetadata endpoint, so
        # it can create a complete FindawayManifest. Queue up the
        # response to that request.
        audiobook_metadata = axis360parsers.sample_data("audiobook_metadata.json")
        axis360parsers.api.queue_response(200, {}, audiobook_metadata)

        manifest, expires = m(data, license_pool=pool)

        assert isinstance(manifest, FindawayManifest)
        metadata = manifest.metadata

        # The manifest contains information from the LicensePool's presentation
        # edition
        assert edition.title == metadata["title"]

        # It contains DRM licensing information from Findaway via the
        # Axis 360 API.
        encrypted = metadata["encrypted"]
        assert (
            "0f547af1-38c1-4b1c-8a1a-169d353065d0" == encrypted["findaway:sessionKey"]
        )
        assert "5babb89b16a4ed7d8238f498" == encrypted["findaway:checkoutId"]
        assert "04960" == encrypted["findaway:fulfillmentId"]
        assert "58ee81c6d3d8eb3b05597cdc" == encrypted["findaway:licenseId"]

        # The spine items and duration have been filled in by the call to
        # the getaudiobookmetadata endpoint.
        assert 8150.87 == metadata["duration"]
        assert 5 == len(manifest.readingOrder)

        # We also know when the licensing document expires.
        assert datetime_utc(2018, 9, 29, 18, 34) == expires

        # Now strategically remove required information from the
        # document and verify that extraction fails.
        #
        for field in (
            "FNDContentID",
            "FNDLicenseID",
            "FNDSessionKey",
            "ExpirationDate",
        ):
            missing_field = get_data()
            del missing_field[field]
            with pytest.raises(RemoteInitiatedServerError) as excinfo:
                m(missing_field, license_pool=pool)
            assert "Required key %s not present" % field in str(excinfo.value)

        # Try with a bad expiration date.
        bad_date = get_data()
        bad_date["ExpirationDate"] = "not-a-date"
        with pytest.raises(RemoteInitiatedServerError) as excinfo:
            m(bad_date, license_pool=pool)
        assert "Could not parse expiration date: not-a-date" in str(excinfo.value)

    def test__parse_axisnow(self, axis360parsers: Axis360FixturePlusParsers) -> None:
        # _parse will create a valid AxisNowManifest given a
        # complete document.

        parser = Axis360FulfillmentInfoResponseParser(api=axis360parsers.api)
        m = parser._parse

        edition, pool = axis360parsers.db.edition(with_license_pool=True)

        def get_data():
            # We'll be modifying this document to simulate failures,
            # so make it easy to load a fresh copy.
            return json.loads(axis360parsers.sample_data("ebook_fulfillment_info.json"))

        # This is the data we just got from a call to Axis 360's
        # getfulfillmentInfo endpoint.
        data = get_data()

        # Since this is an ebook, not an audiobook, there will be no
        # second request to the API, the way there is in the audiobook
        # test.
        manifest, expires = m(data, license_pool=pool)

        assert isinstance(manifest, AxisNowManifest)
        assert {
            "book_vault_uuid": "1c11c31f-81c2-41bb-9179-491114c3f121",
            "isbn": "9780547351551",
        } == json.loads(str(manifest))

        # Try with a bad expiration date.
        bad_date = get_data()
        bad_date["ExpirationDate"] = "not-a-date"
        with pytest.raises(RemoteInitiatedServerError) as excinfo:
            m(bad_date, license_pool=pool)
        assert "Could not parse expiration date: not-a-date" in str(excinfo.value)


class TestAudiobookMetadataParser:
    def test__parse(self, axis360: Axis360Fixture):
        # _parse will find the Findaway account ID and
        # the spine items.
        class Mock(AudiobookMetadataParser):
            @classmethod
            def _extract_spine_item(cls, part):
                return part + " (extracted)"

        metadata = dict(
            fndaccountid="An account ID", readingOrder=["Spine item 1", "Spine item 2"]
        )
        account_id, spine_items = Mock()._parse(metadata)

        assert "An account ID" == account_id
        assert ["Spine item 1 (extracted)", "Spine item 2 (extracted)"] == spine_items

        # No data? Nothing will be parsed.
        account_id, spine_items = Mock()._parse({})
        assert None == account_id
        assert [] == spine_items

    def test__extract_spine_item(self, axis360: Axis360Fixture):
        # _extract_spine_item will turn data from Findaway into
        # a SpineItem object.
        m = AudiobookMetadataParser._extract_spine_item
        item = m(
            dict(duration=100.4, fndpart=2, fndsequence=3, title="The Gathering Storm")
        )
        assert isinstance(item, SpineItem)
        assert "The Gathering Storm" == item.title
        assert 2 == item.part
        assert 3 == item.sequence
        assert 100.4 == item.duration
        assert Representation.MP3_MEDIA_TYPE == item.media_type

        # We get a SpineItem even if all the data about the spine item
        # is missing -- these are the default values.
        item = m({})
        assert None == item.title
        assert 0 == item.part
        assert 0 == item.sequence
        assert 0 == item.duration
        assert Representation.MP3_MEDIA_TYPE == item.media_type


class TestAxis360FulfillmentInfo:
    """An Axis360FulfillmentInfo can fulfill a title whether it's an ebook
    (fulfilled through AxisNow) or an audiobook (fulfilled through
    Findaway).
    """

    def test_fetch_audiobook(self, axis360: Axis360Fixture):
        # When Findaway information is present in the response from
        # the fulfillment API, a second request is made to get
        # spine-item metadata. Information from both requests is
        # combined into a Findaway fulfillment document.
        fulfillment_info = axis360.sample_data("audiobook_fulfillment_info.json")
        axis360.api.queue_response(200, {}, fulfillment_info)

        metadata = axis360.sample_data("audiobook_metadata.json")
        axis360.api.queue_response(200, {}, metadata)

        # Setup.
        edition, pool = axis360.db.edition(with_license_pool=True)
        identifier = pool.identifier
        fulfillment = Axis360FulfillmentInfo(
            axis360.api,
            pool.data_source.name,
            identifier.type,
            identifier.identifier,
            "transaction_id",
        )
        assert None == fulfillment._content_type

        # Turn the crank.
        fulfillment.fetch()

        # The Axis360FulfillmentInfo now contains a Findaway manifest
        # document.
        assert DeliveryMechanism.FINDAWAY_DRM == fulfillment.content_type
        assert isinstance(fulfillment.content, str)

        # The manifest document combines information from the
        # fulfillment document and the metadata document.
        for required in (
            '"findaway:sessionKey": "0f547af1-38c1-4b1c-8a1a-169d353065d0"',
            '"duration": 8150.87',
        ):
            assert required in fulfillment.content

        # The content expiration date also comes from the fulfillment
        # document.
        assert datetime_utc(2018, 9, 29, 18, 34) == fulfillment.content_expires

    def test_fetch_ebook(self, axis360: Axis360Fixture):
        # When no Findaway information is present in the response from
        # the fulfillment API, information from the request is
        # used to make an AxisNow fulfillment document.

        fulfillment_info = axis360.sample_data("ebook_fulfillment_info.json")
        axis360.api.queue_response(200, {}, fulfillment_info)

        # Setup.
        edition, pool = axis360.db.edition(with_license_pool=True)
        identifier = pool.identifier
        fulfillment = Axis360FulfillmentInfo(
            axis360.api,
            pool.data_source.name,
            identifier.type,
            identifier.identifier,
            "transaction_id",
        )
        assert None == fulfillment._content_type

        # Turn the crank.
        fulfillment.fetch()

        # The Axis360FulfillmentInfo now contains an AxisNow manifest
        # document derived from the fulfillment document.
        assert DeliveryMechanism.AXISNOW_DRM == fulfillment.content_type
        assert (
            '{"book_vault_uuid": "1c11c31f-81c2-41bb-9179-491114c3f121", "isbn": "9780547351551"}'
            == fulfillment.content
        )

        # The content expiration date also comes from the fulfillment
        # document.
        assert datetime_utc(2018, 9, 29, 18, 34) == fulfillment.content_expires


class TestAxisNowManifest:
    """Test the simple data format used to communicate an entry point into
    AxisNow."""

    def test_unicode(self):
        manifest = AxisNowManifest("A UUID", "An ISBN")
        assert '{"book_vault_uuid": "A UUID", "isbn": "An ISBN"}' == str(manifest)
        assert DeliveryMechanism.AXISNOW_DRM == manifest.MEDIA_TYPE


class Axis360ProviderFixture(Axis360Fixture):
    def __init__(self, db: DatabaseTransactionFixture, files: AxisFilesFixture):
        super().__init__(db, files)
        mock_api = MockAxis360API(db.session, self.collection)
        self.provider = Axis360BibliographicCoverageProvider(
            self.collection, api_class=mock_api
        )
        self.api = mock_api


@pytest.fixture(scope="function")
def axis360provider(
    db: DatabaseTransactionFixture, api_axis_files_fixture: AxisFilesFixture
) -> Axis360ProviderFixture:
    return Axis360ProviderFixture(db, api_axis_files_fixture)


class TestAxis360BibliographicCoverageProvider:
    """Test the code that looks up bibliographic information from Axis 360."""

    def test_script_instantiation(self, axis360provider: Axis360ProviderFixture):
        """Test that RunCoverageProviderScript can instantiate
        the coverage provider.
        """
        script = RunCollectionCoverageProviderScript(
            Axis360BibliographicCoverageProvider,
            axis360provider.db.session,
            api_class=MockAxis360API,
        )
        [provider] = script.providers
        assert isinstance(provider, Axis360BibliographicCoverageProvider)
        assert isinstance(provider.api, MockAxis360API)

    def test_process_item_creates_presentation_ready_work(
        self, axis360provider: Axis360ProviderFixture
    ):
        """Test the normal workflow where we ask Axis for data,
        Axis provides it, and we create a presentation-ready work.
        """
        data = axis360provider.sample_data("single_item.xml")
        axis360provider.api.queue_response(200, content=data)

        # Here's the book mentioned in single_item.xml.
        identifier = axis360provider.db.identifier(
            identifier_type=Identifier.AXIS_360_ID
        )
        identifier.identifier = "0003642860"

        # This book has no LicensePool.
        assert [] == identifier.licensed_through

        # Run it through the Axis360BibliographicCoverageProvider
        [result] = axis360provider.provider.process_batch([identifier])
        assert identifier == result

        # A LicensePool was created. We know both how many copies of this
        # book are available, and what formats it's available in.
        [pool] = identifier.licensed_through
        assert 9 == pool.licenses_owned
        [lpdm] = pool.delivery_mechanisms
        assert (
            "application/epub+zip (application/vnd.adobe.adept+xml)"
            == lpdm.delivery_mechanism.name
        )

        # A Work was created and made presentation ready.
        assert "Faith of My Fathers : A Family Memoir" == pool.work.title
        assert True == pool.work.presentation_ready

    def test_transient_failure_if_requested_book_not_mentioned(
        self, axis360provider: Axis360ProviderFixture
    ):
        """Test an unrealistic case where we ask Axis 360 about one book and
        it tells us about a totally different book.
        """
        # We're going to ask about abcdef
        identifier = axis360provider.db.identifier(
            identifier_type=Identifier.AXIS_360_ID
        )
        identifier.identifier = "abcdef"

        # But we're going to get told about 0003642860.
        data = axis360provider.sample_data("single_item.xml")
        axis360provider.api.queue_response(200, content=data)

        [result] = axis360provider.provider.process_batch([identifier])

        # Coverage failed for the book we asked about.
        assert isinstance(result, CoverageFailure)
        assert identifier == result.obj
        assert "Book not in collection" == result.exception

        # And nothing major was done about the book we were told
        # about. We created an Identifier record for its identifier,
        # but no LicensePool or Edition.
        wrong_identifier = Identifier.for_foreign_id(
            axis360provider.db.session, Identifier.AXIS_360_ID, "0003642860"
        )
        assert [] == identifier.licensed_through
        assert [] == identifier.primarily_identifies


class TestAxis360AcsFulfillmentInfo:
    @pytest.fixture
    def mock_request(self):
        # Create a mock request object that we can use in the tests
        response = MagicMock(return_value="")
        type(response).headers = PropertyMock(return_value=[])
        type(response).status = PropertyMock(return_value=200)
        mock_request = MagicMock()
        mock_request.__enter__.return_value = response
        mock_request.__exit__.return_value = None
        return mock_request

    @pytest.fixture
    def fulfillment_info(self):
        # A partial of the Axis360AcsFulfillmentInfo object to make it easier to
        # create these objects in our tests by supplying default parameters
        params = {
            "collection": 0,
            "content_type": None,
            "content": None,
            "content_expires": None,
            "data_source_name": None,
            "identifier_type": None,
            "identifier": None,
            "verify": None,
            "content_link": "https://fake.url",
        }
        return partial(Axis360AcsFulfillmentInfo, **params)

    @pytest.fixture
    def patch_urllib_urlopen(self, monkeypatch):
        # Monkeypatch the urllib.request.urlopen function to whatever is passed into
        # this function.
        def patch_urlopen(mock):
            monkeypatch.setattr(urllib.request, "urlopen", mock)

        return patch_urlopen

    def test_url_encoding_not_capitalized(
        self, patch_urllib_urlopen, mock_request, fulfillment_info
    ):
        # Mock the urllopen function to make sure that the URL is not actually requested
        # then make sure that when the request is built the %3a character encoded in the
        # string is not uppercased to be %3A.
        called_url = None

        def mock_urlopen(url, **kwargs):
            nonlocal called_url
            called_url = url
            return mock_request

        patch_urllib_urlopen(mock_urlopen)
        fulfillment = fulfillment_info(
            content_link="https://test.com/?param=%3atest123"
        )
        response = fulfillment.as_response
        assert called_url is not None
        assert called_url.selector == "/?param=%3atest123"
        assert called_url.host == "test.com"
        assert type(response) == Response
        mock_request.__enter__.assert_called()
        mock_request.__enter__.return_value.read.assert_called()
        assert "status" in dir(mock_request.__enter__.return_value)
        assert "headers" in dir(mock_request.__enter__.return_value)
        mock_request.__exit__.assert_called()

    @pytest.mark.parametrize(
        "exception",
        [
            urllib.error.HTTPError(url="", code=301, msg="", hdrs={}, fp=Mock()),  # type: ignore
            socket.timeout(),
            urllib.error.URLError(reason=""),
            ssl.SSLError(),
        ],
        ids=lambda val: val.__class__.__name__,
    )
    def test_exception_returns_problem_detail(
        self, patch_urllib_urlopen, fulfillment_info, exception
    ):
        # Check that when the urlopen function throws an exception, we catch the exception and
        # we turn it into a problem detail to be returned to the client. This mimics the behavior
        # of the http utils function that we are bypassing with this fulfillment method.
        patch_urllib_urlopen(Mock(side_effect=exception))
        fulfillment = fulfillment_info()
        response = fulfillment.as_response
        assert type(response) == ProblemDetail

    @pytest.mark.parametrize(
        ("verify", "verify_mode", "check_hostname"),
        [(True, ssl.CERT_REQUIRED, True), (False, ssl.CERT_NONE, False)],
    )
    def test_verify_ssl(
        self,
        patch_urllib_urlopen,
        fulfillment_info,
        verify,
        verify_mode,
        check_hostname,
        mock_request,
    ):
        # Make sure that when the verify parameter of the fulfillment method is set we use the
        # correct SSL context to either verify or not verify the ssl certificate for the
        # URL we are fetching.
        mock = MagicMock(return_value=mock_request)
        patch_urllib_urlopen(mock)
        fulfillment = fulfillment_info(verify=verify)
        response = fulfillment.as_response
        mock.assert_called()
        assert "context" in mock.call_args[1]
        context = mock.call_args[1]["context"]
        assert context.verify_mode == verify_mode
        assert context.check_hostname == check_hostname
