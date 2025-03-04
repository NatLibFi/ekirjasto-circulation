from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

import flask
from dependency_injector.wiring import Provide, inject
from expiringdict import ExpiringDict
from flask_babel import gettext
from flask_babel import lazy_gettext as _
from sqlalchemy import select

from api.authenticator import Authenticator
from api.circulation import CirculationAPI
from api.config import Configuration
from api.controller.analytics import AnalyticsController
from api.controller.annotation import AnnotationController
from api.controller.catalog_descriptions import CatalogDescriptionsController  # Finland
from api.controller.device_tokens import DeviceTokensController
from api.controller.index import IndexController
from api.controller.loan import LoanController
from api.controller.marc import MARCRecordController
from api.controller.odl_notification import ODLNotificationController
from api.controller.opds_feed import OPDSFeedController
from api.controller.patron_auth_token import PatronAuthTokenController
from api.controller.playtime_entries import PlaytimeEntriesController
from api.controller.profile import ProfileController
from api.controller.select_books import SelectBooksController
from api.controller.urn_lookup import URNLookupController
from api.controller.work import WorkController
from api.custom_index import CustomIndexView
from api.ekirjasto_controller import EkirjastoController  # Finland
from api.lanes import load_lanes
from api.opensearch_analytics_search import OpenSearchAnalyticsSearch  # Finland
from api.problem_details import *
from api.saml.controller import SAMLController
from core.app_server import ApplicationVersionController, load_facets_from_request
from core.feed.annotator.circulation import (
    CirculationManagerAnnotator,
    LibraryAnnotator,
)
from core.lane import Lane, WorkList
from core.model import ConfigurationSetting, Library
from core.model.discovery_service_registration import DiscoveryServiceRegistration
from core.service.container import Services
from core.service.logging.configuration import LogLevel
from core.util.log import LoggerMixin, elapsed_time_logging, log_elapsed_time

if TYPE_CHECKING:
    from api.admin.controller.admin_search import AdminSearchController
    from api.admin.controller.announcement_service import AnnouncementSettings
    from api.admin.controller.catalog_services import CatalogServicesController
    from api.admin.controller.collection_settings import CollectionSettingsController
    from api.admin.controller.custom_lists import CustomListsController
    from api.admin.controller.dashboard import DashboardController
    from api.admin.controller.discovery_service_library_registrations import (
        DiscoveryServiceLibraryRegistrationsController,
    )
    from api.admin.controller.discovery_services import DiscoveryServicesController
    from api.admin.controller.feed import FeedController
    from api.admin.controller.individual_admin_settings import (
        IndividualAdminSettingsController,
    )
    from api.admin.controller.lanes import LanesController
    from api.admin.controller.library_settings import LibrarySettingsController
    from api.admin.controller.metadata_services import MetadataServicesController
    from api.admin.controller.patron import PatronController
    from api.admin.controller.patron_auth_services import PatronAuthServicesController
    from api.admin.controller.quicksight import QuickSightController
    from api.admin.controller.reset_password import ResetPasswordController
    from api.admin.controller.sign_in import SignInController
    from api.admin.controller.sitewide_settings import (
        SitewideConfigurationSettingsController,
    )
    from api.admin.controller.timestamps import TimestampsController
    from api.admin.controller.view import ViewController
    from api.admin.controller.work_editor import WorkController as AdminWorkController


class CirculationManager(LoggerMixin):
    # API Controllers
    index_controller: IndexController
    opds_feeds: OPDSFeedController
    marc_records: MARCRecordController
    loans: LoanController
    annotations: AnnotationController
    urn_lookup: URNLookupController
    work_controller: WorkController
    analytics_controller: AnalyticsController
    profiles: ProfileController
    patron_devices: DeviceTokensController
    version: ApplicationVersionController
    odl_notification_controller: ODLNotificationController
    playtime_entries: PlaytimeEntriesController
    select_books: SelectBooksController

    # Admin controllers
    admin_sign_in_controller: SignInController
    admin_reset_password_controller: ResetPasswordController
    timestamps_controller: TimestampsController
    admin_work_controller: AdminWorkController
    admin_feed_controller: FeedController
    admin_custom_lists_controller: CustomListsController
    admin_lanes_controller: LanesController
    admin_dashboard_controller: DashboardController
    admin_patron_controller: PatronController
    admin_discovery_services_controller: DiscoveryServicesController
    admin_discovery_service_library_registrations_controller: (
        DiscoveryServiceLibraryRegistrationsController
    )
    admin_metadata_services_controller: MetadataServicesController
    admin_patron_auth_services_controller: PatronAuthServicesController
    admin_collection_settings_controller: CollectionSettingsController
    admin_sitewide_configuration_settings_controller: (
        SitewideConfigurationSettingsController
    )
    admin_library_settings_controller: LibrarySettingsController
    admin_individual_admin_settings_controller: IndividualAdminSettingsController
    admin_catalog_services_controller: CatalogServicesController
    admin_announcement_service: AnnouncementSettings
    admin_search_controller: AdminSearchController
    admin_view_controller: ViewController
    admin_quicksight_controller: QuickSightController

    @inject
    def __init__(
        self,
        _db,
        services: Services = Provide[Services],
    ):
        self._db = _db
        self.services = services
        self.analytics = services.analytics.analytics()
        self.external_search = services.search.index()
        self.site_configuration_last_update = (
            Configuration.site_configuration_last_update(self._db, timeout=0)
        )
        self.setup_one_time_controllers()
        self.load_settings()

    def load_facets_from_request(self, *args, **kwargs):
        """Load a faceting object from the incoming request, but also apply some
        application-specific access restrictions:

        * You can't use nonstandard caching rules unless you're an authenticated administrator.
        * You can't access a WorkList that's not accessible to you.
        """

        facets = load_facets_from_request(*args, **kwargs)

        worklist = kwargs.get("worklist")
        if worklist is not None:
            # Try to get the index controller. If it's not initialized
            # for any reason, don't run this check -- we have bigger
            # problems.
            index_controller = getattr(self, "index_controller", None)
            if index_controller and not worklist.accessible_to(
                index_controller.request_patron
            ):
                return NO_SUCH_LANE.detailed(_("Lane does not exist"))

        return facets

    def reload_settings_if_changed(self):
        """If the site configuration has been updated, reload the
        CirculationManager's configuration from the database.
        """
        last_update = Configuration.site_configuration_last_update(self._db)
        if last_update > self.site_configuration_last_update:
            self.load_settings()
            self.site_configuration_last_update = last_update

    @log_elapsed_time(log_level=LogLevel.info, message_prefix="load_settings")
    def load_settings(self):
        """Load all necessary configuration settings and external
        integrations from the database.

        This is called once when the CirculationManager is
        initialized.  It may also be called later to reload the site
        configuration after changes are made in the administrative
        interface.
        """
        with elapsed_time_logging(
            log_method=self.log.debug,
            skip_start=True,
            message_prefix="load_settings - load libraries",
        ):
            libraries = self._db.query(Library).all()

        with elapsed_time_logging(
            log_method=self.log.debug,
            skip_start=True,
            message_prefix="load_settings - populate caches",
        ):
            # Populate caches
            Library.cache_warm(self._db, lambda: libraries)

        self.auth = Authenticator(self._db, libraries, self.analytics)

        # Finland
        self.setup_opensearch_analytics_search()

        # Track the Lane configuration for each library by mapping its
        # short name to the top-level lane.
        new_top_level_lanes = {}
        # Create a CirculationAPI for each library.
        new_circulation_apis = {}
        # Potentially load a CustomIndexView for each library
        new_custom_index_views = {}

        with elapsed_time_logging(
            log_method=self.log.debug,
            message_prefix="load_settings - per-library lanes, custom indexes, api",
        ):
            for library in libraries:
                new_top_level_lanes[library.id] = load_lanes(self._db, library)
                new_custom_index_views[library.id] = CustomIndexView.for_library(
                    library
                )
                new_circulation_apis[library.id] = self.setup_circulation(
                    library, self.analytics
                )

        self.top_level_lanes = new_top_level_lanes
        self.circulation_apis = new_circulation_apis
        self.custom_index_views = new_custom_index_views

        # Assemble the list of patron web client domains from individual
        # library registration settings as well as a sitewide setting.
        patron_web_domains = set()

        def get_domain(url):
            url = url.strip()
            if url == "*":
                return url
            scheme, netloc, path, parameters, query, fragment = urllib.parse.urlparse(
                url
            )
            if scheme and netloc:
                return scheme + "://" + netloc
            else:
                return None

        sitewide_patron_web_client_urls = ConfigurationSetting.sitewide(
            self._db, Configuration.PATRON_WEB_HOSTNAMES
        ).value
        if sitewide_patron_web_client_urls:
            for url in sitewide_patron_web_client_urls.split("|"):
                domain = get_domain(url)
                if domain:
                    patron_web_domains.add(domain)

        domains = self._db.execute(
            select(DiscoveryServiceRegistration.web_client).where(
                DiscoveryServiceRegistration.web_client != None
            )
        ).all()
        for row in domains:
            patron_web_domains.add(get_domain(row.web_client))

        self.patron_web_domains = patron_web_domains
        self.setup_configuration_dependent_controllers()
        # Finland: Disabled cache, because the E-kirjasto authentication provider
        # will have links containing a query parameter for user's locale.
        authentication_document_cache_time = 0  # int(
        #    ConfigurationSetting.sitewide(
        #        self._db, Configuration.AUTHENTICATION_DOCUMENT_CACHE_TIME
        #    ).value_or_default(3600)
        # )
        self.authentication_for_opds_documents = ExpiringDict(
            max_len=1000, max_age_seconds=authentication_document_cache_time
        )

    # Finland
    @property
    def opensearch_analytics_search(self):
        """Retrieve or create a connection to the OpenSearch
        analytics interface.

        This is created lazily so that a failure to connect only
        affects feeds that depend on the search engine, not the whole
        circulation manager.
        """
        if not self._opensearch_analytics_search:
            self.setup_opensearch_analytics_search()
        return self._opensearch_analytics_search

    # Finland
    def setup_opensearch_analytics_search(self):
        try:
            self._opensearch_analytics_search = OpenSearchAnalyticsSearch()
            self.opensearch_analytics_search_initialization_exception = None
        except Exception as e:
            self.log.error("Exception initializing search engine: %s", e)
            self._opensearch_analytics_search = None
            self.opensearch_analytics_search_initialization_exception = e
        return self._opensearch_analytics_search

    def log_lanes(self, lanelist=None, level=0):
        """Output information about the lane layout."""
        lanelist = lanelist or self.top_level_lane.sublanes
        for lane in lanelist:
            self.log.debug("%s%r", "-" * level, lane)
            if lane.sublanes:
                self.log_lanes(lane.sublanes, level + 1)

    def setup_circulation(self, library, analytics):
        """Set up the Circulation object."""
        return CirculationAPI(self._db, library, analytics=analytics)

    def setup_one_time_controllers(self):
        """Set up all the controllers that will be used by the web app.

        This method will be called only once, no matter how many times the
        site configuration changes.
        """
        self.index_controller = IndexController(self)
        self.opds_feeds = OPDSFeedController(self)
        self.marc_records = MARCRecordController(self.services.storage.public())
        self.loans = LoanController(self)
        self.annotations = AnnotationController(self)
        self.urn_lookup = URNLookupController(self)
        self.work_controller = WorkController(self)
        self.analytics_controller = AnalyticsController(self)
        self.profiles = ProfileController(self)
        self.patron_devices = DeviceTokensController(self)
        self.version = ApplicationVersionController()
        self.odl_notification_controller = ODLNotificationController(self)
        self.patron_auth_token = PatronAuthTokenController(self)
        self.catalog_descriptions = CatalogDescriptionsController(self)
        self.playtime_entries = PlaytimeEntriesController(self)
        self.select_books = SelectBooksController(self)

    def setup_configuration_dependent_controllers(self):
        """Set up all the controllers that depend on the
        current site configuration.

        This method will be called fresh every time the site
        configuration changes.
        """
        self.saml_controller = SAMLController(self, self.auth)

        # Finland
        self.ekirjasto_controller = EkirjastoController(self, self.auth)

    def annotator(self, lane, facets=None, *args, **kwargs):
        """Create an appropriate OPDS annotator for the given lane.

        :param lane: A Lane or WorkList.
        :param facets: A faceting object.
        :param annotator_class: Instantiate this annotator class if possible.
           Intended for use in unit tests.
        """
        library = None
        if lane and isinstance(lane, Lane):
            library = lane.library
        elif lane and isinstance(lane, WorkList):
            library = lane.get_library(self._db)
        if not library and hasattr(flask.request, "library"):
            library = flask.request.library

        # If no library is provided, the best we can do is a generic
        # annotator for this application.
        if not library:
            return CirculationManagerAnnotator(lane)

        # At this point we know the request is in a library context, so we
        # can create a LibraryAnnotator customized for that library.

        # Some features are only available if a patron authentication
        # mechanism is set up for this library.
        authenticator = self.auth.library_authenticators.get(library.short_name)
        library_identifies_patrons = (
            authenticator is not None and authenticator.identifies_individuals
        )
        annotator_class = kwargs.pop("annotator_class", LibraryAnnotator)
        return annotator_class(
            self.circulation_apis[library.id],
            lane,
            library,
            top_level_title=gettext("All Books"),
            library_identifies_patrons=library_identifies_patrons,
            facets=facets,
            *args,
            **kwargs,
        )

    @property
    def authentication_for_opds_document(self):
        """Make sure the current request's library has an Authentication For
        OPDS document in the cache, then return the cached version.

        If the cache is disabled, a fresh document is created every time.

        If the query argument `debug` is provided and the
        WSGI_DEBUG_KEY site-wide setting is set to True, the
        authentication document is annotated with a '_debug' section
        describing the current WSGI environment. Since this can reveal
        internal details of deployment, it should only be enabled when
        diagnosing deployment problems.
        """
        name = flask.request.library.short_name
        value = self.authentication_for_opds_documents.get(name, None)
        if value is None:
            # The document was not in the cache, either because it's
            # expired or because the cache itself has been disabled.
            # Create a new one and stick it in the cache for next
            # time.
            value = self.auth.create_authentication_document()
            self.authentication_for_opds_documents[name] = value
        return value
