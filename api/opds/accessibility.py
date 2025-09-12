from enum import Enum

from pydantic import Field, field_validator

from api.opds.base import BaseOpdsModel
from core.util.log import LoggerMixin


class AccessMode(str, Enum):
    """
    https://w3c.github.io/a11y-discov-vocab/crosswalk/#accessmode
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
    https://w3c.github.io/a11y-discov-vocab/crosswalk/#accessmodesufficient
    """

    auditory = "auditory"
    visual = "visual"
    textual = "textual"
    textual_visual = "textual, visual"
    visual_textual = "visual, textual"
    tactile = "tactile"


class AccessibilityFeature(str, Enum):
    """
    https://w3c.github.io/a11y-discov-vocab/crosswalk/#accessibilityfeature
    """

    structural_navigation = "structuralNavigation"
    table_of_contents = "tableOfContents"
    alternative_text = "alternativeText"
    display_transformability = "displayTransformability"
    reading_order = "readingOrder"
    is_fixed_layout = "isFixedLayout"
    long_description = "longDescription"
    described_math = "describedMath"
    transcript = "transcript"
    index_name = "index"
    print_page_numbers = "printPageNumbers"
    page_break_markers = "pageBreakMarkers"  # previously printPageNumbers https://www.w3.org/community/reports/a11y-discov-vocab/CG-FINAL-vocabulary-20241209/#pageBreakMarkers
    audio_description = "audioDescription"
    open_captions = "openCaptions"
    page_break_source = "pageBreakSource"
    page_navigation = "pageNavigation"
    sign_language = "signLanguage"
    synchronized_audio_text = "synchronizedAudioText"
    tactile_graphic = "tactileGraphic"
    tactile_object = "tactileObject"
    large_print = "largePrint"
    high_contrast_audio = "highContrastAudio"
    high_contrast_display = "highContrastDisplay"
    braille = "braille"
    closed_captions = "closedCaptions"
    tts_markup = "ttsMarkup"
    tagged_pdf = "taggedPDF"
    math_ml = "MathML"
    ruby_annotations = "rubyAnnotations"
    full_ruby_annotations = "fullRubyAnnotations"
    aria = "aria"
    bookmarks = "bookmarks"
    captions = "captions"
    chem_ml = "ChemML"
    horizontal_writing = "horizontalWriting"
    latex = "latex"
    latex_chemistry = "latex-chemistry"
    math_ml_chemistry = "MathML-chemistry"
    timing_control = "timingControl"
    unlocked = "unlocked"
    vertical_writing = "verticalWriting"
    with_additional_word_segmentation = "withAdditionalWordSegmentation"
    without_additional_word_segmentation = "withoutAdditionalWordSegmentation"


class Hazard(str, Enum):
    """
    https://w3c.github.io/a11y-discov-vocab/crosswalk/#accessibilityhazard
    """

    flashing = "flashing"
    no_flashing_hazard = "noFlashingHazard"
    unknown_flashing_hazard = "unknownFlashingHazard"
    motion_simulation = "motionSimulation"
    no_motion_simulation_hazard = "noMotionSimulationHazard"
    unknown_motion_simulation_hazard = "unknownMotionSimulationHazard"
    sound = "sound"
    no_sound_hazard = "noSoundHazard"
    unknown_sound_hazard = "unknownSoundHazard"
    none: str = "none"
    unknown = "unknown"


class Certification(BaseOpdsModel):
    """
    https://www.w3.org/TR/epub-a11y-11/#certifiedBy
    """

    certified_by: str | None = Field(None, alias="certifiedBy")


class Accessibility(BaseOpdsModel, LoggerMixin):
    """
    Accessibility data passed on to the feed from both Onix and EPUB files.
    """

    feature: list[AccessibilityFeature] | None = None
    access_mode: list[AccessMode] | None = Field(None, alias="accessMode")
    access_mode_suffifient: list[AccessModeSufficient] | None = Field(
        None, alias="accessModeSufficient"
    )
    hazard: list[Hazard] | None = None

    # https://w3c.github.io/a11y-discov-vocab/crosswalk/#accessibilitysummary
    summary: str | None = None
    certification: Certification | None = None

    # https://w3c.github.io/a11y-discov-vocab/crosswalk/#conformance-and-exemption-declarations
    # Ellibs provides the data as a list, De Marque as a string. It's really a url but there's no need to validate it as such.
    conformance: str | list[str] | None = Field(None, alias="conformsTo")


class AccessibilityDataExtension(BaseOpdsModel):
    """
    Accessibility metadata in an OPDS2 feed.
    """

    # Ellibs provides an empty list if there's no data available.
    # accessibility: Accessibility | list[None] | None = None
    accessibility: Accessibility | list[None] | None = None

    @field_validator("accessibility", mode="before")
    def check_accessibility_type(cls, value):
        if isinstance(value, dict):
            return Accessibility(**value)
        return None
