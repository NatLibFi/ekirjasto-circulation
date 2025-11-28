import pytest

from api.opds.rwpm import (
    AccessibilityFeature,
    AccessMode,
    AccessModeSufficient,
    ConformsTo,
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
            conforms_to=[ConformsTo.epub_1_0_wcag_2_0_level_a],
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
            pytest.param(None, None, id="no data"),
        ],
    )
    def test__map_conforms_to(self, data_mapper, conformance, expected_output):
        assert data_mapper._map_conforms_to(conformance) == expected_output

    @pytest.mark.parametrize(
        "hazards, expected_output",
        [
            pytest.param(["flashing"], ["Flashing content"], id="flashing"),
            pytest.param(
                ["noFlashingHazard", "noSoundHazard", "noMotionSimulationHazard"],
                ["No hazards"],
                id="no hazards",
            ),
            pytest.param(
                ["unknown"], ["The presence of hazards is unknown"], id="unknown"
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
                ["textual"],
                ["textual"],
                ["longDescription"],
                [
                    "Has alternative text",
                    "Readable in read aloud or dynamic braille",
                ],
                id="accessmode textual, accessmodesufficient textual, feature",
            ),
            pytest.param(
                ["auditory"],
                None,
                None,
                ["Not readable in read aloud or dynamic braille"],
                id="accessmode auditory",
            ),
            pytest.param(
                None,
                ["textual, visual"],
                ["tableOfContents", "displayTransformability"],
                [
                    "Appearance can be modified",
                    "Not fully readable in read aloud or dynamic braille",
                ],
                id="accessmode textual, accessmodesufficient textual, features",
            ),
            pytest.param(
                ["auditory"],
                None,
                ["synchronizedAudioText"],
                [
                    "Not readable in read aloud or dynamic braille",
                    "Prerecorded audio synchronized with text",
                ],
                id="accessmode auditory, feature",
            ),
            pytest.param(
                ["textual"],
                ["textual, visual", "textual"],
                ["displayTransformability", "alternativeText"],
                [
                    "Appearance can be modified",
                    "Has alternative text",
                    "Readable in read aloud or dynamic braille",
                ],
                id="accessmode textual,visual and accessmodesufficient textual,visual, textual, features",
            ),
            pytest.param(
                ["visual"],
                None,
                None,
                ["Not readable in read aloud or dynamic braille"],
                id="accessmode visual, no accessmodesufficient",
            ),
            pytest.param(
                None,
                ["textual"],
                ["longDescription"],
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
                ["textual"], None, None, ["all_necessary_content_textual"], id="textual"
            ),
            pytest.param(
                ["auditory"], None, None, ["audio_only_content"], id="auditory"
            ),
            pytest.param(["visual"], None, None, ["visual_only_content"], id="visual"),
            pytest.param(
                None,
                ["textual"],
                None,
                ["all_necessary_content_textual"],
                id="some sufficient text",
            ),
            pytest.param(
                ["textual"],
                None,
                ["longDescription", "alternativeText", "describedMath", "transcript"],
                ["all_necessary_content_textual", "textual_alternatives"],
                id="text alternatives, all textual",
            ),
            pytest.param(
                None,
                None,
                ["longDescription", "alternativeText", "describedMath", "transcript"],
                ["textual_alternatives"],
                id="text alternatives",
            ),
            pytest.param(
                None,
                ["textual, visual"],
                ["longDescription", "alternativeText"],
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
