"""Batch MARC export integration."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from uuid import UUID, uuid4

import pytz
from pydantic import NonNegativeInt
from pymarc import Record
from sqlalchemy import select
from sqlalchemy.engine import ScalarResult
from sqlalchemy.orm import Session

from core.integration.base import HasLibraryIntegrationConfiguration
from core.integration.settings import (
    BaseSettings,
    ConfigurationFormItem,
    ConfigurationFormItemType,
    FormField,
)
from core.marc.builder import Annotator
from core.model import (
    Collection,
    Library,
    LicensePool,
    MarcFile,
    Representation,
    Work,
    create,
)
from core.service.storage.s3 import S3Service
from core.util.log import LoggerMixin
from core.util.uuid import uuid_encode


class MarcExporterSettings(BaseSettings):
    # This setting (in days) controls how often MARC files should be
    # automatically updated.
    update_frequency: NonNegativeInt = FormField(
        30,
        form=ConfigurationFormItem(
            label="Update frequency (in days)",
            type=ConfigurationFormItemType.NUMBER,
            required=True,
        ),
    )


class MarcExporterLibrarySettings(BaseSettings):
    organization_code: str | None = FormField(
        None,
        form=ConfigurationFormItem(
            label="The MARC organization code for this library (003 field).",
            description="MARC organization codes are assigned by the Library of Congress.",
            type=ConfigurationFormItemType.TEXT,
        ),
    )

    web_client_url: str | None = FormField(
        None,
        form=ConfigurationFormItem(
            label="The base URL for the web catalog for this library, for the 856 field.",
            description="If using a library registry that provides a web catalog, this can be left blank.",
            type=ConfigurationFormItemType.TEXT,
        ),
    )


class MARCExporter(
    HasLibraryIntegrationConfiguration[
        MarcExporterSettings, MarcExporterLibrarySettings
    ],
    LoggerMixin,
):
    """Turn a work into a record for a MARC file."""

    # The minimum size each piece of a multipart upload should be
    MINIMUM_UPLOAD_BATCH_SIZE_BYTES = 5 * 1024 * 1024  # 5MB

    def __init__(
        self,
        _db: Session,
        storage_service: S3Service,
    ):
        self._db = _db
        self.storage_service = storage_service

    @classmethod
    def label(cls) -> str:
        return "MARC Export"

    @classmethod
    def description(cls) -> str:
        return (
            "Export metadata into MARC files that can be imported into an ILS manually."
        )

    @classmethod
    def settings_class(cls) -> type[MarcExporterSettings]:
        return MarcExporterSettings

    @classmethod
    def library_settings_class(cls) -> type[MarcExporterLibrarySettings]:
        return MarcExporterLibrarySettings

    @classmethod
    def create_record(
        cls,
        revised: bool,
        work: Work,
        annotator: Annotator,
    ) -> Record | None:
        """Build a complete MARC record for a given work."""
        pool = work.active_license_pool()
        if not pool:
            return None

        edition = pool.presentation_edition
        identifier = pool.identifier

        return annotator.annotate_work_record(revised, work, pool, edition, identifier)

    @staticmethod
    def _date_to_string(date: datetime) -> str:
        return date.astimezone(pytz.UTC).strftime("%Y-%m-%d")

    def _file_key(
        self,
        uuid: UUID,
        library: Library,
        collection: Collection,
        creation_time: datetime,
        since_time: datetime | None = None,
    ) -> str:
        """The path to the hosted MARC file for the given library, collection,
        and date range."""
        root = "marc"
        short_name = str(library.short_name)
        creation = self._date_to_string(creation_time)

        if since_time:
            file_type = f"delta.{self._date_to_string(since_time)}.{creation}"
        else:
            file_type = f"full.{creation}"

        uuid_encoded = uuid_encode(uuid)
        collection_name = collection.name.replace(" ", "_")
        filename = f"{collection_name}.{file_type}.{uuid_encoded}.mrc"
        parts = [root, short_name, filename]
        return "/".join(parts)

    def query_works(
        self,
        collection: Collection,
        since_time: datetime | None,
        creation_time: datetime,
        batch_size: int,
    ) -> ScalarResult:
        query = (
            select(Work)
            .join(LicensePool)
            .join(Collection)
            .where(
                Collection.id == collection.id,
                Work.last_update_time <= creation_time,
            )
        )

        if since_time is not None:
            query = query.where(Work.last_update_time >= since_time)

        return self._db.execute(query).unique().yield_per(batch_size).scalars()

    def records(
        self,
        library: Library,
        collection: Collection,
        annotator: Annotator,
        *,
        creation_time: datetime,
        since_time: datetime | None = None,
        batch_size: int = 500,
    ) -> None:
        """
        Create and export a MARC file for the books in a collection.
        """
        uuid = uuid4()
        key = self._file_key(uuid, library, collection, creation_time, since_time)

        with self.storage_service.multipart(
            key,
            content_type=Representation.MARC_MEDIA_TYPE,
        ) as upload:
            this_batch = BytesIO()

            works = self.query_works(collection, since_time, creation_time, batch_size)
            for work in works:
                # Create a record for each work and add it to the MARC file in progress.
                record = self.create_record(
                    since_time is not None,
                    work,
                    annotator,
                )
                if record:
                    record_bytes = record.as_marc()
                    this_batch.write(record_bytes)
                    if (
                        this_batch.getbuffer().nbytes
                        >= self.MINIMUM_UPLOAD_BATCH_SIZE_BYTES
                    ):
                        # We've reached or exceeded the upload threshold.
                        # Upload one part of the multipart document.
                        upload.upload_part(this_batch.getvalue())
                        this_batch.seek(0)
                        this_batch.truncate()

            # Upload the final part of the multi-document, if
            # necessary.
            if this_batch.getbuffer().nbytes > 0:
                upload.upload_part(this_batch.getvalue())

        if upload.complete:
            create(
                self._db,
                MarcFile,
                id=uuid,
                library=library,
                collection=collection,
                created=creation_time,
                since=since_time,
                key=key,
            )
        else:
            if upload.exception:
                # Log the exception and move on to the next file. We will try again next script run.
                self.log.error(
                    f"Failed to upload MARC file for {library.short_name}/{collection.name}: {upload.exception}",
                    exc_info=upload.exception,
                )
            else:
                # There were no records to upload. This is not an error, but we should log it.
                self.log.info(
                    f"No MARC records to upload for {library.short_name}/{collection.name}."
                )
