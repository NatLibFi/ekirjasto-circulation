import datetime
import urllib.parse
from collections.abc import Generator
from decimal import Decimal
from unittest.mock import MagicMock, patch

import flask

import feedparser
import pytest
from flask import Response as FlaskResponse
from flask import url_for
from werkzeug import Response as wkResponse

from typing import TYPE_CHECKING

from api.circulation import (
    BaseCirculationAPI,
    CirculationAPI,
    FulfillmentInfo,
    HoldInfo,
    LoanInfo,
)
from core.model import (
    DataSource,
    Edition,
    Identifier,
    LicensePool,
    SelectedBook,
    Work,
    get_one,
    get_one_or_create,
)
from core.util.datetime_helpers import datetime_utc, utc_now
from core.util.flask_util import Response
from core.util.opds_writer import OPDSFeed
from core.feed.acquisition import OPDSAcquisitionFeed

from tests.fixtures.api_controller import CirculationControllerFixture
from tests.fixtures.database import DatabaseTransactionFixture
from tests.fixtures.library import LibraryFixture
from core.feed.annotator.circulation import LibraryAnnotator
from core.feed.types import WorkEntry
from core.feed.acquisition import OPDSAcquisitionFeed


if TYPE_CHECKING:
    from api.controller.select_books import SelectBooksController


class SelectBooksFixture(CirculationControllerFixture):
    identifier: Identifier
    lp: LicensePool
    datasource: DataSource
    edition: Edition


    def __init__(self, db: DatabaseTransactionFixture):
        super().__init__(db)
        [self.lp] = self.english_1.license_pools
        self.identifier = self.lp.identifier
        self.edition = self.lp.presentation_edition
        self.datasource = self.lp.data_source.name  # type: ignore


@pytest.fixture(scope="function")
def selected_book_fixture(db: DatabaseTransactionFixture):
    fixture = SelectBooksFixture(db)
    with fixture.wired_container():
        yield fixture


class TestSelectBooksController:

    def test_select_unselect_success(self, selected_book_fixture: SelectBooksFixture):
        """
        Test that a book can be successfully selected and unselected by a patron.
        A successful selection returns a 200 status code, the work is found in
        the database and a feed is returned with a <selected> tag.
        A successful unselection returns a 200 status code, the work is no longer
        in the database and a feed is returned without a <selected> tag.
        """

        with selected_book_fixture.request_context_with_library("/", headers=dict(Authorization=selected_book_fixture.valid_auth)):
            # We have an authenticated patron
            patron = selected_book_fixture.manager.select_books.authenticated_patron_from_request()
            identifier_type = Identifier.GUTENBERG_ID
            identifier = "1234567890"
            edition, _ = selected_book_fixture.db.edition(
                title="Test Book",
                identifier_type=identifier_type,
                identifier_id=identifier,
                with_license_pool=True,
            )
            # The work has an edition
            work = selected_book_fixture.db.work(
                "Test Book", presentation_edition=edition, with_license_pool=True
            )

            # 1. The patron selects the work
            response = selected_book_fixture.manager.select_books.select(
                identifier_type, identifier
            )

            assert 200 == response.status_code
            # The book should be in the database
            selected_book = get_one(
                selected_book_fixture.db.session, SelectedBook, work_id=work.id
            )
            assert selected_book != None
            # The feed should have a <selected> tag
            feed = feedparser.parse(response.data)
            [entry] = feed.entries
            assert entry["selected"]

            # 2. Then the patron unselects the work
            response = selected_book_fixture.manager.select_books.unselect(
                identifier_type, identifier
            )
            assert 200 == response.status_code
            # The book should no longer show up in the database
            selected_book = get_one(
                selected_book_fixture.db.session, SelectedBook, work_id=work.id
            )
            assert selected_book == None
            # The feed should no longer have a <selected> tag
            feed = feedparser.parse(response.data)
            [entry] = feed.entries
            assert "selected" not in entry


    def test_detail(self, selected_book_fixture: SelectBooksFixture):
        """
        Test that a selected book's details are fetched successfully.
        """

        with selected_book_fixture.request_context_with_library("/", headers=dict(Authorization=selected_book_fixture.valid_auth)):
            # A patron first selects a work
            selected_book_fixture.manager.select_books.authenticated_patron_from_request()
            identifier_type = Identifier.GUTENBERG_ID
            identifier = "1234567890"
            edition, _ = selected_book_fixture.db.edition(
                title="Test Book",
                identifier_type=identifier_type,
                identifier_id=identifier,
                with_license_pool=True,
            )
            work = selected_book_fixture.db.work(
                "Test Book", presentation_edition=edition, with_license_pool=True
            )
            selected_book_fixture.manager.select_books.select(
                identifier_type, identifier
            )
            # And then later on the book's details are requested
            response = selected_book_fixture.manager.select_books.detail(
                identifier_type, identifier
            )

            assert response.status_code == 200
            feed = feedparser.parse(response.data)
            [entry] = feed.entries
            assert entry["selected"]

    def test_fetch_selected_books_feed(self, selected_book_fixture: SelectBooksFixture):
        """
        Test that the selected books acquisition feed is fetched successfully.
        """

        with selected_book_fixture.request_context_with_library("/", headers=dict(Authorization=selected_book_fixture.valid_auth)):
            # A patron first selects a work
            selected_book_fixture.manager.select_books.authenticated_patron_from_request()
            identifier_type = Identifier.GUTENBERG_ID
            identifier = "1234567890"
            edition, _ = selected_book_fixture.db.edition(
                title="Test Book",
                identifier_type=identifier_type,
                identifier_id=identifier,
                with_license_pool=True,
            )
            work = selected_book_fixture.db.work(
                "Test Book", presentation_edition=edition, with_license_pool=True
            )
            selected_book_fixture.manager.select_books.select(
                identifier_type, identifier
            )
            # And then later the selected books feed is requested
            response = selected_book_fixture.manager.select_books.fetch_books()
            # The feed contains the selected book
            assert response.status_code == 200
            feed = feedparser.parse(response.get_data())
            assert len(feed.entries) == 1
