import datetime
from unittest.mock import call, patch

import pytest
from freezegun import freeze_time
from requests.exceptions import Timeout

from api.odl import ODLAPI
from api.odl2 import ODL2API
from core.jobs.notifications.holds_notification import HoldsNotificationMonitor
from core.model.devicetokens import DeviceToken, DeviceTokenTypes
from core.util.datetime_helpers import utc_now
from tests.fixtures.odl import OPDS2WithODLApiFixture


class TestVerifiedHoldNotifications:
    def ready_hold(self, fixture: OPDS2WithODLApiFixture, **kwargs):
        patron = fixture.db.patron()
        DeviceToken.create(
            fixture.db.session,
            DeviceTokenTypes.FCM_ANDROID,
            f"token-{patron.id}",
            patron,
        )
        args = dict(
            position=0,
            start=utc_now() - datetime.timedelta(days=1),
            end=utc_now() + datetime.timedelta(days=1),
        )
        args.update(kwargs)
        hold, _ = fixture.pool.on_hold_to(patron, **args)
        return hold

    def queue_license(
        self, fixture: OPDS2WithODLApiFixture, available: int, license_obj=None
    ):
        license_obj = license_obj or fixture.license
        fixture.mock_http.queue_response(
            200,
            "application/vnd.odl.info+json",
            content=dict(
                identifier=license_obj.identifier,
                status="available",
                checkouts=dict(available=available),
                terms=dict(concurrency=license_obj.terms_concurrency),
            ),
        )

    @pytest.mark.parametrize("api_class", [ODLAPI, ODL2API])
    def test_confirmed_availability_sends_once_per_pool(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture, api_class
    ):
        fixture = opds2_with_odl_api_fixture
        fixture.collection.integration_configuration.protocol = api_class.label()
        fixture.setup_license(available=2, concurrency=2)
        hold1 = self.ready_hold(fixture)
        hold2 = self.ready_hold(fixture)
        self.queue_license(fixture, available=2)
        monitor = HoldsNotificationMonitor(fixture.db.session, batch_size=1)

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            monitor.run()

        assert len(fixture.mock_http.requests) == 1
        assert fixture.mock_http.requests == [fixture.license.status_url]
        assert fixture.mock_http.requests_methods == ["GET"]
        assert notifications.send_holds_notifications.call_args_list == [
            call([hold1]),
            call([hold2]),
        ]

    def test_unavailable_copy_preserves_pickup_reservation_without_notification(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ):
        fixture = opds2_with_odl_api_fixture
        hold = self.ready_hold(fixture)
        deadline = hold.end
        self.queue_license(fixture, available=0)

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            HoldsNotificationMonitor(fixture.db.session).run()

        notifications.send_holds_notifications.assert_not_called()
        assert fixture.license.checkouts_available == 0
        assert fixture.pool.licenses_reserved == 0
        assert hold.position == 0
        assert hold.end == deadline
        assert hold.patron_last_notified is None

    def test_only_patron_with_available_copy_is_notified(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ):
        fixture = opds2_with_odl_api_fixture
        fixture.setup_license(available=2, concurrency=2)
        first = self.ready_hold(fixture)
        second = self.ready_hold(fixture, start=utc_now())
        self.queue_license(fixture, available=1)

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            HoldsNotificationMonitor(fixture.db.session).run()

        notifications.send_holds_notifications.assert_called_once_with([first])
        assert len(fixture.mock_http.requests) == 1
        assert first.position == 0
        assert second.position == 0
        assert second.patron_last_notified is None

    @pytest.mark.parametrize(
        "failure", ["timeout", "http_error", "invalid_document", "wrong_identifier"]
    )
    def test_failed_check_preserves_availability_and_retries_on_next_run(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture, failure: str
    ):
        fixture = opds2_with_odl_api_fixture
        hold = self.ready_hold(fixture)
        fixture.api.update_licensepool_and_hold_queue(fixture.pool)
        monitor = HoldsNotificationMonitor(fixture.db.session)
        if failure == "http_error":
            fixture.mock_http.queue_response(503)
        elif failure == "wrong_identifier":
            fixture.mock_http.queue_response(
                200,
                content=dict(
                    identifier="another-license",
                    status="available",
                    checkouts=dict(available=0),
                    terms=dict(concurrency=1),
                ),
            )
        else:
            fixture.mock_http.queue_response(200, content="invalid license document")

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            if failure == "timeout":
                with patch.object(ODLAPI, "_get", side_effect=Timeout):
                    monitor.run()
                fixture.mock_http.reset_mock()
            else:
                monitor.run()

            notifications.send_holds_notifications.assert_not_called()
            assert fixture.license.checkouts_available == 1
            assert fixture.pool.licenses_reserved == 1
            assert hold.position == 0
            assert hold.patron_last_notified is None

            self.queue_license(fixture, available=1)
            monitor.run()
            notifications.send_holds_notifications.assert_called_once_with([hold])

    def test_expired_hold_is_not_notified(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ):
        fixture = opds2_with_odl_api_fixture
        hold = self.ready_hold(fixture, end=utc_now() - datetime.timedelta(seconds=1))

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            HoldsNotificationMonitor(fixture.db.session).run()

        notifications.send_holds_notifications.assert_not_called()
        assert fixture.mock_http.requests == []
        assert hold.patron_last_notified is None

    def test_partial_refresh_saves_success_without_demoting_ready_hold(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ):
        fixture = opds2_with_odl_api_fixture
        hold = self.ready_hold(fixture)
        second_license = fixture.db.license(
            fixture.pool, checkouts_available=1, terms_concurrency=1
        )
        fixture.api.update_licensepool_and_hold_queue(fixture.pool)
        previous_totals = (
            fixture.pool.licenses_reserved,
            fixture.pool.licenses_available,
        )
        self.queue_license(fixture, available=0)
        fixture.mock_http.queue_response(503)

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            HoldsNotificationMonitor(fixture.db.session).run()

        notifications.send_holds_notifications.assert_not_called()
        assert fixture.license.checkouts_available == 0
        assert second_license.checkouts_available == 1
        assert (
            fixture.pool.licenses_reserved,
            fixture.pool.licenses_available,
        ) == previous_totals
        assert hold.position == 0
        assert hold.patron_last_notified is None

    @pytest.mark.parametrize("failed_index", [0, 2, 4])
    @pytest.mark.parametrize("api_class", [ODLAPI, ODL2API])
    def test_two_verified_copies_notify_first_two_despite_failed_license(
        self,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
        failed_index: int,
        api_class,
    ):
        fixture = opds2_with_odl_api_fixture
        fixture.collection.protocol = api_class.label()
        for _ in range(4):
            fixture.db.license(fixture.pool, checkouts_available=1, terms_concurrency=1)
        first = self.ready_hold(fixture)
        second = self.ready_hold(
            fixture, start=utc_now() - datetime.timedelta(hours=12)
        )
        third = self.ready_hold(fixture, start=utc_now())
        fixture.pool.update_availability_from_licenses()
        previous_totals = (
            fixture.pool.licenses_reserved,
            fixture.pool.licenses_available,
        )

        verified = 0
        for index, license_obj in enumerate(fixture.pool.licenses):
            if index == failed_index:
                fixture.mock_http.queue_response(503)
            else:
                self.queue_license(
                    fixture, available=int(verified < 2), license_obj=license_obj
                )
                verified += 1

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            HoldsNotificationMonitor(fixture.db.session, batch_size=1).run()

        assert len(fixture.mock_http.requests) == 5
        assert notifications.send_holds_notifications.call_args_list == [
            call([first]),
            call([second]),
        ]
        assert third.position == 0
        assert third.patron_last_notified is None
        assert (
            fixture.pool.licenses_reserved,
            fixture.pool.licenses_available,
        ) == previous_totals

    @pytest.mark.parametrize("first_patron", ["already_notified", "no_push_token"])
    def test_partial_capacity_is_not_reassigned_to_next_patron(
        self,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
        first_patron: str,
    ):
        fixture = opds2_with_odl_api_fixture
        if first_patron == "already_notified":
            first = self.ready_hold(fixture)
            first.patron_last_notified = utc_now().date()
        else:
            first, _ = fixture.pool.on_hold_to(
                fixture.db.patron(),
                position=0,
                start=utc_now() - datetime.timedelta(days=1),
                end=utc_now() + datetime.timedelta(days=1),
            )
        second = self.ready_hold(fixture, start=utc_now())
        fixture.db.license(fixture.pool, checkouts_available=1, terms_concurrency=1)
        fixture.pool.update_availability_from_licenses()
        self.queue_license(fixture, available=1)
        fixture.mock_http.queue_response(503)

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            HoldsNotificationMonitor(fixture.db.session, batch_size=1).run()

        notifications.send_holds_notifications.assert_not_called()
        assert len(fixture.mock_http.requests) == 2
        assert first.position == second.position == 0
        assert second.patron_last_notified is None

    def test_all_license_checks_failing_preserves_pool_and_holds(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ):
        fixture = opds2_with_odl_api_fixture
        hold = self.ready_hold(fixture)
        fixture.db.license(fixture.pool, checkouts_available=1, terms_concurrency=1)
        fixture.pool.update_availability_from_licenses()
        previous_totals = (
            fixture.pool.licenses_reserved,
            fixture.pool.licenses_available,
        )
        fixture.mock_http.queue_response(503)
        fixture.mock_http.queue_response(503)

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            HoldsNotificationMonitor(fixture.db.session).run()

        notifications.send_holds_notifications.assert_not_called()
        assert len(fixture.mock_http.requests) == 2
        assert all(
            license.checkouts_available == 1 for license in fixture.pool.licenses
        )
        assert (
            fixture.pool.licenses_reserved,
            fixture.pool.licenses_available,
        ) == previous_totals
        assert hold.position == 0
        assert hold.patron_last_notified is None

    @pytest.mark.parametrize("missing", ["status_url", "license"])
    def test_missing_license_information_skips_notification(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture, missing: str
    ):
        fixture = opds2_with_odl_api_fixture
        hold = self.ready_hold(fixture)
        if missing == "status_url":
            fixture.license.status_url = None
        else:
            fixture.license.is_missing = True

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            HoldsNotificationMonitor(fixture.db.session).run()

        notifications.send_holds_notifications.assert_not_called()
        assert fixture.mock_http.requests == []
        assert fixture.license.checkouts_available == 1
        assert hold.patron_last_notified is None

    def test_stops_before_checking_other_licenses_when_capacity_is_sufficient(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ):
        fixture = opds2_with_odl_api_fixture
        hold = self.ready_hold(fixture)
        for _ in range(4):
            fixture.db.license(fixture.pool, checkouts_available=0, terms_concurrency=1)
        self.queue_license(fixture, available=1)
        deadline = hold.end
        previous_totals = (
            fixture.pool.licenses_reserved,
            fixture.pool.licenses_available,
        )

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            HoldsNotificationMonitor(fixture.db.session).run()

        notifications.send_holds_notifications.assert_called_once_with([hold])
        assert fixture.mock_http.requests == [fixture.license.status_url]
        assert hold.end == deadline
        assert (
            fixture.pool.licenses_reserved,
            fixture.pool.licenses_available,
        ) == previous_totals

    def test_checks_locally_free_license_first_then_discovers_new_capacity(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ):
        fixture = opds2_with_odl_api_fixture
        fixture.license.checkouts_available = 0
        free_license = fixture.db.license(
            fixture.pool, checkouts_available=1, terms_concurrency=1
        )
        first = self.ready_hold(fixture)
        first.patron_last_notified = utc_now().date()
        second = self.ready_hold(fixture, start=utc_now())
        self.queue_license(fixture, available=1, license_obj=free_license)
        self.queue_license(fixture, available=1)

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            HoldsNotificationMonitor(fixture.db.session).run()

        assert fixture.mock_http.requests == [
            free_license.status_url,
            fixture.license.status_url,
        ]
        notifications.send_holds_notifications.assert_called_once_with([second])
        assert first.position == second.position == 0
        assert fixture.license.checkouts_available == 1

    def test_pickup_reservation_takes_priority_over_older_waiting_hold(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ):
        fixture = opds2_with_odl_api_fixture
        protected = self.ready_hold(fixture)
        waiting = self.ready_hold(
            fixture,
            position=1,
            start=utc_now() - datetime.timedelta(days=2),
            end=None,
        )
        deadline = protected.end
        fixture.api.update_licensepool_and_hold_queue(fixture.pool)
        self.queue_license(fixture, available=1)

        with fixture.mock_http.patch(), patch(
            "core.jobs.notifications.holds_notification.PushNotifications"
        ) as notifications:
            HoldsNotificationMonitor(fixture.db.session).run()

        notifications.send_holds_notifications.assert_called_once_with([protected])
        assert protected.position == 0
        assert protected.end == deadline
        assert waiting.position == 1

    def test_admin_pickup_period_survives_full_availability_refresh(
        self,
        opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
    ):
        fixture = opds2_with_odl_api_fixture
        pickup_days = 5
        fixture.collection.default_reservation_period = pickup_days
        with freeze_time("2030-01-01 12:00:00") as clock:
            first = self.ready_hold(fixture, position=1, end=None)
            second = self.ready_hold(fixture, position=2, start=utc_now(), end=None)
            fixture.api.update_licensepool_and_hold_queue(fixture.pool)
            deadline = (utc_now() + datetime.timedelta(days=pickup_days)).replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
            assert first.position == 0
            assert first.end == deadline
            assert second.position == 1

            # A full availability refresh that finds no available copy cannot
            # revoke or extend the existing pickup window.
            fixture.license.checkouts_available = 0
            clock.move_to(deadline - datetime.timedelta(seconds=1))
            fixture.api.update_licensepool_and_hold_queue(fixture.pool)
            fixture.pool.update_availability_from_licenses(as_of=utc_now())
            fixture.api.recalculate_holds_in_license_pool(fixture.pool)
            assert first.position == 0
            assert first.end == deadline
            assert second.position == 1

            # Restored capacity still belongs to the existing reservation.
            fixture.license.checkouts_available = 1
            fixture.api.update_licensepool_and_hold_queue(fixture.pool)
            assert first.position == 0
            assert first.end == deadline
            assert second.position == 1

            # Only after the deadline may the next patron receive this copy.
            clock.move_to(deadline + datetime.timedelta(microseconds=1))
            fixture.api.update_licensepool_and_hold_queue(fixture.pool)
            assert first.end == deadline
            assert second.position == 0
            assert second.end == (
                utc_now() + datetime.timedelta(days=pickup_days)
            ).replace(hour=23, minute=59, second=59, microsecond=999999)

    def test_cancelled_pickup_does_not_block_the_next_patron(
        self, opds2_with_odl_api_fixture: OPDS2WithODLApiFixture
    ):
        fixture = opds2_with_odl_api_fixture
        first = self.ready_hold(fixture)
        second = self.ready_hold(fixture, position=1, start=utc_now(), end=None)

        # The circulation layer deletes first after this availability update.
        fixture.api.update_licensepool_and_hold_queue(
            fixture.pool, ignored_holds={first}
        )

        assert second.position == 0
        assert second.end > utc_now()
        assert fixture.pool.licenses_reserved == 1
