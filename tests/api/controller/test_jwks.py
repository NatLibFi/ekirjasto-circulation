import os
import time
from unittest.mock import patch

import pytest
from _pytest.logging import LogCaptureFixture

from api.controller.jwks import (  # Adjust the import based on your project structure
    JwksController,
)
from api.problem_details import JWKS_FILE_ERROR
from tests.fixtures.files import OPDSODLFilesFixture
from tests.fixtures.flask import FlaskAppFixture


# Once all test files are moved to tests/files, we should change this to use the newly defined FilesFixture class.
class JWKSFilesFixture(OPDSODLFilesFixture):
    """A fixture providing access to JWKS files from the "tests/files" directory."""

    def __init__(self):
        super().__init__("jwks")


@pytest.fixture()
def jwks_files_fixture() -> JWKSFilesFixture:
    """A fixture providing access to JWKS files."""
    return JWKSFilesFixture()


class TestJWKSController:
    @pytest.fixture(autouse=True)
    def setup_env(self, jwks_files_fixture: JWKSFilesFixture):
        # Set up environment variable for the JWKS file path
        self.jwks_file_path = os.path.join(
            jwks_files_fixture.directory, "r.cantook.com-jwks.json"
        )
        with patch.dict(
            os.environ, {"PALACE_DEMARQUE_WEBREADER_JWK_FILE": self.jwks_file_path}
        ):
            self.jwks_controller = JwksController()

    def test_get_jwks_return_cached_data(
        self, jwks_files_fixture: JWKSFilesFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Test if JWKS returns cached data correctly."""
        expected_content = jwks_files_fixture.sample_data(
            "r.cantook.com-jwks.json"
        )  # Load expected data from the fixture

        # Simulate cached data
        self.jwks_controller._jwks_cache["data"] = expected_content
        self.jwks_controller._jwks_cache["timestamp"] = (
            time.time() - 1000
        )  # Valid cache, still within TTL

        with flask_app_fixture.test_request_context():
            response = self.jwks_controller.get_jwks()

        # Assert the response
        assert response.data == expected_content  # type: ignore
        assert response.status_code == 200

    def test_get_jwks_loads_from_file(
        self, jwks_files_fixture: JWKSFilesFixture, flask_app_fixture: FlaskAppFixture
    ):
        """Test if JWKS loads correctly from a file."""
        mock_jwks_data = jwks_files_fixture.sample_data(
            "r.cantook.com-jwks.json"
        )  # Use sample JWKS data

        # 1. No cached data, should read from file
        assert self.jwks_controller._jwks_cache["data"] == None
        assert self.jwks_controller._jwks_cache["timestamp"] == 0

        with flask_app_fixture.test_request_context():
            response = self.jwks_controller.get_jwks()

            # Assert the correct loading of data from the file
            assert response.data == mock_jwks_data  # type: ignore
            assert response.status_code == 200
            # Assert that the cache is updated
            assert self.jwks_controller._jwks_cache["data"] == mock_jwks_data
            assert (
                self.jwks_controller._jwks_cache["timestamp"] > 0
            )  # Ensure timestamp is set

        # 2. Cached data is outdated, should read from file
        self.jwks_controller._jwks_cache["data"] = mock_jwks_data
        outdated_cache = self.jwks_controller._jwks_cache["timestamp"] = (
            time.time() - 3601
        )  # Invalid cache, just past TTL
        print(f"Cache timestamp: {outdated_cache}, Current time: {time.time()}")

        with flask_app_fixture.test_request_context():
            response = self.jwks_controller.get_jwks()

        # Assert the response
        assert response.data == mock_jwks_data  # type: ignore
        assert response.status_code == 200
        # Ensure timestamp is updated after reading from file
        assert self.jwks_controller._jwks_cache["timestamp"] != outdated_cache

    def test_get_jwks_file_not_found_returns_problem_detail(
        self, flask_app_fixture: FlaskAppFixture, caplog: LogCaptureFixture
    ):
        """Test that JWKS raises an error if some problem occurs when trying to read the JWKS file."""

        with patch("builtins.open", side_effect=RuntimeError("Simulated read error")):
            with flask_app_fixture.test_request_context():
                response = self.jwks_controller.get_jwks()

        # Assert that the response matches the expected error details
        assert response.status_code == JWKS_FILE_ERROR.status_code
        assert response.uri == JWKS_FILE_ERROR.uri  # type: ignore
        assert response.title == JWKS_FILE_ERROR.title  # type: ignore

        assert "Error reading JWKS file: Simulated read error" in caplog.text
