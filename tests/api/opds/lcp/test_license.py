import pytest

from api.lcp.license import LicenseDocument
from tests.fixtures.files import OPDS2FilesFixture


class TestLicenseDocument:
    @pytest.mark.parametrize(
        "filename",
        [
            "fb.json",
        ],
    )
    def test_license_document(
        self,
        filename: str,
        opds2_files_fixture: OPDS2FilesFixture,
    ) -> None:
        LicenseDocument.model_validate_json(
            opds2_files_fixture.sample_data("lcp/license/" + filename)
        )
