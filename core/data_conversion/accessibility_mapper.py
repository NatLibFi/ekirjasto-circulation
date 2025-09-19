from enum import Enum

from core.util.log import LoggerMixin


class W3C:
    @classmethod
    def get_display_text(cls, value: str) -> str | None:
        """get_display_text the display text for a given conformance level."""
        try:
            level = cls[value.lower()]
            return level.value
        except KeyError:
            return None


class W3CConformanceLevel(W3C, Enum):
    """
    https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#conformance-group
    """

    a = "This publication meets minimum accessibility standards"
    aa = "This publication meets accepted accessibility standards"
    aaa = "This publication exceeds accepted accessibility standards"


class W3CIds(W3C, Enum):
    # Ways of reading
    ways_of_reading_nonvisual_reading_readable = (
        "Readable in read aloud or dynamic braille"
    )
    ways_of_reading_nonvisual_reading_not_fully = (
        "Not fully readable in read aloud or dynamic braille"
    )
    ways_of_reading_nonvisual_reading_none = (
        "Not readable in read aloud or dynamic braille"
    )
    ways_of_reading_nonvisual_reading_no_metadata = (
        "No information about nonvisual reading is available"
    )
    ways_of_reading_nonvisual_reading_alt_text = "Has alternative text"
    # Hazards
    hazards_none = "No hazards"
    hazards_flashing = "Flashing content"
    hazards_motion = "Motion simulation"
    hazards_sound = "Sounds"
    hazards_unknown = "The presence of hazards is unknown"
    hazards_no_metadata = "No information is available"


class AccessibilityDataMapper(LoggerMixin):
    """Maps Schema.org data to W3C display data."""

    def __init__(self):
        pass

    @classmethod
    def map_conforms_to(self, conformance: list[str] | None) -> list[str] | None:
        """
        Map the conformance level to a display category.
        """

        def get_display_text_w3c_conformance_level(level_str: str) -> str | None:
            # A helper function to extract the level from AccessibilityData.conforms_to
            level = level_str.split("_")[-1]
            return W3CConformanceLevel.get_display_text(level)

        if conformance:
            w3c_id = [
                get_display_text_w3c_conformance_level(item) for item in conformance
            ]
            return w3c_id

        return None

    @classmethod
    def map_hazards(self, hazards: list[str] | None) -> list[str] | None:
        """
        Map schema.org based hazards to W3C hazard display texts.
        """
        w3c_variables = self._get_w3c_hazard_variables(hazards)
        w3c_display_texts = self._get_w3c_hazard_display_text(w3c_variables)
        return w3c_display_texts

    @classmethod
    def _get_w3c_hazard_display_text(self, w3c_variables: list[str]) -> list[str]:
        """
        W3C variables are mapped to appropriate displayable texts according to logic in https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#hazards-instructions.

        Returns:
            list[str] | None: A list of W3C display texts.
        """
        w3c_ids = []

        # There are no hazards
        if "no_hazards_or_warnings_confirmed" in w3c_variables or (
            "no_flashing_hazards" in w3c_variables
            and "no_motion_hazards" in w3c_variables
            and "no_sound_hazards" in w3c_variables
        ):
            w3c_ids.append("hazards_none")

        # There is a flashing hazard
        elif "flashing_hazard" in w3c_variables:
            w3c_ids.append("hazards_flashing")

        # There is a motion simulation hazard
        elif "motion_simulation_hazard" in w3c_variables:
            w3c_ids.append("hazards_motion")

        # There is a sound hazard
        elif "sound_hazard" in w3c_variables:
            w3c_ids.append("hazards_sound")

        # Hazards are not known
        elif "unknown_if_contains_hazards" in w3c_variables:
            w3c_ids.append("hazards_unknown")

        # No metadata is provided
        else:
            w3c_ids.append("hazards_no_metadata")

        display_texts = [W3CIds.get_display_text(w3c_id) for w3c_id in w3c_ids]

        return sorted(list(display_texts))

    @classmethod
    def _get_w3c_hazard_variables(self, hazards: list[str]) -> list[str]:
        """
        The function takes schema.org accessMode, accessModeSufficient and feature lists and finds equivalent W3C variables according to the logic descriibed in https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#variables-setup-5.

        The variables are described in https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#understanding-the-variables-5.

        Returns:
            list[str] | None: A list of W3C hazard variables.
        """
        variables = []

        # Flashing hazards is present in the package document.
        if "flashing" in hazards:
            variables.append("flashing_hazard")

        # Motion simulation hazard is present in the package document.
        if "motion_simulation" in hazards:
            variables.append("motion_simulation_hazards")

        # No flashing hazard warning is present in the package document.
        if "no_flashing_hazard" in hazards:
            variables.append("no_flashing_hazards")

        # No accessibility hazards is present in the package document.
        if "none" in hazards:
            variables.append("no_hazards_or_warnings_confirmed")

        # No motion simulation hazard warning is present in the package document.
        if "no_motion_simulation_hazard" in hazards:
            variables.append("no_motion_hazards")

        # No sound hazard warning is present in the package document.
        if "no_sound_hazard" in hazards:
            variables.append("no_sound_hazards")

        # Sound hazard is present in the package document.
        if "sound" in hazards:
            variables.append("sound_hazard")

        # Unknown hazards is present in the package document.
        if "unknown" in hazards:
            variables.append("unknown_if_contains_hazards")

        return variables

    @classmethod
    def map_ways_of_reading(
        self,
        access_mode_list: list[str] | None,
        access_mode_suffiecient_list: list[str] | None,
        feature_list: list[str] | None,
    ) -> list[str] | None:
        """
        Map schema.org accessMode, accessModeSufficient and features to W3C Ways of Reading display texts.
        """

        w3c_ids = self._get_nonvisual_reading_support_w3c_variables(
            access_mode_list=access_mode_list,
            access_mode_sufficient_list=access_mode_suffiecient_list,
            feature_list=feature_list,
        )
        display_texts = self._get_ways_of_reading_display_texts(w3c_ids)

        return display_texts

    @classmethod
    def _get_ways_of_reading_display_texts(self, w3c_variables: list[str]):
        """
        W3C variables are mapped to appropriate displayable texts according to logic in https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#instructions-0.

        Returns:
            list[str] | None: A list of W3C display texts.
        """
        w3c_ids = set()

        for variable in w3c_variables:
            # All content is readable in text form.
            if variable == "all_necessary_content_textual":
                w3c_ids.add("ways_of_reading_nonvisual_reading_readable")

            # Not all content is readable in text form.
            elif (
                variable == "some_sufficient_text" or variable == "textual_alternatives"
            ):
                w3c_ids.add("ways_of_reading_nonvisual_reading_not_fully")

            # The content cannot be read in text form.
            elif variable == "audio_only_content" or variable == "visual_only_content":
                w3c_ids.add("ways_of_reading_nonvisual_reading_none")

            # Text alternatives are provided.
            if variable == "textual_alternatives":
                w3c_ids.add("ways_of_reading_nonvisual_reading_alt_text")

            # No metadata is provided.
        if not w3c_ids:
            w3c_ids.add("ways_of_reading_nonvisual_reading_no_metadata")

        display_texts = [W3CIds.get_display_text(w3c_id) for w3c_id in w3c_ids]

        return sorted(list(display_texts))

    @classmethod
    def _get_nonvisual_reading_support_w3c_variables(
        self,
        access_mode_list: list[str] | None,
        access_mode_sufficient_list: list[str] | None,
        feature_list: list[str] | None,
    ) -> list[str] | None:
        """
        The function takes schema.org accessMode, accessModeSufficient and feature lists and finds equivalent W3C variables
        according to the logic descriibed in https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#variables-setup-0.

        The W3C variables are described in https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#understanding-the-variables-0.

        Returns:
            list[str] | None: A list of W3C nonvisual reading support variables.
        """

        variables = []
        if access_mode_list is not None:
            # There is only a single access mode of textual or accessModeSufficient="textual" (all main content is provided in textual form) is present in the package document.
            if (len(access_mode_list) == 1 and "textual" in access_mode_list) or (
                access_mode_sufficient_list is not None
                and "textual" in access_mode_sufficient_list
            ):
                variables.append("all_necessary_content_textual")

            # There is only a single access mode of auditory (an audiobook).
            elif "auditory" in access_mode_list:
                variables.append("audio_only_content")

            # There is only a single access mode of visual and there are no sufficient access modes that include textual (e.g., comics and manga with no alternative text).
            elif "visual" in access_mode_list and len(access_mode_list) == 1:
                if (
                    access_mode_sufficient_list is None
                    or "textual" not in access_mode_sufficient_list
                ):
                    variables.append("visual_only_content")

        if access_mode_sufficient_list is not None:
            # Sufficient access mode metadata indicates that the content is at least partially readable in textual form (i.e., "textual" is one of a set of sufficient access modes).
            if "textual" in access_mode_sufficient_list:
                variables.append("some_sufficient_text")

        if feature_list:
            # At least one of the following is present in the package document:
            if {
                "long_description",
                "alternative_text",
                "described_math",
                "transcript",
            } & set(feature_list):
                variables.append("textual_alternatives")

        return variables
