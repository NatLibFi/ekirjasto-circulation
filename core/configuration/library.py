from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

import wcag_contrast_ratio
from pydantic import (
    ConstrainedFloat,
    EmailStr,
    HttpUrl,
    PositiveFloat,
    PositiveInt,
    root_validator,
    validator,
)
from pydantic.fields import ModelField
from sqlalchemy.orm import Session

from api.admin.problem_details import INVALID_CONFIGURATION_OPTION, UNKNOWN_LANGUAGE
from core.entrypoint import EntryPoint
from core.facets import FacetConstants
from core.integration.settings import (
    BaseSettings,
    ConfigurationFormItem,
    ConfigurationFormItemType,
    FormField,
    SettingsValidationError,
)
from core.util import LanguageCodes


class PercentFloat(ConstrainedFloat):
    ge = 0
    le = 1


# The "level" property determines which admins will be able to modify the
# setting. Level 1 settings can be modified by anyone. Level 2 settings can be
# modified only by library managers and system admins (i.e. not by librarians).
# Level 3 settings can be changed only by system admins. If no level is
# specified, the setting will be treated as Level 1 by default.
class Level(IntEnum):
    ALL_ACCESS = 1
    SYS_ADMIN_OR_MANAGER = 2
    SYS_ADMIN_ONLY = 3


@dataclass(frozen=True)
class LibraryConfFormItem(ConfigurationFormItem):
    category: str = "Basic Information"
    level: Level = Level.ALL_ACCESS
    read_only: bool | None = None
    skip: bool | None = None
    paired: str | None = None
    default_library_only: bool | None = False
    non_default_library_only: bool | None = False

    def to_dict(
        self, db: Session, key: str, required: bool = False, default: Any = None
    ) -> tuple[int, dict[str, Any]]:
        """Serialize additional form items specific to library settings."""
        weight, item = super().to_dict(db, key, required, default)
        item["category"] = self.category
        item["level"] = self.level
        if self.read_only is not None:
            item["readOnly"] = self.read_only
        if self.skip is not None:
            item["skip"] = self.skip
        if self.paired is not None:
            item["paired"] = self.paired

        # Finland, hiding settings for default or other libraries
        if self.default_library_only is not None:
            item["defaultLibraryOnly"] = self.default_library_only
        if self.non_default_library_only is not None:
            item["nonDefaultLibraryOnly"] = self.non_default_library_only

        if (
            "default" in item
            and isinstance(item["default"], list)
            and len(item["default"]) == 0
        ):
            del item["default"]

        return weight, item


class LibrarySettings(BaseSettings):
    _additional_form_fields = {
        "name": LibraryConfFormItem(
            label="Name",
            description="The human-readable name of this library.",
            category="Basic Information",
            level=Level.SYS_ADMIN_ONLY,
            required=True,
            weight=-1,
        ),
        "short_name": LibraryConfFormItem(
            label="Short name",
            description="A short name of this library, to use when identifying it "
            "in scripts or URLs, e.g. 'NYPL'.",
            category="Basic Information",
            level=Level.SYS_ADMIN_ONLY,
            required=True,
            weight=-1,
        ),
        "logo": LibraryConfFormItem(
            label="Logo image",
            description="The image should be in GIF, PNG, or JPG format, approximately square, no larger than "
            "135x135 pixels, and look good on a light or dark mode background. "
            "Larger images will be accepted, but scaled down (maintaining aspect ratio) such that "
            "the longest dimension does not exceed 135 pixels.",
            category="Client Interface Customization",
            type=ConfigurationFormItemType.IMAGE,
            level=Level.ALL_ACCESS,
        ),
        "announcements": LibraryConfFormItem(
            label="Scheduled announcements",
            description="Announcements will be displayed to authenticated patrons.",
            type=ConfigurationFormItemType.ANNOUNCEMENTS,
            category="Announcements",
            level=Level.ALL_ACCESS,
        ),
    }

    website: HttpUrl = FormField(
        ...,
        form=LibraryConfFormItem(
            label="URL of the library's website",
            description='The library\'s main website, e.g. "https://www.nypl.org/" '
            "(not this Circulation Manager's URL).",
            category="Basic Information",
            level=Level.SYS_ADMIN_ONLY,
        ),
    )
    # Finland
    kirkanta_consortium_slug: str | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Consortium",
            description="If selected, the municipalities are kept automatically in sync with Kirkanta.",
            category="Kirkanta Synchronization",
            type=ConfigurationFormItemType.SELECT,
            non_default_library_only=True,
            # The keys here should match the Kirkanta consortium slug
            # See https://api.kirjastot.fi/v4/consortium?limit=9999
            options={
                "disabled": "None (don't sync)",
                "kirkkonummi": "Kirkkonummi",
                "fredrika": "Fredrikabiblioteken",
                "siilinjarvikoha": "Siilinjärvi",
                "ratamo-kirjastot": "RATAMO-kirjastot",
                "keski-kirjastot": "Keski-kirjastot",
                "sotkamo": "Sotkamo",
                "kyyti": "Kyyti",
                "joki-kirjastot": "Joki-kirjastot",
                "blanka": "Blanka",
                "lapin-kirjasto": "Lapin kirjasto",
                "toki": "TOKI-kirjastot",
                "leppavirta": "Leppävirta",
                "loisto": "Loisto",
                "rutakko": "Rutakko-kirjastot",
                "lumme": "Lumme-kirjastot",
                "tritonia-2": "Tritonia",
                "helle": "Helle-kirjastot",
                "satakirjastot": "Satakirjastot",
                "anders": "Anders",
                "somero": "Somero",
                "sulkava2": "Sulkava",
                "kirkes-kirjastot": "Kirkes-kirjastot",
                "vaara-kirjastot": "Vaara-kirjastot",
                "lukki-kirjastot": "Lukki-kirjastot",
                "heili": "Heili-kirjastot",
                "kuopionkirjasto": "Kuopion kaupunginkirjasto",
                "piki": "PIKI-kirjastot",
                "louna-kirjastot": "Louna-kirjastot",
                "stepkoulutus-kirjastot": "STEP-koulutus, kirjastot",
                "eepos": "Eepos-kirjastot",
                "vaasa": "Vaasa",
                "lastu": "Lastu",
                "kansallisgalleria-kirjasto": "Kansallisgalleria / Kirjasto",
                "outi": "OUTI-kirjastot",
                "helmet": "Helmet",
                "vaski-kirjastot": "Vaski-kirjastot",
                "kainet": "Kainet-kirjastot",
                "vanamo": "Vanamo",
            },
            level=Level.SYS_ADMIN_ONLY,
        ),
        alias="kirkanta_consortium_slug",
    )
    # Finland
    municipalities: list[str] | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="The municipalities belonging to this consortium",
            type=ConfigurationFormItemType.LIST,
            non_default_library_only=True,
            description="Each value should be a valid "
            '<a href="https://koodistopalvelu.kanta.fi/codeserver/pages/classification-view-page.xhtml?classificationKey=362&versionKey=440" target="_blank">'
            "municipality code</a>. This list is populated automatically if the consortium is selected.",
            category="Kirkanta Synchronization",
            level=Level.ALL_ACCESS,
        ),
        alias="municipalities",
    )
    allow_holds: bool = FormField(
        True,
        form=LibraryConfFormItem(
            label="Allow books to be put on hold",
            type=ConfigurationFormItemType.SELECT,
            options={
                "true": "Allow holds",
                "false": "Disable holds",
            },
            category="Loans, Holds, & Fines",
            level=Level.SYS_ADMIN_ONLY,
            default_library_only=True,
        ),
    )
    enabled_entry_points: list[str] = FormField(
        [x.INTERNAL_NAME for x in EntryPoint.DEFAULT_ENABLED],
        form=LibraryConfFormItem(
            label="Enabled entry points",
            description="Patrons will see the selected entry points at the "
            "top level and in search results. <p>Currently supported "
            "audiobook vendors: Bibliotheca, Axis 360",
            type=ConfigurationFormItemType.MENU,
            options={
                entrypoint.INTERNAL_NAME: str(
                    EntryPoint.LOCALIZED_DISPLAY_TITLES[entrypoint]
                )
                for entrypoint in EntryPoint.ENTRY_POINTS
            },
            category="Lanes & Filters",
            format="narrow",
            read_only=True,
            level=Level.SYS_ADMIN_ONLY,
            default_library_only=True,
        ),
    )
    featured_lane_size: PositiveInt = FormField(
        15,
        form=LibraryConfFormItem(
            label="Maximum number of books in the 'featured' lanes",
            category="Lanes & Filters",
            level=Level.ALL_ACCESS,
            default_library_only=True,
        ),
    )
    minimum_featured_quality: PercentFloat = FormField(
        0.65,
        form=LibraryConfFormItem(
            label="Minimum quality for books that show up in 'featured' lanes",
            description="Between 0 and 1.",
            category="Lanes & Filters",
            level=Level.ALL_ACCESS,
            default_library_only=True,
        ),
    )
    facets_enabled_order: list[str] = FormField(
        FacetConstants.DEFAULT_ENABLED_FACETS[FacetConstants.ORDER_FACET_GROUP_NAME],
        form=LibraryConfFormItem(
            label="Allow patrons to sort by",
            type=ConfigurationFormItemType.MENU,
            options={
                facet: FacetConstants.FACET_DISPLAY_TITLES[facet]
                for facet in FacetConstants.ORDER_FACETS
            },
            category="Lanes & Filters",
            paired="facets_default_order",
            level=Level.SYS_ADMIN_OR_MANAGER,
            default_library_only=True,
        ),
    )
    facets_default_order: str = FormField(
        FacetConstants.ORDER_AUTHOR,
        form=LibraryConfFormItem(
            label="Default Sort by",
            type=ConfigurationFormItemType.SELECT,
            options={
                facet: FacetConstants.FACET_DISPLAY_TITLES[facet]
                for facet in FacetConstants.ORDER_FACETS
            },
            category="Lanes & Filters",
            skip=True,
            default_library_only=True,
        ),
    )
    facets_enabled_available: list[str] = FormField(
        FacetConstants.DEFAULT_ENABLED_FACETS[
            FacetConstants.AVAILABILITY_FACET_GROUP_NAME
        ],
        form=LibraryConfFormItem(
            label="Allow patrons to filter availability to",
            type=ConfigurationFormItemType.MENU,
            options={
                facet: FacetConstants.FACET_DISPLAY_TITLES[facet]
                for facet in FacetConstants.AVAILABILITY_FACETS
            },
            category="Lanes & Filters",
            paired="facets_default_available",
            level=Level.SYS_ADMIN_OR_MANAGER,
            default_library_only=True,
        ),
    )
    facets_default_available: str = FormField(
        FacetConstants.AVAILABLE_ALL,
        form=LibraryConfFormItem(
            label="Default Availability",
            type=ConfigurationFormItemType.SELECT,
            options={
                facet: FacetConstants.FACET_DISPLAY_TITLES[facet]
                for facet in FacetConstants.AVAILABILITY_FACETS
            },
            category="Lanes & Filters",
            skip=True,
            default_library_only=True,
        ),
    )
    facets_enabled_collection: list[str] = FormField(
        FacetConstants.DEFAULT_ENABLED_FACETS[
            FacetConstants.COLLECTION_FACET_GROUP_NAME
        ],
        form=LibraryConfFormItem(
            label="Allow patrons to filter collection to",
            type=ConfigurationFormItemType.MENU,
            options={
                facet: FacetConstants.FACET_DISPLAY_TITLES[facet]
                for facet in FacetConstants.COLLECTION_FACETS
            },
            category="Lanes & Filters",
            paired="facets_default_collection",
            level=Level.SYS_ADMIN_OR_MANAGER,
            default_library_only=True,
        ),
    )
    facets_default_collection: str = FormField(
        FacetConstants.COLLECTION_FULL,
        form=LibraryConfFormItem(
            label="Default Collection",
            type=ConfigurationFormItemType.SELECT,
            options={
                facet: FacetConstants.FACET_DISPLAY_TITLES[facet]
                for facet in FacetConstants.COLLECTION_FACETS
            },
            category="Lanes & Filters",
            skip=True,
            default_library_only=True,
        ),
    )
    # Finland
    facets_enabled_language: list[str] = FormField(
        FacetConstants.DEFAULT_ENABLED_FACETS[FacetConstants.LANGUAGE_FACET_GROUP_NAME],
        form=LibraryConfFormItem(
            label="Allow patrons to filter language to",
            type=ConfigurationFormItemType.MENU,
            options={
                facet: FacetConstants.FACET_DISPLAY_TITLES[facet]
                for facet in FacetConstants.LANGUAGE_FACETS
            },
            category="Lanes & Filters",
            paired="facets_default_language",
            level=Level.SYS_ADMIN_OR_MANAGER,
            default_library_only=True,
        ),
    )
    facets_default_language: str = FormField(
        FacetConstants.LANGUAGE_ALL,
        form=LibraryConfFormItem(
            label="Default Language",
            type=ConfigurationFormItemType.SELECT,
            options={
                facet: FacetConstants.FACET_DISPLAY_TITLES[facet]
                for facet in FacetConstants.LANGUAGE_FACETS
            },
            category="Lanes & Filters",
            skip=True,
            default_library_only=True,
        ),
    )
    facets_enabled_dynamic: list[str] = FormField(
        list(FacetConstants.FACET_DISPLAY_TITLES_DYNAMIC.keys()),
        form=LibraryConfFormItem(
            label="Allow patrons to filter by dynamic facets",
            type=ConfigurationFormItemType.MENU,
            options={
                facet: FacetConstants.GROUP_DISPLAY_TITLES[facet]
                for facet in FacetConstants.FACET_DISPLAY_TITLES_DYNAMIC.keys()
            },
            category="Lanes & Filters",
            read_only=True,
            format="narrow",
            level=Level.SYS_ADMIN_ONLY,
            default_library_only=True,
        ),
    )
    library_description: str | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="A short description of this library",
            description="This will be shown to people who aren't sure they've chosen the right library.",
            category="Basic Information",
            level=Level.SYS_ADMIN_ONLY,
        ),
    )
    help_email: EmailStr | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Patron support email address",
            description="An email address a patron can use if they need help, "
            "e.g. 'palacehelp@yourlibrary.org'.",
            category="Basic Information",
            level=Level.ALL_ACCESS,
        ),
        alias="help-email",
    )
    help_web: HttpUrl | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Patron support website",
            description="A URL for patrons to get help. Either this field or "
            "patron support email address must be provided.",
            category="Basic Information",
            level=Level.ALL_ACCESS,
        ),
        alias="help-web",
    )
    copyright_designated_agent_email_address: EmailStr | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Copyright designated agent email",
            description="Patrons of this library should use this email "
            "address to send a DMCA notification (or other copyright "
            "complaint) to the library.<br/>If no value is specified here, "
            "the general patron support address will be used.",
            category="Patron Support",
            level=Level.SYS_ADMIN_OR_MANAGER,
        ),
    )
    configuration_contact_email_address: EmailStr | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="A point of contact for the organization responsible for configuring this library",
            description="This email address will be shared as part of "
            "integrations that you set up through this interface. It will not "
            "be shared with the general public. This gives the administrator "
            "of the remote integration a way to contact you about problems with "
            "this library's use of that integration.<br/>If no value is specified here, "
            "the general patron support address will be used.",
            category="Patron Support",
            level=Level.SYS_ADMIN_OR_MANAGER,
        ),
    )
    default_notification_email_address: EmailStr = FormField(
        "noreply@thepalaceproject.org",
        form=LibraryConfFormItem(
            label="Write-only email address for vendor hold notifications",
            description="This address must trash all email sent to it. Vendor hold notifications "
            "contain sensitive patron information, but "
            '<a href="https://confluence.nypl.org/display/SIM/About+Hold+Notifications" target="_blank">'
            "cannot be forwarded to patrons</a> because they contain vendor-specific instructions."
            "<br/>The default address will work, but for greater security, set up your own address that "
            "trashes all incoming email.",
            level=Level.SYS_ADMIN_OR_MANAGER,
            default_library_only=True,
        ),
    )
    color_scheme: str = FormField(
        "blue",
        form=LibraryConfFormItem(
            label="Mobile color scheme",
            description="This tells mobile applications what color scheme to use when rendering "
            "this library's OPDS feed.",
            type=ConfigurationFormItemType.SELECT,
            options={
                "amber": "Amber",
                "black": "Black",
                "blue": "Blue",
                "bluegray": "Blue Gray",
                "brown": "Brown",
                "cyan": "Cyan",
                "darkorange": "Dark Orange",
                "darkpurple": "Dark Purple",
                "green": "Green",
                "gray": "Gray",
                "indigo": "Indigo",
                "lightblue": "Light Blue",
                "orange": "Orange",
                "pink": "Pink",
                "purple": "Purple",
                "red": "Red",
                "teal": "Teal",
            },
            category="Client Interface Customization",
            level=Level.SYS_ADMIN_OR_MANAGER,
            default_library_only=True,
        ),
    )
    web_primary_color: str = FormField(
        "#377F8B",
        form=LibraryConfFormItem(
            label="Web primary color",
            description="This is the brand primary color for the web application. "
            "Must have sufficient contrast with white.",
            category="Client Interface Customization",
            type=ConfigurationFormItemType.COLOR,
            level=Level.SYS_ADMIN_OR_MANAGER,
            default_library_only=True,
        ),
        alias="web-primary-color",
    )
    web_secondary_color: str = FormField(
        "#D53F34",
        form=LibraryConfFormItem(
            label="Web secondary color",
            description="This is the brand secondary color for the web application. "
            "Must have sufficient contrast with white.",
            category="Client Interface Customization",
            type=ConfigurationFormItemType.COLOR,
            level=Level.SYS_ADMIN_OR_MANAGER,
            default_library_only=True,
        ),
        alias="web-secondary-color",
    )
    web_css_file: HttpUrl | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Custom CSS file for web",
            description="Give web applications a CSS file to customize the catalog display.",
            category="Client Interface Customization",
            level=Level.SYS_ADMIN_ONLY,
            default_library_only=True,
        ),
        alias="web-css-file",
    )
    web_header_links: list[str] = FormField(
        [],
        form=LibraryConfFormItem(
            label="Web header links",
            description="This gives web applications a list of links to display in the header. "
            "Specify labels for each link in the same order under 'Web header labels'.",
            category="Client Interface Customization",
            type=ConfigurationFormItemType.LIST,
            level=Level.SYS_ADMIN_OR_MANAGER,
            default_library_only=True,
        ),
        alias="web-header-links",
    )
    web_header_labels: list[str] = FormField(
        [],
        form=LibraryConfFormItem(
            label="Web header labels",
            description="Labels for each link under 'Web header links'.",
            category="Client Interface Customization",
            type=ConfigurationFormItemType.LIST,
            level=Level.SYS_ADMIN_OR_MANAGER,
            default_library_only=True,
        ),
        alias="web-header-labels",
    )
    hidden_content_types: list[str] = FormField(
        [],
        form=LibraryConfFormItem(
            label="Hidden content types",
            description="A list of content types to hide from all clients, e.g. "
            "<code>application/pdf</code>. This can be left blank except to "
            "solve specific problems.",
            category="Client Interface Customization",
            type=ConfigurationFormItemType.LIST,
            level=Level.SYS_ADMIN_ONLY,
        ),
    )
    max_outstanding_fines: PositiveFloat | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Maximum amount in fines a patron can have before losing lending privileges",
            category="Loans, Holds, & Fines",
            level=Level.ALL_ACCESS,
        ),
    )
    loan_limit: PositiveInt | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Maximum number of books a patron can have on loan at once",
            description="Note: depending on distributor settings, a patron may be able to exceed "
            "the limit by checking out books directly from a distributor's app. They may also get "
            "a limit exceeded error before they reach these limits if a distributor has a smaller limit.",
            category="Loans, Holds, & Fines",
            level=Level.ALL_ACCESS,
        ),
    )
    hold_limit: PositiveInt | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Maximum number of books a patron can have on hold at once",
            description="Note: depending on distributor settings, a patron may be able to exceed "
            "the limit by placing holds directly from a distributor's app. They may also get "
            "a limit exceeded error before they reach these limits if a distributor has a smaller limit.",
            category="Loans, Holds, & Fines",
            level=Level.ALL_ACCESS,
        ),
    )
    terms_of_service: HttpUrl | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Terms of service URL",
            category="Links",
            level=Level.ALL_ACCESS,
        ),
        alias="terms-of-service",
    )
    privacy_policy: HttpUrl | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Privacy policy URL",
            category="Links",
            level=Level.ALL_ACCESS,
        ),
        alias="privacy-policy",
    )
    copyright: HttpUrl | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Copyright URL",
            category="Links",
            level=Level.SYS_ADMIN_OR_MANAGER,
        ),
    )
    about: HttpUrl | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="About URL",
            category="Links",
            level=Level.ALL_ACCESS,
        ),
    )
    license: HttpUrl | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="License URL",
            category="Links",
            level=Level.SYS_ADMIN_OR_MANAGER,
        ),
    )
    registration_url: HttpUrl | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Patron registration URL",
            description="A URL where someone who doesn't have a library card yet can sign up for one.",
            category="Patron Support",
            level=Level.ALL_ACCESS,
        ),
        alias="register",
    )
    patron_password_reset: HttpUrl | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Password Reset Link",
            description="A link to a web page where a user can reset their virtual library card password",
            category="Patron Support",
            level=Level.SYS_ADMIN_ONLY,
        ),
        alias="http://librarysimplified.org/terms/rel/patron-password-reset",
    )
    large_collection_languages: list[str] | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="The primary languages represented in this library's collection",
            type=ConfigurationFormItemType.LIST,
            format="language-code",
            description="Each value can be either the full name of a language or an "
            '<a href="https://www.loc.gov/standards/iso639-2/php/code_list.php" target="_blank">'
            "ISO-639-2</a> language code.",
            category="Languages",
            level=Level.ALL_ACCESS,
            default_library_only=True,
        ),
        alias="large_collections",
    )
    small_collection_languages: list[str] | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Other major languages represented in this library's collection",
            type=ConfigurationFormItemType.LIST,
            format="language-code",
            description="Each value can be either the full name of a language or an "
            '<a href="https://www.loc.gov/standards/iso639-2/php/code_list.php" target="_blank">'
            "ISO-639-2</a> language code.",
            category="Languages",
            level=Level.ALL_ACCESS,
            default_library_only=True,
        ),
        alias="small_collections",
    )
    tiny_collection_languages: list[str] | None = FormField(
        None,
        form=LibraryConfFormItem(
            label="Other languages in this library's collection",
            type=ConfigurationFormItemType.LIST,
            format="language-code",
            description="Each value can be either the full name of a language or an "
            '<a href="https://www.loc.gov/standards/iso639-2/php/code_list.php" target="_blank">'
            "ISO-639-2</a> language code.",
            category="Languages",
            level=Level.ALL_ACCESS,
            default_library_only=True,
        ),
        alias="tiny_collections",
    )

    @root_validator
    def validate_header_links(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Verify that header links and labels are the same length."""
        header_links = values.get("web_header_links")
        header_labels = values.get("web_header_labels")
        if header_links and header_labels and len(header_links) != len(header_labels):
            raise SettingsValidationError(
                problem_detail=INVALID_CONFIGURATION_OPTION.detailed(
                    "There must be the same number of web header links and web header labels."
                )
            )
        return values

    @validator("web_primary_color", "web_secondary_color")
    def validate_web_color_contrast(cls, value: str, field: ModelField) -> str:
        """
        Verify that the web primary and secondary color both contrast
        well on white, as these colors will serve as button backgrounds with
        white test, as well as text color on white backgrounds.
        """

        def hex_to_rgb(hex: str) -> tuple[float, ...]:
            hex = hex.lstrip("#")
            return tuple(int(hex[i : i + 2], 16) / 255.0 for i in (0, 2, 4))

        passes = wcag_contrast_ratio.passes_AA(
            wcag_contrast_ratio.rgb(hex_to_rgb(value), hex_to_rgb("#ffffff"))
        )
        if not passes:
            check_url = (
                "https://contrast-ratio.com/#%23"
                + value[1:]
                + "-on-%23"
                + "#ffffff"[1:]
            )
            field_label = cls.get_form_field_label(field.name)
            raise SettingsValidationError(
                problem_detail=INVALID_CONFIGURATION_OPTION.detailed(
                    f"The {field_label} doesn't have enough contrast to pass the WCAG 2.0 AA guidelines and "
                    f"will be difficult for some patrons to read. Check contrast <a href='{check_url}' "
                    "target='_blank'>here</a>."
                )
            )
        return value

    @validator(
        "municipalities",
    )
    def validate_municipalities(
        cls, value: list[str] | None, field: ModelField
    ) -> list[str] | None:
        """Verify that municipality IDs are valid."""
        if not value:
            return value

        for code in value:
            if not code.isdigit():
                raise SettingsValidationError(
                    problem_detail=UNKNOWN_LANGUAGE.detailed(
                        f'"{cls.get_form_field_label(field.name)}": "{code}" is not a valid language code.'
                    )
                )

        return cls._remove_duplicates(value)

    @classmethod
    def _remove_duplicates(cls, values: list[str]):
        return list(set(values))

    @validator(
        "large_collection_languages",
        "small_collection_languages",
        "tiny_collection_languages",
    )
    def validate_language_codes(
        cls, value: list[str] | None, field: ModelField
    ) -> list[str] | None:
        """Verify that collection languages are valid."""
        if value is not None:
            languages = []
            for language in value:
                validated_language = LanguageCodes.string_to_alpha_3(language)
                if validated_language is None:
                    field_label = cls.get_form_field_label(field.name)
                    raise SettingsValidationError(
                        problem_detail=UNKNOWN_LANGUAGE.detailed(
                            f'"{field_label}": "{language}" is not a valid language code.'
                        )
                    )
                if validated_language not in languages:
                    languages.append(validated_language)
            return languages
        return value

    def merge_with(self, other_instance: LibrarySettings) -> LibrarySettings:
        """Where there is no value for an attribute, use value from the other instance"""
        merged_attrs = self.__dict__.copy()
        for key, value in other_instance.__dict__.items():
            if value is not None and getattr(self, key, None) is None:
                merged_attrs[key] = value
        return LibrarySettings(**merged_attrs)
