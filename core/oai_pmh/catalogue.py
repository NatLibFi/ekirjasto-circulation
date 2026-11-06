"""Library-scoped catalogue selection for the MARC-only OAI-PMH provider."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from datetime import datetime
from functools import cached_property
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from core.model import Contribution, Edition, Identifier, Library, LicensePool, Work
from core.model.constants import LinkRelations
from core.model.licensing import LicensePoolDeliveryMechanism
from core.model.resource import Hyperlink, Representation, Resource
from core.oai_pmh.models import OAIHeader


class OAICatalogue:
    BATCH_SIZE = 500

    def __init__(self, session: Session, library: Library):
        """Bind catalogue queries and fallback datestamps to a library and session."""
        self.session = session
        self.library = library
        self.initialized_at = cast(datetime, library.oai_pmh_initialized_at)

    @cached_property
    def collection_ids(self) -> set[int]:
        """Cache the IDs of all persisted collections available to this library."""
        return {
            collection.id
            for collection in self.library.collections
            if collection.id is not None
        }

    def works(self):
        """Build a query for presentation-ready works in the library's collections.

        Eager-load the relationships needed to select a pool using the same
        rules as OPDS; final harvest eligibility is checked in ``_headers``.
        """
        return (
            select(Work)
            .where(
                Work.presentation_ready == True,
                Work.license_pools.any(
                    LicensePool.collection_id.in_(self.collection_ids)
                ),
            )
            .options(
                selectinload(Work.license_pools).joinedload(LicensePool.identifier),
                selectinload(Work.license_pools).joinedload(
                    LicensePool.presentation_edition
                ),
                selectinload(Work.license_pools).joinedload(LicensePool.data_source),
                selectinload(Work.license_pools).selectinload(
                    LicensePool.delivery_mechanisms
                ),
            )
        )

    def open_access_urls(self, works: Sequence[Work]) -> dict[int, str | None]:
        """Resolve usable open-access downloads for a batch of candidate works.

        Load download resources together and apply the shared resource ranking
        rules. Map each candidate pool ID to its explicit download URL, its
        selected representation URL, or None when no download is usable.
        """
        pools = [
            pool
            for work in works
            for pool in work.license_pools
            if pool.collection_id in self.collection_ids
            and not pool.superceded
            and pool.open_access
        ]
        if not pools:
            return {}
        resources: dict[int, list[Resource]] = defaultdict(list)
        rows = self.session.execute(
            select(Hyperlink.identifier_id, Resource)
            .join(Resource, Hyperlink.resource_id == Resource.id)
            .where(
                Hyperlink.identifier_id.in_({pool.identifier_id for pool in pools}),
                Hyperlink.rel == LinkRelations.OPEN_ACCESS_DOWNLOAD,
            )
            .options(
                joinedload(Resource.representation).joinedload(Representation.resource),
                joinedload(Resource.data_source),
            )
        )
        for identifier_id, resource in rows.unique():
            resources[identifier_id].append(resource)
        urls: dict[int, str | None] = {}
        for pool in pools:
            assert pool.id is not None and pool.identifier_id is not None
            resource = pool.select_open_access_resource(resources[pool.identifier_id])
            urls[pool.id] = pool._open_access_download_url or (
                resource.representation.public_url
                if resource and resource.representation
                else None
            )
        return urls

    def _headers(
        self,
        *,
        after: int = 0,
        upper: int | None = None,
        identifier: tuple[str, str] | None = None,
    ) -> Iterator[OAIHeader]:
        """Yield eligible publication headers in ascending work-ID order.

        Scan candidates in batches after the exclusive cursor and up to the
        inclusive optional ceiling. Select one pool per work using OPDS rules,
        then check harvest eligibility and any requested publication identity.
        Use the latest work or pool timestamp, falling back to repository
        initialization, and truncate modification times to whole seconds.
        """
        if not self.collection_ids:
            return
        query = self.works()
        if upper is not None:
            query = query.where(Work.id <= upper)
        if identifier is not None:
            # Restrict candidate works, then verify their selected identifier.
            query = query.where(
                Work.license_pools.any(
                    LicensePool.identifier.has(
                        (Identifier.type == identifier[0])
                        & (Identifier.identifier == identifier[1])
                    )
                )
            )
        while True:
            works = (
                self.session.scalars(
                    query.where(Work.id > after)
                    .order_by(Work.id)
                    .limit(self.BATCH_SIZE)
                )
                .unique()
                .all()
            )
            if not works:
                return
            urls = self.open_access_urls(works)
            for work in works:
                # Select first, then filter: do not substitute a different pool
                # when the publication OPDS would expose is not harvestable.
                pool = work.active_license_pool(
                    collection_ids=self.collection_ids, open_access_urls=urls
                )
                if (
                    pool is None
                    or pool.suppressed
                    or not pool.delivery_mechanisms
                    or not pool.presentation_edition
                    or not pool.presentation_edition.title
                    or not pool.identifier
                    or pool.identifier.type is None
                    or pool.identifier.identifier is None
                ):
                    continue
                identity = (pool.identifier.type, pool.identifier.identifier)
                if identifier is not None and identity != identifier:
                    continue
                times = [
                    value
                    for value in (
                        work.last_update_time,
                        pool.last_checked,
                        pool.availability_time,
                    )
                    if value is not None
                ]
                modified = max(times) if times else self.initialized_at
                yield OAIHeader(
                    work_id=work.id,
                    identifier_id=pool.identifier.id,
                    identifier_type=identity[0],
                    identifier_value=identity[1],
                    pool_id=pool.id,
                    modified_at=modified.replace(microsecond=0),
                    entered_at=pool.availability_time or self.initialized_at,
                )
            # Advance past scanned candidates even if none produced a header.
            after = works[-1].id
            if len(works) < self.BATCH_SIZE:
                return

    def earliest_datestamp(self) -> datetime:
        """Return the oldest visible modification time, or initialization if empty.

        Scan eligible headers up to the current work-ID ceiling so Identify
        reports a datestamp drawn from the harvestable catalogue.
        """
        return min(
            (header.modified_at for header in self._headers(upper=self.upper_bound())),
            default=self.initialized_at,
        )

    def upper_bound(self) -> int:
        """Return the largest current work ID, or zero, to bound a new harvest."""
        return self.session.scalar(select(func.max(Work.id))) or 0

    def headers(
        self,
        *,
        after: int = 0,
        upper: int | None = None,
        limit: int,
        since: datetime | None = None,
        until: datetime | None = None,
        identifier: tuple[str, str] | None = None,
    ) -> list[OAIHeader]:
        """Return at most ``limit`` eligible headers in ascending work-ID order.

        Apply inclusive modification-date bounds after pool selection. The
        ``after`` cursor is exclusive and the optional ``upper`` ceiling is
        inclusive; an optional identifier matches the selected publication.
        """
        headers: list[OAIHeader] = []
        if limit <= 0:
            return headers
        for header in self._headers(after=after, upper=upper, identifier=identifier):
            if since is not None and header.modified_at < since:
                continue
            if until is not None and header.modified_at > until:
                continue
            headers.append(header)
            if len(headers) == limit:
                break
        return headers

    def load_pools(self, headers: Sequence[OAIHeader]) -> dict[int, LicensePool]:
        """Load the requested page's pools and MARC dependencies, keyed by pool ID.

        Eager-load identifiers, editions, contributors, summaries, genres, and
        delivery mechanisms to avoid fetching these separately for each record.
        """
        pools = (
            self.session.execute(
                select(LicensePool)
                .where(LicensePool.id.in_([header.pool_id for header in headers]))
                .options(
                    joinedload(LicensePool.identifier),
                    joinedload(LicensePool.presentation_edition)
                    .selectinload(Edition.contributions)
                    .joinedload(Contribution.contributor),
                    joinedload(LicensePool.work).undefer(Work.summary_text),
                    joinedload(LicensePool.work)
                    .selectinload(Work.work_genres)
                    .joinedload("genre"),
                    selectinload(LicensePool.delivery_mechanisms).joinedload(
                        LicensePoolDeliveryMechanism.delivery_mechanism
                    ),
                )
            )
            .unique()
            .scalars()
        )
        return {pool.id: pool for pool in pools}

    def isbn_identifiers(
        self, headers: Sequence[OAIHeader]
    ) -> dict[int, Identifier | None]:
        """Resolve one ISBN per page identifier, including recursive equivalents.

        Prefer the identifier itself when it is an ISBN, otherwise choose the
        equivalent ISBN with the lowest database ID. Include None for identifiers
        with no ISBN so record generation can avoid individual fallback queries.
        """
        ids = [header.identifier_id for header in headers]
        equivalents = Identifier.recursively_equivalent_identifier_ids(
            self.session, ids
        )
        isbn_ids = {value for values in equivalents.values() for value in values}
        isbns = (
            self.session.scalars(
                select(Identifier)
                .where(
                    Identifier.id.in_(isbn_ids | set(ids)),
                    Identifier.type == Identifier.ISBN,
                )
                .order_by(Identifier.id)
            )
            .unique()
            .all()
        )
        return {
            identifier_id: next(
                (isbn for isbn in isbns if isbn.id == identifier_id), None
            )
            or next(
                (
                    isbn
                    for isbn in isbns
                    if isbn.id in equivalents.get(identifier_id, [])
                ),
                None,
            )
            for identifier_id in ids
        }
