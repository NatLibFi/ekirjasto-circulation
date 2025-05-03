from __future__ import annotations

import json

import flask
from flask import Response
from flask_babel import lazy_gettext as _
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from core.util.datetime_helpers import utc_now
from api.controller.circulation_manager import CirculationManagerController
from api.odl import ODLAPI
from api.odl2 import ODL2API
from api.problem_details import INVALID_LOAN_FOR_ODL_NOTIFICATION, NO_ACTIVE_LOAN, INVALID_INPUT
from core.model import get_one
from core.model.patron import Loan, Patron
from core.model.credential import Credential
from core.model.licensing import License
from api.integration.registry.license_providers import LicenseProvidersRegistry
from api.lcp.status import LoanStatus
from core.util.problem_detail import ProblemDetailException

class ODLNotificationController(CirculationManagerController):
    """Receive notifications from an ODL distributor when the
    status of a loan changes.
    """

    def __init__(
        self,
        db: Session,
    ) -> None:
        self.db = db

    def _get_loan(
        self, patron_identifier: str | None, license_identifier: str | None
    ) -> Loan | None:
        if patron_identifier is None or license_identifier is None:
            return None
        return self.db.execute(
            select(Loan)
            .join(License)
            .join(Patron)
            .join(Credential)
            .where(
                License.identifier == license_identifier,
                Credential.credential == patron_identifier,
                Credential.type == Credential.IDENTIFIER_TO_REMOTE_SERVICE,
            )
        ).scalar_one_or_none()

    def notify(
        self, patron_identifier: str | None, license_identifier: str | None
    ) -> Response:
        loan = self._get_loan(patron_identifier, license_identifier)
        return self._process_notification(loan)

    def _process_notification(self, loan: Loan | None) -> Response:
        status_doc_json = flask.request.data

        try:
            status_doc = LoanStatus.parse_raw(status_doc_json)
        except ValidationError as e:
            self.log.exception(f"Unable to parse loan status document. {e}")
            raise ProblemDetailException(INVALID_INPUT) from e

        # We don't have a record of this loan. This likely means that the loan has been returned
        # and our local record has been deleted. This is expected, except in the case where the
        # distributor thinks the loan is still active.
        if loan is None and status_doc.active:
            self.log.error(
                f"No loan found for active OPDS + ODL Notification. Document: {status_doc.to_serializable()}"
            )
            raise ProblemDetailException(
                NO_ACTIVE_LOAN.detailed(_("No loan was found."), status_code=404)
            )

        if loan:
            collection = loan.license_pool.collection
            if collection.protocol not in (ODLAPI.label(), ODL2API.label()):
                raise ProblemDetailException(INVALID_LOAN_FOR_ODL_NOTIFICATION)

            # TODO: This should really just trigger a celery task to do an availability sync on the
            #   license, since this is flagging that we might be out of sync with the distributor.
            #   Once we move the OPDS2WithODL scripts to celery this should be possible.
            #   For now we just mark the loan as expired.
            if not status_doc.active:
                try:
                    with self.db.begin_nested():
                        loan.end = utc_now()
                except StaleDataError:
                    # This can happen if this callback happened while we were returning this
                    # item. We can fetch the loan, but it's deleted by the time we go to do
                    # the update. This is not a problem, as we were just marking the loan as
                    # completed anyway so we just continue.
                    ...

        return Response(status=204)

    # def notify(self, license_identifier: str) -> Response:
    #     loan = get_one(self._db, Loan, id=license_identifier)
    #     # TOIMISKO paremmin
    #     # loan = get_one(
    #     #     self._db,
    #     #     Loan,
    #     #     id=license_identifier,
    #     #     patron=patron,
    #     # )

    #     if not loan:
    #         return NO_ACTIVE_LOAN.detailed(_("No loan was found for this identifier."))

    #     status_doc = flask.request.data

    #     try:
    #         status_doc = LoanStatus.parse_raw(status_doc)
    #     except ValidationError as e:
    #         self.log.exception(f"Unable to parse loan status document. {e}")
    #         raise ProblemDetailException(INVALID_INPUT) from e

    #     # We don't have a record of this loan. This likely means that the loan has been returned
    #     # and our local record has been deleted. This is expected, except in the case where the
    #     # distributor thinks the loan is still active.
    #     if loan is None and status_doc.active:
    #         self.log.error(
    #             f"No loan found for active OPDS + ODL Notification. Document: {status_doc.model_dump_json()}"
    #         )
    #         raise ProblemDetailException(
    #             NO_ACTIVE_LOAN.detailed(_("No loan was found."), status_code=404)
    #         )

    #     if loan:
    #         collection = loan.license_pool.collection
    #         if collection.protocol not in (ODLAPI.label(), ODL2API.label()):
    #             raise ProblemDetailException(INVALID_LOAN_FOR_ODL_NOTIFICATION)

    #         # TODO: This should really just trigger a celery task to do an availability sync on the
    #         #   license, since this is flagging that we might be out of sync with the distributor.
    #         #   Once we move the OPDS2WithODL scripts to celery this should be possible.
    #         #   For now we just mark the loan as expired.
    #         if not status_doc.active:
    #             # This loan is no longer active. Update the pool's availability
    #             # and delete the loan.

    #             # Update the license
    #             loan.license.checkin()

    #             # If there are holds, the license is reserved for the next patron.
    #             _db.delete(loan)
    #             self.update_licensepool(loan.license_pool)
    #             try:
    #                 with self._db.begin_nested():
    #                     loan.end = utc_now()
    #             except StaleDataError:
    #                 # This can happen if this callback happened while we were returning this
    #                 # item. We can fetch the loan, but it's deleted by the time we go to do
    #                 # the update. This is not a problem, as we were just marking the loan as
    #                 # completed anyway so we just continue.
    #                 ...

    #     return Response(status=204)