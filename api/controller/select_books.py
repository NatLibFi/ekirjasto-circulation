from __future__ import annotations

from typing import Any

import flask
from flask import Response, redirect
from flask_babel import lazy_gettext as _
from lxml import etree
from werkzeug import Response as wkResponse

from api.circulation_exceptions import (
    AuthorizationBlocked,
    AuthorizationExpired,
    CirculationException,
    PatronAuthorizationFailedException,
)
from api.controller.circulation_manager import CirculationManagerController
from core.feed.acquisition import OPDSAcquisitionFeed
from core.model.patron import SelectedBook
from core.util.http import RemoteIntegrationException
from core.util.opds_writer import OPDSFeed
from core.util.problem_detail import ProblemDetail

class SelectBooksController(CirculationManagerController):

    def fetch_books(self, work_identifier):
        patron = flask.request.patron
        selected_booklist = patron.get_selected_books()

        for book in selected_booklist:
            if book.identifier == work_identifier:
                return book

        return None
    
    def unselect(self, identifier_type, identifier):
        """
        Unselect a book from the authenticated patron's selected books list.

        This method returns an OPDS entry with loan or hold-specific information.

        :param identifier_type: The type of identifier for the book
        :param identifier: The identifier for the book

        :return: a Response object
        """
        library = flask.request.library
        work = self.load_work(library, identifier_type, identifier)
        patron = flask.request.patron
        pools = self.load_licensepools(library, identifier_type, identifier)

        unselected_book = patron.unselect_book(work)
        item = self._get_patron_loan_or_hold(patron, pools)

        return OPDSAcquisitionFeed.single_entry_loans_feed(
            self.circulation, item, selected_book=unselected_book
        )

    def select(self, identifier_type, identifier):
        """
        Add a book to the authenticated patron's selected books list.

        This method returns an OPDS entry with the selected book and
        loan or hold-specific information.

        :param identifier_type: The type of the book identifier (e.g., ISBN).
        :param identifier: The identifier for the book.

        :return: An OPDSEntryResponse containing the selected book information.
        """
        library = flask.request.library
        work = self.load_work(library, identifier_type, identifier)
        patron = flask.request.patron
        pools = self.load_licensepools(library, identifier_type, identifier)

        if isinstance(pools, ProblemDetail):
            return pools

        selected_book = patron.select_book(work)


        item = self._get_patron_loan_or_hold(patron, pools)

        return OPDSAcquisitionFeed.single_entry_loans_feed(
            self.circulation, item, selected_book=selected_book
        )
    
    def _get_patron_loan_or_hold(self, patron, pools):
        """
        Retrieve the active loan or hold for a patron from a set of license pools.

        This method checks if the patron has an active loan or hold for any of the
        given license pools. If an active loan is found, it is returned alongside
        the corresponding license pool. If no loan is found, it checks for an active
        hold. If neither a loan nor a hold is found, it returns the first license
        pool from the list.

        :param patron: The patron for whom to find an active loan or hold.
        :param pools: A list of LicensePool objects associated with the identifier.
        :return: An active Loan or Hold object, or a LicensePool if no loan or hold is found.
        """
        loan, pool = self.get_patron_loan(patron, pools)
        hold = None

        if not loan:
            hold, pool = self.get_patron_hold(patron, pools)

        item = loan or hold
        pool = pool or pools[0]
        return item or pool