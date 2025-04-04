from __future__ import annotations

import binascii
import datetime
import json
import uuid
from abc import ABC
from collections.abc import Callable
from typing import Any, Literal, TypeVar
from functools import cached_property, partial

import dateutil
from dependency_injector.wiring import Provide, inject
from flask import url_for
from flask_babel import lazy_gettext as _
from lxml.etree import Element
from pydantic import AnyHttpUrl, HttpUrl, PositiveInt
from requests import Response
from sqlalchemy.sql.expression import or_
from uritemplate import URITemplate

from api.circulation import (
    BaseCirculationAPI,
    BaseCirculationEbookLoanSettings,
    FetchFulfillment,
    Fulfillment,
    HoldInfo,
    LoanInfo,
    PatronActivityCirculationAPI,
    RedirectFulfillment,
    UrlFulfillment,
)
from api.circulation_exceptions import *
from api.lcp.hash import Hasher, HasherFactory, HashingAlgorithm
from api.lcp.license import LicenseDocument
from api.lcp.status import LoanStatus
from api.odl_api.auth import OpdsWithOdlException
from core import util
from core.integration.settings import (
    ConfigurationFormItem,
    ConfigurationFormItemType,
    FormField,
)
from core.lcp.credential import (
    LCPCredentialFactory,
    LCPHashedPassphrase,
    LCPUnhashedPassphrase,
)
from core.metadata_layer import FormatData, LicenseData, TimestampData
from core.model import (
    Collection,
    DataSource,
    DeliveryMechanism,
    Edition,
    ExternalIntegration,
    Hold,
    Hyperlink,
    Library,
    LicensePool,
    LicensePoolDeliveryMechanism,
    Loan,
    MediaTypes,
    Representation,
    RightsStatus,
    Session,
    get_one,
)
from core.model.licensing import LicenseStatus
from core.model.patron import Patron
from core.monitor import CollectionMonitor
from core.opds_import import (
    BaseOPDSImporter,
    OPDSImporter,
    OPDSImporterSettings,
    OPDSImportMonitor,
    OPDSXMLParser,
)
from core.service.container import Services
from core.util import base64
from core.util.datetime_helpers import to_utc, utc_now
from core.util.http import HTTP, BadResponseException, RemoteIntegrationException
from core.util.log import LoggerMixin


class ODLAPIConstants:
    DEFAULT_PASSPHRASE_HINT = "View the help page for more information."
    DEFAULT_PASSPHRASE_HINT_URL = "https://lyrasis.zendesk.com/"


class ODLSettings(OPDSImporterSettings):
    external_account_id: AnyHttpUrl = FormField(
        form=ConfigurationFormItem(
            label=_("ODL feed URL"),
            required=True,
        ),
    )

    username: str = FormField(
        form=ConfigurationFormItem(
            label=_("Library's API username"),
            required=True,
        )
    )

    password: str = FormField(
        key=ExternalIntegration.PASSWORD,
        form=ConfigurationFormItem(
            label=_("Library's API password"),
            required=True,
        ),
    )

    default_reservation_period: PositiveInt | None = FormField(
        default=Collection.STANDARD_DEFAULT_RESERVATION_PERIOD,
        form=ConfigurationFormItem(
            label=_("Default Reservation Period (in Days)"),
            description=_(
                "The number of days a patron has to check out a book after a hold becomes available."
            ),
            type=ConfigurationFormItemType.NUMBER,
            required=False,
        ),
    )

    passphrase_hint: str = FormField(
        default=ODLAPIConstants.DEFAULT_PASSPHRASE_HINT,
        form=ConfigurationFormItem(
            label=_("Passphrase hint"),
            description=_(
                "Hint displayed to the user when opening an LCP protected publication."
            ),
            type=ConfigurationFormItemType.TEXT,
            required=True,
        ),
    )

    passphrase_hint_url: HttpUrl = FormField(
        default=ODLAPIConstants.DEFAULT_PASSPHRASE_HINT_URL,
        form=ConfigurationFormItem(
            label=_("Passphrase hint URL"),
            description=_(
                "Hint URL available to the user when opening an LCP protected publication."
            ),
            type=ConfigurationFormItemType.TEXT,
            required=True,
        ),
    )

    encryption_algorithm: HashingAlgorithm = FormField(
        default=HashingAlgorithm.SHA256,
        form=ConfigurationFormItem(
            label=_("Passphrase encryption algorithm"),
            description=_("Algorithm used for encrypting the passphrase."),
            type=ConfigurationFormItemType.SELECT,
            required=False,
            options={alg: alg.name for alg in HashingAlgorithm},
        ),
    )


class ODLLibrarySettings(BaseCirculationEbookLoanSettings):
    pass


SettingsType = TypeVar("SettingsType", bound=ODLSettings, covariant=True)
LibrarySettingsType = TypeVar(
    "LibrarySettingsType", bound=ODLLibrarySettings, covariant=True
)


class BaseODLAPI(PatronActivityCirculationAPI[SettingsType, LibrarySettingsType], LoggerMixin, ABC):
    """ODL (Open Distribution to Libraries) is a specification that allows
    libraries to manage their own loans and holds. It offers a deeper level
    of control to the library, but it requires the circulation manager to
    keep track of individual copies rather than just license pools, and
    manage its own holds queues.

    In addition to circulating books to patrons of a library on the current circulation
    manager, this API can be used to circulate books to patrons of external libraries.
    """

    SET_DELIVERY_MECHANISM_AT = BaseCirculationAPI.FULFILL_STEP

    # Possible status values in the License Status Document:

    # The license is available but the user hasn't fulfilled it yet.
    READY_STATUS = "ready"

    # The license is available and has been fulfilled on at least one device.
    ACTIVE_STATUS = "active"

    # The license has been revoked by the distributor.
    REVOKED_STATUS = "revoked"

    # The license has been returned early by the user.
    RETURNED_STATUS = "returned"

    # The license was returned early and was never fulfilled.
    CANCELLED_STATUS = "cancelled"

    # The license has expired.
    EXPIRED_STATUS = "expired"

    STATUS_VALUES = [
        READY_STATUS,
        ACTIVE_STATUS,
        REVOKED_STATUS,
        RETURNED_STATUS,
        CANCELLED_STATUS,
        EXPIRED_STATUS,
    ]

    @inject
    def __init__(
        self,
        _db: Session,
        collection: Collection,
        analytics: Any = Provide[Services.analytics.analytics],
    ) -> None:
        super().__init__(_db, collection)
        if collection.protocol != self.label():
            raise ValueError(
                "Collection protocol is %s, but passed into %s!"
                % (collection.protocol, self.__class__.__name__)
            )
        self.collection_id = collection.id
        settings = self.settings
        self.data_source_name = settings.data_source
        # Create the data source if it doesn't exist yet.
        DataSource.lookup(_db, self.data_source_name, autocreate=True)

        self.username = settings.username
        self.password = settings.password
        self.analytics = analytics

        self._hasher_factory = HasherFactory()
        self._credential_factory = LCPCredentialFactory()
        self._hasher_instance: Hasher | None = None

    def _get_hasher(self) -> Hasher:
        """Returns a Hasher instance

        :return: Hasher instance
        """
        settings = self.settings
        if self._hasher_instance is None:
            self._hasher_instance = self._hasher_factory.create(
                settings.encryption_algorithm
            )

        return self._hasher_instance

    def _get(
        self, url: str, headers: dict[str, str] | None = None, *args: Any, **kwargs: Any
    ) -> Response:
        """Make a normal HTTP request, but include an authentication
        header with the credentials for the collection.
        """

        username = self.username
        password = self.password
        headers = dict(headers or {})
        auth_header = "Basic %s" % base64.b64encode(f"{username}:{password}")
        headers["Authorization"] = auth_header

        return HTTP.get_with_timeout(
            url, headers=headers, timeout=30
        )  # Added a bigger timeout for loan checkouts

    def _url_for(self, *args: Any, **kwargs: Any) -> str:
        """Wrapper around flask's url_for to be overridden for tests."""
        return url_for(*args, **kwargs)

    def get_license_status_document(self, loan: Loan) -> dict[str, Any]:
        """Get the License Status Document for a loan.

        For a new loan, create a local loan with no external identifier and
        pass it in to this method.

        This will create the remote loan if one doesn't exist yet. The loan's
        internal database id will be used to receive notifications from the
        distributor when the loan's status changes.
        """
        _db = Session.object_session(loan)

        if loan.external_identifier:
            url = loan.external_identifier
        else:
            id = loan.license.identifier
            checkout_id = str(uuid.uuid1())
            if self.collection is None:
                raise ValueError(f"Collection not found: {self.collection_id}")
            default_loan_period = self.collection.default_loan_period(
                loan.patron.library
            )

            expires = utc_now() + datetime.timedelta(days=default_loan_period)
            # The patron UUID is generated randomly on each loan, so the distributor
            # doesn't know when multiple loans come from the same patron.
            patron_id = str(uuid.uuid1())

            library_short_name = loan.patron.library.short_name

            db = Session.object_session(loan)
            patron = loan.patron
            hasher = self._get_hasher()

            unhashed_pass: LCPUnhashedPassphrase = (
                self._credential_factory.get_patron_passphrase(db, patron)
            )
            hashed_pass: LCPHashedPassphrase = unhashed_pass.hash(hasher)
            self._credential_factory.set_hashed_passphrase(db, patron, hashed_pass)
            encoded_pass: str = base64.b64encode(binascii.unhexlify(hashed_pass.hashed))

            notification_url = self._url_for(
                "odl_notify",
                library_short_name=library_short_name,
                loan_id=loan.id,
                _external=True,
            )

            checkout_url = str(loan.license.checkout_url)
            url_template = URITemplate(checkout_url)
            url = url_template.expand(
                id=str(id),
                checkout_id=checkout_id,
                patron_id=patron_id,
                expires=expires.isoformat(),
                notification_url=notification_url,
                passphrase=encoded_pass,
                hint=self.settings.passphrase_hint,
                hint_url=self.settings.passphrase_hint_url,
            )

        try:
            response = self._get(url, allowed_response_codes=["2xx"])
        except BadResponseException as e:
            response = e.response
            header_string = ", ".join(
                {f"{k}: {v}" for k, v in response.headers.items()}
            )
            response_string = (
                response.text
                if len(response.text) < 100
                else response.text[:100] + "..."
            )
            raise BadResponseException(
                url,
                f"Error getting License Status Document for loan ({loan.id}):  Url '{url}' returned "
                f"status code {response.status_code}. Expected 2XX. Response headers: {header_string}. "
                f"Response content: {response_string}.",
                response,
            )

        try:
            status_doc = json.loads(response.content)
        except ValueError as e:
            raise RemoteIntegrationException(
                url, "License Status Document was not valid JSON."
            ) from e
        if status_doc.get("status") not in self.STATUS_VALUES:
            raise RemoteIntegrationException(
                url, "License Status Document had an unknown status value."
            )
        return status_doc  # type: ignore[no-any-return]

    @staticmethod
    def _notification_url(
        short_name: str | None, license_id: str
    ) -> str:
        """Get the notification URL that should be passed in the ODL checkout link.

        This is broken out into a separate function to make it easier to override
        in tests.
        """
        return url_for(
            "odl_notify",
            library_short_name=short_name,
            license_identifier=license_id,
            _external=True,
        )

    def _request_loan_status(
        self, url: str, ignored_problem_types: list[str] | None = None
    ) -> LoanStatus:
        try:
            self.log.info("url: ", url)
            response = self._get(url, allowed_response_codes=["2xx"])
            status_doc = LoanStatus.from_json(response.content)
            self.log.info(f"status_doc: {status_doc}")
        except Exception as e:
            self.log.exception(
                f"Error validating Loan Status Document. '{url}' returned and invalid document. {e}"
            )
            raise RemoteIntegrationException(
                url, "Loan Status Document not valid."
            ) from e
        except BadResponseException as e:
            response = e.response
            error_message = f"Error requesting Loan Status Document. '{url}' returned status code {response.status_code}."
            if isinstance(e, OpdsWithOdlException):
                # It this problem type is explicitly ignored, we just raise the exception instead of proceeding with
                # self.logging the information about it. The caller will handle the exception.
                if ignored_problem_types and e.type in ignored_problem_types:
                    self.log.debug(f"ignored: {e.type}")
                    raise
                error_message += f" Problem Detail: '{e.type}' - {e.title}"
                if e.detail:
                    self.log.debug(f"e detail: {e.detail}")
                    error_message += f" - {e.detail}"
            else:
                header_string = ", ".join(
                    {f"{k}: {v}" for k, v in response.headers.items()}
                )
                response_string = (
                    response.text
                    if len(response.text) < 100
                    else response.text[:100] + "..."
                )
                error_message += f" Response headers: {header_string}. Response content: {response_string}."
            self.log.exception(error_message)
            raise
        return status_doc

    def checkin(self, patron: Patron, pin: str, licensepool: LicensePool) -> None:
        """Return a loan early."""
        _db = Session.object_session(patron)

        loan = (
            _db.query(Loan)
            .filter(Loan.patron == patron)
            .filter(Loan.license_pool_id == licensepool.id)
        )
        if loan.count() < 1:
            raise NotCheckedOut()
        loan_result = loan.one()

        if licensepool.open_access or licensepool.unlimited_access:
            # If this is an open-access book, we don't need to do anything.
            return

        self._checkin(loan_result)

    def _checkin(self, loan: Loan) -> None:
        _db = Session.object_session(loan)
        if loan.external_identifier is None:
            # We can't return a loan that doesn't have an external identifier. This should never happen
            # but if it does, we self.log an error and continue on, so it doesn't stay on the patrons
            # bookshelf forever.
            self.self.log.error(f"Loan {loan.id} has no external identifier.")
            return
        if loan.license is None:
            # We can't return a loan that doesn't have a license. This should never happen but if it does,
            # we self.log an error and continue on, so it doesn't stay on the patrons bookshelf forever.
            self.self.log.error(f"Loan {loan.id} has no license.")
            return

        loan_status = self._request_loan_status(loan.external_identifier)
        if not loan_status.active:
            self.self.log.warning(
                f"Loan {loan.id} was {loan_status.status} was already returned early, revoked by the distributor, or it expired."
            )
            loan.license.checkin()
            loan.license_pool.update_availability_from_licenses()
            return

        return_link = loan_status.links.get(rel="return", content_type=LoanStatus.content_type())
        if not return_link:
            self.log.info("no return link")
            # The distributor didn't provide a link to return this loan. This means that the distributor
            # does not support early returns, and the patron will have to wait until the loan expires.
            raise CannotReturn()

        # The parameters for this link (if its templated) are defined here:
        # https://readium.org/lcp-specs/releases/lsd/latest.html#34-returning-a-publication
        # None of them are required, and often the link is not templated. But in the case
        # of the open source LCP server, the link is templated, so we need to process the
        # template before we can make the request.
        return_url = return_link.href
        self.log.info("return url: ", return_url)

        # Hit the distributor's return link, and if it's successful, update the pool
        # availability.
        loan_status = self._request_loan_status(return_url)
        if loan_status.active:
            # If the distributor says the loan is still active, we didn't return it, and
            # something went wrong. We self.log an error and don't delete the loan, so the patron
            # can try again later.
            self.self.log.error(
                f"Loan {loan.id} was {loan_status.status} not returned. The distributor says it's still active. {loan_status}"
            )
            raise CannotReturn()
        loan.license.checkin()
        self.update_licensepool(loan.license_pool)

    def checkout(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        delivery_mechanism: LicensePoolDeliveryMechanism,
    ) -> LoanInfo:
        """Create a new loan."""
        _db = Session.object_session(patron)

        loan = (
            _db.query(Loan)
            .filter(Loan.patron == patron)
            .filter(Loan.license_pool_id == licensepool.id)
        )
        if loan.count() > 0:
            raise AlreadyCheckedOut()

        if licensepool.open_access or licensepool.unlimited_access:
            return LoanInfo(
                licensepool.collection,
                licensepool.data_source.name,
                licensepool.identifier.type,
                licensepool.identifier.identifier,
                start_date = None,
                end_date = None,
                external_identifier = None
            )
        else:
            hold = get_one(_db, Hold, patron=patron, license_pool_id=licensepool.id)
            return self._checkout(patron, licensepool, hold)

    def _checkout(
        self, patron: Patron, licensepool: LicensePool, hold: Hold | None = None
    ) -> Loan:
        db = Session.object_session(patron)

        if not any(l for l in licensepool.licenses if not l.is_inactive):
            raise NoLicenses()

        # Make sure pool info is updated.
        self.update_licensepool(licensepool)

        # If there's a holds queue, the patron must have a non-expired hold
        # with position 0 to check out the book.
        if (
            not hold
            or (hold.position and hold.position > 0)
            or (hold.end and hold.end < utc_now())
        ) and licensepool.licenses_available < 1:
            raise NoAvailableCopies()

        if self.collection is None:
            raise ValueError(f"Collection not found: {self.collection_id}")
        default_loan_period = self.collection.default_loan_period(patron.library)
        self.log.info(f"Collection: {self.collection}, Loan period: {default_loan_period}")
        requested_expiry = utc_now() + datetime.timedelta(days=default_loan_period)
        self.log.info(f"Requested expiry: {requested_expiry}")
        patron_id = patron.identifier_to_remote_service(licensepool.data_source)
        library_short_name = patron.library.short_name
        hasher = self._get_hasher()
        unhashed_pass: LCPUnhashedPassphrase = (
            self._credential_factory.get_patron_passphrase(db, patron)
        )
        hashed_pass: LCPHashedPassphrase = unhashed_pass.hash(hasher)
        self._credential_factory.set_hashed_passphrase(db, patron, hashed_pass)
        encoded_pass = base64.b64encode(binascii.unhexlify(hashed_pass.hashed))

        licenses = licensepool.best_available_licenses()
        for l in licenses:
            print(f"Amount: {len(licenses)}, license {l.identifier}")

        license_: License | None = None
        loan_status: LoanStatus | None = None
        for license_ in licenses:
            try:
                self.log.info(f"Trying license: {license_.identifier}")
                loan_status = self._checkout_license(
                    license_,
                    library_short_name,
                    patron_id,
                    requested_expiry.isoformat(),
                    encoded_pass,
                )
                break
            except NoAvailableCopies:
                # This license had no available copies, so we try the next one.
                ...

        if license_ is None or loan_status is None:
            # It could be that we have a hold which means we thought the book was available, but it wasn't. We raise a NoAvailableCopies() and have the handler handle the patron's hold position.
            self.log.warning(f"License or status was nothing")
            licensepool.update_availability_from_licenses()
            raise NoAvailableCopies()

        if not loan_status.active:
            # Something went wrong with this loan, and we don't actually
            # have the book checked out. This should never happen.
            self.log.warning(f"Loan status for license {license_.identifier} was {loan_status.status} instead of active")
            raise CannotLoan()

        # We save the link to the loan status document in the loan's external_identifier field, so
        # we are able to retrieve it later.
        loan_status_document_link: Link | None = loan_status.links.get(
            rel="self", content_type=LoanStatus.content_type()
        )

        if not loan_status_document_link:
            self.log.warning(f"There was no loan status link for license {license_.identifier}")
            raise CannotLoan()

        loan = LoanInfo(
            licensepool.collection,
            licensepool.data_source.name,
            licensepool.identifier.type,
            licensepool.identifier.identifier,
            start_date=utc_now(),
            end_date=loan_status.potential_rights.end,
            external_identifier=loan_status_document_link.href,
        )
        print(f"loan info in odl: {loan}")

        # collection: Collection | int,
        # data_source_name: str | DataSource | None,
        # identifier_type: str | None,
        # identifier: str | None,
        # start_date: datetime.datetime | None,
        # end_date: datetime.datetime | None,
        # fulfillment_info: FulfillmentInfo | None = None,
        # external_identifier: str | None = None,
        # locked_to: DeliveryMechanismInfo | None = None,

        license_.loan_to(patron)
        # We also need to update the remaining checkouts for the license.
        license_.checkout()

        self.log.info(f"License {license_.identifier}: checkouts left after loan: {license_.checkouts_left}")

        # If there was a hold CirculationAPI will take care of deleting it. So we just need to
        # update the license pool to reflect the loan. Since update_availability_from_licenses
        # takes into account holds, we need to tell it to ignore the hold about to be deleted.
        licensepool.update_availability_from_licenses()
        return loan

    def _checkout_license(
        self,
        license_: License,
        library_short_name: str | None,
        patron_id: str,
        expiry: str,
        encoded_pass: str,
    ) -> LoanStatus:
        identifier = str(license_.identifier)
        checkout_id = str(uuid.uuid4())
        notification_url = self._notification_url(
            library_short_name,
            identifier,
        )

        # We should never be able to get here if the license doesn't have a checkout_url, but
        # we assert it anyway, to be sure we fail fast if it happens.
        assert license_.checkout_url is not None
        url_template = URITemplate(license_.checkout_url)
        checkout_url = url_template.expand(
            id=identifier,
            checkout_id=checkout_id,
            patron_id=patron_id,
            expires=expiry,
            notification_url=notification_url,
            passphrase=encoded_pass,
            hint=self.settings.passphrase_hint,
            hint_url=self.settings.passphrase_hint_url,
        )

        try:
            loan_status = self._request_loan_status(
                checkout_url,
                ignored_problem_types=[
                    "http://opds-spec.org/odl/error/checkout/unavailable"
                ],
            )
            self.log.info(f"Loan status: {loan_status}")
            return loan_status
        except OpdsWithOdlException as e:
            if e.type == "http://opds-spec.org/odl/error/checkout/unavailable":
                # TODO: This would be a good place to do an async availability update, since we know
                #   the book is unavailable, when we thought it was available. For now, we know that
                #   the license has no checkouts_available, so we do that update.
                license_.checkouts_available = 0
                raise NoAvailableCopies() from e
            raise

    def fulfill(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        delivery_mechanism: LicensePoolDeliveryMechanism,
    ) -> FulfillmentInfo:
        """Get the actual resource file to the patron."""
        _db = Session.object_session(patron)

        loan = (
            _db.query(Loan)
            .filter(Loan.patron == patron)
            .filter(Loan.license_pool_id == licensepool.id)
        ).one()
        return self._fulfill(loan, delivery_mechanism)

    @staticmethod
    def _get_resource_for_delivery_mechanism(
        requested_delivery_mechanism: DeliveryMechanism, licensepool: LicensePool
    ) -> Resource:
        resource = next(
            (
                lpdm.resource
                for lpdm in licensepool.available_delivery_mechanisms
                if lpdm.delivery_mechanism == requested_delivery_mechanism
                and lpdm.resource is not None
            ),
            None,
        )
        if resource is None:
            raise FormatNotAvailable()
        return resource

    def _unlimited_access_fulfill(
        self, loan: Loan, delivery_mechanism: LicensePoolDeliveryMechanism
    ) -> Fulfillment:
        licensepool = loan.license_pool
        resource = self._get_resource_for_delivery_mechanism(
            delivery_mechanism.delivery_mechanism, licensepool
        )
        if resource.representation is None:
            raise FormatNotAvailable()
        content_link = resource.representation.public_url
        content_type = resource.representation.media_type
        return RedirectFulfillment(content_link, content_type) # Tää pitää kattoo, ei ole circulation.py:ssä


    def _license_fulfill(
        self, loan: Loan, delivery_mechanism: LicensePoolDeliveryMechanism
    ) -> Fulfillment:
        # We are unable to fulfill a loan that doesn't have its external identifier set,
        # We are unable to fulfill a loan that doesn't have its external identifier set,
        # since we use this to get to the checkout link. It shouldn't be possible to get
        # into this state.
        license_status_url = loan.external_identifier
        assert license_status_url is not None

        loan_status = self._request_loan_status(license_status_url)

        if not loan_status.active:
            # This loan isn't available for some reason. It's possible
            # the distributor revoked it or the patron already returned it
            # through the DRM system, and we didn't get a notification
            # from the distributor yet.
            db = Session.object_session(loan)
            db.delete(loan)
            raise CannotFulfill()

        drm_scheme = delivery_mechanism.delivery_mechanism.drm_scheme
        fulfill_cls: Callable[[str, str | None], UrlFulfillment]
        if drm_scheme == DeliveryMechanism.NO_DRM:
            # If we have no DRM, we can just redirect to the content link and let the patron download the book.
            fulfill_link = loan_status.links.get(
                rel="publication",
                content_type=delivery_mechanism.delivery_mechanism.content_type,
            )
            fulfill_cls = RedirectFulfillment
        elif drm_scheme == DeliveryMechanism.FEEDBOOKS_AUDIOBOOK_DRM:
            # For DeMarque audiobook content using "FEEDBOOKS_AUDIOBOOK_DRM", the link
            # we are looking for is stored in the 'manifest' rel.
            fulfill_link = loan_status.links.get(rel="manifest", content_type=FEEDBOOKS_AUDIO)
            fulfill_cls = partial(FetchFulfillment, allowed_response_codes=["2xx"])
        else:
            # We are getting content via a license loan_statusument, so we need to find the link
            # that corresponds to the delivery mechanism we are using.
            fulfill_link = loan_status.links.get(rel="license", content_type=drm_scheme)
            fulfill_cls = partial(FetchFulfillment, allowed_response_codes=["2xx"])

        if fulfill_link is None:
            raise CannotFulfill()

        return fulfill_cls(fulfill_link.href, fulfill_link.content_type)

    def _fulfill(
        self,
        loan: Loan,
        delivery_mechanism: LicensePoolDeliveryMechanism,
    ) -> Fulfillment:
        if loan.license_pool.open_access or loan.license_pool.unlimited_access:
                return self._unlimited_access_fulfill(loan, delivery_mechanism)
        else:
            return self._license_fulfill(loan, delivery_mechanism)


    def _count_holds_before(self, holdinfo: HoldInfo, pool: LicensePool) -> int:
        # Count holds on the license pool that started before this hold and
        # aren't expired.
        _db = Session.object_session(pool)
        return (
            _db.query(Hold)
            .filter(Hold.license_pool_id == pool.id)
            .filter(Hold.start < holdinfo.start_date)
            .filter(
                or_(
                    Hold.end == None,
                    Hold.end > utc_now(),
                    Hold.position > 0,
                )
            )
            .count()
        )

    def _update_hold_data(self, hold: Hold) -> None:
        pool: LicensePool = hold.license_pool
        holdinfo = HoldInfo(
            pool.collection,
            pool.data_source.name,
            pool.identifier.type,
            pool.identifier.identifier,
            hold.start,
            hold.end,
            hold.position,
        )
        library = hold.patron.library
        self._update_hold_end_date(holdinfo, pool, library=library)
        hold.end = holdinfo.end_date
        hold.position = holdinfo.hold_position
        print(f"hold end: {hold.end}, position: {hold.position}")

    def _update_hold_end_date(
        self, holdinfo: HoldInfo, pool: LicensePool, library: Library
    ) -> None:
        _db = Session.object_session(pool)

        # First make sure the hold position is up-to-date, since we'll
        # need it to calculate the end date.
        original_position = holdinfo.hold_position
        self._update_hold_position(holdinfo, pool)
        assert holdinfo.hold_position is not None
        if self.collection is None:
            raise ValueError(f"Collection not found: {self.collection_id}")
        default_loan_period = self.collection.default_loan_period(library)
        default_reservation_period = self.collection.default_reservation_period

        # If the hold was already to check out and already has an end date,
        # it doesn't need an update.
        if holdinfo.hold_position == 0 and original_position == 0 and holdinfo.end_date:
            print("no update needed")
            return

        # If the patron is in the queue, we need to estimate when the book
        # will be available for check out. We can do slightly better than the
        # default calculation since we know when all current loans will expire,
        # but we're still calculating the worst case.
        elif holdinfo.hold_position > 0:
            # Find the current loans and reserved holds for the licenses.
            current_loans = (
                _db.query(Loan)
                .filter(Loan.license_pool_id == pool.id)
                .filter(or_(Loan.end == None, Loan.end > utc_now()))
                .order_by(Loan.start)
                .all()
            )
            current_holds = (
                _db.query(Hold)
                .filter(Hold.license_pool_id == pool.id)
                .filter(
                    or_(
                        Hold.end == None,
                        Hold.end > utc_now(),
                        Hold.position > 0,
                    )
                )
                .order_by(Hold.start)
                .all()
            )
            assert pool.licenses_owned is not None
            licenses_reserved = min(
                pool.licenses_owned - len(current_loans), len(current_holds)
            )
            current_reservations = current_holds[:licenses_reserved]

            # The licenses will have to go through some number of cycles
            # before one of them gets to this hold. This leavs out the first cycle -
            # it's already started so we'll handle it separately.
            cycles = (
                holdinfo.hold_position - licenses_reserved - 1
            ) // pool.licenses_owned

            # Each of the owned licenses is currently either on loan or reserved.
            # Figure out which license this hold will eventually get if every
            # patron keeps their loans and holds for the maximum time.
            copy_index = (
                holdinfo.hold_position - licenses_reserved - 1
            ) % pool.licenses_owned

            # In the worse case, the first cycle ends when a current loan expires, or
            # after a current reservation is checked out and then expires.
            if len(current_loans) > copy_index:
                next_cycle_start = current_loans[copy_index].end
            else:
                reservation = current_reservations[copy_index - len(current_loans)]
                next_cycle_start = reservation.end + datetime.timedelta(
                    days=default_loan_period
                )
            self.log.info(f"loans: {current_loans} holds: {current_holds} licenses reserved: {licenses_reserved}")
            self.log.info(f"current reservations: {current_reservations} cycles: {cycles} copy index: {copy_index} next cycle strt: {next_cycle_start}")
            # Assume all cycles after the first cycle take the maximum time.
            cycle_period = default_loan_period + default_reservation_period
            holdinfo.end_date = next_cycle_start + datetime.timedelta(
                days=(cycle_period * cycles)
            )

        # If the end date isn't set yet or the position just became 0, the
        # hold just became available. The patron's reservation period starts now.
        else:
            holdinfo.end_date = utc_now() + datetime.timedelta(
                days=default_reservation_period
            )

    def _update_hold_position(self, holdinfo: HoldInfo, pool: LicensePool) -> None:
        _db = Session.object_session(pool)
        loans_count = (
            _db.query(Loan)
            .filter(
                Loan.license_pool_id == pool.id,
            )
            .filter(or_(Loan.end == None, Loan.end > utc_now()))
            .count()
        )
        holds_count = self._count_holds_before(holdinfo, pool)

        assert pool.licenses_owned is not None
        remaining_licenses = pool.licenses_owned - loans_count

        if remaining_licenses > holds_count:
            # The hold is ready to check out.
            holdinfo.hold_position = 0

        else:
            # Add 1 since position 0 indicates the hold is ready.
            holdinfo.hold_position = holds_count + 1

    def update_licensepool(self, licensepool: LicensePool) -> None:
        # Update the pool and the next holds in the queue when a license is reserved.
        licensepool.update_availability_from_licenses(
            as_of=utc_now(),
        )
        holds = licensepool.get_active_holds()
        print(f"update_licensepool: holds: {holds}, reserved: {licensepool.licenses_reserved}")
        for hold in holds[: licensepool.licenses_reserved]:
            if hold.position != 0:
                # This hold just got a reserved license.
                print(f"holds: {holds}, reserved: {licensepool.licenses_reserved}, position: {hold.position}")
                self._update_hold_data(hold)

    def place_hold(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        notification_email_address: str | None,
    ) -> HoldInfo:
        """Create a new hold."""
        return self._place_hold(patron, licensepool)

    def _place_hold(self, patron: Patron, licensepool: LicensePool) -> HoldInfo:
        _db = Session.object_session(patron)

        # Make sure pool info is updated.
        self.update_licensepool(licensepool)

        if licensepool.licenses_available > 0:
            raise CurrentlyAvailable()

        # Check for local hold
        hold = get_one(
            _db,
            Hold,
            patron_id=patron.id,
            license_pool_id=licensepool.id,
        )

        if hold is not None:
            raise AlreadyOnHold()

        patrons_in_hold_queue = (
            licensepool.patrons_in_hold_queue
            if licensepool.patrons_in_hold_queue
            else 0
        )
        licensepool.patrons_in_hold_queue = patrons_in_hold_queue + 1
        holdinfo = HoldInfo(
            licensepool.collection,
            licensepool.data_source.name,
            licensepool.identifier.type,
            licensepool.identifier.identifier,
            start_date=utc_now(),
            end_date=None,
            hold_position=licensepool.patrons_in_hold_queue,
        )
        library = patron.library
        self._update_hold_end_date(holdinfo, licensepool, library=library) # MIKSI feilaa

        return holdinfo

    def release_hold(self, patron: Patron, pin: str, licensepool: LicensePool) -> None:
        """Cancel a hold."""
        _db = Session.object_session(patron)

        hold = get_one(
            _db,
            Hold,
            license_pool_id=licensepool.id,
            patron=patron,
        )
        if not hold:
            raise NotOnHold()
        self._release_hold(hold)

    def _release_hold(self, hold: Hold) -> Literal[True]:
        # If the book was ready and the patron revoked the hold instead
        # of checking it out, but no one else had the book on hold, the
        # book is now available for anyone to check out. If someone else
        # had a hold, the license is now reserved for the next patron.
        # If someone else had a hold, the license is now reserved for the
        # next patron, and we need to update that hold.
        _db = Session.object_session(hold)
        licensepool = hold.license_pool
        _db.delete(hold)
        self.update_licensepool(licensepool)
        return True

    def patron_activity(self, patron: Patron, pin: str) -> list[LoanInfo | HoldInfo]:
        """Look up non-expired loans for this collection in the database."""
        _db = Session.object_session(patron)
        loans = (
            _db.query(Loan)
            .join(Loan.license_pool)
            .filter(LicensePool.collection_id == self.collection_id)
            .filter(Loan.patron == patron)
            .filter(
                or_(
                    Loan.end >= utc_now(),
                    Loan.end == None,
                )
            )
        )

        # Get the patron's holds. If there are any expired holds, delete them.
        # Update the end date and position for the remaining holds.
        holds = (
            _db.query(Hold)
            .join(Hold.license_pool)
            .filter(LicensePool.collection_id == self.collection_id)
            .filter(Hold.patron == patron)
        )
        remaining_holds = []
        for hold in holds:
            if hold.end and hold.end < utc_now():
                _db.delete(hold)
                self.update_licensepool(hold.license_pool)
            else:
                self._update_hold_data(hold)
                remaining_holds.append(hold)

        return [
            LoanInfo(
                loan.license_pool.collection,
                loan.license_pool.data_source.name,
                loan.license_pool.identifier.type,
                loan.license_pool.identifier.identifier,
                loan.start,
                loan.end,
                external_identifier=loan.external_identifier,
            )
            for loan in loans
        ] + [
            HoldInfo(
                hold.license_pool.collection,
                hold.license_pool.data_source.name,
                hold.license_pool.identifier.type,
                hold.license_pool.identifier.identifier,
                start_date=hold.start,
                end_date=hold.end,
                hold_position=hold.position,
            )
            for hold in remaining_holds
        ]

    def update_loan(self, loan: Loan, status_doc: dict[str, Any] | None = None) -> None:
        """Check a loan's status, and if it is no longer active, delete the loan
        and update its pool's availability.
        """
        _db = Session.object_session(loan)

        if not status_doc:
            status_doc = self.get_license_status_document(loan)

        status = status_doc.get("status")
        # We already check that the status is valid in get_license_status_document,
        # but if the document came from a notification it hasn't been checked yet.
        if status not in self.STATUS_VALUES:
            raise BadResponseException(
                str(loan.license.checkout_url),
                "The License Status Document had an unknown status value.",
            )

        if status in [
            self.REVOKED_STATUS,
            self.RETURNED_STATUS,
            self.CANCELLED_STATUS,
            self.EXPIRED_STATUS,
        ]:
            # This loan is no longer active. Update the pool's availability
            # and delete the loan.

            # Update the license
            loan.license.checkin()

            # If there are holds, the license is reserved for the next patron.
            _db.delete(loan)
            self.update_licensepool(loan.license_pool)

    def update_availability(self, licensepool: LicensePool) -> None:
        pass


class ODLAPI(
    BaseODLAPI[ODLSettings, ODLLibrarySettings],
):
    """ODL (Open Distribution to Libraries) is a specification that allows
    libraries to manage their own loans and holds. It offers a deeper level
    of control to the library, but it requires the circulation manager to
    keep track of individual copies rather than just license pools, and
    manage its own holds queues.

    In addition to circulating books to patrons of a library on the current circulation
    manager, this API can be used to circulate books to patrons of external libraries.
    """

    @classmethod
    def settings_class(cls) -> type[ODLSettings]:
        return ODLSettings

    @classmethod
    def library_settings_class(cls) -> type[ODLLibrarySettings]:
        return ODLLibrarySettings

    @classmethod
    def label(cls) -> str:
        return ExternalIntegration.ODL

    @classmethod
    def description(cls) -> str:
        return "Import books from a distributor that uses ODL (Open Distribution to Libraries)."


class ODLXMLParser(OPDSXMLParser):
    NAMESPACES = dict(OPDSXMLParser.NAMESPACES, odl="http://opds-spec.org/odl")


class BaseODLImporter(BaseOPDSImporter[SettingsType], ABC):
    FEEDBOOKS_AUDIO = "{}; protection={}".format(
        MediaTypes.AUDIOBOOK_MANIFEST_MEDIA_TYPE,
        DeliveryMechanism.FEEDBOOKS_AUDIOBOOK_DRM,
    )

    CONTENT_TYPE = "content-type"
    DRM_SCHEME = "drm-scheme"

    LICENSE_FORMATS = {
        FEEDBOOKS_AUDIO: {
            CONTENT_TYPE: MediaTypes.AUDIOBOOK_MANIFEST_MEDIA_TYPE,
            DRM_SCHEME: DeliveryMechanism.FEEDBOOKS_AUDIOBOOK_DRM,
        }
    }

    @classmethod
    def fetch_license_info(
        cls, document_link: str, do_get: Callable[..., tuple[int, Any, bytes]]
    ) -> dict[str, Any] | None:
        status_code, _, response = do_get(document_link, headers={})
        if status_code in (200, 201):
            license_info_document = json.loads(response)
            return license_info_document  # type: ignore[no-any-return]
        else:
            cls.logger().warning(
                f"License Info Document is not available. "
                f"Status link {document_link} failed with {status_code} code."
            )
            return None

    @classmethod
    def parse_license_info(
        cls,
        license_info_document: dict[str, Any],
        license_info_link: str,
        checkout_link: str | None,
    ) -> LicenseData | None:
        """Check the license's attributes passed as parameters:
        - if they're correct, turn them into a LicenseData object
        - otherwise, return a None

        :param license_info_document: License Info Document
        :param license_info_link: Link to fetch License Info Document
        :param checkout_link: License's checkout link

        :return: LicenseData if all the license's attributes are correct, None, otherwise
        """

        identifier = license_info_document.get("identifier")
        document_status = license_info_document.get("status")
        document_checkouts = license_info_document.get("checkouts", {})
        document_left = document_checkouts.get("left")
        document_available = document_checkouts.get("available")
        document_terms = license_info_document.get("terms", {})
        document_expires = document_terms.get("expires")
        document_concurrency = document_terms.get("concurrency")
        document_format = license_info_document.get("format")

        if identifier is None:
            cls.logger().error("License info document has no identifier.")
            return None

        expires = None
        if document_expires is not None:
            expires = dateutil.parser.parse(document_expires)
            expires = util.datetime_helpers.to_utc(expires)

        if document_status is not None:
            status = LicenseStatus.get(document_status)
            if status.value != document_status:
                cls.logger().warning(
                    f"Identifier # {identifier} unknown status value "
                    f"{document_status} defaulting to {status.value}."
                )
        else:
            status = LicenseStatus.unavailable
            cls.logger().warning(
                f"Identifier # {identifier} license info document does not have "
                f"required key 'status'."
            )

        if document_available is not None:
            available = int(document_available)
        else:
            available = 0
            cls.logger().warning(
                f"Identifier # {identifier} license info document does not have "
                f"required key 'checkouts.available'."
            )

        left = None
        if document_left is not None:
            left = int(document_left)

        concurrency = None
        if document_concurrency is not None:
            concurrency = int(document_concurrency)

        content_types = None
        if document_format is not None:
            if isinstance(document_format, str):
                content_types = [document_format]
            elif isinstance(document_format, list):
                content_types = document_format

        return LicenseData(
            identifier=identifier,
            checkout_url=checkout_link,
            status_url=license_info_link,
            expires=expires,
            checkouts_left=left,
            checkouts_available=available,
            status=status,
            terms_concurrency=concurrency,
            content_types=content_types,
        )

    @classmethod
    def get_license_data(
        cls,
        license_info_link: str,
        checkout_link: str | None,
        feed_license_identifier: str | None,
        feed_license_expires: datetime.datetime | None,
        feed_concurrency: int | None,
        do_get: Callable[..., tuple[int, Any, bytes]],
    ) -> LicenseData | None:
        license_info_document = cls.fetch_license_info(license_info_link, do_get)

        if not license_info_document:
            return None

        parsed_license = cls.parse_license_info(
            license_info_document, license_info_link, checkout_link
        )

        if not parsed_license:
            return None

        if parsed_license.identifier != feed_license_identifier:
            # There is a mismatch between the license info document and
            # the feed we are importing. Since we don't know which to believe
            # we log an error and continue.
            cls.logger().error(
                f"Mismatch between license identifier in the feed ({feed_license_identifier}) "
                f"and the identifier in the license info document "
                f"({parsed_license.identifier}) ignoring license completely."
            )
            return None

        if parsed_license.expires != feed_license_expires:
            cls.logger().error(
                f"License identifier {feed_license_identifier}. Mismatch between license "
                f"expiry in the feed ({feed_license_expires}) and the expiry in the license "
                f"info document ({parsed_license.expires}) setting license status "
                f"to unavailable."
            )
            parsed_license.status = LicenseStatus.unavailable

        if parsed_license.terms_concurrency != feed_concurrency:
            cls.logger().error(
                f"License identifier {feed_license_identifier}. Mismatch between license "
                f"concurrency in the feed ({feed_concurrency}) and the "
                f"concurrency in the license info document ("
                f"{parsed_license.terms_concurrency}) setting license status "
                f"to unavailable."
            )
            parsed_license.status = LicenseStatus.unavailable

        return parsed_license


class ODLImporter(OPDSImporter, BaseODLImporter[ODLSettings]):
    """Import information and formats from an ODL feed.

    The only change from OPDSImporter is that this importer extracts
    format information from 'odl:license' tags.
    """

    NAME = ODLAPI.label()
    PARSER_CLASS = ODLXMLParser

    # The media type for a License Info Document, used to get information
    # about the license.
    LICENSE_INFO_DOCUMENT_MEDIA_TYPE = "application/vnd.odl.info+json"

    @classmethod
    def settings_class(cls) -> type[ODLSettings]:
        return ODLSettings

    @classmethod
    def _detail_for_elementtree_entry(
        cls,
        parser: OPDSXMLParser,
        entry_tag: Element,
        feed_url: str | None = None,
        do_get: Callable[..., tuple[int, Any, bytes]] | None = None,
    ) -> dict[str, Any]:
        do_get = do_get or Representation.cautious_http_get

        # TODO: Review for consistency when updated ODL spec is ready.
        subtag = parser.text_of_optional_subtag
        data = OPDSImporter._detail_for_elementtree_entry(parser, entry_tag, feed_url)
        formats = []
        licenses = []

        odl_license_tags = parser._xpath(entry_tag, "odl:license") or []
        medium = None
        for odl_license_tag in odl_license_tags:
            identifier = subtag(odl_license_tag, "dcterms:identifier")
            full_content_type = subtag(odl_license_tag, "dcterms:format")

            if not medium:
                medium = Edition.medium_from_media_type(full_content_type)

            # By default, dcterms:format includes the media type of a
            # DRM-free resource.
            content_type = full_content_type
            drm_schemes: list[str | None] = []

            # But it may instead describe an audiobook protected with
            # the Feedbooks access-control scheme.
            if full_content_type == cls.FEEDBOOKS_AUDIO:
                content_type = MediaTypes.AUDIOBOOK_MANIFEST_MEDIA_TYPE
                drm_schemes.append(DeliveryMechanism.FEEDBOOKS_AUDIOBOOK_DRM)

            # Additional DRM schemes may be described in <odl:protection>
            # tags.
            protection_tags = parser._xpath(odl_license_tag, "odl:protection") or []
            for protection_tag in protection_tags:
                drm_scheme = subtag(protection_tag, "dcterms:format")
                if drm_scheme:
                    drm_schemes.append(drm_scheme)

            for drm_scheme in drm_schemes or [None]:
                formats.append(
                    FormatData(
                        content_type=content_type,
                        drm_scheme=drm_scheme,
                        rights_uri=RightsStatus.IN_COPYRIGHT,
                    )
                )

            data["medium"] = medium

            checkout_link = None
            for link_tag in parser._xpath(odl_license_tag, "odl:tlink") or []:
                rel = link_tag.attrib.get("rel")
                if rel == Hyperlink.BORROW:
                    checkout_link = link_tag.attrib.get("href")
                    break

            # Look for a link to the License Info Document for this license.
            odl_status_link = None
            for link_tag in parser._xpath(odl_license_tag, "atom:link") or []:
                attrib = link_tag.attrib
                rel = attrib.get("rel")
                type = attrib.get("type", "")
                if rel == "self" and type.startswith(
                    cls.LICENSE_INFO_DOCUMENT_MEDIA_TYPE
                ):
                    odl_status_link = attrib.get("href")
                    break

            expires = None
            concurrent_checkouts = None

            terms = parser._xpath(odl_license_tag, "odl:terms")
            if terms:
                concurrent_checkouts = subtag(terms[0], "odl:concurrent_checkouts")
                expires = subtag(terms[0], "odl:expires")

            concurrent_checkouts_int = (
                int(concurrent_checkouts) if concurrent_checkouts is not None else None
            )
            expires_datetime = (
                to_utc(dateutil.parser.parse(expires)) if expires is not None else None
            )

            if not odl_status_link:
                parsed_license = None
            else:
                parsed_license = cls.get_license_data(
                    odl_status_link,
                    checkout_link,
                    identifier,
                    expires_datetime,
                    concurrent_checkouts_int,
                    do_get,
                )

            if parsed_license is not None:
                licenses.append(parsed_license)

        if not data.get("circulation"):
            data["circulation"] = dict()
        if not data["circulation"].get("formats"):
            data["circulation"]["formats"] = []
        data["circulation"]["formats"].extend(formats)
        if not data["circulation"].get("licenses"):
            data["circulation"]["licenses"] = []
        data["circulation"]["licenses"].extend(licenses)
        data["circulation"]["licenses_owned"] = None
        data["circulation"]["licenses_available"] = None
        data["circulation"]["licenses_reserved"] = None
        data["circulation"]["patrons_in_hold_queue"] = None
        return data


class ODLImportMonitor(OPDSImportMonitor):
    """Import information from an ODL feed."""

    PROTOCOL = ODLImporter.NAME
    SERVICE_NAME = "ODL Import Monitor"

    def __init__(
        self,
        _db: Session,
        collection: Collection,
        import_class: type[OPDSImporter],
        **import_class_kwargs: Any,
    ):
        # Always force reimport ODL collections to get up to date license information
        super().__init__(
            _db, collection, import_class, force_reimport=True, **import_class_kwargs
        )


class ODLHoldReaper(CollectionMonitor):
    """Check for holds that have expired and delete them, and update
    the holds queues for their pools."""

    SERVICE_NAME = "ODL Hold Reaper"
    PROTOCOL = ODLAPI.label()

    def __init__(
        self,
        _db: Session,
        collection: Collection,
        api: ODLAPI | None = None,
        **kwargs: Any,
    ):
        super().__init__(_db, collection, **kwargs)
        self.api = api or ODLAPI(_db, collection)

    def run_once(self, progress: TimestampData) -> TimestampData:
        # Find holds that have expired.
        expired_holds = (
            self._db.query(Hold)
            .join(Hold.license_pool)
            .filter(LicensePool.collection_id == self.api.collection_id)
            .filter(Hold.end < utc_now())
            .filter(Hold.position == 0)
        )

        changed_pools = set()
        total_deleted_holds = 0
        for hold in expired_holds:
            changed_pools.add(hold.license_pool)
            self._db.delete(hold)
            total_deleted_holds += 1

        for pool in changed_pools:
            self.api.update_licensepool(pool)

        message = "Holds deleted: %d. License pools updated: %d" % (
            total_deleted_holds,
            len(changed_pools),
        )
        progress = TimestampData(achievements=message)
        return progress
