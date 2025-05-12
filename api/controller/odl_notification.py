from __future__ import annotations

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

    def notify(self, license_id):
        loan = get_one(self._db, Loan, id=license_id)

        library = flask.request.library
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

            # We might be out of sync with the distributor. Mark the loan as expired and update the license pool.
            if not status_doc.active:
                try:
                    with self._db.begin_nested():
                        loan.end = utc_now()
                    api = self.manager.circulation_apis[library.id].api_for_license_pool(loan.license_pool)
                    api.update_availability(loan.license_pool)
                except StaleDataError:
                    # This can happen if this callback happened while we were returning this
                    # item. We can fetch the loan, but it's deleted by the time we go to do
                    # the update. This is not a problem, as we were just marking the loan as
                    # completed anyway so we just continue.
                    ...

        return Response(_("Success"), 200)