from __future__ import annotations

import dataclasses
import datetime
import logging
import math
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from threading import Thread
from types import TracebackType
from typing import Any, Literal, TypeVar

import flask
import requests
from flask import Response
from flask_babel import lazy_gettext as _
from pydantic import PositiveInt
from sqlalchemy import select
from sqlalchemy.orm import Query, Session
from typing_extensions import Self

from api.circulation_exceptions import *
from api.integration.registry.license_providers import LicenseProvidersRegistry
from api.util.patron import PatronUtility
from core.analytics import Analytics
from core.config import CannotLoadConfiguration
from core.integration.base import HasLibraryIntegrationConfiguration
from core.integration.registry import IntegrationRegistry
from core.integration.settings import (
    BaseSettings,
    ConfigurationFormItem,
    ConfigurationFormItemType,
    FormField,
)
from core.model import (
    CirculationEvent,
    Collection,
    DataSource,
    DeliveryMechanism,
    Hold,
    Library,
    License,
    LicensePool,
    LicensePoolDeliveryMechanism,
    Loan,
    Patron,
    Resource,
    RightsStatus,
    Session,
    get_one,
)
from core.model.integration import IntegrationConfiguration
from core.model.patron import LoanCheckout
from core.util.datetime_helpers import utc_now
from core.util.http import HTTP, BadResponseException, ResponseCodesT
from core.util.log import LoggerMixin


class CirculationInfo:
    def __init__(
        self,
        collection: Collection | int | None,
        data_source_name: str | DataSource | None,
        identifier_type: str | None,
        identifier: str | None,
    ) -> None:
        """A loan, hold, or whatever.

        :param collection: The Collection that gives us the right to
        borrow this title, or the numeric database ID of the
        same. This does not have to be specified in the constructor --
        the code that instantiates CirculationInfo may not have
        access to a database connection -- but it needs to be present
        by the time the LoanInfo is connected to a LicensePool.

        :param data_source_name: The name of the data source that provides
            the LicencePool.
        :param identifier_type: The type of the Identifier associated
            with the LicensePool.
        :param identifier: The string identifying the LicensePool.

        """
        self.collection_id: int | None
        if isinstance(collection, int):
            self.collection_id = collection
        elif isinstance(collection, Collection) and collection.id is not None:
            self.collection_id = collection.id
        else:
            self.collection_id = None

        self.data_source_name = data_source_name
        self.identifier_type = identifier_type
        self.identifier = identifier

    def collection(self, _db: Session) -> Collection | None:
        """Find the Collection to which this object belongs."""
        if self.collection_id is None:
            return None
        return Collection.by_id(_db, self.collection_id)

    def license_pool(self, _db: Session) -> LicensePool:
        """Find the LicensePool model object corresponding to this object."""
        collection = self.collection(_db)
        pool, is_new = LicensePool.for_foreign_id(
            _db,
            self.data_source_name,
            self.identifier_type,
            self.identifier,
            collection=collection,
        )
        return pool

    def fd(self, d: datetime.datetime | None) -> str | None:
        # Stupid method to format a date
        if not d:
            return None
        else:
            date = datetime.datetime.strftime(d, "%Y/%m/%d %H:%M:%S")
            return date


# TODO: Can be deleted, only used in Overdrive
class DeliveryMechanismInfo(CirculationInfo):
    """A record of a technique that must be (but is not, currently, being)
    used to fulfill a certain loan.

    Although this class is similar to `FormatInfo` in
    core/metadata.py, usage here is strictly limited to recording
    which `LicensePoolDeliveryMechanism` a specific loan is currently
    locked to.

    If, in the course of investigating a patron's loans, you discover
    general facts about a LicensePool's availability or formats, that
    information needs to be stored in a `CirculationData` and applied to
    the LicensePool separately.
    """

    def __init__(
        self,
        content_type: str | None,
        drm_scheme: str | None,
        rights_uri: str | None = RightsStatus.IN_COPYRIGHT,
        resource: Resource | None = None,
    ) -> None:
        """Constructor.

        :param content_type: Once the loan is fulfilled, the resulting document
            will be of this media type.
        :param drm_scheme: Fulfilling the loan will require negotiating this DRM
            scheme.
        :param rights_uri: Once the loan is fulfilled, the resulting
            document will be made available under this license or
            copyright regime.
        :param resource: The loan can be fulfilled by directly serving the
            content in the given `Resource`.
        """
        self.content_type = content_type
        self.drm_scheme = drm_scheme
        self.rights_uri = rights_uri
        self.resource = resource

    def apply(
        self, loan: Loan, autocommit: bool = True
    ) -> LicensePoolDeliveryMechanism | None:
        """Set an appropriate LicensePoolDeliveryMechanism on the given
        `Loan`, creating a DeliveryMechanism if necessary.

        :param loan: A Loan object.
        :param autocommit: Set this to false if you are in the middle
            of a nested transaction.
        :return: A LicensePoolDeliveryMechanism if one could be set on the
            given Loan; None otherwise.
        """
        _db = Session.object_session(loan)

        # Create or update the DeliveryMechanism.
        delivery_mechanism, is_new = DeliveryMechanism.lookup(
            _db, self.content_type, self.drm_scheme
        )

        if (
            loan.fulfillment
            and loan.fulfillment.delivery_mechanism == delivery_mechanism
        ):
            # The work has already been done. Do nothing.
            return None

        # At this point we know we need to update the local delivery
        # mechanism.
        pool = loan.license_pool
        if not pool:
            # This shouldn't happen, but bail out if it does.
            return None

        # Look up the LicensePoolDeliveryMechanism for the way the
        # server says this book is available, creating the object if
        # necessary.
        #
        # We set autocommit=False because we're probably in the middle
        # of a nested transaction.
        lpdm = LicensePoolDeliveryMechanism.set(
            pool.data_source,
            pool.identifier,
            self.content_type,
            self.drm_scheme,
            self.rights_uri,
            self.resource,
            autocommit=autocommit,
        )
        loan.fulfillment = lpdm
        return lpdm


class FulfillmentInfo(CirculationInfo):
    """A record of a technique that can be used *right now* to fulfill
    a loan.
    """

    def __init__(
        self,
        collection: Collection | int | None,
        data_source_name: str | DataSource | None,
        identifier_type: str | None,
        identifier: str | None,
        content_link: str | None,
        content_type: str | None,
        content: str | None,
        content_expires: datetime.datetime | None,
        content_link_redirect: bool = False,
    ) -> None:
        """Constructor.

        One and only one of `content_link` and `content` should be
        provided.

        :param collection: A Collection object explaining which Collection
            the loan is found in.
        :param identifier_type: A possible value for Identifier.type indicating
            a type of identifier such as ISBN.
        :param identifier: A possible value for Identifier.identifier containing
            the identifier used to designate the item beinf fulfilled.
        :param content_link: A "next step" URL towards fulfilling the
            work. This may be a link to an ACSM file, a
            streaming-content web application, a direct download, etc.
        :param content_type: Final media type of the content, once acquired.
            E.g. EPUB_MEDIA_TYPE or
            Representation.TEXT_HTML_MEDIA_TYPE + DeliveryMechanism.STREAMING_PROFILE
        :param content: "Next step" content to be served. This may be
            the actual content of the item on loan (in which case its
            is of the type mentioned in `content_type`) or an
            intermediate document such as an ACSM file or audiobook
            manifest (in which case its media type will differ from
            `content_type`).
        :param content_expires: A time after which the "next step"
            link or content will no longer be usable.
        :param content_link_redirect: Force the API layer to redirect the client to
            the content_link
        """
        super().__init__(collection, data_source_name, identifier_type, identifier)
        self._content_link = content_link
        self._content_type = content_type
        self._content = content
        self._content_expires = content_expires
        self.content_link_redirect = content_link_redirect

    def __repr__(self) -> str:
        if self.content:
            blength = len(self.content)
        else:
            blength = 0
        return (
            "<FulfillmentInfo: content_link: %r, content_type: %r, content: %d bytes, expires: %r, content_link_redirect: %s>"
            % (
                self.content_link,
                self.content_type,
                blength,
                self.fd(self.content_expires),
                self.content_link_redirect,
            )
        )

    @property
    def as_response(self) -> Response | ProblemDetail | None:
        """Bypass the normal process of creating a Flask Response.

        :return: A Response object, or None if you're okay with the
           normal process.
        """
        return None

    @property
    def content_link(self) -> str | None:
        return self._content_link

    @content_link.setter
    def content_link(self, value: str | None) -> None:
        self._content_link = value

    @property
    def content_type(self) -> str | None:
        return self._content_type

    @content_type.setter
    def content_type(self, value: str | None) -> None:
        self._content_type = value

    @property
    def content(self) -> str | None:
        return self._content

    @content.setter
    def content(self, value: str | None) -> None:
        self._content = value

    @property
    def content_expires(self) -> datetime.datetime | None:
        return self._content_expires

    @content_expires.setter
    def content_expires(self, value: datetime.datetime | None) -> None:
        self._content_expires = value


# TODO: Remove this class, it's only used by Axis.
class APIAwareFulfillmentInfo(FulfillmentInfo, ABC):
    """This that acts like FulfillmentInfo but is prepared to make an API
    request on demand to get data, rather than having all the data
    ready right now.

    This class is useful in situations where generating a full
    FulfillmentInfo object would be costly. We only want to incur that
    cost when the patron wants to fulfill this title and is not just
    looking at their loans.
    """

    def __init__(
        self,
        api: CirculationApiType,
        data_source_name: str | None,
        identifier_type: str | None,
        identifier: str | None,
        key: Any,
    ) -> None:
        """Constructor.

        :param api: An object that knows how to make API requests.
        :param data_source_name: The name of the data source that's
           offering to fulfill a book.
        :param identifier: The Identifier of the book being fulfilled.
        :param key: Any special data, such as a license key, which must
           be used to fulfill the book.
        """
        super().__init__(
            api.collection,
            data_source_name,
            identifier_type,
            identifier,
            None,
            None,
            None,
            None,
        )
        self.api = api
        self.key = key

        self._fetched = False
        self.content_link_redirect = False

    def fetch(self) -> None:
        """It's time to tell the API that we want to fulfill this book."""
        if self._fetched:
            # We already sent the API request..
            return None
        self.do_fetch()
        self._fetched = True

    @abstractmethod
    def do_fetch(self) -> None:
        """Actually make the API request.

        When implemented, this method must set values for some or all
        of _content_link, _content_type, _content, and
        _content_expires.
        """
        ...

    @property
    def content_link(self) -> str | None:
        self.fetch()
        return self._content_link

    @content_link.setter
    def content_link(self, value: str | None) -> None:
        raise NotImplementedError()

    @property
    def content_type(self) -> str | None:
        self.fetch()
        return self._content_type

    @content_type.setter
    def content_type(self, value: str | None) -> None:
        raise NotImplementedError()

    @property
    def content(self) -> str | None:
        self.fetch()
        return self._content

    @content.setter
    def content(self, value: str | None) -> None:
        raise NotImplementedError()

    @property
    def content_expires(self) -> datetime.datetime | None:
        self.fetch()
        return self._content_expires

    @content_expires.setter
    def content_expires(self, value: datetime.datetime | None) -> None:
        raise NotImplementedError()


class Fulfillment(ABC):
    """
    Represents a method of fulfilling a loan.
    """

    @abstractmethod
    def response(self) -> Response:
        """
        Return a Flask Response object that can be used to fulfill a loan.
        """
        ...


class UrlFulfillment(Fulfillment, ABC):
    """
    Represents a method of fulfilling a loan that has a URL to an external resource.
    """

    def __init__(self, content_link: str, content_type: str | None = None) -> None:
        self.content_link = content_link
        self.content_type = content_type

    def __repr__(self) -> str:
        repr_data = [f"content_link: {self.content_link}"]
        if self.content_type:
            repr_data.append(f"content_type: {self.content_type}")
        return f"<{self.__class__.__name__}: {', '.join(repr_data)}>"


class DirectFulfillment(Fulfillment):
    """
    Represents a method of fulfilling a loan by directly serving some content
    that we know about locally.
    """

    def __init__(self, content: str, content_type: str | None) -> None:
        self.content = content
        self.content_type = content_type

    def response(self) -> Response:
        return Response(self.content, content_type=self.content_type)

    def __repr__(self) -> str:
        length = len(self.content)
        return f"<{self.__class__.__name__}: content_type: {self.content_type}, content: {length} bytes>"


class RedirectFulfillment(UrlFulfillment):
    """
    Fulfill a loan by redirecting the client to a URL.
    """

    def response(self) -> Response:
        return Response(
            f"Redirecting to {self.content_link} ...",
            status=302,
            headers={"Location": self.content_link},
            content_type="text/plain",
        )


class FetchResponse(Response):
    """
    Response object that defaults to no mimetype if none is provided.
    """

    default_mimetype = None


class FetchFulfillment(UrlFulfillment, LoggerMixin):
    """
    Fulfill a loan by fetching a URL and returning the content. This should be
    avoided for large files, since it will be slow and unreliable as well as
    blocking the server.

    In some cases for small files like ACSM or LCPL files, the server may be
    the only entity that can fetch the file, so we fetch it and then return it
    to the client.
    """

    def __init__(
        self,
        content_link: str,
        content_type: str | None = None,
        *,
        include_headers: dict[str, str] | None = None,
        allowed_response_codes: ResponseCodesT = None,
    ) -> None:
        super().__init__(content_link, content_type)
        self.include_headers = include_headers or {}
        self.allowed_response_codes = allowed_response_codes or []

    def get(self, url: str) -> requests.Response:
        return HTTP.get_with_timeout(
            url,
            headers=self.include_headers,
            allowed_response_codes=self.allowed_response_codes,
            allow_redirects=True,
        )

    def response(self) -> Response:
        try:
            response = self.get(self.content_link)
        except BadResponseException as ex:
            response = ex.response
            self.log.exception(
                f"Error fulfilling loan. Bad response from: {self.content_link}. "
                f"Status code: {response.status_code}. "
                f"Response: {response.text}."
            )
            raise

        headers = {"Cache-Control": "private"}

        if self.content_type:
            headers["Content-Type"] = self.content_type
        elif "Content-Type" in response.headers:
            headers["Content-Type"] = response.headers["Content-Type"]

        return FetchResponse(
            response.content, status=response.status_code, headers=headers
        )


class LoanAndHoldInfoMixin:
    collection_id: int
    identifier_type: str
    identifier: str

    def collection(self, _db: Session) -> Collection:
        """Find the Collection to which this object belongs."""
        collection = Collection.by_id(_db, self.collection_id)
        if collection is None:
            raise ValueError(f"collection_id {self.collection_id} could not be found.")
        return collection

    def license_pool(self, _db: Session) -> LicensePool:
        """Find the LicensePool model object corresponding to this object."""
        collection = self.collection(_db)
        pool, is_new = LicensePool.for_foreign_id(
            _db,
            collection.data_source,
            self.identifier_type,
            self.identifier,
            collection=collection,
        )
        return pool


@dataclasses.dataclass(kw_only=True)
class LoanInfo(LoanAndHoldInfoMixin):
    """A record of a loan."""

    collection_id: int
    identifier_type: str
    identifier: str
    start_date: datetime.datetime | None = None
    end_date: datetime.datetime | None
    external_identifier: str | None = None
    locked_to: DeliveryMechanismInfo | None = None
    fulfillment_info: FulfillmentInfo | None = None
    license_identifier: str | None = None

    @classmethod
    def from_license_pool(
        cls,
        license_pool: LicensePool,
        *,
        start_date: datetime.datetime | None = None,
        end_date: datetime.datetime | None,
        external_identifier: str | None = None,
        locked_to: DeliveryMechanismInfo | None = None,
        fulfillment_info: FulfillmentInfo | None = None,
        license_identifier: str | None = None,
    ) -> Self:
        collection_id = license_pool.collection_id
        assert collection_id is not None
        identifier_type = license_pool.identifier.type
        assert identifier_type is not None
        identifier = license_pool.identifier.identifier
        assert identifier is not None
        return cls(
            collection_id=collection_id,
            identifier_type=identifier_type,
            identifier=identifier,
            start_date=start_date,
            end_date=end_date,
            external_identifier=external_identifier,
            locked_to=locked_to,
            fulfillment_info=fulfillment_info,
            license_identifier=license_identifier,
        )

    def __repr__(self) -> str:
        return "<LoanInfo for {}/{}, start={} end={} fulfillment:{} locked to={} license={} collection={}>".format(
            self.identifier_type,
            self.identifier,
            self.start_date.isoformat() if self.start_date else self.start_date,
            self.end_date.isoformat() if self.end_date else self.end_date,
            self.fulfillment_info,
            self.locked_to,
            self.license_identifier,
            self.collection_id,
        )

    def create_or_update(
        self, patron: Patron, license_pool: LicensePool | None = None
    ) -> tuple[Loan, bool]:
        session = Session.object_session(patron)
        license_pool = license_pool or self.license_pool(session)

        loanable: LicensePool | License
        if self.license_identifier is not None:
            loanable = (
                session.execute(  # type: ignore
                    select(License).where(
                        License.identifier == self.license_identifier,
                        License.license_pool == license_pool,
                    )
                )
                .scalars()
                .first()
            )
        else:
            loanable = license_pool

        loan, is_new = loanable.loan_to(
            patron,
            start=self.start_date,
            end=self.end_date,
            external_identifier=self.external_identifier,
        )
        return loan, is_new


@dataclasses.dataclass(kw_only=True)
class HoldInfo(LoanAndHoldInfoMixin):
    """A record of a hold.

    :param identifier_type: Ex. Identifier.BIBLIOTHECA_ID.
    :param identifier: Expected to be the unicode string of the isbn, etc.
    :param start_date: When the patron made the reservation.
    :param end_date: When reserved book is expected to become available.
        Expected to be passed in date, not unicode format.
    :param hold_position:  Patron's place in the hold line. When not available,
        default to be passed is None, which is equivalent to "first in line".
    """

    collection_id: int
    identifier_type: str
    identifier: str
    start_date: datetime.datetime | None = None
    end_date: datetime.datetime | None = None
    hold_position: int | None

    @classmethod
    def from_license_pool(
        cls,
        license_pool: LicensePool,
        *,
        start_date: datetime.datetime | None = None,
        end_date: datetime.datetime | None = None,
        hold_position: int | None,
    ) -> Self:
        collection_id = license_pool.collection_id
        assert collection_id is not None
        identifier_type = license_pool.identifier.type
        assert identifier_type is not None
        identifier = license_pool.identifier.identifier
        assert identifier is not None
        return cls(
            collection_id=collection_id,
            identifier_type=identifier_type,
            identifier=identifier,
            start_date=start_date,
            end_date=end_date,
            hold_position=hold_position,
        )

    def __repr__(self) -> str:
        return "<HoldInfo for {}/{}, start={} end={}, position={}>".format(
            self.identifier_type,
            self.identifier,
            self.start_date.isoformat() if self.start_date else self.start_date,
            self.end_date.isoformat() if self.end_date else self.end_date,
            self.hold_position,
        )

    def create_or_update(
        self, patron: Patron, license_pool: LicensePool | None = None
    ) -> tuple[Hold, bool]:
        session = Session.object_session(patron)
        license_pool = license_pool or self.license_pool(session)
        return license_pool.on_hold_to(  # type: ignore[no-any-return]
            patron,
            start=self.start_date,
            end=self.end_date,
            position=self.hold_position,
        )


class BaseCirculationEbookLoanSettings(BaseSettings):
    """A mixin for settings that apply to ebook loans."""

    ebook_loan_duration: PositiveInt | None = FormField(
        default=Collection.STANDARD_DEFAULT_LOAN_PERIOD,
        form=ConfigurationFormItem(
            label=_("Ebook Loan Duration (in Days)"),
            type=ConfigurationFormItemType.NUMBER,
            description=_(
                "When a patron uses SimplyE to borrow an ebook from this collection, SimplyE will ask for a loan that lasts this number of days. This must be equal to or less than the maximum loan duration negotiated with the distributor."
            ),
        ),
    )


class BaseCirculationLoanSettings(BaseSettings):
    """A mixin for settings that apply to loans."""

    default_loan_duration: PositiveInt | None = FormField(
        default=Collection.STANDARD_DEFAULT_LOAN_PERIOD,
        form=ConfigurationFormItem(
            label=_("Default Loan Period (in Days)"),
            type=ConfigurationFormItemType.NUMBER,
            description=_(
                "Until it hears otherwise from the distributor, this server will assume that any given loan for this library from this collection will last this number of days. This number is usually a negotiated value between the library and the distributor. This only affects estimates&mdash;it cannot affect the actual length of loans."
            ),
        ),
    )


class CirculationInternalFormatsMixin:
    """A mixin for CirculationAPIs that have internal formats."""

    # Different APIs have different internal names for delivery
    # mechanisms. This is a mapping of (content_type, drm_type)
    # 2-tuples to those internal names.
    #
    # For instance, the combination ("application/epub+zip",
    # "vnd.adobe/adept+xml") is called "ePub" in Axis 360 and 3M, but
    # is called "ebook-epub-adobe" in Overdrive.
    delivery_mechanism_to_internal_format: dict[tuple[str | None, str | None], str] = {}

    def internal_format(self, delivery_mechanism: LicensePoolDeliveryMechanism) -> str:
        """Look up the internal format for this delivery mechanism or
        raise an exception.

        :param delivery_mechanism: A LicensePoolDeliveryMechanism
        """
        d = delivery_mechanism.delivery_mechanism
        key = (d.content_type, d.drm_scheme)
        internal_format = self.delivery_mechanism_to_internal_format.get(key)
        if internal_format is None:
            raise DeliveryMechanismError(
                _(
                    "Could not map delivery mechanism %(mechanism_name)s to internal delivery mechanism!",
                    mechanism_name=d.name,
                )
            )
        return internal_format


class BaseCirculationApiSettings(BaseSettings):
    _additional_form_fields = {
        "export_marc_records": ConfigurationFormItem(
            label="Generate MARC Records",
            type=ConfigurationFormItemType.SELECT,
            description="Generate MARC Records for this collection. This setting only applies if a MARC Exporter is configured.",
            options={
                "false": "Do not generate MARC records",
                "true": "Generate MARC records",
            },
        )
    }


SettingsType = TypeVar("SettingsType", bound=BaseCirculationApiSettings, covariant=True)
LibrarySettingsType = TypeVar("LibrarySettingsType", bound=BaseSettings, covariant=True)


class BaseCirculationAPI(
    HasLibraryIntegrationConfiguration[SettingsType, LibrarySettingsType],
    LoggerMixin,
    ABC,
):
    """Encapsulates logic common to all circulation APIs."""

    BORROW_STEP = "borrow"
    FULFILL_STEP = "fulfill"

    # In 3M only, when a book is in the 'reserved' state the patron
    # cannot revoke their hold on the book.
    CAN_REVOKE_HOLD_WHEN_RESERVED = True

    # If the client must set a delivery mechanism at the point of
    # checkout (Axis 360), set this to BORROW_STEP. If the client may
    # wait til the point of fulfillment to set a delivery mechanism
    # (Overdrive), set this to FULFILL_STEP. If there is no choice of
    # delivery mechanisms (3M), set this to None.
    SET_DELIVERY_MECHANISM_AT: str | None = FULFILL_STEP

    def __init__(self, _db: Session, collection: Collection):
        self._db = _db
        self._integration_configuration_id = collection.integration_configuration.id
        self.collection_id = collection.id

    @property
    def collection(self) -> Collection | None:
        if self.collection_id is None:
            return None
        return Collection.by_id(self._db, id=self.collection_id)

    @classmethod
    def default_notification_email_address(
        self, library_or_patron: Library | Patron, pin: str
    ) -> str:
        """What email address should be used to notify this library's
        patrons of changes?

        :param library_or_patron: A Library or a Patron.
        """
        if isinstance(library_or_patron, Patron):
            library = library_or_patron.library
        else:
            library = library_or_patron
        return library.settings.default_notification_email_address

    def integration_configuration(self) -> IntegrationConfiguration:
        config = get_one(
            self._db, IntegrationConfiguration, id=self._integration_configuration_id
        )
        if config is None:
            raise ValueError(
                f"No Configuration available for {self.__class__.__name__} (id={self._integration_configuration_id})"
            )
        return config

    @property
    def settings(self) -> SettingsType:
        return self.settings_load(self.integration_configuration())

    def library_settings(self, library: Library | int) -> LibrarySettingsType | None:
        libconfig = self.integration_configuration().for_library(library)
        if libconfig is None:
            return None
        config = self.library_settings_load(libconfig)
        return config

    @abstractmethod
    def checkin(self, patron: Patron, pin: str, licensepool: LicensePool) -> None:
        """Return a book early.

        :param patron: a Patron object for the patron who wants to check out the book.
        :param pin: The patron's alleged password.
        :param licensepool: Contains lending info as well as link to parent Identifier.
        """
        ...

    @abstractmethod
    def checkout(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        delivery_mechanism: LicensePoolDeliveryMechanism,
    ) -> LoanInfo | HoldInfo:
        """Check out a book on behalf of a patron.

        :param patron: a Patron object for the patron who wants to check out the book.
        :param pin: The patron's alleged password.
        :param licensepool: Contains lending info as well as link to parent Identifier.
        :param delivery_mechanism: Represents the patron's desired book format.

        :return: a LoanInfo object.
        """
        ...

    def can_fulfill_without_loan(
        self,
        patron: Patron | None,
        pool: LicensePool,
        lpdm: LicensePoolDeliveryMechanism,
    ) -> bool:
        """In general, you can't fulfill a book without a loan."""
        return False

    @abstractmethod
    def fulfill(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        delivery_mechanism: LicensePoolDeliveryMechanism,
    ) -> Fulfillment | FulfillmentInfo:
        """Get the actual resource file to the patron."""
        ...

    @abstractmethod
    def place_hold(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        notification_email_address: str | None,
    ) -> HoldInfo:
        """Place a book on hold.

        :return: A HoldInfo object
        """
        ...

    @abstractmethod
    def release_hold(self, patron: Patron, pin: str, licensepool: LicensePool) -> None:
        """Release a patron's hold on a book.

        :raises CannotReleaseHold: If there is an error communicating
            with the provider, or the provider refuses to release the hold for
            any reason.
        """
        ...

    @abstractmethod
    def update_availability(self, licensepool: LicensePool) -> None:
        """Update availability information for a book."""
        ...


CirculationApiType = BaseCirculationAPI[BaseCirculationApiSettings, BaseSettings]


class PatronActivityCirculationAPI(
    BaseCirculationAPI[SettingsType, LibrarySettingsType], ABC
):
    """
    A CirculationAPI that can return a patron's current checkouts and holds, that
    were made outside the Palace platform.
    """

    @abstractmethod
    def patron_activity(
        self, patron: Patron, pin: str
    ) -> Iterable[LoanInfo | HoldInfo]:
        """Return a patron's current checkouts and holds."""
        ...


class CirculationAPI:
    """Implement basic circulation logic and abstract away the details
    between different circulation APIs behind generic operations like
    'borrow'.
    """

    def __init__(
        self,
        db: Session,
        library: Library,
        analytics: Analytics | None = None,
        registry: IntegrationRegistry[CirculationApiType] | None = None,
    ):
        """Constructor.

        :param db: A database session (probably a scoped session, which is
            why we can't derive it from `library`).

        :param library: A Library object representing the library
          whose circulation we're concerned with.

        :param analytics: An Analytics object for tracking
          circulation events.

        :param registry: An IntegrationRegistry mapping Collection protocols to
           API classes that should be instantiated to deal with these
           protocols. The default registry will work fine unless you're a
           unit test.

           Since instantiating these API classes may result in API
           calls, we only instantiate one CirculationAPI per library,
           and keep them around as long as possible.
        """
        self._db = db
        self.library_id = library.id
        self.analytics = analytics
        self.initialization_exceptions = dict()
        self.registry = registry or LicenseProvidersRegistry()

        # Each of the Library's relevant Collections is going to be
        # associated with an API object.
        self.api_for_collection = {}

        # When we get our view of a patron's loans and holds, we need
        # to include loans whose license pools are in one of the
        # Collections we manage. We don't need to care about loans
        # from any other Collections.
        self.collection_ids_for_sync = []

        self.log = logging.getLogger("Circulation API")
        for collection in library.collections:
            if collection.protocol in self.registry:
                api = None
                try:
                    api = self.registry[collection.protocol](db, collection)
                except CannotLoadConfiguration as exception:
                    self.log.exception(
                        "Error loading configuration for {}: {}".format(
                            collection.name, str(exception)
                        )
                    )
                    self.initialization_exceptions[collection.id] = exception
                if api:
                    self.api_for_collection[collection.id] = api
                    if isinstance(api, PatronActivityCirculationAPI):
                        self.collection_ids_for_sync.append(collection.id)

    @property
    def library(self) -> Library | None:
        if self.library_id is None:
            return None
        return Library.by_id(self._db, self.library_id)

    def api_for_license_pool(
        self, licensepool: LicensePool
    ) -> CirculationApiType | None:
        """Find the API to use for the given license pool."""
        return self.api_for_collection.get(licensepool.collection.id)

    def can_revoke_hold(self, licensepool: LicensePool, hold: Hold) -> bool:
        """Some circulation providers allow you to cancel a hold
        when the book is reserved to you. Others only allow you to cancel
        a hold while you're in the hold queue.
        """
        if hold.position is None or hold.position > 0:
            return True
        api = self.api_for_license_pool(licensepool)
        if api and api.CAN_REVOKE_HOLD_WHEN_RESERVED:
            return True
        return False

    def _collect_event(
        self,
        patron: Patron | None,
        licensepool: LicensePool | None,
        name: str,
        include_neighborhood: bool = False,
        duration: int | None = None,  # Finland
    ) -> None:
        """Collect an analytics event.

        :param patron: The Patron associated with the event. If this
            is not specified, the current request's authenticated
            patron will be used.
        :param licensepool: The LicensePool associated with the event.
        :param name: The name of the event.
        :param include_neighborhood: If this is True, _and_ the
            current request's authenticated patron is the same as the
            patron in `patron`, _and_ the authenticated patron has
            associated neighborhood information obtained from the ILS,
            then that neighborhood information (but not the patron's
            identity) will be associated with the circulation event.
        """
        if not self.analytics:
            return

        # It would be really useful to know which patron caused this
        # this event -- this will help us get a library and
        # potentially a neighborhood.
        if flask.request:
            request_patron = getattr(flask.request, "patron", None)
        else:
            request_patron = None
        patron = patron or request_patron

        # We need to figure out which library is associated with
        # this circulation event.
        if patron:
            # The library of the patron who caused the event.
            library = patron.library
        elif flask.request and getattr(flask.request, "library", None):
            # The library associated with the current request.
            library = getattr(flask.request, "library")
        else:
            # The library associated with the CirculationAPI itself.
            library = self.library

        neighborhood = None
        if (
            include_neighborhood
            and flask.request
            and request_patron
            and request_patron == patron
        ):
            neighborhood = getattr(request_patron, "neighborhood", None) or getattr(
                request_patron, "cached_neighborhood", None
            )  # Finland: add cached_neighborhood option

        self.analytics.collect_event(
            library, licensepool, name, neighborhood=neighborhood, duration=duration
        )

    # Finland
    def _collect_checkout_history(
        self, patron: Patron, license_pool: LicensePool
    ) -> None:
        """Save history for checkout, for later use in loan history or analytics"""
        __transaction = self._db.begin_nested()
        checkout = LoanCheckout(
            patron=patron, license_pool=license_pool, timestamp=utc_now()
        )
        self._db.add(checkout)
        __transaction.commit()

    def _collect_checkout_event(self, patron: Patron, licensepool: LicensePool) -> None:
        """A simple wrapper around _collect_event for handling checkouts.

        This is called in two different places -- one when loaning
        licensed books and one when 'loaning' open-access books.
        """
        return self._collect_event(
            patron, licensepool, CirculationEvent.CM_CHECKOUT, include_neighborhood=True
        )

    def borrow(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        delivery_mechanism: LicensePoolDeliveryMechanism,
        hold_notification_email: str | None = None,
    ) -> tuple[Loan | None, Hold | None, bool]:
        """Either borrow a book or put it or leave it on hold. Don't worry about fulfilling
        the loan yet.

        :return: A 3-tuple (`Loan`, `Hold`, `is_new`). Either `Loan`
            or `Hold` must be None, but not both.
        """
        # Short-circuit the request if the patron lacks borrowing
        # privileges. This can happen for a few different reasons --
        # fines, blocks, expired card, etc.
        PatronUtility.assert_borrowing_privileges(patron)

        now = utc_now()
        api = self.api_for_license_pool(licensepool)

        # Okay, it's not an open-access book. This means we need to go
        # to an external service to get the book.

        if not api:
            # If there's no API for the pool, the pool is probably associated
            # with a collection that this library doesn't have access to.
            raise NoLicenses()

        must_set_delivery_mechanism = (
            api.SET_DELIVERY_MECHANISM_AT == BaseCirculationAPI.BORROW_STEP
        )

        if must_set_delivery_mechanism and not delivery_mechanism:
            raise DeliveryMechanismMissing()

        # Do we (think we) already have this book out on loan?
        existing_loan = get_one(
            self._db,
            Loan,
            patron=patron,
            license_pool=licensepool,
            on_multiple="interchangeable",
        )

        loan_info = None
        hold_info = None
        new_loan = False

        # Some exceptions may be raised during the borrow process even
        # if the book is not actually available for loan.  In those
        # cases, we will store the exception here and try to place the
        # book on hold. If the hold placement succeeds, there's no
        # problem. If the hold placement fails because the book is
        # actually available, it's better to raise this exception than
        # one that says "you tried to place a currently available book
        # on hold" -- that's probably not what the patron actually
        # tried to do.
        loan_exception = None

        # Enforce any library-specific limits on loans or holds.
        self.enforce_limits(patron, licensepool)

        # When the patron is at the top of the hold queue ("reverved")
        # but the loan fails, we want to raise an exception  when that
        # happens.
        reserved_license_exception = False

        # Since that didn't raise an exception, we don't know of any
        # reason why the patron shouldn't be able to get a loan or a
        # hold. There are race conditions that will allow someone to
        # get a hold in excess of their hold limit (because we thought
        # they were getting a loan but someone else checked out the
        # book right before we got to it) but they're rare and not
        # serious. There are also vendor-side restrictions that may
        # impose additional limits on patron activity, but that will
        # just result in exceptions being raised later in this method
        # rather than in enforce_limits.

        # We try to check out the book even if we believe it's not
        # available -- someone else may have checked it in since we
        # last looked.
        try:
            loan_info = api.checkout(
                patron, pin, licensepool, delivery_mechanism=delivery_mechanism
            )
            if isinstance(loan_info, HoldInfo):
                # If the API couldn't give us a loan, it may have given us
                # a hold instead of raising an exception.
                hold_info = loan_info
                loan_info = None
            else:
                # We asked the API to create a loan and it gave us a
                # LoanInfo object, rather than raising an exception like
                # AlreadyCheckedOut.
                #
                # For record-keeping purposes we're going to treat this as
                # a newly transacted loan, although it's possible that the
                # API does something unusual like return LoanInfo instead
                # of raising AlreadyCheckedOut.
                new_loan = True
        except AlreadyCheckedOut:
            # This is good, but we didn't get the real loan info.
            # Just fake it.
            loan_info = LoanInfo.from_license_pool(
                licensepool,
                start_date=None,
                end_date=now + datetime.timedelta(hours=1),
                external_identifier=(
                    existing_loan.external_identifier if existing_loan else None
                ),
            )
        except AlreadyOnHold:
            # We're trying to check out a book that we already have on hold.
            hold_info = HoldInfo.from_license_pool(
                licensepool,
                hold_position=None,
            )
        except NoAvailableCopies:
            if existing_loan:
                # The patron tried to renew a loan but there are
                # people waiting in line for them to return the book,
                # so renewals are not allowed.
                raise CannotRenew(
                    _("You cannot renew a loan if other patrons have the work on hold.")
                )

            # The patron had a hold and was in the hold queue's 0th position believing
            # there were copies available for them to checkout.
            else:
                # That's fine, we'll just (try to) place a hold.
                #
                # Since the patron incorrectly believed there were
                # copies available, update availability information
                # immediately.
                api.update_availability(licensepool)
        except NoAvailableCopiesWhenReserved:
            raise
        except NoLicenses:
            # Since the patron incorrectly believed there were
            # licenses available, update availability information
            # immediately.
            api.update_availability(licensepool)
            raise
        except PatronLoanLimitReached as e:
            # The server-side loan limits didn't apply to this patron,
            # but there's a vendor-side loan limit that does. However,
            # we don't necessarily know whether or not this book is
            # available! We'll try putting the book on hold just in
            # case, and raise this exception only if that doesn't
            # work.
            loan_exception = e

        if loan_info:
            # We successfully secured a loan.  Now create it in our
            # database.
            __transaction = self._db.begin_nested()

            loan, new_loan_record = loan_info.create_or_update(patron, licensepool)
            self.log.info(f"Create/update loan: {loan}")
            if must_set_delivery_mechanism:
                loan.fulfillment = delivery_mechanism  # type: ignore
            existing_hold = get_one(
                self._db,
                Hold,
                patron=patron,
                license_pool=licensepool,
                on_multiple="interchangeable",
            )
            if existing_hold:
                # The book was on hold, and now we have a loan.
                # Delete the record of the hold.
                self.log.info(f"Hold became loan, deleting hold {existing_hold.id}")
                self._db.delete(existing_hold)
            __transaction.commit()

            if loan and new_loan:
                # Send out an analytics event to record the fact that
                # a loan was initiated through the circulation
                # manager.
                self._collect_checkout_history(patron, licensepool)
                self._collect_checkout_event(patron, licensepool)
            return loan, None, new_loan_record  # type: ignore

        # At this point we know that we neither successfully
        # transacted a loan, nor discovered a preexisting loan.

        # Checking out a book didn't work, so let's try putting
        # the book on hold.
        if not hold_info:
            # At this point, we need to check to see if the patron
            # has reached their hold limit since we did not raise
            # an exception for it earlier when limits were enforced.
            at_hold_limit = self.patron_at_hold_limit(patron)
            if not at_hold_limit:
                try:
                    hold_info = api.place_hold(
                        patron, pin, licensepool, hold_notification_email
                    )
                except AlreadyOnHold as e:
                    hold_info = HoldInfo.from_license_pool(
                        licensepool,
                        hold_position=None,
                    )
                except CurrentlyAvailable:
                    if loan_exception:
                        # We tried to take out a loan and got an
                        # exception.  But we weren't sure whether the real
                        # problem was the exception we got or the fact
                        # that the book wasn't available. Then we tried to
                        # place a hold, which didn't work because the book
                        # is currently available. That answers the
                        # question: we should have let the first exception
                        # go through.  Raise it now.
                        raise loan_exception

                    # This shouldn't normally happen, but if it does,
                    # treat it as any other exception.
                    raise
            else:
                raise PatronHoldLimitReached

        # It's pretty rare that we'd go from having a loan for a book
        # to needing to put it on hold, but we do check for that case.
        __transaction = self._db.begin_nested()
        hold, is_new = hold_info.create_or_update(patron, licensepool)
        self.log.info(f"Create/update hold: {hold}")

        if hold and is_new:
            # Send out an analytics event to record the fact that
            # a hold was initiated through the circulation
            # manager.
            self._collect_event(
                patron,
                licensepool,
                CirculationEvent.CM_HOLD_PLACE,
                include_neighborhood=True,
            )

        if existing_loan:
            self._db.delete(existing_loan)
        __transaction.commit()

        return None, hold, is_new

    def enforce_limits(self, patron: Patron, pool: LicensePool) -> None:
        """Enforce library-specific patron loan and hold limits.

        :param patron: A Patron.
        :param pool: A LicensePool the patron is trying to access. As
           a side effect, this method may update `pool` with the latest
           availability information from the remote API.
        :raises PatronLoanLimitReached: If `pool` is currently
            available but the patron is at their loan limit.
        :raises PatronHoldLimitReached: If `pool` is currently
            unavailable and the patron is at their hold limit.
        """
        if pool.open_access or pool.unlimited_access:
            # Open-access books and books with unlimited access
            # are able to be checked out even if the patron is
            # at their loan limit.
            return

        at_loan_limit = self.patron_at_loan_limit(patron)
        at_hold_limit = self.patron_at_hold_limit(patron)

        if not at_loan_limit and not at_hold_limit:
            # This patron can take out either a loan or a hold, so the
            # limits don't apply.
            return

        if not at_loan_limit and at_hold_limit:
            # This patron can take out a loan, but not a hold. This is relevant when
            # the patron can't place a hold but can take out a loan.
            return

        if at_loan_limit and at_hold_limit:
            # This patron can neither take out a loan or place a hold.
            # Raise PatronLoanLimitReached for the most understandable
            # error message.
            raise PatronLoanLimitReached(limit=patron.library.settings.loan_limit)

        # At this point it's important that we get up-to-date
        # availability information about this LicensePool, to reduce
        # the risk that (e.g.) we apply the loan limit to a book that
        # would be placed on hold instead.
        api = self.api_for_license_pool(pool)
        if api is not None:
            api.update_availability(pool)

        currently_available = pool.licenses_available > 0
        if currently_available and at_loan_limit:
            raise PatronLoanLimitReached(limit=patron.library.settings.loan_limit)
        if not currently_available and at_hold_limit:
            raise PatronHoldLimitReached(limit=patron.library.settings.hold_limit)

    def patron_at_loan_limit(self, patron: Patron) -> bool:
        """Is the given patron at their loan limit?

        This doesn't belong in Patron because the loan limit is not core functionality.
        Of course, Patron itself isn't really core functionality...

        :param patron: A Patron.
        """
        loan_limit = patron.library.settings.loan_limit
        if not loan_limit:
            return False

        # Open-access loans, and loans of indefinite duration, don't count towards the loan limit
        # because they don't block anyone else.
        non_open_access_loans_with_end_date = [
            loan
            for loan in patron.loans
            if loan.license_pool and loan.license_pool.open_access == False and loan.end
        ]
        return len(non_open_access_loans_with_end_date) >= loan_limit

    def patron_at_hold_limit(self, patron: Patron) -> bool:
        """Is the given patron at their hold limit?

        This doesn't belong in Patron because the hold limit is not core functionality.
        Of course, Patron itself isn't really core functionality...

        :param patron: A Patron.
        """
        hold_limit = patron.library.settings.hold_limit
        if not hold_limit:
            return False
        return len(patron.holds) >= hold_limit

    def can_fulfill_without_loan(
        self,
        patron: Patron | None,
        pool: LicensePool | None,
        lpdm: LicensePoolDeliveryMechanism | None,
    ) -> bool:
        """Can we deliver the given book in the given format to the given
        patron, even though the patron has no active loan for that
        book?

        In general this is not possible, but there are some
        exceptions, managed in subclasses of BaseCirculationAPI.

        :param patron: A Patron. This is probably None, indicating
            that someone is trying to fulfill a book without identifying
            themselves.

        :param delivery_mechanism: The LicensePoolDeliveryMechanism
            representing a format for a specific title.
        """
        if not lpdm or not pool:
            return False
        if pool.open_access:
            return True
        api = self.api_for_license_pool(pool)
        if not api:
            return False
        return api.can_fulfill_without_loan(patron, pool, lpdm)

    def fulfill(
        self,
        patron: Patron,
        pin: str,
        licensepool: LicensePool,
        delivery_mechanism: LicensePoolDeliveryMechanism,
    ) -> Fulfillment | FulfillmentInfo:
        """Fulfil a book that a patron has previously checked out.

        :param delivery_mechanism: A LicensePoolDeliveryMechanism
            explaining how the patron wants the book to be delivered. If
            the book has previously been delivered through some other
            mechanism, this parameter is ignored and the previously used
            mechanism takes precedence.

        :return: A Fulfillment object.

        """
        fulfillment: Fulfillment
        loan = get_one(
            self._db,
            Loan,
            patron=patron,
            license_pool=licensepool,
            on_multiple="interchangeable",
        )
        api = self.api_for_license_pool(licensepool)
        if not api:
            raise CannotFulfill()

        if not loan and not self.can_fulfill_without_loan(
            patron, licensepool, delivery_mechanism
        ):
            raise NoActiveLoan(_("Cannot find your active loan for this work."))

        if (
            loan
            and loan.fulfillment is not None
            and not loan.fulfillment.compatible_with(delivery_mechanism)
        ):
            raise DeliveryMechanismConflict(
                _(
                    "You already fulfilled this loan as %(loan_delivery_mechanism)s, you can't also do it as %(requested_delivery_mechanism)s",
                    loan_delivery_mechanism=loan.fulfillment.delivery_mechanism.name,
                    requested_delivery_mechanism=delivery_mechanism.delivery_mechanism.name,
                )
            )

        fulfillment = api.fulfill(
            patron,
            pin,
            licensepool,
            delivery_mechanism=delivery_mechanism,
        )  # type: ignore
        if not fulfillment:
            raise NoAcceptableFormat()

        # Send out an analytics event to record the fact that
        # a fulfillment was initiated through the circulation
        # manager.
        self._collect_event(
            patron, licensepool, CirculationEvent.CM_FULFILL, include_neighborhood=True
        )

        # Make sure the delivery mechanism we just used is associated
        # with the loan, if any.
        if (
            loan
            and loan.fulfillment is None
            and not delivery_mechanism.delivery_mechanism.is_streaming
        ):
            __transaction = self._db.begin_nested()
            loan.fulfillment = delivery_mechanism
            __transaction.commit()

        return fulfillment

    def revoke_loan(
        self, patron: Patron, pin: str, licensepool: LicensePool
    ) -> Literal[True]:
        """Revoke a patron's loan for a book."""
        loan = get_one(
            self._db,
            Loan,
            patron=patron,
            license_pool=licensepool,
            on_multiple="interchangeable",
        )

        # Finland: Calculate the duration of the loan.
        duration = None
        if loan is not None and loan.start is not None:
            try:
                time_difference = utc_now() - loan.start
                duration = math.floor(time_difference.total_seconds())

            except Exception as e:
                self.log.warning(
                    f"Could not calculate duration of loan with start time #{loan.start}."
                )

        if loan is not None:
            api = self.api_for_license_pool(licensepool)
            if api is None:
                self.log.error(
                    f"Patron: {patron!r} tried to revoke loan for licensepool: {licensepool!r} but no api was found."
                )
                raise CannotReturn("No API available.")
            try:
                api.checkin(patron, pin, licensepool)
            except NotCheckedOut as e:
                # The book wasn't checked out in the first
                # place. Everything's fine.
                pass

            __transaction = self._db.begin_nested()
            self.log.info(f"In revoke_loan(), deleting loan #{loan.id}")
            self._db.delete(loan)
            patron.last_loan_activity_sync = None
            __transaction.commit()
            # Send out an analytics event to record the fact that
            # a loan was revoked through the circulation
            # manager.
            self._collect_event(
                patron,
                licensepool,
                CirculationEvent.CM_CHECKIN,
                include_neighborhood=True,
                duration=duration,
            )

        # Any other CannotReturn exception will be propagated upwards
        # at this point.
        return True

    def release_hold(
        self, patron: Patron, pin: str, licensepool: LicensePool
    ) -> Literal[True]:
        """Remove a patron's hold on a book."""
        hold = get_one(
            self._db,
            Hold,
            patron=patron,
            license_pool=licensepool,
            on_multiple="interchangeable",
        )
        api = self.api_for_license_pool(licensepool)
        if api is None:
            raise TypeError(f"No api for licensepool: {licensepool}")
        try:
            api.release_hold(patron, pin, licensepool)
        except NotOnHold:
            # The book wasn't on hold in the first place. Everything's
            # fine.
            pass
        # Any other CannotReleaseHold exception will be propagated
        # upwards at this point
        if hold:
            __transaction = self._db.begin_nested()
            logging.info(f"Deleting hold: {hold}")
            self._db.delete(hold)
            __transaction.commit()

            # Send out an analytics event to record the fact that
            # a hold was revoked through the circulation
            # manager.
            self._collect_event(
                patron,
                licensepool,
                CirculationEvent.CM_HOLD_RELEASE,
                include_neighborhood=True,
            )

        return True

    def patron_activity(
        self, patron: Patron, pin: str
    ) -> tuple[list[LoanInfo], list[HoldInfo], bool]:
        """Return a record of the patron's current activity
        vis-a-vis all relevant external loan sources.

        We check each source in a separate thread for speed.

        :return: A 2-tuple (loans, holds) containing `HoldInfo` and
            `LoanInfo` objects.
        """
        log = self.log

        class PatronActivityThread(Thread):
            def __init__(
                self,
                api: PatronActivityCirculationAPI[
                    BaseCirculationApiSettings, BaseSettings
                ],
                patron: Patron,
                pin: str,
            ) -> None:
                self.api = api
                self.patron = patron
                self.pin = pin
                self.activity: Iterable[LoanInfo | HoldInfo] | None = None
                self.exception: Exception | None = None
                self.trace: tuple[
                    type[BaseException], BaseException, TracebackType
                ] | tuple[None, None, None] | None = None
                super().__init__()

            def run(self) -> None:
                before = time.time()
                try:
                    self.activity = self.api.patron_activity(self.patron, self.pin)
                except Exception as e:
                    self.exception = e
                    self.trace = sys.exc_info()
                after = time.time()
                log.debug(
                    "Synced %s in %.2f sec", self.api.__class__.__name__, after - before
                )

                # While testing we are in a Session scope
                # we need to only close this if api._db is a flask_scoped_session.
                if getattr(self.api, "_db", None) and type(self.api._db) != Session:
                    # Since we are in a Thread using a flask_scoped_session
                    # we can assume a new Session was opened due to the thread activity.
                    # We must close this session to avoid connection pool leaks
                    self.api._db.close()

        threads = []
        before = time.time()
        for api in list(self.api_for_collection.values()):
            if isinstance(api, PatronActivityCirculationAPI):
                thread = PatronActivityThread(api, patron, pin)
                threads.append(thread)
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        loans: list[LoanInfo] = []
        holds: list[HoldInfo] = []
        complete = True
        for thread in threads:
            if thread.exception:
                # Something went wrong, so we don't have a complete
                # picture of the patron's loans.
                complete = False
                self.log.error(
                    "%s errored out: %s",
                    thread.api.__class__.__name__,
                    thread.exception,
                    exc_info=thread.trace,
                )
            if thread.activity:
                for i in thread.activity:
                    if not isinstance(i, (LoanInfo, HoldInfo)):
                        self.log.warning(  # type: ignore[unreachable]
                            "value %r from patron_activity is neither a loan nor a hold.",
                            i,
                        )
                        continue

                    if isinstance(i, LoanInfo):
                        loans.append(i)
                    elif isinstance(i, HoldInfo):
                        holds.append(i)

        after = time.time()
        self.log.debug("Full sync took %.2f sec", after - before)
        return loans, holds, complete

    def local_loans(self, patron: Patron) -> Query[Loan]:
        return (
            self._db.query(Loan)
            .join(Loan.license_pool)
            .filter(LicensePool.collection_id.in_(self.collection_ids_for_sync))
            .filter(Loan.patron == patron)
        )

    def local_holds(self, patron: Patron) -> Query[Hold]:
        return (
            self._db.query(Hold)
            .join(Hold.license_pool)
            .filter(LicensePool.collection_id.in_(self.collection_ids_for_sync))
            .filter(Hold.patron == patron)
        )

    def sync_bookshelf(
        self, patron: Patron, pin: str, force: bool = False
    ) -> tuple[list[Loan] | Query[Loan], list[Hold] | Query[Hold]]:
        """Sync our internal model of a patron's bookshelf with any external
        vendors that provide books to the patron's library.

        :param patron: A Patron.
        :param pin: The password authenticating the patron; used by some vendors
           that perform a cross-check against the library ILS.
        :param force: If this is True, the method will call out to external
           vendors even if it looks like the system has up-to-date information
           about the patron.
        """
        # Get our internal view of the patron's current state.
        local_loans = self.local_loans(patron)
        local_holds = self.local_holds(patron)

        if patron and patron.last_loan_activity_sync and not force:
            # Our local data is considered fresh, so we can return it
            # without calling out to the vendor APIs.
            return local_loans, local_holds

        # Assuming everything goes well, we will set
        # Patron.last_loan_activity_sync to this value -- the moment
        # just before we started contacting the vendor APIs.
        last_loan_activity_sync: datetime.datetime | None = utc_now()

        # Update the external view of the patron's current state.
        remote_loans, remote_holds, complete = self.patron_activity(patron, pin)
        __transaction = self._db.begin_nested()

        if not complete:
            # We were not able to get a complete picture of the
            # patron's loan activity. Until we are able to do that, we
            # should never assume that our internal model of the
            # patron's loans is good enough to cache.
            last_loan_activity_sync = None

        now = utc_now()
        local_loans_by_identifier = {}
        local_holds_by_identifier = {}
        for l in local_loans:
            if not l.license_pool:
                self.log.error("Active loan with no license pool!")
                continue
            i = l.license_pool.identifier
            if not i:
                self.log.error(
                    "Active loan on license pool %s, which has no identifier!",
                    l.license_pool,
                )
                continue
            key = (i.type, i.identifier)
            local_loans_by_identifier[key] = l
        for h in local_holds:
            if not h.license_pool:
                self.log.error("Active hold with no license pool!")
                continue
            i = h.license_pool.identifier
            if not i:
                self.log.error(
                    "Active hold on license pool %r, which has no identifier!",
                    h.license_pool,
                )
                continue
            key = (i.type, i.identifier)
            local_holds_by_identifier[key] = h

        active_loans = []
        active_holds = []
        start: datetime.datetime | None
        end: datetime.datetime | None
        for loan in remote_loans:
            # This is a remote loan. Find or create the corresponding
            # local loan.
            key = (loan.identifier_type, loan.identifier)
            if key in local_loans_by_identifier:
                local_loan = local_loans_by_identifier[key]
            active_loans.append(local_loan)

            # Check the local loan off the list we're keeping so we
            # don't delete it later.
            key = (loan.identifier_type, loan.identifier)
            if key in local_loans_by_identifier:
                del local_loans_by_identifier[key]

        for hold in remote_holds:
            start = hold.start_date
            end = hold.end_date
            position = hold.hold_position
            key = (hold.identifier_type, hold.identifier)
            if key in local_holds_by_identifier:
                # We already have the Hold object, we don't need to look
                # it up again.
                local_hold = local_holds_by_identifier[key]

                # But maybe the remote's opinions as to the hold's
                # start or end date have changed.
                local_hold.update(start, end, position)
            active_holds.append(local_hold)

            # Check the local hold off the list we're keeping so that
            # we don't delete it later.
            if key in local_holds_by_identifier:
                del local_holds_by_identifier[key]

        # Now that we're in sync (or not), set last_loan_activity_sync
        # to the conservative value obtained earlier.
        if patron:
            patron.last_loan_activity_sync = last_loan_activity_sync

        __transaction.commit()
        self.log.info(
            f"Patron id {patron.id}: active loans {len(active_loans)}, "
            f"active holds {len(active_holds)} [synced: {patron.last_loan_activity_sync}]"
        )
        return active_loans, active_holds
