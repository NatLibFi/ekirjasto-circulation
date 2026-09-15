import pytest
from werkzeug.datastructures import ImmutableMultiDict

from api.admin.form_data import ProcessFormData
from api.metadata.finto import FintoAI, FintoAILibrarySettings, FintoAISettings
from core.util.problem_detail import ProblemDetailException


class TestFintoAI:
    def test_settings_defaults(self):
        settings = FintoAISettings()

        assert settings.limit == 10
        assert settings.threshold == 0.1
        assert settings.work_languages == ["fin", "swe", "eng"]
        assert settings.keyword_language == "fin"
        assert FintoAILibrarySettings() == FintoAILibrarySettings()

    def test_settings_validate_limits(self):
        for field, value in [("limit", 0), ("threshold", -0.1), ("threshold", 1.1)]:
            with pytest.raises(ProblemDetailException):
                FintoAISettings(**{field: value})

    def test_settings_validate_keyword_extraction_options(self):
        """Reject unsupported work and keyword languages before saving settings."""
        with pytest.raises(ProblemDetailException):
            FintoAISettings(work_languages=["nor"])
        with pytest.raises(ProblemDetailException):
            FintoAISettings(keyword_language="swe")

    def test_admin_form_sets_keyword_extraction_options(self):
        """Ensure menu form fields are converted from submitted keys to settings."""
        form_data = ImmutableMultiDict(
            [
                ("work_languages_fin", "fin"),
                ("work_languages_eng", "eng"),
                ("keyword_language", "book"),
            ]
        )

        settings = ProcessFormData.get_settings(FintoAISettings, form_data)

        assert settings.work_languages == ["fin", "eng"]
        assert settings.keyword_language == "book"

    def test_metadata(self):
        assert FintoAI.label() == "Finto AI"
        assert "Suggest keywords" in FintoAI.description()
        assert FintoAI.settings_class() is FintoAISettings
        assert FintoAI.library_settings_class() is FintoAILibrarySettings
        assert FintoAI.multiple_services_allowed() is False
