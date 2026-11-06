from __future__ import annotations

import gettext
import os
import re
import urllib.parse
from collections.abc import Mapping
from datetime import datetime

import pytz
from pymarc import Field, Record, Subfield
from sqlalchemy.orm.session import Session

from core.classifier import SubjectClassifier
from core.model import (
    Contributor,
    DeliveryMechanism,
    Edition,
    Identifier,
    LicensePool,
    Representation,
    Work,
)
from core.util import LanguageCodes
from core.util.datetime_helpers import utc_now
from core.util.log import LoggerMixin

# Finna needs Finnish data.
localedir = os.path.join(os.path.dirname(__file__), "../../translations")
try:
    fi = gettext.translation("messages", localedir=localedir, languages=["fi"])
    fi.install()
    _ = fi.gettext
except FileNotFoundError:
    # Fallback: use the default (no translation)
    _ = lambda x: x  # type: ignore[assignment]


class Annotator(LoggerMixin):
    """The Annotator knows how to add information about a Work to
    a MARC record."""

    # From https://www.loc.gov/standards/valuelist/marctarget.html
    AUDIENCE_TERMS: Mapping[str, str] = {
        SubjectClassifier.AUDIENCE_CHILDREN: _(SubjectClassifier.AUDIENCE_CHILDREN),
        SubjectClassifier.AUDIENCE_YOUNG_ADULT: _(
            SubjectClassifier.AUDIENCE_YOUNG_ADULT
        ),
        SubjectClassifier.AUDIENCE_ADULTS_ONLY: _(
            SubjectClassifier.AUDIENCE_ADULTS_ONLY
        ),
        SubjectClassifier.AUDIENCE_ADULT: _(SubjectClassifier.AUDIENCE_ADULT),
    }

    # TODO: Add remaining formats. Maybe there's a better place to
    # store this so it's easier to keep up-to-date.
    # There doesn't seem to be any particular vocabulary for this.
    FORMAT_TERMS: Mapping[tuple[str | None, str | None], str] = {
        (
            Representation.EPUB_MEDIA_TYPE,
            DeliveryMechanism.LCP_DRM,
        ): "LCP-suojattu EPUB e-kirja",
        (
            Representation.PDF_MEDIA_TYPE,
            DeliveryMechanism.LCP_DRM,
        ): "LCP-suojattu PDF e-kirja",
        (
            Representation.AUDIOBOOK_PACKAGE_LCP_MEDIA_TYPE,
            DeliveryMechanism.LCP_DRM,
        ): "LCP-suojattu äänikirja",
        (
            DeliveryMechanism.EKIRJASTO_STREAMING_PROFILE,
            DeliveryMechanism.LCP_DRM,
        ): "LCP-suojattu EPUB striimattava e-kirja",
    }

    def __init__(
        self,
        cm_url: str,
        library_short_name: str,
        web_client_urls: list[str],
        organization_code: str | None,
        *,
        modified_at: datetime | None = None,
        entered_at: datetime | None = None,
        isbn_identifiers: Mapping[int, Identifier | None] | None = None,
    ) -> None:
        """Configure MARC links, optional fields, and precomputed harvest metadata.

        Optional timestamps override the modification and entry dates in control
        fields 005 and 008. An ISBN mapping supplies resolved ISBNs by publication
        identifier ID; missing entries skip ISBN output without fallback queries.
        """
        self.cm_url = cm_url
        self.library_short_name = library_short_name
        self.web_client_urls = web_client_urls
        self.organization_code = organization_code
        self.modified_at = modified_at
        self.entered_at = entered_at
        self.isbn_identifiers = isbn_identifiers

    def annotate_work_record(
        self,
        revised: bool,
        work: Work,
        active_license_pool: LicensePool,
        edition: Edition,
        identifier: Identifier,
    ) -> Record:
        """Add metadata from this work to a MARC record.

        :param revised: Whether this record is being revised.
        :param work: The Work whose record is being annotated.
        :param active_license_pool: Of all the LicensePools associated with this
           Work, the client has expressed interest in this one.
        :param edition: The Edition to use when associating bibliographic
           metadata with this entry.
        :param identifier: Of all the Identifiers associated with this
           Work, the client has expressed interest in this one.

        :return: A pymarc Record object.
        """
        record = Record(leader=self.leader(revised), force_utf8=True)
        self.add_control_fields(record, identifier, active_license_pool, edition)
        # Batch callers can provide resolved ISBNs to avoid per-record queries.
        if self.isbn_identifiers is None:
            self.add_isbn(record, identifier)
        elif identifier.id is not None and (
            isbn := self.isbn_identifiers.get(identifier.id)
        ):
            self.add_isbn(record, isbn)
        # Harvest dates keep control fields stable across repeated requests.
        if self.modified_at is not None:
            record["005"].data = self.modified_at.astimezone(pytz.UTC).strftime(
                "%Y%m%d%H%M%S.0"
            )
        if self.entered_at is not None:
            # Replace only the entry date; retain the remaining fixed-length data.
            record["008"].data = (
                self.entered_at.astimezone(pytz.UTC).strftime("%y%m%d")
                + record["008"].data[6:]
            )

        # TODO: The 240 and 130 fields are for translated works, so they can be grouped even
        #  though they have different titles. We do not group editions of the same work in
        #  different languages, so we can't use those yet.

        self.add_title(record, edition)
        self.add_contributors(record, edition)
        self.add_publisher(record, edition)
        self.add_physical_description(record, edition)
        self.add_audience(record, work)
        self.add_series(record, edition)
        self.add_formats(record, active_license_pool)

        if self.organization_code:
            self.add_marc_organization_code(record, self.organization_code)

        self.add_summary(record, work)
        self.add_genres(record, work)

        self.add_keywords(record, edition)

        self.add_web_client_urls(
            record,
            identifier,
            self.library_short_name,
            self.cm_url,
            self.web_client_urls,
        )

        return record

    @classmethod
    def leader(cls, revised: bool) -> str:
        # The record length is automatically updated once fields are added.
        initial_record_length = "00000"

        if revised:
            record_status = "c"  # Corrected or revised
        else:
            record_status = "n"  # New record

        # Distributors consistently seem to use type "a" - language material - for
        # ebooks, though there is also type "m" for computer files.
        record_type = "a"
        bibliographic_level = "m"  # Monograph/item

        leader = (
            initial_record_length + record_status + record_type + bibliographic_level
        )
        # Additional information about the record that's always the same.
        leader += "  2200000   4500"
        return leader

    @classmethod
    def add_control_fields(
        cls, record: Record, identifier: Identifier, pool: LicensePool, edition: Edition
    ) -> None:
        # Leader/06 identifies the general type of material. The default leader
        # uses `a` (language material), which is correct for e-books but not for
        # audiobooks (`i`, non-musical sound recording).
        if edition.medium == Edition.AUDIO_MEDIUM:
            record.leader = record.leader[:6] + "i" + record.leader[7:]

        # Unique identifier for this record.
        record.add_field(Field(tag="001", data=identifier.urn))

        # Field 003 (MARC organization code) is library-specific, so it's added separately.

        record.add_field(Field(tag="005", data=utc_now().strftime("%Y%m%d%H%M%S.0")))

        # Field 006: m = computer file, d = the file is a document
        record.add_field(Field(tag="006", data="m        d        "))

        # Field 007: more details about the material.
        if edition.medium == Edition.AUDIO_MEDIUM:
            # s = sound recording, r = remote/online resource. The remaining
            # positions are not known for the online audiobook.
            field_007 = "sr" + "|" * 12
        else:
            # c = electronic resource, r = remote/online resource.
            # Since this depends on the pool, it might be better not to cache it.
            # But it's probably not a huge problem if it's outdated.
            # File formats: a=one format, m=multiple formats, u=unknown
            if len(pool.delivery_mechanisms) == 1:
                file_formats_code = "a"
            else:
                file_formats_code = "m"
            field_007 = "cr cn ---" + file_formats_code + "nuuu"
        record.add_field(Field(tag="007", data=field_007))

        # Field 008 (fixed-length data elements):
        data = utc_now().strftime("%y%m%d")
        publication_date = edition.issued or edition.published
        if publication_date:
            date_type = "s"  # single known date
            # Not using strftime because some years are pre-1900.
            date_value = "%04i" % publication_date.year
        else:
            date_type = "n"  # dates unknown
            date_value = "    "
        data += date_type + date_value
        data += "    "
        # TODO: Start tracking place of publication when available. Since we don't have
        # this yet, assume everything was published in the US.
        data += "xxu"
        data += "                 "
        language = "eng"
        if edition.language:
            language = LanguageCodes.string_to_alpha_3(edition.language)
        data += language
        data += "  "
        record.add_field(Field(tag="008", data=data))

    @classmethod
    def add_marc_organization_code(cls, record: Record, marc_org: str) -> None:
        record.add_field(Field(tag="003", data=marc_org))

    @classmethod
    def add_isbn(cls, record: Record, identifier: Identifier) -> None:
        # Add the ISBN if we have one.
        isbn = None
        if identifier.type == Identifier.ISBN:
            isbn = identifier
        if not isbn:
            _db = Session.object_session(identifier)
            identifier_ids = identifier.equivalent_identifier_ids()[identifier.id]
            isbn = (
                _db.query(Identifier)
                .filter(Identifier.type == Identifier.ISBN)
                .filter(Identifier.id.in_(identifier_ids))
                .order_by(Identifier.id)
                .first()
            )
        if isbn and isbn.identifier:
            record.add_field(
                Field(
                    tag="020",
                    indicators=[" ", " "],
                    subfields=[
                        Subfield(code="a", value=isbn.identifier),
                    ],
                )
            )

    @classmethod
    def add_title(cls, record: Record, edition: Edition) -> None:
        # Non-filing characters are used to indicate when the beginning of a title
        # should not be used in sorting. This code tries to identify them by comparing
        # the title and the sort_title.
        non_filing_characters = 0
        if (
            edition.title != edition.sort_title
            and edition.sort_title is not None
            and ("," in edition.sort_title)
        ):
            stemmed = edition.sort_title[: edition.sort_title.rindex(",")]
            if edition.title is None:
                cls.logger().warning(
                    "Edition %s has a sort title, but no title.", edition.id
                )
                non_filing_characters = 0
            else:
                non_filing_characters = edition.title.index(stemmed)
                # MARC only supports up to 9 non-filing characters, but if we got more
                # something is probably wrong anyway.
                if non_filing_characters > 9:
                    cls.logger().warning(
                        "Edition %s has %s non-filing characters, but MARC only supports up to 9.",
                        edition.id,
                        non_filing_characters,
                    )
                    non_filing_characters = 0

        subfields = [Subfield("a", str(edition.title or ""))]
        if edition.subtitle:
            subfields += [Subfield("b", str(edition.subtitle))]
        if edition.author:
            subfields += [Subfield("c", str(edition.author))]
        record.add_field(
            Field(
                tag="245",
                indicators=["0", str(non_filing_characters)],
                subfields=subfields,
            )
        )

    @classmethod
    def add_contributors(cls, record: Record, edition: Edition) -> None:
        """Create contributor fields for this edition.

        TODO: Use canonical names from LoC.
        """
        primary_author = edition.primary_author

        # If there's one author, use the 100 field.
        if primary_author:
            record.add_field(
                Field(
                    tag="100",
                    indicators=["1", " "],
                    subfields=[
                        Subfield("a", str(primary_author.sort_name)),
                    ],
                )
            )

        if len(edition.contributions) > 1:
            for contribution in edition.contributions:
                # The primary author is already represented by the 100 field.
                # Do not emit the same contribution again as a 700 field.
                if (
                    primary_author
                    and contribution.role == Contributor.Role.PRIMARY_AUTHOR
                    and contribution.contributor == primary_author
                ):
                    continue
                contributor = contribution.contributor
                if contributor.sort_name and contribution.role:
                    record.add_field(
                        Field(
                            tag="700",
                            indicators=["1", " "],
                            subfields=[
                                Subfield("a", str(contributor.sort_name)),
                                Subfield("e", _(contribution.role)),
                            ],
                        )
                    )

    @classmethod
    def add_publisher(cls, record: Record, edition: Edition) -> None:
        if edition.publisher:
            publication_date = edition.issued or edition.published
            year = ""
            if publication_date:
                year = str(publication_date.year)
            record.add_field(
                Field(
                    tag="264",
                    indicators=[" ", "1"],
                    subfields=[
                        Subfield("a", "[Kustannuspaikka tuntematon]"),
                        Subfield("b", str(edition.publisher or "")),
                        Subfield("c", year),
                    ],
                )
            )

    @classmethod
    def add_physical_description(cls, record: Record, edition: Edition) -> None:
        # These 3xx fields are for a physical description of the item.
        if edition.medium == Edition.BOOK_MEDIUM:
            record.add_field(
                Field(
                    tag="300",
                    indicators=[" ", " "],
                    subfields=[
                        Subfield("a", "verkkoaineisto"),
                    ],
                )
            )

            record.add_field(
                Field(
                    tag="336",
                    indicators=[" ", " "],
                    subfields=[
                        Subfield("a", "teksti"),
                        Subfield("b", "txt"),
                        Subfield("2", "rdacontent"),
                    ],
                )
            )
        elif edition.medium == Edition.AUDIO_MEDIUM:
            description = "verkkoaineisto"
            if edition.duration is not None:
                description += f" ({cls.format_duration(float(edition.duration))})"
            record.add_field(
                Field(
                    tag="300",
                    indicators=[" ", " "],
                    subfields=[Subfield("a", description)],
                )
            )
            record.add_field(
                Field(
                    tag="336",
                    indicators=[" ", " "],
                    subfields=[
                        Subfield("a", "puhe"),
                        Subfield("b", "spw"),
                        Subfield("2", "rdacontent"),
                    ],
                )
            )

        record.add_field(
            Field(
                tag="337",
                indicators=[" ", " "],
                subfields=[
                    Subfield("a", "tietokonekäyttöinen"),
                    Subfield("b", "c"),
                    Subfield("2", "rdamedia"),
                ],
            )
        )

        record.add_field(
            Field(
                tag="338",
                indicators=[" ", " "],
                subfields=[
                    Subfield("a", "verkkoaineisto"),
                    Subfield("b", "cr"),
                    Subfield("2", "rdacarrier"),
                ],
            )
        )

        file_type = None
        if edition.medium == Edition.BOOK_MEDIUM:
            file_type = "tekstitiedosto"
        elif edition.medium == Edition.AUDIO_MEDIUM:
            file_type = "äänitiedosto"
        if file_type:
            record.add_field(
                Field(
                    tag="347",
                    indicators=[" ", " "],
                    subfields=[
                        Subfield("a", file_type),
                        Subfield("2", "rda"),
                    ],
                )
            )

        # Form of work
        form = None
        if edition.medium == Edition.BOOK_MEDIUM:
            form = "eBook"
        elif edition.medium == Edition.AUDIO_MEDIUM:
            # This field doesn't seem to be used for audio.
            pass
        if form:
            record.add_field(
                Field(
                    tag="380",
                    indicators=[" ", " "],
                    subfields=[
                        Subfield("a", "e-kirja"),
                        Subfield("2", "tlcgt"),
                    ],
                )
            )

    @staticmethod
    def format_duration(duration: float) -> str:
        """Format an audiobook duration in the units used by MARC 300."""
        seconds = round(duration)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)

        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}min")
        if seconds or not parts:
            parts.append(f"{seconds}s")
        return " ".join(parts)

    @classmethod
    def add_audience(cls, record: Record, work: Work) -> None:
        audience = _(work.audience) or _(  # type:ignore[arg-type]
            SubjectClassifier.AUDIENCE_ADULT
        )
        record.add_field(
            Field(
                tag="385",
                indicators=[" ", " "],
                subfields=[
                    Subfield("a", audience),
                    Subfield("2", "tlctarget"),
                ],
            )
        )

    @classmethod
    def add_series(cls, record: Record, edition: Edition) -> None:
        if edition.series:
            subfields = [Subfield("a", str(edition.series))]
            if edition.series_position:
                subfields.extend([Subfield("v", str(edition.series_position))])
            record.add_field(
                Field(
                    tag="490",
                    indicators=["0", " "],
                    subfields=subfields,
                )
            )

    @classmethod
    def add_formats(cls, record: Record, pool: LicensePool) -> None:
        for lpdm in pool.delivery_mechanisms:
            dm = lpdm.delivery_mechanism
            format = cls.FORMAT_TERMS.get((dm.content_type, dm.drm_scheme))
            if format:
                record.add_field(
                    Field(
                        tag="538",
                        indicators=[" ", " "],
                        subfields=[
                            Subfield("a", format),
                        ],
                    )
                )

    @classmethod
    def add_summary(cls, record: Record, work: Work) -> None:
        summary = work.summary_text
        if summary:
            # E.g. De Marque summaries include html tags.
            stripped = re.sub("<[^>]+?>", " ", summary)
            record.add_field(
                Field(
                    tag="520",
                    indicators=[" ", " "],
                    subfields=[Subfield("a", stripped)],
                )
            )

    @classmethod
    def add_genres(cls, record: Record, work: Work) -> None:
        """Create genre fields for this work."""
        genres = work.genres
        for genre in genres:
            record.add_field(
                Field(
                    tag="653",
                    indicators=[" ", "0"],
                    subfields=[
                        Subfield("a", _(genre.name)),
                    ],
                )
            )

    @classmethod
    def add_keywords(cls, record: Record, edition: Edition) -> None:
        """Create keyword fields for this work."""
        if edition.medium == Edition.AUDIO_MEDIUM:
            record.add_field(
                Field(
                    tag="655",
                    indicators=["1", "7"],
                    subfields=[
                        Subfield("a", _("äänikirja")),
                        Subfield("2", "slm/fin"),
                        Subfield("0", "https://urn.fi/URN:NBN:fi:au:slm:s579"),
                    ],
                )
            )
        if edition.medium == Edition.BOOK_MEDIUM:
            record.add_field(
                Field(
                    tag="655",
                    indicators=["1", "7"],
                    subfields=[
                        Subfield("a", "e-kirja"),
                        Subfield("2", "slm/fin"),
                    ],
                )
            )

    @classmethod
    def add_web_client_urls(
        cls,
        record: Record,
        identifier: Identifier,
        library_short_name: str,
        cm_url: str,
        web_client_urls: list[str],
    ) -> None:
        qualified_identifier = urllib.parse.quote(
            f"{identifier.type}/{identifier.identifier}", safe=""
        )

        for web_client_base_url in web_client_urls:
            link = "{}/{}/works/{}".format(
                cm_url,
                library_short_name,
                qualified_identifier,
            )
            encoded_link = urllib.parse.quote(link, safe="")
            url = f"{web_client_base_url}/book/{encoded_link}"
            record.add_field(
                Field(
                    tag="856",
                    indicators=["4", "0"],
                    subfields=[Subfield(code="u", value=url)],
                )
            )


# Neutral name used by OAI-PMH; kept as an alias for existing callers.
MARCRecordBuilder = Annotator
