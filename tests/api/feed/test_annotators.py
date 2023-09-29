from datetime import timedelta

from core.classifier import Classifier
from core.feed.acquisition import OPDSAcquisitionFeed
from core.feed.annotator.base import Annotator
from core.feed.annotator.circulation import CirculationManagerAnnotator
from core.feed.annotator.verbose import VerboseAnnotator
from core.feed.types import FeedEntryType, Link, WorkEntry
from core.feed.util import strftime
from core.model import tuple_to_numericrange
from core.model.classification import Subject
from core.model.contributor import Contributor
from core.model.datasource import DataSource
from core.model.edition import Edition
from core.model.measurement import Measurement
from core.model.resource import Hyperlink, Resource
from core.model.work import Work
from core.util.datetime_helpers import utc_now
from tests.core.test_opds import TestAnnotatorsFixture, annotators_fixture  # noqa
from tests.fixtures.database import (  # noqa
    DatabaseTransactionFixture,
    DBStatementCounter,
)


class TestAnnotators:
    def test_all_subjects(self, annotators_fixture: TestAnnotatorsFixture):
        data, db, session = (
            annotators_fixture,
            annotators_fixture.db,
            annotators_fixture.session,
        )

        work = db.work(genre="Fiction", with_open_access_download=True)
        edition = work.presentation_edition
        identifier = edition.primary_identifier
        source1 = DataSource.lookup(session, DataSource.GUTENBERG)
        source2 = DataSource.lookup(session, DataSource.OCLC)

        subjects = [
            (source1, Subject.FAST, "fast1", "name1", 1),
            (source1, Subject.LCSH, "lcsh1", "name2", 1),
            (source2, Subject.LCSH, "lcsh1", "name2", 1),
            (source1, Subject.LCSH, "lcsh2", "name3", 3),
            (
                source1,
                Subject.DDC,
                "300",
                "Social sciences, sociology & anthropology",
                1,
            ),
        ]

        for source, subject_type, subject, name, weight in subjects:
            identifier.classify(source, subject_type, subject, name, weight=weight)

        # Mock Work.all_identifier_ids (called by VerboseAnnotator.categories)
        # so we can track the value that was passed in for `cutoff`.
        def mock_all_identifier_ids(policy=None):
            work.called_with_policy = policy
            # Do the actual work so that categories() gets the
            # correct information.
            return work.original_all_identifier_ids(policy)

        work.original_all_identifier_ids = work.all_identifier_ids
        work.all_identifier_ids = mock_all_identifier_ids
        category_tags = VerboseAnnotator.categories(work)

        # When we are generating subjects as part of an OPDS feed, by
        # default we set a cutoff of 100 equivalent identifiers. This
        # gives us reasonable worst-case performance at the cost of
        # not showing every single random subject under which an
        # extremely popular book is filed.
        assert 100 == work.called_with_policy.equivalent_identifier_cutoff

        ddc_uri = Subject.uri_lookup[Subject.DDC]
        rating_value = "ratingValue"
        assert [
            {
                "term": "300",
                rating_value: 1,
                "label": "Social sciences, sociology & anthropology",
            }
        ] == category_tags[ddc_uri]

        fast_uri = Subject.uri_lookup[Subject.FAST]
        assert [{"term": "fast1", "label": "name1", rating_value: 1}] == category_tags[
            fast_uri
        ]

        lcsh_uri = Subject.uri_lookup[Subject.LCSH]
        assert [
            {"term": "lcsh1", "label": "name2", rating_value: 2},
            {"term": "lcsh2", "label": "name3", rating_value: 3},
        ] == sorted(category_tags[lcsh_uri], key=lambda x: x[rating_value])

        genre_uri = Subject.uri_lookup[Subject.SIMPLIFIED_GENRE]
        assert [
            dict(label="Fiction", term=Subject.SIMPLIFIED_GENRE + "Fiction")
        ] == category_tags[genre_uri]

        # Age range assertions
        work = db.work(fiction=False, audience=Classifier.AUDIENCE_CHILDREN)
        work.target_age = tuple_to_numericrange((8, 12))
        categories = Annotator.categories(work)
        assert categories[Subject.SIMPLIFIED_FICTION_STATUS] == [
            dict(
                term=f"{Subject.SIMPLIFIED_FICTION_STATUS}Nonfiction",
                label="Nonfiction",
            )
        ]
        assert categories[Subject.uri_lookup[Subject.AGE_RANGE]] == [
            dict(term=work.target_age_string, label=work.target_age_string)
        ]

    def test_content(self, db: DatabaseTransactionFixture):
        work = db.work()
        work.summary_text = "A Summary"
        assert Annotator.content(work) == "A Summary"

        resrc = Resource()
        db.session.add(resrc)
        resrc.set_fetched_content("text", "Representation Summary", None)

        work.summary = resrc
        work.summary_text = None
        # The resource sets the summary
        assert Annotator.content(work) == "Representation Summary"
        assert work.summary_text == "Representation Summary"

        assert Annotator.content(None) == ""

    def test_appeals(self, annotators_fixture: TestAnnotatorsFixture):
        data, db, session = (
            annotators_fixture,
            annotators_fixture.db,
            annotators_fixture.session,
        )

        work = db.work(with_open_access_download=True)
        work.appeal_language = 0.1
        work.appeal_character = 0.2
        work.appeal_story = 0.3
        work.appeal_setting = 0.4
        work.calculate_opds_entries(verbose=True)

        category_tags = VerboseAnnotator.categories(work)
        appeal_tags = category_tags[Work.APPEALS_URI]
        expect = [
            (Work.APPEALS_URI + Work.LANGUAGE_APPEAL, Work.LANGUAGE_APPEAL, 0.1),
            (Work.APPEALS_URI + Work.CHARACTER_APPEAL, Work.CHARACTER_APPEAL, 0.2),
            (Work.APPEALS_URI + Work.STORY_APPEAL, Work.STORY_APPEAL, 0.3),
            (Work.APPEALS_URI + Work.SETTING_APPEAL, Work.SETTING_APPEAL, 0.4),
        ]
        actual = [(x["term"], x["label"], x["ratingValue"]) for x in appeal_tags]
        assert set(expect) == set(actual)

    def test_authors(self, annotators_fixture: TestAnnotatorsFixture):
        db = annotators_fixture.db
        edition = db.edition()
        [c_orig] = list(edition.contributors)

        c1 = edition.add_contributor("c1", Contributor.AUTHOR_ROLE, _sort_name="c1")
        # No name contributor
        c_none = edition.add_contributor("c2", Contributor.AUTHOR_ROLE)
        c_none.display_name = ""
        c_none._sort_name = ""

        authors = Annotator.authors(edition)
        # The default, c1 and c_none
        assert len(edition.contributions) == 3
        # Only default and c1 are used in the feed, because c_none has no name
        assert len(authors["authors"]) == 2
        assert set(map(lambda x: x.name, authors["authors"])) == {
            c1.sort_name,
            c_orig.sort_name,
        }

    def test_detailed_author(self, annotators_fixture: TestAnnotatorsFixture):
        data, db, session = (
            annotators_fixture,
            annotators_fixture.db,
            annotators_fixture.session,
        )

        c, ignore = db.contributor("Familyname, Givenname")
        c.display_name = "Givenname Familyname"
        c.family_name = "Familyname"
        c.wikipedia_name = "Givenname Familyname (Author)"
        c.viaf = "100"
        c.lc = "n100"

        author = VerboseAnnotator.detailed_author(c)

        assert "Givenname Familyname" == author.name
        assert "Familyname, Givenname" == author.sort_name
        assert "Givenname Familyname (Author)" == author.wikipedia_name
        assert "http://viaf.org/viaf/100" == author.viaf
        assert "http://id.loc.gov/authorities/names/n100" == author.lc

        work = db.work(authors=[], with_license_pool=True)
        work.presentation_edition.add_contributor(c, Contributor.PRIMARY_AUTHOR_ROLE)

        [same_tag] = VerboseAnnotator.authors(work.presentation_edition)["authors"]
        assert same_tag.dict() == author.dict()

    def test_duplicate_author_names_are_ignored(
        self, annotators_fixture: TestAnnotatorsFixture
    ):
        data, db, session = (
            annotators_fixture,
            annotators_fixture.db,
            annotators_fixture.session,
        )

        # Ignores duplicate author names
        work = db.work(with_license_pool=True)
        duplicate = db.contributor()[0]
        duplicate.sort_name = work.author

        edition = work.presentation_edition
        edition.add_contributor(duplicate, Contributor.AUTHOR_ROLE)

        assert 1 == len(Annotator.authors(edition)["authors"])

    def test_all_annotators_mention_every_relevant_author(
        self, annotators_fixture: TestAnnotatorsFixture
    ):
        data, db, session = (
            annotators_fixture,
            annotators_fixture.db,
            annotators_fixture.session,
        )

        work = db.work(authors=[], with_license_pool=True)
        edition = work.presentation_edition

        primary_author, ignore = db.contributor()
        author, ignore = db.contributor()
        illustrator, ignore = db.contributor()
        barrel_washer, ignore = db.contributor()

        edition.add_contributor(primary_author, Contributor.PRIMARY_AUTHOR_ROLE)
        edition.add_contributor(author, Contributor.AUTHOR_ROLE)

        # This contributor is relevant because we have a MARC Role Code
        # for the role.
        edition.add_contributor(illustrator, Contributor.ILLUSTRATOR_ROLE)

        # This contributor is not relevant because we have no MARC
        # Role Code for the role.
        edition.add_contributor(barrel_washer, "Barrel Washer")

        illustrator_code = Contributor.MARC_ROLE_CODES[Contributor.ILLUSTRATOR_ROLE]

        tags = Annotator.authors(edition)
        # We made two <author> tags and one <contributor>
        # tag, for the illustrator.
        assert 2 == len(tags["authors"])
        assert 1 == len(tags["contributors"])
        assert [None, None, illustrator_code] == [
            x.role for x in (tags["authors"] + tags["contributors"])
        ]

        # Verbose annotator only creates author tags
        tags = VerboseAnnotator.authors(edition)
        assert 2 == len(tags["authors"])
        assert 0 == len(tags["contributors"])
        assert [None, None] == [x.role for x in (tags["authors"])]

    def test_ratings(self, annotators_fixture: TestAnnotatorsFixture):
        data, db, session = (
            annotators_fixture,
            annotators_fixture.db,
            annotators_fixture.session,
        )

        work = db.work(with_license_pool=True, with_open_access_download=True)
        work.quality = 1.0 / 3
        work.popularity = 0.25
        work.rating = 0.6
        work.calculate_opds_entries(verbose=True)
        entry = OPDSAcquisitionFeed._create_entry(
            work,
            work.active_license_pool(),
            work.presentation_edition,
            work.presentation_edition.primary_identifier,
            VerboseAnnotator(),
        )
        assert entry.computed is not None

        ratings = [
            (
                getattr(rating, "ratingValue"),
                getattr(rating, "additionalType"),
            )
            for rating in entry.computed.ratings
        ]
        expected = [
            ("0.3333", Measurement.QUALITY),
            ("0.2500", Measurement.POPULARITY),
            ("0.6000", None),
        ]
        assert set(expected) == set(ratings)

    def test_subtitle(self, annotators_fixture: TestAnnotatorsFixture):
        data, db, session = (
            annotators_fixture,
            annotators_fixture.db,
            annotators_fixture.session,
        )

        work = db.work(with_license_pool=True, with_open_access_download=True)
        work.presentation_edition.subtitle = "Return of the Jedi"
        work.calculate_opds_entries()

        feed = OPDSAcquisitionFeed(
            db.fresh_str(),
            db.fresh_url(),
            [work],
            CirculationManagerAnnotator(None),
        )._feed

        computed = feed.entries[0].computed
        assert computed is not None
        assert computed.subtitle is not None
        assert computed.subtitle.text == "Return of the Jedi"

        # If there's no subtitle, the subtitle tag isn't included.
        work.presentation_edition.subtitle = None
        work.calculate_opds_entries()
        feed = OPDSAcquisitionFeed(
            db.fresh_str(),
            db.fresh_url(),
            [work],
            CirculationManagerAnnotator(None),
        )._feed

        computed = feed.entries[0].computed
        assert computed is not None
        assert computed.subtitle == None

    def test_series(self, annotators_fixture: TestAnnotatorsFixture):
        data, db, session = (
            annotators_fixture,
            annotators_fixture.db,
            annotators_fixture.session,
        )

        work = db.work(with_license_pool=True, with_open_access_download=True)
        work.presentation_edition.series = "Harry Otter and the Lifetime of Despair"
        work.presentation_edition.series_position = 4
        work.calculate_opds_entries()

        feed = OPDSAcquisitionFeed(
            db.fresh_str(),
            db.fresh_url(),
            [work],
            CirculationManagerAnnotator(None),
        )._feed
        computed = feed.entries[0].computed
        assert computed is not None

        assert computed.series is not None
        assert computed.series.name == work.presentation_edition.series  # type: ignore[attr-defined]
        assert computed.series.position == str(  # type: ignore[attr-defined]
            work.presentation_edition.series_position
        )

        # The series position can be 0, for a prequel for example.
        work.presentation_edition.series_position = 0
        work.calculate_opds_entries()

        feed = OPDSAcquisitionFeed(
            db.fresh_str(),
            db.fresh_url(),
            [work],
            CirculationManagerAnnotator(None),
        )._feed
        computed = feed.entries[0].computed
        assert computed is not None
        assert computed.series is not None
        assert computed.series.name == work.presentation_edition.series  # type: ignore[attr-defined]
        assert computed.series.position == str(  # type: ignore[attr-defined]
            work.presentation_edition.series_position
        )

        # If there's no series title, the series tag isn't included.
        work.presentation_edition.series = None
        work.calculate_opds_entries()
        feed = OPDSAcquisitionFeed(
            db.fresh_str(),
            db.fresh_url(),
            [work],
            CirculationManagerAnnotator(None),
        )._feed
        computed = feed.entries[0].computed
        assert computed is not None
        assert computed.series == None

        # No series name
        assert Annotator.series(None, "") == None

    def test_samples(self, annotators_fixture: TestAnnotatorsFixture):
        data, db, session = (
            annotators_fixture,
            annotators_fixture.db,
            annotators_fixture.session,
        )

        work = db.work(with_license_pool=True)
        edition = work.presentation_edition

        resource = Resource(url="sampleurl")
        session.add(resource)
        session.commit()

        sample_link = Hyperlink(
            rel=Hyperlink.SAMPLE,
            resource_id=resource.id,
            identifier_id=edition.primary_identifier_id,
            data_source_id=2,
        )
        session.add(sample_link)
        session.commit()

        with DBStatementCounter(db.database.connection) as counter:
            links = Annotator.samples(edition)
            count = counter.count

            assert len(links) == 1
            assert links[0].id == sample_link.id
            assert links[0].resource.url == "sampleurl"
            # accessing resource should not be another query
            assert counter.count == count

        # No edition = No samples
        assert Annotator.samples(None) == []


class TestAnnotator:
    def test_annotate_work_entry(self, db: DatabaseTransactionFixture):
        work = db.work(with_license_pool=True)
        pool = work.active_license_pool()
        edition: Edition = work.presentation_edition
        now = utc_now()

        edition.cover_full_url = "http://coverurl.jpg"
        edition.cover_thumbnail_url = "http://thumburl.gif"
        work.summary_text = "Summary"
        edition.language = None
        work.last_update_time = now
        edition.publisher = "publisher"
        edition.imprint = "imprint"
        edition.issued = utc_now().date()

        # datetime for > today
        pool.availability_time = (utc_now() + timedelta(days=1)).date()

        entry = WorkEntry(
            work=work,
            edition=edition,
            identifier=edition.primary_identifier,
            license_pool=pool,
        )
        Annotator().annotate_work_entry(entry)
        data = entry.computed
        assert data is not None

        # Images
        assert len(data.image_links) == 2
        assert data.image_links[0] == Link(
            href=edition.cover_full_url, rel=Hyperlink.IMAGE, type="image/jpeg"
        )
        assert data.image_links[1] == Link(
            href=edition.cover_thumbnail_url,
            rel=Hyperlink.THUMBNAIL_IMAGE,
            type="image/gif",
        )

        # Other values
        assert data.imprint == FeedEntryType(text="imprint")
        assert data.summary and data.summary.text == "Summary"
        assert data.summary and data.summary.get("type") == "html"
        assert data.publisher == FeedEntryType(text="publisher")
        assert data.issued == edition.issued

        # Missing values
        assert data.language == None
        assert data.updated == FeedEntryType(text=strftime(now))