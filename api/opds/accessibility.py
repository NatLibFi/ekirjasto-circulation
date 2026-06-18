"""Accessibility metadata structures for OPDS publications."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from enum import Enum
from functools import cached_property
from typing import Any, cast

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
)

from api.opds.base import BaseOpdsModel
from api.opds.util import StrOrTuple, drop_if_falsy, obj_or_tuple_to_tuple

_logger = logging.getLogger(__name__)


class AccessMode(str, Enum):
    """
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#accessmode-and-accessmodesufficient
    """

    auditory = "auditory"
    visual = "visual"
    textual = "textual"
    chart_on_visual = "chartOnVisual"
    chem_on_visual = "chemOnVisual"
    color_dependent = "colorDependent"
    diagram_on_visual = "diagramOnVisual"
    math_on_visual = "mathOnVisual"
    music_on_visual = "musicOnVisual"
    text_on_visual = "textOnVisual"
    tactile = "tactile"


class AccessModeSufficient(str, Enum):
    """
    Single or combined access modes that are sufficient to understand
    the intellectual content of a publication.

    https://github.com/readium/webpub-manifest/tree/master/contexts/default#accessmode-and-accessmodesufficient
    """

    auditory = "auditory"
    visual = "visual"
    textual = "textual"
    tactile = "tactile"


class AccessibilityFeature(str, Enum):
    """
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#features-and-hazards
    """

    structural_navigation = "structuralnavigation"
    table_of_contents = "tableofcontents"
    alternative_text = "alternativetext"
    display_transformability = "displaytransformability"
    reading_order = "readingorder"
    is_fixed_layout = "isfixedlayout"
    long_description = "longdescription"
    long_descriptions = "longdescriptions"  # ?
    described_math = "describedmath"
    transcript = "transcript"
    index_name = "index"
    print_page_numbers = "printpagenumbers"
    page_break_markers = "pagebreakmarkers"  # previously printPageNumbers https://www.w3.org/community/reports/a11y-discov-vocab/CG-FINAL-vocabulary-20241209/#pageBreakMarkers
    audio_description = "audiodescription"
    open_captions = "opencaptions"
    page_break_source = "pagebreaksource"
    page_navigation = "pagenavigation"
    sign_language = "signlanguage"
    synchronized_audio_text = "synchronizedaudiotext"
    tactile_graphic = "tactilegraphic"
    tactile_object = "tactileobject"
    large_print = "largeprint"
    high_contrast_audio = "highcontrastaudio"
    high_contrast_display = "highcontrastdisplay"
    braille = "braille"
    closed_captions = "closedcaptions"
    tts_markup = "ttsmarkup"
    tagged_pdf = "taggedpdf"
    math_ml = "mathml"
    ruby_annotations = "rubyannotations"
    full_ruby_annotations = "fullrubyannotations"
    aria = "aria"
    bookmarks = "bookmarks"
    captions = "captions"
    chem_ml = "chemml"
    horizontal_writing = "horizontalwriting"
    latex = "latex"
    latex_chemistry = "latex-chemistry"
    math_ml_chemistry = "mathml-chemistry"
    timing_control = "timingcontrol"
    unlocked = "unlocked"
    vertical_writing = "verticalwriting"
    with_additional_word_segmentation = "withadditionalwordsegmentation"
    without_additional_word_segmentation = "withoutadditionalwordsegmentation"
    none: str = "none"
    unknown = "unknown"


class Hazard(str, Enum):
    """
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#features-and-hazards
    """

    flashing = "flashing"
    no_flashing_hazard = "noflashinghazard"
    unknown_flashing_hazard = "unknownflashinghazard"
    motion_simulation = "motionsimulation"
    no_motion_simulation_hazard = "nomotionsimulationhazard"
    unknown_motion_simulation_hazard = "unknownmotionsimulationhazard"
    sound = "sound"
    no_sound_hazard = "nosoundhazard"
    unknown_sound_hazard = "unknownsoundhazard"
    none: str = "none"
    unknown = "unknown"


class Certification(BaseOpdsModel):
    """
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#certification
    """

    certified_by: str | None = Field(None, alias="certifiedBy")
    report: str | list[str] | None = None
    credential: str | None = None


class Exemption(str, Enum):
    """
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#exemption
    """

    eaa_disproportionate_burden = "eaa-disproportionate-burden"
    eaa_fundamental_alteration = "eaa-fundamental-alteration"
    eaa_microenterprise = "eaa-microenterprise"


def _coerce_enum_list(value: Any, enum_cls: type[Enum], field_name: str) -> Any:
    """Coerce a list of strings to enum members, logging and dropping unknown values.

    Performs a case-insensitive match against ``enum_cls`` values so that
    publications with miscased entries (e.g. ``"TaggedPDF"`` instead of
    ``"taggedPDF"``) still import. Unknown values are dropped with a warning
    so they can be reported upstream.
    """
    if not isinstance(value, list):
        return value
    lookup = {member.value.lower(): member for member in enum_cls}
    coerced: list[Any] = []
    for item in value:
        if isinstance(item, enum_cls):
            coerced.append(item)
            continue
        if isinstance(item, str):
            match = lookup.get(item.lower())
            if match is not None:
                if match.value != item:
                    _logger.warning(
                        "Coerced %s value %r to canonical %r",
                        field_name,
                        item,
                        match.value,
                    )
                coerced.append(match)
                continue
            _logger.warning("Dropping unknown %s value %r", field_name, item)
            continue
        coerced.append(item)
    return coerced


def _coerce_single_access_mode_sufficient(
    value: str, lookup: dict[str, AccessModeSufficient]
) -> AccessModeSufficient | None:
    """Coerce a single access mode sufficient value with logging.

    Returns the coerced enum member or None if the value is unknown.
    """
    match = lookup.get(value.lower())
    if match is None:
        _logger.warning("Dropping unknown access_mode_sufficient value %r", value)
        return None
    if match.value != value:
        _logger.warning(
            "Coerced access_mode_sufficient value %r to canonical %r",
            value,
            match.value,
        )
    return match


def _coerce_access_mode_sufficient_string(
    value: str, lookup: dict[str, AccessModeSufficient]
) -> tuple[AccessModeSufficient, ...] | AccessModeSufficient | None:
    """Coerce a string that may contain comma-separated access mode sufficient values.

    Returns a tuple for comma-separated values, a single enum for single values,
    or None if all values were unknown.
    """
    if "," not in value:
        return _coerce_single_access_mode_sufficient(value.strip(), lookup)

    # Handle comma-separated values
    parts: list[AccessModeSufficient] = []
    for part in value.split(","):
        coerced = _coerce_single_access_mode_sufficient(part.strip(), lookup)
        if coerced is not None:
            parts.append(coerced)
    return tuple(parts) if parts else None


class Accessibility(BaseOpdsModel):
    """
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#accessibility-metadata
    """

    feature: list[AccessibilityFeature] | None = None
    access_mode: list[AccessMode] | None = Field(None, alias="accessMode")
    access_mode_sufficient: list[StrOrTuple[AccessModeSufficient]] = Field(
        default_factory=list, alias="accessModeSufficient"
    )

    @cached_property
    def sufficient_access_modes(self) -> Sequence[Sequence[AccessModeSufficient]]:
        """Normalize each item in access_mode_sufficient to a tuple."""
        return tuple(
            obj_or_tuple_to_tuple(item) for item in self.access_mode_sufficient
        )

    hazard: list[Hazard] | None = None

    # https://github.com/readium/webpub-manifest/tree/master/contexts/default#summary
    summary: str | None = None
    certification: Certification | None = None

    # https://github.com/readium/webpub-manifest/tree/master/contexts/default#conformance
    # Ellibs provides the data as a list, De Marque as a string. RWPM does not define either, it just says it can hold one or more profiles.
    conforms_to: StrOrTuple[str] | None = Field(None, alias="conformsTo")
    exemption: Exemption | None = None

    @cached_property
    def conformance_profiles(self) -> Sequence[str]:
        return obj_or_tuple_to_tuple(self.conforms_to)

    @field_validator("access_mode_sufficient", mode="before")
    @classmethod
    def normalize_access_mode_sufficient(
        cls, value: list[Any] | None
    ) -> list[Any] | None:
        """Normalize accessModeSufficient values by splitting comma-separated items.

        Some data sources (e.g., Ellibs) provide comma-separated values like
        "auditory, textual, visual" as individual list items. This validator
        splits those into proper tuples that can be handled by StrOrTuple.
        """
        if not value:
            return value

        lookup = {member.value.lower(): member for member in AccessModeSufficient}
        normalized: list[Any] = []

        for item in value:
            if isinstance(item, str):
                coerced = _coerce_access_mode_sufficient_string(item, lookup)
                if coerced is not None:
                    normalized.append(coerced)
            else:
                normalized.append(item)

        return normalized

    @field_validator("feature", mode="before")
    @classmethod
    def coerce_features(cls, value: Any) -> Any:
        return _coerce_enum_list(value, AccessibilityFeature, "feature")

    @field_validator("hazard", mode="before")
    @classmethod
    def coerce_hazards(cls, value: Any) -> Any:
        return _coerce_enum_list(value, Hazard, "hazard")

    @field_validator("access_mode", mode="before")
    @classmethod
    def coerce_access_modes(cls, value: Any) -> Any:
        return _coerce_enum_list(value, AccessMode, "access_mode")

    @model_serializer(mode="wrap")
    def _serialize(self, serializer: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = cast(dict[str, Any], serializer(self))
        drop_if_falsy(self, "conforms_to", data)
        drop_if_falsy(self, "exemption", data)
        drop_if_falsy(self, "access_mode", data)
        drop_if_falsy(self, "access_mode_sufficient", data)
        drop_if_falsy(self, "feature", data)
        drop_if_falsy(self, "hazard", data)
        drop_if_falsy(self, "certification", data)
        drop_if_falsy(self, "summary", data)
        return data
