from collections.abc import Generator

import pytest

from api.admin.exceptions import AdminNotAuthorized
from api.admin.model.work_inventory import WorkInventoryRequest
from core.model.admin import AdminRole
from core.model.licensing import LicensePoolStatus
from tests.fixtures.api_admin import AdminControllerFixture
from tests.fixtures.api_controller import ControllerFixture


class WorkInventoryFixture(AdminControllerFixture):
    def __init__(self, controller_fixture: ControllerFixture):
        super().__init__(controller_fixture)
        self.library = self.ctrl.db.default_library()
        self.collection = self.ctrl.db.default_collection()
        self.admin.add_role(AdminRole.LIBRARIAN, self.library)

    def add_work(self, title: str, status: LicensePoolStatus):
        work = self.ctrl.db.work(
            title,
            "Inventory Author",
            language="eng",
            with_open_access_download=True,
        )
        [pool] = work.license_pools
        pool.collection = self.collection
        pool.status = status
        return work


@pytest.fixture(scope="function")
def inventory_fixture(
    controller_fixture: ControllerFixture,
) -> Generator[WorkInventoryFixture, None, None]:
    fixture = WorkInventoryFixture(controller_fixture)
    with fixture.ctrl.wired_container():
        yield fixture


class TestWorkInventoryRequest:
    def test_defaults_and_lenient_values(self):
        request = WorkInventoryRequest.from_query(
            {
                "offset": "-8",
                "include_total": "FALSE",
                "direction": "DESC",
                "status": "active,exhausted",
                "title": "  title  ",
                "licenses_owned": "invalid",
                "duration_min": "bad",
                "data_source_modified_min": "not-a-date",
                "fiction": "unknown",
            }
        )

        assert request.offset == 0
        assert request.sort == "title"
        assert request.descending is True
        assert request.include_total is False
        assert request.statuses == ("active", "exhausted")
        assert request.text_filters == {"title": "title"}
        assert request.count_filters == {}
        assert request.number_filters == {}
        assert request.date_filters == {}
        assert request.fiction is None

    def test_parses_filter_groups(self):
        request = WorkInventoryRequest.from_query(
            {
                "offset": "12",
                "sort": "author",
                "language": "eng",
                "format": "Book",
                "audience": "Adult",
                "licenses_owned_min": "2",
                "number_of_pages_max": "320",
                "title_created_date_min": "2025-01-02",
                "genres": "Fantasy",
                "fiction": "false",
                "target_age": "8",
            }
        )

        assert request.offset == 12
        assert request.sort == "author"
        assert request.exact_filters == {
            "language": "eng",
            "format": "Book",
            "audience": "Adult",
        }
        assert request.count_filters == {"licenses_owned_min": 2}
        assert request.number_filters == {"number_of_pages_max": 320.0}
        assert (
            request.date_filters["title_created_date_min"].isoformat() == "2025-01-02"
        )
        assert request.genre == "Fantasy"
        assert request.fiction is False
        assert request.target_age == 8


class TestWorkInventoryController:
    @pytest.mark.parametrize("legacy_sort", ["genres", "keywords"])
    def test_removed_sorts_fall_back_to_title(
        self, inventory_fixture: WorkInventoryFixture, legacy_sort: str
    ):
        inventory_fixture.add_work("Zebra title", LicensePoolStatus.active)
        inventory_fixture.add_work("Apple title", LicensePoolStatus.active)

        with inventory_fixture.request_context_with_library_and_admin(
            f"/?sort={legacy_sort}&direction=asc"
        ):
            page = inventory_fixture.manager.admin_dashboard_controller.all_works()

        assert [row["title"] for row in page["works"]] == [
            "Apple title",
            "Zebra title",
        ]

    def test_genre_and_keyword_filters_remain_available(
        self, inventory_fixture: WorkInventoryFixture
    ):
        matching = inventory_fixture.ctrl.db.work(
            "Fantasy title",
            "Inventory Author",
            genre="Fantasy",
            language="eng",
            with_open_access_download=True,
        )
        matching.license_pools[0].collection = inventory_fixture.collection
        matching.presentation_edition.keywords = ["wizard"]
        inventory_fixture.add_work("Other title", LicensePoolStatus.active)

        with inventory_fixture.request_context_with_library_and_admin(
            "/?genres=Fantasy&keywords=wizard"
        ):
            page = inventory_fixture.manager.admin_dashboard_controller.all_works()

        assert [row["id"] for row in page["works"]] == [matching.id]

    def test_library_scope_filter_sort_and_optional_total(
        self, inventory_fixture: WorkInventoryFixture
    ):
        active = inventory_fixture.add_work("Zebra title", LicensePoolStatus.active)
        active.license_pools[0].licenses_owned = 3
        active.license_pools[0].patrons_in_hold_queue = 2
        exhausted = inventory_fixture.add_work(
            "Apple title", LicensePoolStatus.exhausted
        )
        other_library = inventory_fixture.ctrl.db.library("Other library", "other")
        other_collection = inventory_fixture.ctrl.db.collection(name="Other collection")
        other_collection.libraries.append(other_library)
        out_of_scope = inventory_fixture.add_work(
            "Outside title", LicensePoolStatus.active
        )
        out_of_scope.license_pools[0].collection = other_collection

        with inventory_fixture.request_context_with_library_and_admin(
            "/?status=active&sort=title&direction=asc&include_total=0"
        ):
            page = inventory_fixture.manager.admin_dashboard_controller.all_works()

        assert page["total"] is None
        assert page["has_more"] is False
        assert page["limit"] == 50
        assert [row["title"] for row in page["works"]] == ["Zebra title"]
        assert page["works"][0]["id"] == active.id
        assert page["works"][0]["license_pool_status"] == "active"
        assert page["works"][0]["licenses_owned"] == 3
        assert page["works"][0]["patrons_in_hold_queue"] == 2
        assert page["language_options"]["eng"]
        assert {row["id"] for row in page["works"]}.isdisjoint(
            {exhausted.id, out_of_scope.id}
        )

    def test_count_filters_sorting_and_total(self, inventory_fixture):
        inventory_fixture.add_work("Zebra title", LicensePoolStatus.active)
        matching = inventory_fixture.add_work("Apple title", LicensePoolStatus.active)
        matching.license_pools[0].licenses_owned = 4

        with inventory_fixture.request_context_with_library_and_admin(
            "/?licenses_owned_min=4&sort=title&direction=desc"
        ):
            page = inventory_fixture.manager.admin_dashboard_controller.all_works()

        assert page["total"] == 1
        assert [row["id"] for row in page["works"]] == [matching.id]

    def test_pagination_uses_offset_and_fixed_page_size(self, inventory_fixture):
        for i in range(3):
            inventory_fixture.add_work(f"Title {i:02d}", LicensePoolStatus.active)

        with inventory_fixture.request_context_with_library_and_admin(
            "/?sort=title&offset=1"
        ):
            page = inventory_fixture.manager.admin_dashboard_controller.all_works()

        assert page["total"] == 3
        assert page["offset"] == 1
        assert page["limit"] == 50
        assert page["has_more"] is False
        assert [row["title"] for row in page["works"]] == ["Title 01", "Title 02"]

    def test_requires_librarian_access(self, inventory_fixture: WorkInventoryFixture):
        inventory_fixture.admin.remove_role(
            AdminRole.LIBRARIAN, inventory_fixture.library
        )
        with inventory_fixture.request_context_with_library_and_admin("/"):
            with pytest.raises(AdminNotAuthorized):
                inventory_fixture.manager.admin_dashboard_controller.all_works()
