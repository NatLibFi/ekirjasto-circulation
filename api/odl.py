from __future__ import annotations

import binascii
import datetime
import json
import uuid
from abc import ABC
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

import dateutil
from dependency_injector.wiring import Provide, inject
from flask import url_for
from flask_babel import lazy_gettext as _
from lxml.etree import Element
from pydantic import PositiveInt, ValidationError
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
from api.lcp.status import LoanStatus
from api.odl_api.auth import OpdsWithOdlException
from api.opds.types.link import BaseLink
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
    License,
    LicensePool,
    LicensePoolDeliveryMechanism,
    Loan,
    MediaTypes,
    Representation,
    Resource,
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
from core.util.pydantic import HttpUrl


class ODLAPIConstants:
    DEFAULT_PASSPHRASE_HINT = "View the help page for more information."
    DEFAULT_PASSPHRASE_HINT_URL = "https://lyrasis.zendesk.com/"


class ODLSettings(OPDSImporterSettings):
    external_account_id: HttpUrl = FormField(
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


class BaseODLAPI(
    PatronActivityCirculationAPI[SettingsType, LibrarySettingsType], LoggerMixin, ABC
):
    """ODL (Open Distribution to Libraries) is a specification that allows
    libraries to manage their own loans and holds. It offers a deeper level
    of control to the library, but it requires the circulation manager to
    keep track of individual copies rather than just license pools, and
    manage its own holds queues.

    In addition to circulating books to patrons of a library on the current circulation
    manager, this API can be used to circulate books to patrons of external libraries.
    """

    SET_DELIVERY_MECHANISM_AT = BaseCirculationAPI.FULFILL_STEP

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

        try:
            response = HTTP.get_with_timeout(
                url, headers=headers, timeout=30, *args, **kwargs
            )
            return response
        except BadResponseException as e:
            response = e.response
            if opds_exception := OpdsWithOdlException.from_response(response):
                raise opds_exception from e
            raise

    def _url_for(self, *args: Any, **kwargs: Any) -> str:
        """Wrapper around flask's url_for to be overridden for tests."""
        return url_for(*args, **kwargs)

    @staticmethod
    def _notification_url(
        short_name: str | None, patron_id: str, license_id: str
    ) -> str:
        """Get the notification URL that should be passed in the ODL checkout link.

        This is broken out into a separate function to make it easier to override
        in tests.
        """
        return url_for(
            "opds2_with_odl_notification",
            library_short_name=short_name,
            patron_identifier=patron_id,
            license_identifier=license_id,
            _external=True,
        )

    def _request_loan_status(
        self, url: str, ignored_problem_types: list[str] | None = None
    ) -> LoanStatus:
        """Retrieves the Loan Status Document."""
        try:
            response = self._get(url, allowed_response_codes=["2xx"])
            status_doc = LoanStatus.model_validate_json(response.content)
        except ValidationError as e:
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
                # logging the information about it. The caller will handle the exception.
                if ignored_problem_types and e.type in ignored_problem_types:
                    raise
                error_message += f" Problem Detail: '{e.type}' - {e.title}"
                if e.detail:
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
        self.log.info(f"Checking in a loan in license pool {licensepool}")
        _db = Session.object_session(patron)

        loan = (
            _db.query(Loan)
            .filter(Loan.patron == patron)
            .filter(Loan.license_pool_id == licensepool.id)
        )
        if loan.count() < 1:
            raise NotCheckedOut()
        loan_result = loan.one()

        if licensepool.open_access:
            # If this is an open-access book, we don't need to do anything.
            return

        self._checkin(loan_result)

    def _checkin(self, loan: Loan) -> None:
        _db = Session.object_session(loan)
        if loan.external_identifier is None:
            # We can't return a loan that doesn't have an external identifier. This should never happen
            # but if it does, we self.log an error and continue on, so it doesn't stay on the patrons
            # bookshelf forever.
            self.log.error(f"Loan {loan.id} has no external identifier.")
            return
        if loan.license is None:
            # We can't return a loan that doesn't have a license. This should never happen but if it does,
            # we self.log an error and continue on, so it doesn't stay on the patrons bookshelf forever.
            self.log.error(f"Loan {loan.id} has no license.")  # type: ignore
            return

        loan_status = self._request_loan_status(loan.external_identifier)
        if not loan_status.active:
            self.log.warning(
                f"Loan {loan.id} was {loan_status.status} was already returned early, revoked by the distributor, or it expired."
            )
            loan.license.checkin()
            self.update_licensepool_and_hold_queue(loan.license_pool)
            return

        assert loan_status.links  # To satisfy mypy
        return_link = loan_status.links.get(
            rel="return", type=LoanStatus.content_type()
        )
        if not return_link:
            # The distributor didn't provide a link to return this loan. This means that the distributor
            # does not support early returns, and the patron will have to wait until the loan expires.
            raise CannotReturn()

        # The parameters for this link (if its templated) are defined here:
        # https://readium.org/lcp-specs/releases/lsd/latest.html#34-returning-a-publication
        # None of them are required, and often the link is not templated. But in the case
        # of the open source LCP server, the link is templated, so we need to process the
        # template before we can make the request.
        return_url = return_link.href

        # Hit the distributor's return link, and if it's successful, update the pool
        # availability.
        loan_status = self._request_loan_status(return_url)
        if loan_status.active:
            # If the distributor says the loan is still active, we didn't return it, and
            # something went wrong. We self.log an error and don't delete the loan, so the patron
            # can try again later.
            self.log.error(
                f"Loan {loan.id} was {loan_status.status} not returned. The distributor says it's still active. {loan_status.model_dump_json()}"
            )
            raise CannotReturn()
        loan.license.checkin()
        self.update_licensepool_and_hold_queue(loan.license_pool)

    def checkout(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        delivery_mechanism: LicensePoolDeliveryMechanism,
    ) -> LoanInfo:
        """Create a new loan."""
        self.log.info(f"Checking out a loan in license pool {licensepool}")
        _db = Session.object_session(patron)

        loan = (
            _db.query(Loan)
            .filter(Loan.patron == patron)
            .filter(Loan.license_pool_id == licensepool.id)
        )
        if loan.count() > 0:
            raise AlreadyCheckedOut()

        if licensepool.open_access or licensepool.unlimited_access:
            return LoanInfo.from_license_pool(
                licensepool,
                end_date=None,
            )
        else:
            hold = get_one(_db, Hold, patron=patron, license_pool_id=licensepool.id)
            return self._checkout(patron, licensepool, hold)

    def _checkout(
        self, patron: Patron, licensepool: LicensePool, hold: Hold | None = None
    ) -> LoanInfo:
        db = Session.object_session(patron)

        if not any(l for l in licensepool.licenses if not l.is_inactive):
            raise NoLicenses()
        # Make sure pool info is updated.
        self.update_licensepool_and_hold_queue(licensepool)

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
        requested_expiry = utc_now() + datetime.timedelta(days=default_loan_period)
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
        self.log.info(
            f"Available licenses in license pool {licensepool.identifier}: {len(licenses)}"
        )

        license_: License | None = None
        loan_status: LoanStatus | None = None
        for license_ in licenses:
            try:
                self.log.info(
                    f"Trying license id {license_.identifier} with {license_.checkouts_available} checkouts..."
                )
                loan_status = self._checkout_license(
                    license_,
                    library_short_name,
                    patron_id,
                    requested_expiry.isoformat(),
                    encoded_pass,
                )
                break
            except NoAvailableCopies:
                self.log.info(
                    f"No available checkouts for license: {license_.identifier}. Trying the next one..."
                )
                # This license had no available copies, so we try the next one.
                ...

        # No best available licenses were found in the first place or, for some reason, there's no status.
        if license_ is None or loan_status is None:
            # Instantly get updated information from licenses.
            licensepool.update_availability_from_licenses()
            # If we have a hold, it means we thought the book was available, but it wasn't.
            if hold:
                # The license should be available at most by the default loan period in E-Kirjasto.
                hold.end = utc_now() + datetime.timedelta(days=default_loan_period)
                # Update the pool's queue and raise a specific error message.
                self._recalculate_holds_in_license_pool(licensepool)
                raise NoAvailableCopiesWhenReserved()
            raise NoAvailableCopies()

        if not loan_status.active:
            # Something went wrong with this loan, and we don't actually
            # have the book checked out. This should never happen.
            self.log.warning(
                f"Loan status for license {license_.identifier} was {loan_status.status} instead of active"
            )
            raise CannotLoan()

        assert loan_status.links  # To satisfy mypy
        # We save the link to the loan status document in the loan's external_identifier field, so
        # we are able to retrieve it later.
        loan_status_document_link: BaseLink | None = loan_status.links.get(
            rel="self", type=LoanStatus.content_type()
        )

        if not loan_status_document_link:
            self.log.warning(
                f"There was no loan status link for license {license_.identifier}"
            )
            raise CannotLoan()

        loan_start = utc_now()
        loan = LoanInfo.from_license_pool(
            licensepool,
            start_date=loan_start,
            end_date=loan_status.potential_rights.end,
            external_identifier=loan_status_document_link.href,
            license_identifier=license_.identifier,
        )

        # We also need to update the remaining checkouts for the license.
        license_.checkout()

        # If there was a hold CirculationAPI will take care of deleting it. So we just need to
        # update the license pool to reflect the loan. Since update_availability_from_licenses
        # takes into account holds, we need to tell it to ignore the hold about to be deleted.
        self.update_licensepool_and_hold_queue(
            licensepool, ignored_holds={hold} if hold else None
        )
        self.log.info(f"License {license_.identifier} checked out with LoanInfo {loan}")
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
            patron_id,
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
    ) -> Fulfillment:
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
                for lpdm in licensepool.delivery_mechanisms
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
        return RedirectFulfillment(content_link, content_type)

    def _license_fulfill(
        self, loan: Loan, delivery_mechanism: LicensePoolDeliveryMechanism
    ) -> Fulfillment:
        self.log.info(
            f"Fulfilling loan of license {loan.license.identifier} in license pool {loan.license_pool}"
        )
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
            self.log.warning(
                f"Loan status was not active but {loan_status.status}, can not fulfill"
            )
            raise CannotFulfill()

        drm_scheme = delivery_mechanism.delivery_mechanism.drm_scheme
        fulfill_cls: Callable[[str, str | None], UrlFulfillment]
        assert loan_status.links  # To satisfy mypy
        if drm_scheme == DeliveryMechanism.NO_DRM:
            # If we have no DRM, we can just redirect to the content link and let the patron download the book.
            fulfill_link = loan_status.links.get(
                rel="publication",
                type=delivery_mechanism.delivery_mechanism.content_type,  # type: ignore
            )
            fulfill_cls = RedirectFulfillment
        elif drm_scheme == DeliveryMechanism.FEEDBOOKS_AUDIOBOOK_DRM:
            # For DeMarque audiobook content using "FEEDBOOKS_AUDIOBOOK_DRM", the link
            # we are looking for is stored in the 'manifest' rel.
            fulfill_link = loan_status.links.get(
                rel="manifest", type=BaseODLImporter.FEEDBOOKS_AUDIO
            )
            fulfill_cls = partial(FetchFulfillment, allowed_response_codes=["2xx"])
        else:
            # We are getting content via a license loan_status document, so we need to find the link
            # that corresponds to the delivery mechanism we are using.
            fulfill_link = loan_status.links.get(rel="license", type=drm_scheme)  # type: ignore
            fulfill_cls = partial(FetchFulfillment, allowed_response_codes=["2xx"])

        if fulfill_link is None:
            raise CannotFulfill()

        self.log.info(f"Fulfilling with {drm_scheme}, Link: {fulfill_link.href}")
        return fulfill_cls(fulfill_link.href, fulfill_link.type)

    def _fulfill(
        self,
        loan: Loan,
        delivery_mechanism: LicensePoolDeliveryMechanism,
    ) -> Fulfillment:
        if loan.license_pool.open_access or loan.license_pool.unlimited_access:
            return self._unlimited_access_fulfill(loan, delivery_mechanism)
        else:
            return self._license_fulfill(loan, delivery_mechanism)

    def _recalculate_holds_in_license_pool(self, licensepool: LicensePool) -> None:
        """Set any holds ready for checkout and update the position for all other holds in the queue."""
        holds = licensepool.holds_by_start_date()
        ready_for_checkout = holds[: licensepool.licenses_reserved]
        waiting = holds[licensepool.licenses_reserved :]
        self.log.info(
            f"Holds in License pool [{licensepool.identifier}]: {len(holds)} "
            f"holds / {len(ready_for_checkout)} ready to checkout / {len(waiting)} in queue"
        )

        assert self.collection is not None
        default_reservation_period = self.collection.default_reservation_period
        # If we had available copies, reserve them for the same amount of holds at the top of the queue.
        for hold in ready_for_checkout:
            if hold.position != 0:
                hold.position = 0
                # And start the reservation period which ends end of day.
                hold.end = (
                    utc_now() + datetime.timedelta(days=default_reservation_period)
                ).replace(hour=23, minute=59, second=59, microsecond=999999)

        # Update the rest of the queue.
        for idx, hold in enumerate(waiting):
            position = idx + 1
            if hold.position != position:
                hold.position = position

    def update_licensepool_and_hold_queue(
        self, licensepool: LicensePool, ignored_holds: set[Hold] | None = None
    ) -> None:
        """Update availability information of the license pool and recaulculate its holds queue."""
        licensepool.update_availability_from_licenses(
            as_of=utc_now(), ignored_holds=ignored_holds
        )
        self._recalculate_holds_in_license_pool(licensepool)

    def place_hold(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        notification_email_address: str | None,
    ) -> HoldInfo:
        """Create a new hold."""
        self.log.info(f"Placing hold in license pool {licensepool}")
        return self._place_hold(patron, licensepool)

    def _place_hold(self, patron: Patron, licensepool: LicensePool) -> HoldInfo:
        _db = Session.object_session(patron)
        # Make sure pool info is updated.
        licensepool.update_availability_from_licenses()

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
        holdinfo = HoldInfo.from_license_pool(
            licensepool,
            start_date=utc_now(),
            end_date=utc_now() + datetime.timedelta(days=365),  # E-Kirjasto
            hold_position=licensepool.patrons_in_hold_queue,
        )
        return holdinfo

    def release_hold(self, patron: Patron, pin: str, licensepool: LicensePool) -> None:
        """Cancel a hold."""
        self.log.info(f"Releasing hold in license pool {licensepool}")
        _db = Session.object_session(patron)

        hold = get_one(
            _db,
            Hold,
            license_pool_id=licensepool.id,
            patron=patron,
        )
        if not hold:
            raise NotOnHold()
        # The hold itself will be deleted by the caller CirculationAPI,
        # so we just need to update the license pool to reflect the released hold.
        self.update_licensepool_and_hold_queue(licensepool, ignored_holds={hold})

    def patron_activity(self, patron: Patron, pin: str) -> list[LoanInfo | HoldInfo]:
        """Look up non-expired loans for this collection in the database and update holds."""
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
            licensepool = hold.license_pool
            # Delete expired holds and update the pool and queue to reflect the change.
            if hold.end and hold.end < utc_now():
                _db.delete(hold)
                self._recalculate_holds_in_license_pool(licensepool)
            else:
                # Check to see if the position has changed in the queue or maybe the hold is ready for checkout.
                self._recalculate_holds_in_license_pool(licensepool)
                remaining_holds.append(hold)
        return [
            LoanInfo.from_license_pool(
                loan.license_pool,
                start_date=loan.start,
                end_date=loan.end,
                external_identifier=loan.external_identifier,
            )
            for loan in loans
        ] + [
            HoldInfo.from_license_pool(
                hold.license_pool,
                start_date=hold.start,
                end_date=hold.end,
                hold_position=hold.position,
            )
            for hold in remaining_holds
        ]

    def update_availability(self, licensepool: LicensePool) -> None:
        licensepool.update_availability_from_licenses()

    def delete_expired_loan(self, loan: Loan) -> None:
        """
        Delete a loan we know to be expired and update the license pool.
        """
        _db = Session.object_session(loan)
        loan.license.checkin()
        self.log.info(f"Deleting loan #{loan.id}")
        _db.delete(loan)
        self.update_licensepool_and_hold_queue(loan.license_pool)


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

        cls.logger().info(
            f"License identifier {identifier} / status {status} / concurrency {concurrency} / expires {expires} / checkouts left {left} / available / {available} / content types {content_types}"
        )

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
        cls.logger().info(f"Parsing License Info Document {license_info_link}")
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
            self.api.update_licensepool_and_hold_queue(pool)

        message = "Holds deleted: %d. License pools updated: %d" % (
            total_deleted_holds,
            len(changed_pools),
        )
        progress = TimestampData(achievements=message)
        return progress
