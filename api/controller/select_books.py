from __future__ import annotations

import flask

from api.controller.circulation_manager import CirculationManagerController
from core.feed.acquisition import OPDSAcquisitionFeed
from core.util.problem_detail import ProblemDetail


class SelectBooksController(CirculationManagerController):
    def fetch_books(self):
        """
        Generate an OPDS feed response containing the selected books for the
        authenticated patron.

        This method creates an OPDS acquisition feed with the books currently
        selected by the patron and returns it as a response.

        :return: An OPDSEntryResponse.
        """
        patron = flask.request.patron

        feed = OPDSAcquisitionFeed.selected_books_for(self.circulation, patron)

        response = feed.as_response(
            max_age=0,
            private=True,
            mime_types=flask.request.accept_mimetypes,
        )

        # For loans, the patron's last loan activity sync time was set. Not yet
        # clear if such is needed for selected books.
        # response.last_modified = last_modified

        return response

    def unselect(self, identifier_type, identifier):
        """
        Unselect a book from the authenticated patron's selected books list.

        This method returns an OPDS entry with loan or hold-specific information.

        :param identifier_type: The type of identifier for the book
        :param identifier: The identifier for the book

        :return: An OPDSEntryResponse
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

        :return: An OPDSEntryResponse.
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
        Retrieve the active loan or hold for a patron from a set of license
        pools.

        This method checks if the patron has an active loan or hold for any of
        the given license pools. If an active loan is found, it is returned
        alongside the corresponding license pool. If no loan is found, it
        checks for an active hold. If neither a loan nor a hold is found, it
        returns the first license pool from the list.

        :param patron: The patron for whom to find an active loan or hold.
        :param pools: A list of LicensePool objects associated with the
        identifier.

        :return: An active Loan or Hold object, or a LicensePool if no loan
        or hold is found.
        """
        # TODO: move this function to circulation_manager.py becuase it's
        # used in multiple controllers
        loan, pool = self.get_patron_loan(patron, pools)
        hold = None

        if not loan:
            hold, pool = self.get_patron_hold(patron, pools)

        item = loan or hold
        pool = pool or pools[0]
        return item or pool

    def detail(self, identifier_type, identifier):

        """
        Return an OPDS feed entry for a selected book.

        If the request method is DELETE, this method unselects the book.

        Whether the request is GET or DELETE, it returns an OPDS entry with
        loan or hold-specific information and the selected book information.

        :param identifier_type: The type of the book identifier (e.g., ISBN).
        :param identifier: The identifier for the book.

        :return: An OPDSEntryResponse.
        """
        patron = flask.request.patron
        library = flask.request.library

        if flask.request.method == "DELETE":
            return self.unselect(identifier_type, identifier)

        if flask.request.method == "GET":

            pools = self.load_licensepools(library, identifier_type, identifier)
            if isinstance(pools, ProblemDetail):
                return pools

            item = self._get_patron_loan_or_hold(patron, pools)

            work = self.load_work(library, identifier_type, identifier)
            selected_book = patron.load_selected_book(work)

            return OPDSAcquisitionFeed.single_entry_loans_feed(self.circulation, item, selected_book=selected_book)
