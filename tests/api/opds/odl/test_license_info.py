import pytest

from api.opds.odl.license_info import LicenseInfo
from tests.fixtures.files import OPDS2WithODLFilesFixture


class TestLicenseInfo:
    @pytest.mark.parametrize(
        "filename",
        [
            "feedbooks-checked-out.json",
            "feedbooks-loan-limited.json",
            "feedbooks-not-checked-out.json",
            "feedbooks-unavailable.json",
        ],
    )
    def test_license_info_feedbooks(
        self, filename: str, opds2_with_odl_files_fixture: OPDS2WithODLFilesFixture
    ) -> None:
        info = LicenseInfo.model_validate_json(
            opds2_with_odl_files_fixture.sample_data("license_info/" + filename)
        )
        assert info.identifier == "urn:uuid:123"
        assert len(info.protection.formats) == 1

    @pytest.mark.parametrize(
        "filename",
        [
            "ellibs-checked-out.json",
        ],
    )
    def test_license_info_ellibs(
        self, filename: str, opds2_with_odl_files_fixture: OPDS2WithODLFilesFixture
    ) -> None:
        info = LicenseInfo.model_validate_json(
            opds2_with_odl_files_fixture.sample_data("license_info/" + filename)
        )
        assert info.identifier == "urn:license:123"
        assert len(info.protection.formats) == 0  # Ellibs does not provide this data
