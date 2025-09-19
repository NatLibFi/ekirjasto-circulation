import pytest

from core.data_conversion.accessibility_mapper import AccessibilityDataMapper, W3CConformanceLevel, W3CIds


class TestAccessibilityDataMapper:

    @pytest.fixture
    def data_mapper(self):
        return AccessibilityDataMapper()

    @pytest.mark.parametrize(
        "conformance, expected_output",
        [
            (["epub_1_0_wcag_2_0_level_a"], [W3CConformanceLevel.a.value]),
            (["epub_1_1_wcag_2_2_level_aa"], [W3CConformanceLevel.aa.value]),
            (["epub_1_1_wcag_2_2_level_aaa"], [W3CConformanceLevel.aaa.value]),
        ]
    )
    def test_map_conforms_to(self, data_mapper, conformance, expected_output):
        assert data_mapper.map_conforms_to(conformance) == expected_output


    @pytest.mark.parametrize(
        "hazards, expected_output",
        [
            (["flashing"], ["Flashing content"]),
            (["no_flashing_hazard", "no_sound_hazard", "no_motion_simulation_hazard"], ["No hazards"]),
            (["unknown"], ["The presence of hazards is unknown"]),
        ]
    )
    def test_map_hazards(self, data_mapper, hazards, expected_output):
        assert data_mapper.map_hazards(hazards) == expected_output


    @pytest.mark.parametrize(
        "access_mode_list, access_mode_sufficient_list, feature_list, expected_output",
        [
            (["textual"], ["textual"], ["long_description"],
             ["Has alternative text", "Not fully readable in read aloud or dynamic braille", "Readable in read aloud or dynamic braille"]),
             
            (["auditory"], None, None,
             ["Not readable in read aloud or dynamic braille"]),
             
            (["visual"], None, None,
             ["Not readable in read aloud or dynamic braille"]),
             
            (None, ["textual"], ["long_description"],
             ["Has alternative text", "Not fully readable in read aloud or dynamic braille"]),
        ]
    )
    def test_map_ways_of_reading(self, data_mapper, access_mode_list, access_mode_sufficient_list, feature_list, expected_output):
        assert data_mapper.map_ways_of_reading(access_mode_list, access_mode_sufficient_list, feature_list) == expected_output


    @pytest.mark.parametrize(
        "access_mode_list, access_mode_sufficient_list, feature_list, expected_output",
        [
            (["textual"], None, None, ["all_necessary_content_textual"]),
            (["auditory"], None, None, ["audio_only_content"]),
            (["visual"], None, None, ["visual_only_content"]),
            (None, ["textual"], None, ["some_sufficient_text"]),
            (None, None, ["long_description", "alternative_text", "described_math", "transcript"], ["textual_alternatives"]),
            (None, None, ["long_description", "alternative_text"], ["textual_alternatives"]),
        ]
    )
    def test__get_nonvisual_reading_support_w3c_variables(self, data_mapper, access_mode_list, access_mode_sufficient_list, feature_list, expected_output):
        assert data_mapper._get_nonvisual_reading_support_w3c_variables(
            access_mode_list, access_mode_sufficient_list, feature_list
        ) == expected_output