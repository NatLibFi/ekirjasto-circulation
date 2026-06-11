import pytest

from api.opds.accessibility import (
    AccessibilityFeature,
    AccessMode,
    AccessModeSufficient,
    Hazard,
)
from core.data_conversion.accessibility_mapper import (
    AccessibilityDataMapper,
    W3CDisplayTexts,
)
from core.metadata_layer import AccessibilityData


class TestAccessibilityDataMapper:
    @pytest.fixture
    def data_mapper(self):
        return AccessibilityDataMapper()

    def test_map_accessibility_with_data(self, data_mapper):
        mock_data = AccessibilityData(
            conforms_to=["https://www.w3.org/TR/epub-a11y-11#wcag-2.2-aa"],
            access_mode=[AccessMode.textual],
            access_mode_sufficient=[AccessModeSufficient.textual],
            features=[AccessibilityFeature.alternative_text],
        )
        data_mapper.accessibility = mock_data

        result = data_mapper.map_accessibility(mock_data)

        assert result == {
            "conforms_to": data_mapper._map_conforms_to(mock_data.conforms_to),
            "ways_of_reading": data_mapper._map_ways_of_reading(
                mock_data.access_mode,
                mock_data.access_mode_sufficient,
                mock_data.features,
            ),
        }

    def test_map_accessibility_with_no_data(self, data_mapper):
        mock_data = None
        result = data_mapper.map_accessibility(mock_data)
        assert not result

    @pytest.mark.parametrize(
        "conformance, expected_output",
        [
            pytest.param(
                ["EPUB Accessibility 1.0 - WCAG 2.0 Level A"],
                [W3CDisplayTexts.a],
                id="wcag-a-text",
            ),
            pytest.param(
                ["EPUB Accessibility 1.1 - WCAG 2.1 Level AAA"],
                [W3CDisplayTexts.aaa],
                id="wcag-aaa-text",
            ),
            pytest.param(
                ["http://www.idpf.org/epub/a11y/accessibility-20170105.html#wcag-a"],
                [W3CDisplayTexts.a],
                id="wcag-a-link",
            ),
            pytest.param(
                ["https://www.w3.org/TR/epub-a11y-11#wcag-2.0-aa"],
                [W3CDisplayTexts.aa],
                id="wcag-aa-link",
            ),
            pytest.param(
                ["https://www.w3.org/TR/epub-a11y-11#wcag-2.2-aaa"],
                [W3CDisplayTexts.aaa],
                id="wcag-aaa-link",
            ),
            pytest.param(
                ["EPUB Accessibility 1.0 - WCAG 2.2 Level AAA"],
                [W3CDisplayTexts.aaa],
                id="1.0-wcag-aaa",
            ),
            pytest.param(None, None, id="no data"),
        ],
    )
    def test__map_conforms_to(self, data_mapper, conformance, expected_output):
        assert data_mapper._map_conforms_to(conformance) == expected_output

    @pytest.mark.parametrize(
        "hazards, expected_output",
        [
            pytest.param([Hazard.flashing], ["Flashing content"], id="flashing"),
            pytest.param(
                [
                    Hazard.no_flashing_hazard,
                    Hazard.no_sound_hazard,
                    Hazard.no_motion_simulation_hazard,
                ],
                ["No hazards"],
                id="no hazards",
            ),
            pytest.param(
                [Hazard.unknown], ["The presence of hazards is unknown"], id="unknown"
            ),
            pytest.param(None, None, id="no data"),
        ],
    )
    def test__map_hazards(self, data_mapper, hazards, expected_output):
        assert data_mapper._map_hazards(hazards) == expected_output

    @pytest.mark.parametrize(
        "access_mode_list, access_mode_sufficient_list, feature_list, expected_output",
        [
            pytest.param(
                [AccessMode.textual],
                [AccessModeSufficient.textual],
                [AccessibilityFeature.long_description],
                [
                    "Has alternative text",
                    "Readable in read aloud or dynamic braille",
                ],
                id="accessmode textual, accessmodesufficient textual, feature",
            ),
            pytest.param(
                [AccessMode.auditory],
                None,
                None,
                ["Not readable in read aloud or dynamic braille"],
                id="accessmode auditory",
            ),
            pytest.param(
                None,
                ["textual, visual"],
                [
                    AccessibilityFeature.table_of_contents,
                    AccessibilityFeature.display_transformability,
                ],
                [
                    "Appearance can be modified",
                    "Not fully readable in read aloud or dynamic braille",
                ],
                id="accessmode textual, accessmodesufficient textual, features",
            ),
            pytest.param(
                [AccessMode.auditory],
                None,
                [AccessibilityFeature.synchronized_audio_text],
                [
                    "Not readable in read aloud or dynamic braille",
                    "Prerecorded audio synchronized with text",
                ],
                id="accessmode auditory, feature",
            ),
            pytest.param(
                [AccessMode.textual],
                ["textual, visual", AccessModeSufficient.textual],
                [
                    AccessibilityFeature.display_transformability,
                    AccessibilityFeature.alternative_text,
                ],
                [
                    "Appearance can be modified",
                    "Has alternative text",
                    "Readable in read aloud or dynamic braille",
                ],
                id="accessmode textual,visual and accessmodesufficient textual,visual, textual, features",
            ),
            pytest.param(
                [AccessMode.visual],
                None,
                None,
                ["Not readable in read aloud or dynamic braille"],
                id="accessmode visual, no accessmodesufficient",
            ),
            pytest.param(
                None,
                [AccessModeSufficient.textual],
                [AccessibilityFeature.long_description],
                [
                    "Has alternative text",
                    "Readable in read aloud or dynamic braille",
                ],
                id="no accessmode, just accessmodesufficient and feature",
            ),
            pytest.param(None, None, None, None, id="no data"),
        ],
    )
    def test__map_ways_of_reading(
        self,
        data_mapper,
        access_mode_list,
        access_mode_sufficient_list,
        feature_list,
        expected_output,
    ):
        assert (
            data_mapper._map_ways_of_reading(
                access_mode_list, access_mode_sufficient_list, feature_list
            )
            == expected_output
        )

    @pytest.mark.parametrize(
        "access_mode_list, access_mode_sufficient_list, feature_list, expected_output",
        [
            pytest.param(
                [AccessMode.textual],
                None,
                None,
                ["all_necessary_content_textual"],
                id="only textual",
            ),
            pytest.param(
                [AccessMode.auditory],
                None,
                None,
                ["audio_only_content"],
                id="only auditory",
            ),
            pytest.param(
                [AccessMode.visual],
                None,
                None,
                ["visual_only_content"],
                id="only visual",
            ),
            pytest.param(
                [AccessMode.textual, AccessMode.visual, AccessMode.auditory],
                [
                    "auditory, textual, visual",
                    "auditory, textual",
                    AccessModeSufficient.textual,
                ],
                [
                    AccessibilityFeature.reading_order,
                    AccessibilityFeature.synchronized_audio_text,
                    AccessibilityFeature.table_of_contents,
                    AccessibilityFeature.page_navigation,
                ],
                ["some_sufficient_text", "synchronised_pre_recorded_audio"],
                id="multiple access modes",
            ),
            pytest.param(
                None,
                [AccessModeSufficient.textual],
                ["none"],
                ["all_necessary_content_textual"],
                id="some sufficient text",
            ),
            pytest.param(
                [AccessMode.textual],
                None,
                [
                    AccessibilityFeature.long_description,
                    AccessibilityFeature.alternative_text,
                    AccessibilityFeature.described_math,
                    AccessibilityFeature.transcript,
                ],
                ["all_necessary_content_textual", "textual_alternatives"],
                id="text alternatives, all textual",
            ),
            pytest.param(
                None,
                None,
                [
                    AccessibilityFeature.long_description,
                    AccessibilityFeature.alternative_text,
                    AccessibilityFeature.described_math,
                    AccessibilityFeature.transcript,
                ],
                ["textual_alternatives"],
                id="text alternatives",
            ),
            pytest.param(
                None,
                ["textual, visual"],
                [
                    AccessibilityFeature.long_description,
                    AccessibilityFeature.alternative_text,
                ],
                ["some_sufficient_text", "textual_alternatives"],
                id="some text alternatives",
            ),
            pytest.param(None, None, None, None, id="no data"),
        ],
    )
    def test__get_ways_of_reading_w3c_variables(
        self,
        data_mapper,
        access_mode_list,
        access_mode_sufficient_list,
        feature_list,
        expected_output,
    ):
        assert (
            data_mapper._get_ways_of_reading_w3c_variables(
                access_mode_list, access_mode_sufficient_list, feature_list
            )
            == expected_output
        )
