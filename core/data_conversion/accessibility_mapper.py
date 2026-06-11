from typing import Any

from flask_babel import gettext as _

from api.opds.accessibility import (
    AccessibilityFeature,
    AccessMode,
    AccessModeSufficient,
    Hazard,
)


def _list_contains(
    item_list: list[Any], item: AccessMode | AccessModeSufficient | AccessibilityFeature
) -> bool:
    """
    Check if a specific item is present in a list that may contain both individual
    values (enums) and comma-separated string combinations.

    Handles AccessMode, AccessModeSufficient, and AccessibilityFeature enums,
    and works with lists that may contain enum members, strings, or mixed formats.

    Args:
        item_list: List of values which may contain enum members, strings,
                   or comma-separated string combinations
        item: The item to search for (AccessMode, AccessModeSufficient, or AccessibilityFeature)

    Returns:
        True if the item is found either as an individual item or within any combination
    """
    item_value = item.value
    for element in item_list:
        # Check if element is the item itself (enum)
        if element == item:
            return True
        # Check if element is a string version of the item
        if isinstance(element, str):
            if element == item_value:
                return True
            # Check if item is part of a comma-separated combination
            if "," in element:
                parts = [part.strip() for part in element.split(",")]
                if item_value in parts:
                    return True
    return False


def _list_is_only(
    item_list: list[Any], item: AccessMode | AccessModeSufficient | AccessibilityFeature
) -> bool:
    """
    Check if an item_list contains ONLY a single specified item (not a combination).

    Handles AccessMode, AccessModeSufficient, and AccessibilityFeature enums,
    and works with lists that may contain enum members, strings, or mixed formats.

    Args:
        item_list: List of values
        item: The item to check for (AccessMode, AccessModeSufficient, or AccessibilityFeature)

    Returns:
        True if the list contains exactly one item that is the specified item
    """
    if len(item_list) != 1:
        return False

    element = item_list[0]
    item_value = item.value

    # Check if element is the item itself (enum)
    if element == item:
        return True
    # Check if element is a string version of the item (not a combination)
    if isinstance(element, str) and element == item_value and "," not in element:
        return True

    return False


# Aliases for backward compatibility and semantic clarity
def _mode_list_contains(
    mode_list: list[Any], mode: AccessMode | AccessModeSufficient
) -> bool:
    """Alias to _list_contains for backward compatibility."""
    return _list_contains(mode_list, mode)


def _mode_list_is_only(
    mode_list: list[Any], mode: AccessMode | AccessModeSufficient
) -> bool:
    """Alias to _list_is_only for backward compatibility."""
    return _list_is_only(mode_list, mode)


def _normalize_features_list(
    feature_list: list[Any],
) -> list[AccessibilityFeature]:
    """
    Normalize a feature list to always contain AccessibilityFeature enum members.

    Handles both string values (from tests) and enum members (from real data).
    """
    normalized: list[AccessibilityFeature] = []
    for item in feature_list:
        if isinstance(item, AccessibilityFeature):
            normalized.append(item)
        elif isinstance(item, str):
            # Try to find the enum member by value
            for feature in AccessibilityFeature:
                if feature.value.lower() == item.lower():
                    normalized.append(feature)
                    break
    return normalized


class W3CVariables:
    # Ways of reading
    all_necessary_content_textual = "all_necessary_content_textual"
    some_sufficient_text = "some_sufficient_text"
    textual_alternatives = "textual_alternatives"
    audio_only_content = "audio_only_content"
    visual_only_content = "visual_only_content"
    all_content_audio = "all_content_audio"
    audio_content = "audio_content"
    textual_alternatives = "textual_alternatives"
    all_textual_content_can_be_modified = "all_textual_content_can_be_modified"
    is_fixed_layout = "is_fixed_layout"
    synchronised_pre_recorded_audio = "synchronised_pre_recorded_audio"

    # Hazards
    flashing_hazard = "flashing_hazard"
    motion_simulation_hazard = "motion_simulation_hazard"
    sound_hazard = "sound_hazard"
    no_flashing_hazards = "no_flashing_hazards"
    no_hazards_or_warnings_confirmed = "no_hazards_or_warnings_confirmed"
    no_motion_hazards = "no_motion_hazards"
    no_sound_hazards = "no_sound_hazards"
    unknown_if_contains_hazards = "unknown_if_contains_hazards"


class W3CDisplayTexts:
    # Conformance
    a = _("This publication meets minimum accessibility standards")
    aa = _("This publication meets accepted accessibility standards")
    aaa = _("This publication exceeds accepted accessibility standards")

    # Ways of reading
    ways_of_reading_nonvisual_reading_readable = _(
        "Readable in read aloud or dynamic braille"
    )
    ways_of_reading_nonvisual_reading_not_fully = _(
        "Not fully readable in read aloud or dynamic braille"
    )
    ways_of_reading_nonvisual_reading_none = _(
        "Not readable in read aloud or dynamic braille"
    )
    ways_of_reading_nonvisual_reading_no_metadata = _(
        "No information about nonvisual reading is available"
    )
    ways_of_reading_nonvisual_reading_alt_text = _("Has alternative text")
    ways_of_reading_visual_adjustments_modifiable = _("Appearance can be modified")
    ways_of_reading_visual_adjustments_unmodifiable = _("Appearance cannot be modified")
    ways_of_reading_prerecorded_audio_synchronized = _(
        "Prerecorded audio synchronized with text"
    )
    ways_of_reading_prerecorded_audio_only = _("Prerecorded audio only")
    ways_of_reading_prerecorded_audio_complementary = _("Prerecorded audio clips")

    # Hazards
    hazards_none = _("No hazards")
    hazards_flashing = _("Flashing content")
    hazards_motion = _("Motion simulation")
    hazards_sound = _("Sounds")
    hazards_unknown = _("The presence of hazards is unknown")
    hazards_no_metadata = _("No information is available")


class AccessibilityDataMapper:
    """
    Maps Schema.org Accessibility metadata to W3C display data.

    Apart from conformance, the schema.org data is first mapped to W3C variables
    because they define which W3C id - and its display text - should be given.
    """

    @classmethod
    def map_accessibility(self, accessibility_data: Any) -> dict | None:
        """
        Maps all accessibility metadata to W3C display content.

        Returns:
            dict | None: A dictionary of W3C display fields.

        """
        mappings = dict()
        if accessibility_data:
            # E-kirjasto only maps the W3C recommended fields https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/guidelines/#rec-fields.
            conforms_to = self._map_conforms_to(accessibility_data.conforms_to)
            mappings["conforms_to"] = conforms_to
            ways_of_reading = self._map_ways_of_reading(
                accessibility_data.access_mode,
                accessibility_data.access_mode_sufficient,
                accessibility_data.features,
            )
            mappings["ways_of_reading"] = ways_of_reading
            return mappings

        return None

    @classmethod
    def _map_conforms_to(cls, conforms_to: list[str] | None) -> list[Any] | None:
        """
        Map the conformance level to a display texts according to logic in
        https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#conformance-instructions.

        Returns:
            list[str] | None: A list of W3C display texts.
        """
        level_mappings = {
            "a": W3CDisplayTexts.a,
            "aa": W3CDisplayTexts.aa,
            "aaa": W3CDisplayTexts.aaa,
        }

        if conforms_to:
            # Extract W3C IDs whether they are links or descriptions.
            w3c_ids = [
                item.split("#")[-1].split("-")[-1]
                if item.startswith("http")
                else item.split(" ")[-1].lower()
                for item in conforms_to
            ]
            # Map W3C IDs to display texts
            display_texts = [
                level_mappings.get(w3c_id)
                for w3c_id in w3c_ids
                if w3c_id in level_mappings
            ]

            return display_texts
        return None

    @classmethod
    def _map_hazards(cls, hazards: list[str] | None) -> list[str] | None:
        """
        Map schema.org based hazards to W3C hazard display texts.

        Returns:
            list[str] | None: A list of W3C display texts.
        """
        if hazards:
            w3c_variables = cls._get_w3c_hazard_variables(hazards)
            if w3c_variables:
                w3c_display_texts = cls._get_w3c_hazard_display_texts(w3c_variables)
                return w3c_display_texts
        return None

    @classmethod
    def _get_w3c_hazard_display_texts(cls, w3c_variables: list[str]) -> list[str]:
        """
        W3C variables are mapped to appropriate displayable texts according to logic in
        https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#hazards-instructions.

        Returns:
            list[str] | None: A list of W3C display texts.
        """
        display_texts = list()

        hazard_mapping = {
            # There are no hazards
            W3CVariables.no_hazards_or_warnings_confirmed: W3CDisplayTexts.hazards_none,
            # There is a flashing hazard
            W3CVariables.flashing_hazard: W3CDisplayTexts.hazards_flashing,
            # There is a motion simulation hazard
            W3CVariables.motion_simulation_hazard: W3CDisplayTexts.hazards_motion,
            # There is a sound hazard
            W3CVariables.sound_hazard: W3CDisplayTexts.hazards_sound,
            # Hazards are not known
            W3CVariables.unknown_if_contains_hazards: W3CDisplayTexts.hazards_unknown,
        }

        # First check that there are no hazards
        if W3CVariables.no_hazards_or_warnings_confirmed in w3c_variables or (
            W3CVariables.no_flashing_hazards in w3c_variables
            and W3CVariables.no_motion_hazards in w3c_variables
            and W3CVariables.no_sound_hazards in w3c_variables
        ):
            display_texts.append(
                hazard_mapping[W3CVariables.no_hazards_or_warnings_confirmed]
            )
        else:
            for variable in w3c_variables:
                if variable in hazard_mapping:
                    display_texts.append(hazard_mapping[variable])

        return sorted(display_texts)

    @classmethod
    def _get_w3c_hazard_variables(cls, hazards: list[str]) -> list[str]:
        """
        The function takes schema.org hazard and finds equivalent W3C variables
        according to the logic described in
        https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#variables-setup-5.

        Returns:
            list[str] | None: A list of W3C hazard variables.
        """
        w3c_variables = list()

        hazard_mapping = {
            # Flashing hazards is present.
            Hazard.flashing: W3CVariables.flashing_hazard,
            # Motion simulation hazard is present.
            Hazard.motion_simulation: W3CVariables.motion_simulation_hazard,
            # No flashing hazard warning is present.
            Hazard.no_flashing_hazard: W3CVariables.no_flashing_hazards,
            # No accessibility hazards is present.
            Hazard.none: W3CVariables.no_hazards_or_warnings_confirmed,
            # No motion simulation hazard warning is present.
            Hazard.no_motion_simulation_hazard: W3CVariables.no_motion_hazards,
            # No sound hazard warning is present.
            Hazard.no_sound_hazard: W3CVariables.no_sound_hazards,
            # Sound hazard is present.
            Hazard.sound: W3CVariables.sound_hazard,
            # Unknown hazards is present.
            Hazard.unknown: W3CVariables.unknown_if_contains_hazards,
        }

        for hazard in hazards:
            if hazard in hazard_mapping:
                w3c_variables.append(hazard_mapping[hazard])  # type:ignore[index]

        return w3c_variables

    @classmethod
    def _map_ways_of_reading(
        cls,
        access_mode_list: list[str] | None,
        access_mode_suffiecient_list: list[str] | None,
        feature_list: list[str] | None,
    ) -> list[str] | None:
        """
        Map schema.org accessMode, accessModeSufficient and features to W3C Ways of Reading display texts.

        Returns:
            list[str] | None: A list of W3C display texts.
        """

        w3c_ids = cls._get_ways_of_reading_w3c_variables(
            access_mode_list=access_mode_list,
            access_mode_sufficient_list=access_mode_suffiecient_list,
            feature_list=feature_list,
        )
        if w3c_ids:
            display_texts = cls._get_ways_of_reading_display_texts(w3c_ids)
            return display_texts

        return None

    @classmethod
    def _get_ways_of_reading_display_texts(cls, w3c_variables: list[str]):
        """
        W3C variables are mapped to appropriate displayable texts according to logic in
        https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#instructions,
        https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#instructions-0,
        https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#instructions-1

        Returns:
            list[str] | None: A list of W3C display texts.
        """
        display_texts = set()

        mappings = {
            # All content is readable in text form.
            W3CVariables.all_necessary_content_textual: W3CDisplayTexts.ways_of_reading_nonvisual_reading_readable,
            # Not all content is readable in text form.
            W3CVariables.some_sufficient_text: W3CDisplayTexts.ways_of_reading_nonvisual_reading_not_fully,
            W3CVariables.textual_alternatives: W3CDisplayTexts.ways_of_reading_nonvisual_reading_not_fully,
            # The content cannot be read in text form.
            W3CVariables.audio_only_content: W3CDisplayTexts.ways_of_reading_nonvisual_reading_none,
            W3CVariables.visual_only_content: W3CDisplayTexts.ways_of_reading_nonvisual_reading_none,
            # All main content is provided in audio form.
            W3CVariables.all_content_audio: W3CDisplayTexts.ways_of_reading_prerecorded_audio_only,
            # Prerecorded audio content is included as part of the work.
            W3CVariables.audio_content: W3CDisplayTexts.ways_of_reading_prerecorded_audio_complementary,
            # Text alternatives are provided.
            W3CVariables.textual_alternatives: W3CDisplayTexts.ways_of_reading_nonvisual_reading_alt_text,
            # All textual content can be modified is present.
            W3CVariables.all_textual_content_can_be_modified: W3CDisplayTexts.ways_of_reading_visual_adjustments_modifiable,
            # Fixed format is present.
            W3CVariables.is_fixed_layout: W3CDisplayTexts.ways_of_reading_visual_adjustments_unmodifiable,
            # Text-synchronised prerecorded audio narration (natural or synthesized voice) is
            # included for substantially all textual matter, including all alternative descriptions, e.g. via a SMIL media overlay.
            W3CVariables.synchronised_pre_recorded_audio: W3CDisplayTexts.ways_of_reading_prerecorded_audio_synchronized,
        }

        for variable in w3c_variables:
            if variable in mappings:
                display_texts.add(mappings[variable])

        return sorted(list(display_texts))

    @classmethod
    def _get_ways_of_reading_w3c_variables(
        cls,
        access_mode_list: list[str] | None,
        access_mode_sufficient_list: list[str] | None,
        feature_list: list[str] | None,
    ) -> list[str] | None:
        """
        The function takes schema.org accessMode, accessModeSufficient and feature lists and finds equivalent W3C variables
        according to the logic descriibed in:
        https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#variables-setup,
        https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#variables-setup-0,
        https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#variables-setup-1.

        Returns:
            list[str] | None: A list of W3C variables.
        """

        w3c_variables = list()

        if access_mode_list is not None:
            # All main content is provided in textual form.
            if _mode_list_is_only(access_mode_list, AccessMode.textual):
                w3c_variables.append(W3CVariables.all_necessary_content_textual)

            # The content is at least partially readable in textual form.
            elif _mode_list_contains(access_mode_list, AccessMode.textual):
                w3c_variables.append(W3CVariables.some_sufficient_text)

            # There is only a single access mode of auditory (an audiobook).
            elif _mode_list_is_only(access_mode_list, AccessMode.auditory):
                w3c_variables.append(W3CVariables.audio_only_content)

            # Prerecorded audio content is included as part of the work.
            elif _mode_list_contains(access_mode_list, AccessMode.auditory):
                w3c_variables.append(W3CVariables.audio_content)

            # There is only a single access mode of visual and there are no sufficient access modes
            # that include textual (e.g., comics and manga with no alternative text).
            elif _mode_list_is_only(access_mode_list, AccessMode.visual):
                if access_mode_sufficient_list is None or not _mode_list_contains(
                    access_mode_sufficient_list, AccessModeSufficient.textual
                ):
                    w3c_variables.append(W3CVariables.visual_only_content)

        elif access_mode_sufficient_list is not None:
            # All main content is provided in audio form.
            if _mode_list_is_only(
                access_mode_sufficient_list, AccessModeSufficient.auditory
            ):
                w3c_variables.append(W3CVariables.all_content_audio)

            # All main content is provided in textual form. There was no accessModes.
            elif _mode_list_is_only(
                access_mode_sufficient_list, AccessModeSufficient.textual
            ):
                w3c_variables.append(W3CVariables.all_necessary_content_textual)

            # The content is at least partially readable in textual form (i.e., "textual" is one
            # of a set of sufficient access modes).
            elif _mode_list_contains(
                access_mode_sufficient_list, AccessModeSufficient.textual
            ):
                w3c_variables.append(W3CVariables.some_sufficient_text)

        if feature_list:
            # Normalize feature_list to enum members (handles both strings and enums)
            normalized_features = _normalize_features_list(feature_list)

            # At least one of the following is present:
            if any(
                _list_contains(normalized_features, feature)
                for feature in [
                    AccessibilityFeature.long_description,
                    AccessibilityFeature.alternative_text,
                    AccessibilityFeature.described_math,
                    AccessibilityFeature.transcript,
                ]
            ):
                w3c_variables.append(W3CVariables.textual_alternatives)

            # All textual content can be modified is present.
            if _list_contains(
                normalized_features, AccessibilityFeature.display_transformability
            ):
                w3c_variables.append(W3CVariables.all_textual_content_can_be_modified)

            # Fixed format is present.
            if _list_contains(
                normalized_features, AccessibilityFeature.is_fixed_layout
            ):
                w3c_variables.append(W3CVariables.is_fixed_layout)

            # Text-synchronised prerecorded audio narration is present.
            if _list_contains(
                normalized_features, AccessibilityFeature.synchronized_audio_text
            ):
                w3c_variables.append(W3CVariables.synchronised_pre_recorded_audio)

        return w3c_variables if w3c_variables else None
