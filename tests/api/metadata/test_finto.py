import pytest

from api.metadata.finto import FintoAI, FintoAILibrarySettings, FintoAISettings
from core.util.problem_detail import ProblemDetailException


class TestFintoAI:
    def test_settings_defaults(self):
        settings = FintoAISettings()

        assert settings.limit == 10
        assert settings.threshold == 0.1
        assert FintoAILibrarySettings() == FintoAILibrarySettings()

    def test_settings_validate_limits(self):
        for field, value in [("limit", 0), ("threshold", -0.1), ("threshold", 1.1)]:
            with pytest.raises(ProblemDetailException):
                FintoAISettings(**{field: value})

    def test_metadata(self):
        assert FintoAI.label() == "Finto AI"
        assert "Suggest keywords" in FintoAI.description()
        assert FintoAI.settings_class() is FintoAISettings
        assert FintoAI.library_settings_class() is FintoAILibrarySettings
        assert FintoAI.multiple_services_allowed() is False
