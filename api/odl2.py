from __future__ import annotations

import datetime
import logging
from collections.abc import Callable, Sequence
from typing import Any

from flask_babel import lazy_gettext as _
from pydantic import PositiveInt
from requests import Response
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import and_, or_

from api.circulation import HoldInfo, LoanInfo
from api.circulation_exceptions import PatronHoldLimitReached, PatronLoanLimitReached
from api.lcp.status import LoanStatus
from api.odl import BaseODLAPI, BaseODLImporter, ODLLibrarySettings, ODLSettings
from api.opds import opds2, rwpm
from api.opds.odl import odl
from api.opds.odl.license_info import LicenseInfo
from core.integration.settings import (
    ConfigurationFormItem,
    ConfigurationFormItemType,
    FormField,
)
from core.metadata_layer import FormatData, LicenseData, Metadata, TimestampData
from core.model import Collection, Edition, LicensePool, Loan, RightsStatus
from core.model.configuration import ExternalIntegration
from core.model.licensing import License, LicenseStatus
from core.model.patron import Hold, Patron
from core.monitor import CollectionMonitor
from core.opds2_import import OPDS2Importer, OPDS2ImporterSettings, OPDS2ImportMonitor
from core.util.datetime_helpers import utc_now
from core.util.http import HTTP


class ODL2Settings(ODLSettings, OPDS2ImporterSettings):
    skipped_license_formats: list[str] = FormField(
        default=["text/html"],
        alias="odl2_skipped_license_formats",
        form=ConfigurationFormItem(
            label=_("Skipped license formats"),
            description=_(
                "List of license formats that will NOT be imported into Circulation Manager."
            ),
            type=ConfigurationFormItemType.LIST,
            required=False,
        ),
    )

    loan_limit: PositiveInt | None = FormField(
        default=None,
        alias="odl2_loan_limit",
        form=ConfigurationFormItem(
            label=_("Loan limit per patron"),
            description=_(
                "The maximum number of books a patron can have loaned out at any given time."
            ),
            type=ConfigurationFormItemType.NUMBER,
            required=False,
        ),
    )

    hold_limit: PositiveInt | None = FormField(
        default=None,
        alias="odl2_hold_limit",
        form=ConfigurationFormItem(
            label=_("Hold limit per patron"),
            description=_(
                "The maximum number of books a patron can have on hold at any given time."
            ),
            type=ConfigurationFormItemType.NUMBER,
            required=False,
        ),
    )


class ODL2API(BaseODLAPI[ODL2Settings, ODLLibrarySettings]):
    @classmethod
    def settings_class(cls) -> type[ODL2Settings]:
        return ODL2Settings

    @classmethod
    def library_settings_class(cls) -> type[ODLLibrarySettings]:
        return ODLLibrarySettings

    @classmethod
    def label(cls) -> str:
        return ExternalIntegration.ODL2

    @classmethod
    def description(cls) -> str:
        return "Import books from a distributor that uses OPDS2 + ODL (Open Distribution to Libraries)."

    def __init__(self, _db: Session, collection: Collection) -> None:
        super().__init__(_db, collection)
        self.loan_limit = self.settings.loan_limit
        self.hold_limit = self.settings.hold_limit

    def _checkout(
        self, patron: Patron, licensepool: LicensePool, hold: Hold | None = None
    ) -> LoanInfo:
        # If the loan limit is not None or 0
        if self.loan_limit:
            loans = list(
                filter(
                    lambda x: x.license_pool.collection.id == self.collection_id,
                    patron.loans,
                )
            )
            if len(loans) >= self.loan_limit:  # Changed back operator > to >=
                raise PatronLoanLimitReached(limit=self.loan_limit)
        return super()._checkout(patron, licensepool, hold)

    def _place_hold(self, patron: Patron, licensepool: LicensePool) -> HoldInfo:
        # If the hold limit is not None or 0
        if self.hold_limit:
            holds = list(
                filter(
                    lambda x: x.license_pool.collection.id == self.collection_id,
                    patron.holds,
                )
            )
            if len(holds) >= self.hold_limit:  # Changed back operator > to >=
                raise PatronHoldLimitReached(limit=self.hold_limit)
        return super()._place_hold(patron, licensepool)


class ODL2Importer(BaseODLImporter[ODL2Settings], OPDS2Importer):
    """Import information and formats from an ODL feed.

    The only change from OPDS2Importer is that this importer extracts
    FormatData and LicenseData from ODL 2.x's "licenses" arrays.
    """

    NAME = ODL2API.label()

    @classmethod
    def settings_class(cls) -> type[ODL2Settings]:
        return ODL2Settings

    def __init__(
        self,
        db: Session,
        collection: Collection,
        data_source_name: str | None = None,
        http_get: Callable[..., Response] | None = None,
    ):
        """Initialize a new instance of ODL2Importer class.

        :param db: Database session
        :type db: sqlalchemy.orm.session.Session

        :param collection: Circulation Manager's collection.
            LicensePools created by this OPDS2Import class will be associated with the given Collection.
            If this is None, no LicensePools will be created -- only Editions.
        :type collection: Collection

        :param data_source_name: Name of the source of this OPDS feed.
            All Editions created by this import will be associated with this DataSource.
            If there is no DataSource with this name, one will be created.
            NOTE: If `collection` is provided, its .data_source will take precedence over any value provided here.
            This is only for use when you are importing OPDS metadata without any particular Collection in mind.
        :type data_source_name: str
        """
        super().__init__(
            db,
            collection,
            data_source_name,
            http_get,
        )
        self._logger = logging.getLogger(__name__)
        self.http_get = http_get or HTTP.get_with_timeout

    def _extract_publication_metadata(
        self,
        publication: opds2.BasePublication,
        data_source_name: str | None,
        feed_self_url: str,
    ) -> Metadata:
        """Extract a Metadata object from opds2.BasePublication.

        :param publication: Feed object
        :param publication: Publication object
        :param data_source_name: Data source's name

        :return: Publication's metadata
        """
        metadata = super()._extract_publication_metadata(
            publication, data_source_name, feed_self_url
        )

        formats = []
        licenses = []
        medium = None

        # E-Kirjasto: If this is a generic OPDS2 publication, it is an open-access title.
        if isinstance(publication, odl.Publication):
            skipped_license_formats = set(self.settings.skipped_license_formats)
            publication_availability = publication.metadata.availability.available

            for odl_license in publication.licenses:
                identifier = odl_license.metadata.identifier

                checkout_link = odl_license.links.get(
                    rel=opds2.AcquisitionLinkRelations.borrow,
                    type=LoanStatus.content_type(),
                    raising=True,
                ).href

                license_info_document_link = odl_license.links.get(
                    rel=rwpm.LinkRelations.self,
                    type=LicenseInfo.content_type(),
                    raising=True,
                ).href

                expires = odl_license.metadata.terms.expires_datetime
                concurrency = odl_license.metadata.terms.concurrency

                parsed_license = (
                    LicenseData(
                        identifier=identifier,
                        checkout_url=None,
                        status_url=license_info_document_link,
                        status=LicenseStatus.unavailable,
                        checkouts_available=0,
                    )
                    if (
                        not odl_license.metadata.availability.available
                        or not publication_availability
                    )
                    else self.get_license_data(
                        license_info_document_link,
                        checkout_link,
                        identifier,
                        expires,
                        concurrency,
                        self.http_get,
                    )
                )

                if parsed_license is not None:
                    licenses.append(parsed_license)

                license_formats = set(odl_license.metadata.formats)
                for license_format in license_formats:
                    if (
                        skipped_license_formats
                        and license_format in skipped_license_formats
                    ):
                        continue

                    if not medium:
                        medium = Edition.medium_from_media_type(license_format)

                    drm_schemes: Sequence[str | None]
                    if license_format in self.LICENSE_FORMATS:
                        # Special case to handle DeMarque audiobooks which include the protection
                        # in the content type. When we see a license format of
                        # application/audiobook+json; protection=http://www.feedbooks.com/audiobooks/access-restriction
                        # it means that this audiobook title is available through the DeMarque streaming manifest
                        # endpoint.
                        drm_schemes = [
                            self.LICENSE_FORMATS[license_format][self.DRM_SCHEME]
                        ]
                        license_format = self.LICENSE_FORMATS[license_format][
                            self.CONTENT_TYPE
                        ]
                    else:
                        drm_schemes = (
                            odl_license.metadata.protection.formats
                            if odl_license.metadata.protection
                            else []
                        )

                    for drm_scheme in drm_schemes or [None]:
                        formats.append(
                            FormatData(
                                content_type=license_format,
                                drm_scheme=drm_scheme,
                                rights_uri=RightsStatus.IN_COPYRIGHT,
                            )
                        )

            metadata.circulation.licenses = licenses
            metadata.circulation.licenses_owned = None
            metadata.circulation.licenses_available = None
            metadata.circulation.licenses_reserved = None
            metadata.circulation.patrons_in_hold_queue = None
            metadata.circulation.formats.extend(formats)
            metadata.medium = medium

        return metadata


class ODL2ImportMonitor(OPDS2ImportMonitor):
    """Import information from an ODL feed."""

    PROTOCOL = ODL2API.label()
    SERVICE_NAME = "ODL 2.x Import Monitor"

    def __init__(
        self,
        _db: Session,
        collection: Collection,
        import_class: type[ODL2Importer],
        **import_class_kwargs: Any,
    ) -> None:
        # Always force reimport ODL collections to get up to date license information
        super().__init__(
            _db, collection, import_class, force_reimport=True, **import_class_kwargs
        )


class ODL2LoanReaper(CollectionMonitor):
    """Check for loans that have expired and delete them, and update
    the holds queues for their pools."""

    SERVICE_NAME = "ODL2 Loan Reaper"
    PROTOCOL = ODL2API.label()

    def __init__(
        self,
        _db: Session,
        collection: Collection,
        api: ODL2API | None = None,
        **kwargs: Any,
    ):
        super().__init__(_db, collection, **kwargs)
        self.api = api or ODL2API(_db, collection)

    def run_once(self, progress: TimestampData) -> TimestampData:
        # Find loans that have expired.
        self.log.info("Loan Reaper Job started")
        now = utc_now()
        expired_loans = (
            self._db.query(Loan)
            .join(Loan.license_pool)
            .filter(
                and_(
                    LicensePool.open_access == False,
                    LicensePool.collection_id == self.api.collection_id,
                    or_(
                        Loan.end
                        < now
                        - datetime.timedelta(
                            days=1
                        ),  # Loans that ended before yesterday
                        Loan.start < now - datetime.timedelta(days=90),
                        Loan.end == None,
                    ),  # Loans that started more than 90 days ago and have no end date
                )
            )
        )

        changed_pools = set()
        total_deleted_loans = 0
        for loan in expired_loans:
            # Only a license can be checked in
            if loan.license:
                loan.license.checkin()
            changed_pools.add(loan.license_pool)
            self.log.info(f"Deleting loan {loan} in pool {loan.license_pool}")
            self._db.delete(loan)
            total_deleted_loans += 1

        for pool in changed_pools:
            self.api.update_licensepool_and_hold_queue(pool)

        message = "Loans deleted: %d. License pools updated: %d" % (
            total_deleted_loans,
            len(changed_pools),
        )
        progress = TimestampData(achievements=message)
        return progress


class ODL2HoldReaper(CollectionMonitor):
    """Check for loans that have expired and delete them, and update
    the holds queues for their pools."""

    SERVICE_NAME = "ODL2 Hold Reaper"
    PROTOCOL = ODL2API.label()

    def __init__(
        self,
        _db: Session,
        collection: Collection,
        api: ODL2API | None = None,
        **kwargs: Any,
    ):
        super().__init__(_db, collection, **kwargs)
        self.api = api or ODL2API(_db, collection)

    def run_once(self, progress: TimestampData) -> TimestampData:
        """Find expired holds."""
        self.log.info("Hold Reaper Job started")
        now = utc_now()
        expired_holds = (
            self._db.query(Hold)
            .join(Hold.license_pool)
            .filter(LicensePool.collection_id == self.api.collection_id)
            .filter(or_(Hold.end < now, Hold.end == None))
            .filter(Hold.position == 0)
        )
        changed_pools = set()
        total_deleted_holds = 0
        for hold in expired_holds:
            changed_pools.add(hold.license_pool)
            self._db.delete(hold)
            # log circulation event:  hold expired
            total_deleted_holds += 1

        for pool in changed_pools:
            self.api.update_licensepool_and_hold_queue(pool)

        message = "Holds deleted: %d. License pools updated: %d" % (
            total_deleted_holds,
            len(changed_pools),
        )
        progress = TimestampData(achievements=message)
        return progress


class ODL2HoldQueueReaper(CollectionMonitor):
    """Delete hold queues in expired license pools."""

    SERVICE_NAME = "Hold Queue Reaper"
    PROTOCOL = ODL2API.label()

    def __init__(
        self,
        _db: Session,
        collection: Collection,
        api: ODL2API | None = None,
        **kwargs: Any,
    ):
        super().__init__(_db, collection, **kwargs)
        self.api = api or ODL2API(_db, collection)

    def run_once(self, progress: TimestampData) -> TimestampData:
        """Delete holds in expired license pools."""
        self.log.info("Hold Queue Reaper Job started")

        # Find license pools where all licenses are unavailable
        expired_pools = (
            select(LicensePool.id)
            .outerjoin(License, License.license_pool_id == LicensePool.id)
            .filter(LicensePool.collection_id == self.api.collection_id)
            .group_by(LicensePool.id)
            # Checks if the total amount of licenses matches the amount of 'unavailable' licenses
            .having(
                func.count(License.id)
                == func.count(
                    case(
                        whens=[(License.status == LicenseStatus.unavailable, 1)],
                        else_=None,
                    )
                )
            )
        ).subquery()
        # Find all the holds in those license pools
        holds = (
            self._db.query(Hold)
            .join(Hold.license_pool)
            .filter(Hold.license_pool_id.in_(select(expired_pools)))
        ).all()

        holds_count = len(holds)
        message = ["Hold queue reaping is done!"]
        if holds:
            cleaned_pools = set()
            # Execute the deletions
            for hold in holds:
                cleaned_pools.add(hold.license_pool)
                self._db.delete(hold)
            message.append(
                f"Removed {holds_count} holds from {len(cleaned_pools)} license pools:"
            )
            for pool in cleaned_pools:
                edition = pool.presentation_edition
                identifier = pool.identifier
                message.append(
                    f"   {identifier.identifier}/{edition.title}: {len(pool.holds)} holds"
                )
        else:
            message.append("No expired license pools to reap.")

        progress = TimestampData(achievements="\n".join(message))
        return progress
