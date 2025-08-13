from __future__ import annotations

import flask
from flask import Response
from flask_babel import lazy_gettext as _
from pydantic import ValidationError

from api.controller.circulation_manager import CirculationManagerController
from api.lcp.status import LoanStatus
from api.odl import ODLAPI
from api.odl2 import ODL2API
from api.problem_details import (
    INVALID_INPUT,
    INVALID_LOAN_FOR_ODL_NOTIFICATION,
    NO_ACTIVE_LOAN,
)
from core.model import get_one
from core.model.credential import Credential
from core.model.licensing import License
from core.model.patron import Loan, Patron
from core.util.problem_detail import ProblemDetailException


class ODLNotificationController(CirculationManagerController):
    """Receive notifications from an ODL distributor when the
    status of a loan changes.
    """

    def _get_loan(
        self, patron_identifier: str | None, license_identifier: str | None
    ) -> Loan | None:
        if patron_identifier is None or license_identifier is None:
            return None

        patron_identifier = str(patron_identifier)
        license_identifier = str(license_identifier)

        # When the loan was made the patron's identifier to this remote API was saved to credential.credential. It's now
        # used to identify the loan for this license.
        loan = (
            self._db.query(Loan)
            .join(License)
            .join(Patron)
            .join(Credential)
            .filter(
                License.identifier == license_identifier,
                Credential.credential == patron_identifier,
                Credential.type == Credential.IDENTIFIER_TO_REMOTE_SERVICE,
            )
            .one_or_none()
        )
        return loan

    # TODO: This method is deprecated and should be removed once all the loans
    #   created using the old endpoint have expired.
    def notify_deprecated(self, loan_id: int) -> Response:
        loan = get_one(self._db, Loan, id=loan_id)
        return self._process_notification(loan)

    def notify(
        self, patron_identifier: str | None, license_identifier: str | None
    ) -> Response:
        self.log.info(
            f"Loan notification received [patron: {patron_identifier}] [license: {license_identifier}]"
        )
        loan = self._get_loan(patron_identifier, license_identifier)
        return self._process_notification(loan)

    def _process_notification(self, loan: Loan | None) -> Response:
        library = flask.request.library  # type: ignore
        status_doc_json = flask.request.data

        try:
            status_doc = LoanStatus.model_validate_json(status_doc_json)
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

            # We might be out of sync with the distributor. Mark the loan as expired and update the license pool.
            if not status_doc.active:
                self.log.info(f"Loan status was {status_doc.status}, will delete loan")
                api = self.manager.circulation_apis[library.id].api_for_license_pool(
                    loan.license_pool
                )
                api.delete_expired_loan(loan)

        return Response(_("Success"), 200)
