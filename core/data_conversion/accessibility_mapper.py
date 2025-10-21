from typing import Any

from flask_babel import gettext as _

from api.opds.rwpm import AccessibilityFeature, AccessMode, AccessModeSufficient, Hazard


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
    def _map_conforms_to(cls, conforms_to: list[str] | None) -> list[str] | None:
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

        display_texts = list()
        if conforms_to:
            # Extract the WCAG level from the url so we get a W3C id like string (= resembles a W3C variable).
            w3c_ids = [item.split("#")[-1].split("-")[-1] for item in conforms_to]
            for id in w3c_ids:
                display_texts.append(level_mappings[id])
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
            if (
                len(access_mode_list) == 1 and AccessMode.textual in access_mode_list
            ) or (
                access_mode_sufficient_list is not None
                and AccessModeSufficient.textual in access_mode_sufficient_list
            ):
                w3c_variables.append(W3CVariables.all_necessary_content_textual)

            # There is only a single access mode of auditory (an audiobook).
            elif len(access_mode_list) == 1 and AccessMode.auditory in access_mode_list:
                w3c_variables.append(W3CVariables.audio_only_content)

            # Prerecorded audio content is included as part of the work.
            elif AccessMode.auditory in access_mode_list:
                w3c_variables.append(W3CVariables.audio_content)

            # There is only a single access mode of visual and there are no sufficient access modes
            # that include textual (e.g., comics and manga with no alternative text).
            elif AccessMode.visual in access_mode_list and len(access_mode_list) == 1:
                if (
                    access_mode_sufficient_list is None
                    or AccessModeSufficient.textual not in access_mode_sufficient_list
                ):
                    w3c_variables.append(W3CVariables.visual_only_content)

        if access_mode_sufficient_list is not None:
            # The content is at least partially readable in textual form (i.e., "textual" is one
            # of a set of sufficient access modes).
            if (
                AccessModeSufficient.textual in access_mode_sufficient_list
                or AccessModeSufficient.textual_visual in access_mode_sufficient_list
                or AccessModeSufficient.visual_textual in access_mode_sufficient_list
            ):
                w3c_variables.append(W3CVariables.some_sufficient_text)

            # All main content is provided in audio form.
            if AccessModeSufficient.auditory in access_mode_sufficient_list:
                w3c_variables.append(W3CVariables.all_content_audio)

        if feature_list:
            # At least one of the following is present:
            if {
                AccessibilityFeature.long_description,
                AccessibilityFeature.alternative_text,
                AccessibilityFeature.described_math,
                AccessibilityFeature.transcript,
            } & set(feature_list):
                w3c_variables.append(W3CVariables.textual_alternatives)

            # All textual content can be modified is present.
            if AccessibilityFeature.display_transformability in feature_list:
                w3c_variables.append(W3CVariables.all_textual_content_can_be_modified)

            # Fixed format is present.
            if AccessibilityFeature.is_fixed_layout in feature_list:
                w3c_variables.append(W3CVariables.is_fixed_layout)

            # Text-synchronised prerecorded audio narration is present.
            if AccessibilityFeature.synchronized_audio_text in feature_list:
                w3c_variables.append(W3CVariables.synchronised_pre_recorded_audio)

        return w3c_variables if w3c_variables else None
