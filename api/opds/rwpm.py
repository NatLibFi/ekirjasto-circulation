from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from enum import Enum
from functools import cached_property
from typing import Literal, TypeVar

from pydantic import (
    AwareDatetime,
    Field,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    field_validator,
)

from api.opds.base import BaseOpdsModel
from api.opds.types.language import LanguageCode, LanguageMap
from api.opds.types.link import BaseLink, CompactCollection
from api.opds.util import StrModelOrTuple, StrOrModel, StrOrTuple, obj_or_tuple_to_tuple


class Encryption(BaseOpdsModel):
    """
    The Encryption Module defines how a given resource has been encrypted or obfuscated, and provides
    relevant information for decryption by a User Agent.

    https://github.com/readium/webpub-manifest/blob/master/modules/encryption.md#encryption-object
    https://readium.org/webpub-manifest/schema/extensions/encryption/properties.schema.json
    """

    algorithm: str
    scheme: str | None = None
    profile: str | None = None
    compression: Literal["deflate"] | None = None
    original_length: PositiveInt | None = Field(None, alias="originalLength")


class PresentationProperties(BaseOpdsModel):
    """
    Presentation Hints Properties and Metadata Object.

    https://readium.org/webpub-manifest/schema/experimental/presentation/properties.schema.json
    https://readium.org/webpub-manifest/schema/experimental/presentation/metadata.schema.json
    """

    clipped: bool | None = None
    fit: Literal["contain", "cover", "width", "height"] | None = None
    orientation: Literal["auto", "landscape", "portrait"] | None = None


class EPubProperties(BaseOpdsModel):
    """
    EPub extensions to the Properties Object.

    https://readium.org/webpub-manifest/schema/extensions/epub/properties.schema.json
    """

    contains: (
        set[Literal["mathml", "onix", "remote-resources", "js", "svg", "xmp"]] | None
    ) = None
    layout: Literal["fixed", "reflowable"] | None = None


class EncryptionProperties(BaseOpdsModel):
    """
    Encryption extensions to the Properties Object.

    https://readium.org/webpub-manifest/schema/extensions/encryption/properties.schema.json
    """

    encrypted: Encryption | None = None


class DivinaProperties(BaseOpdsModel):
    """
    Divina extensions to the Properties Object.

    https://readium.org/webpub-manifest/schema/extensions/divina/properties.schema.json
    """

    break_scroll_before: bool = Field(False, alias="break-scroll-before")


class LinkProperties(
    EPubProperties,
    EncryptionProperties,
    DivinaProperties,
    PresentationProperties,
):
    """
    Each Link Object may contain a Properties Object, containing a number of relevant information.

    https://github.com/readium/webpub-manifest/blob/master/properties.md
    https://github.com/readium/webpub-manifest/blob/b43ec57fd28028316272987ccb10c326f0130280/schema/link.schema.json#L33-L54
    """

    page: Literal["left", "right", "center"] | None = None


class Link(BaseLink):
    """
    Link to another resource.

    https://github.com/readium/webpub-manifest/blob/master/README.md#23-links
    https://readium.org/webpub-manifest/schema/link.schema.json
    """

    title: str | None = None
    height: PositiveInt | None = None
    width: PositiveInt | None = None
    bitrate: PositiveFloat | None = None
    duration: PositiveFloat | None = None
    language: StrOrTuple[LanguageCode] | None = None

    @cached_property
    def languages(self) -> Sequence[LanguageCode]:
        return obj_or_tuple_to_tuple(self.language)

    alternate: CompactCollection[Link] = Field(default_factory=CompactCollection)
    children: CompactCollection[Link] = Field(default_factory=CompactCollection)

    properties: LinkProperties = Field(default_factory=LinkProperties)


class AltIdentifier(BaseOpdsModel):
    """
    An identifier for the publication.

    https://github.com/readium/webpub-manifest/tree/master/contexts/default#identifier
    https://github.com/readium/webpub-manifest/blob/master/schema/altIdentifier.schema.json
    """

    value: str
    scheme: str | None = None


class Named(BaseOpdsModel):
    """
    An object with a required translatable name.
    """

    name: LanguageMap

    # E-kirjasto specific validation
    @field_validator("name", mode="before")
    def validate_name(cls, value):
        if value is None or value == "":
            return "[Unknown]"
        return value


class Contributor(Named):
    """
    A contributor to the publication.

    https://github.com/readium/webpub-manifest/tree/master/contexts/default#contributors
    https://github.com/readium/webpub-manifest/blob/master/schema/contributor-object.schema.json
    """

    sort_as: str | None = Field(None, alias="sortAs")
    identifier: str | None = None
    alt_identifier: list[StrOrModel[AltIdentifier]] = Field(
        default_factory=list, alias="altIdentifier"
    )

    @cached_property
    def alt_identifiers(self) -> Sequence[AltIdentifier]:
        return [
            AltIdentifier(value=alt_id) if isinstance(alt_id, str) else alt_id
            for alt_id in self.alt_identifier
        ]

    position: NonNegativeInt | None = None
    links: CompactCollection[Link] = Field(default_factory=CompactCollection)


class ContributorWithRole(Contributor):
    """
    A generic contributor, where an optional role can be included.
    """

    # TODO: Add some validation for the roles that we accept here.
    #   We might want to make role required here, or default it to
    #   something generic like "contributor".
    role: StrOrTuple[str] | None = None

    @cached_property
    def roles(self) -> Sequence[str]:
        return obj_or_tuple_to_tuple(self.role)


class SubjectScheme(str, Enum):
    """
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#subjects
    """

    BISAC = "https://www.bisg.org/#bisac"
    Thema = "https://ns.editeur.org/thema/"


class Subject(Named):
    """
    A subject of the publication.

    https://github.com/readium/webpub-manifest/tree/master/contexts/default#subjects
    https://github.com/readium/webpub-manifest/blob/master/schema/subject-object.schema.json
    """

    sort_as: str | None = Field(None, alias="sortAs")
    code: str | None = None
    scheme: str | None = None
    links: CompactCollection[Link] = Field(default_factory=CompactCollection)


class BelongsTo(BaseOpdsModel):
    """
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#collections--series
    https://github.com/readium/webpub-manifest/blob/b43ec57fd28028316272987ccb10c326f0130280/schema/metadata.schema.json#L138-L147
    """

    series_data: StrModelOrTuple[Contributor] | None = Field(None, alias="series")

    @cached_property
    def series(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.series_data, Contributor)

    collection: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def collections(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.collection, Contributor)


NamedT = TypeVar("NamedT", bound=Named)


def _named_or_sequence_to_sequence(
    value: str | NamedT | tuple[str | NamedT, ...] | None, cls: type[NamedT]
) -> Sequence[NamedT]:
    return tuple(
        cls(name=item) if isinstance(item, str) else item  # type: ignore[misc]
        for item in obj_or_tuple_to_tuple(value)
    )


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
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#accessmode-and-accessmodesufficient
    """

    auditory = "auditory"
    visual = "visual"
    textual = "textual"
    textual_visual = "textual, visual"
    visual_textual = "visual, textual"
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


class ConformsTo(str, Enum):
    """
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#conformance
    """

    epub_1_0_wcag_2_0_level_a = (
        "http://www.idpf.org/epub/a11y/accessibility-20170105.html#wcag-a",
    )
    epub_1_0_wcag_2_0_level_aa = (
        "http://www.idpf.org/epub/a11y/accessibility-20170105.html#wcag-aa"
    )
    epub_1_0_wcag_2_0_level_aaa = (
        "http://www.idpf.org/epub/a11y/accessibility-20170105.html#wcag-aaa"
    )

    epub_1_1_wcag_2_0_level_a = "https://www.w3.org/TR/epub-a11y-11#wcag-2.0-a"
    epub_1_1_wcag_2_0_level_aa = "https://www.w3.org/TR/epub-a11y-11#wcag-2.0-aa"
    epub_1_1_wcag_2_0_level_aaa = "https://www.w3.org/TR/epub-a11y-11#wcag-2.0-aaa"

    epub_1_1_wcag_2_1_level_a = "https://www.w3.org/TR/epub-a11y-11#wcag-2.1-a"
    epub_1_1_wcag_2_1_level_aa = "https://www.w3.org/TR/epub-a11y-11#wcag-2.1-aa"
    epub_1_1_wcag_2_1_level_aaa = "https://www.w3.org/TR/epub-a11y-11#wcag-2.1-aaa"

    epub_1_1_wcag_2_2_level_a = "https://www.w3.org/TR/epub-a11y-11#wcag-2.2-a"
    epub_1_1_wcag_2_2_level_aa = "https://www.w3.org/TR/epub-a11y-11#wcag-2.2-aa"
    epub_1_1_wcag_2_2_level_aaa = "https://www.w3.org/TR/epub-a11y-11#wcag-2.2-aaa"

    epub_1_0_wcag_2_0_level_a_text = "EPUB Accessibility 1.0 - WCAG 2.0 Level A"
    epub_1_0_wcag_2_0_level_aa_text = "EPUB Accessibility 1.0 - WCAG 2.0 Level AA"
    epub_1_0_wcag_2_0_level_aaa_text = "EPUB Accessibility 1.0 - WCAG 2.0 Level AAA"

    epub_1_0_wcag_2_1_level_a = "EPUB Accessibility 1.0 - WCAG 2.1 Level A"
    epub_1_0_wcag_2_1_level_aa = "EPUB Accessibility 1.0 - WCAG 2.1 Level AA"
    epub_1_0_wcag_2_1_level_aaa = "EPUB Accessibility 1.0 - WCAG 2.1 Level AAA"

    epub_1_0_wcag_2_2_level_a = "EPUB Accessibility 1.0 - WCAG 2.2 Level A"
    epub_1_0_wcag_2_2_level_aa = "EPUB Accessibility 1.0 - WCAG 2.2 Level AA"
    epub_1_0_wcag_2_2_level_aaa = "EPUB Accessibility 1.0 - WCAG 2.2 Level AAA"

    epub_1_1_wcag_2_0_level_a_text = "EPUB Accessibility 1.1 - WCAG 2.0 Level A"
    epub_1_1_wcag_2_0_level_aa_text = "EPUB Accessibility 1.1 - WCAG 2.0 Level AA"
    epub_1_1_wcag_2_0_level_aaa_text = "EPUB Accessibility 1.1 - WCAG 2.0 Level AAA"

    epub_1_1_wcag_2_1_level_a_text = "EPUB Accessibility 1.1 - WCAG 2.1 Level A"
    epub_1_1_wcag_2_1_level_aa_text = "EPUB Accessibility 1.1 - WCAG 2.1 Level AA"
    epub_1_1_wcag_2_1_level_aaa_text = "EPUB Accessibility 1.1 - WCAG 2.1 Level AAA"

    epub_1_1_wcag_2_2_level_a_text = "EPUB Accessibility 1.1 - WCAG 2.2 Level A"
    epub_1_1_wcag_2_2_level_aa_text = "EPUB Accessibility 1.1 - WCAG 2.2 Level AA"
    epub_1_1_wcag_2_2_level_aaa_text = "EPUB Accessibility 1.1 - WCAG 2.2 Level AAA"


class Exemption(str, Enum):
    """
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#exemption
    """

    eaa_disproportionate_burden = "eaa-disproportionate-burden"
    eaa_fundamental_alteration = "eaa-fundamental-alteration"
    eaa_microenterprise = "eaa-microenterprise"


class Accessibility(BaseOpdsModel):
    """
    https://github.com/readium/webpub-manifest/tree/master/contexts/default#accessibility-metadata
    """

    feature: list[AccessibilityFeature] | None = None
    access_mode: list[AccessMode] | None = Field(None, alias="accessMode")
    access_mode_sufficient: list[AccessModeSufficient] | None = Field(
        None, alias="accessModeSufficient"
    )
    hazard: list[Hazard] | None = None

    # https://github.com/readium/webpub-manifest/tree/master/contexts/default#summary
    summary: str | None = None
    certification: Certification | None = None

    # https://github.com/readium/webpub-manifest/tree/master/contexts/default#conformance
    # Ellibs provides the data as a list, De Marque as a string. RWPM does not define either, it just says it can hold one or more profiles.
    conforms_to: list[ConformsTo] | None = Field(None, alias="conformsTo")
    exemption: Exemption | None = None

    @field_validator("conforms_to", mode="before")
    def validate_conforms_to(cls, value: str | list[str]) -> list[str]:
        """Ensure that the conforms_to field is always a list of ConformsTo enums."""
        if isinstance(value, str):
            return [ConformsTo(value)]
        elif isinstance(value, list):
            return [ConformsTo(v) for v in value]

    @field_validator("feature", "hazard", mode="before")
    def to_lower(cls, values: list[str] | None) -> list[str] | None:
        """Convert all feature strings to lowercase to ensure consistent mapping."""
        if values is not None:
            return [v.lower() for v in values]
        return values


class Metadata(BaseOpdsModel):
    """
    Metadata associated with a publication.

    https://github.com/readium/webpub-manifest/tree/master/contexts/default
    https://github.com/readium/webpub-manifest/blob/master/schema/metadata.schema.json
    """

    title: LanguageMap
    type: str | None = Field(None, alias="@type")
    sort_as: str | None = Field(None, alias="sortAs")
    subtitle: LanguageMap | None = None
    identifier: str | None = None
    alt_identifier: list[StrOrModel[AltIdentifier]] = Field(
        default_factory=list, alias="altIdentifier"
    )

    @cached_property
    def alt_identifiers(self) -> Sequence[AltIdentifier]:
        return [
            AltIdentifier(value=alt_id) if isinstance(alt_id, str) else alt_id
            for alt_id in self.alt_identifier
        ]

    modified: AwareDatetime | None = None
    published: AwareDatetime | date | None = None

    @field_validator("published", mode="before")
    def check_year(cls, value):
        """
        Format a year into a datetime object.
        """
        if isinstance(value, str) and value.isdigit() and len(value) == 4:
            year = int(value)
            return datetime(year, 1, 1)
        return value

    language: StrOrTuple[LanguageCode] | None = None

    @cached_property
    def languages(self) -> Sequence[LanguageCode]:
        return obj_or_tuple_to_tuple(self.language)

    author: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def authors(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.author, Contributor)

    translator: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def translators(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.translator, Contributor)

    editor: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def editors(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.editor, Contributor)

    artist: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def artists(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.artist, Contributor)

    illustrator: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def illustrators(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.illustrator, Contributor)

    letterer: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def letterers(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.letterer, Contributor)

    penciler: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def pencilers(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.penciler, Contributor)

    colorist: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def colorists(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.colorist, Contributor)

    inker: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def inkers(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.inker, Contributor)

    narrator: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def narrators(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.narrator, Contributor)

    contributor: StrModelOrTuple[ContributorWithRole] | None = None

    @cached_property
    def contributors(self) -> Sequence[ContributorWithRole]:
        return _named_or_sequence_to_sequence(self.contributor, ContributorWithRole)

    publisher: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def publishers(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.publisher, Contributor)

    imprint: StrModelOrTuple[Contributor] | None = None

    @cached_property
    def imprints(self) -> Sequence[Contributor]:
        return _named_or_sequence_to_sequence(self.imprint, Contributor)

    subject: StrModelOrTuple[Subject] | None = None

    @cached_property
    def subjects(self) -> Sequence[Subject]:
        return _named_or_sequence_to_sequence(self.subject, Subject)

    layout: Literal["fixed", "reflowable", "scrolled"] | None = None
    reading_progression: Literal["ltr", "rtl"] = Field(
        "ltr", alias="readingProgression"
    )
    description: str | None = None
    duration: PositiveFloat | None = None
    number_of_pages: PositiveInt | None = Field(None, alias="numberOfPages")
    abridged: bool | None = None

    belongs_to: BelongsTo = Field(default_factory=BelongsTo, alias="belongsTo")

    presentation: PresentationProperties = Field(default_factory=PresentationProperties)

    # Ellibs provides an empty list if there's no data available.
    accessibility: Accessibility | list | None = None

    @field_validator("accessibility", mode="before")
    def check_accessibility_type(cls, value):
        if isinstance(value, dict):
            return Accessibility(**value)
        return None


class LinkRelations(str, Enum):
    """
    https://readium.org/webpub-manifest/relationships.html
    """

    alternate = "alternate"
    contents = "contents"
    cover = "cover"
    manifest = "manifest"
    search = "search"
    self = "self"
