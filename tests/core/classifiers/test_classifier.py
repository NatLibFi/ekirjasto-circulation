"""Test logic surrounding classification schemes."""


import pytest
from psycopg2.extras import NumericRange

from core import classifier
from core.classifier import (
    DeMarqueClassifier,
    GenreData,
    Lowercased,
    SchemaAudienceClassifier,
    SubjectClassifier,
    WorkClassifier,
    fiction_genres,
    nonfiction_genres,
)
from core.classifier.age import AgeClassifier, GradeLevelClassifier
from core.classifier.simplified import SimplifiedGenreClassifier
from core.model import DataSource, Genre, Identifier, Subject, Work
from tests.fixtures.database import DatabaseTransactionFixture

genres = dict()
GenreData.populate(globals(), genres, fiction_genres, nonfiction_genres)


class TestLowercased:
    def test_constructor(self):
        l = Lowercased("A string")

        # A string is lowercased.
        assert l == "a string"

        # A Lowercased object is returned rather than creating a new
        # object.
        assert l is Lowercased(l)

        # A number such as a Dewey Decimal number is converted to a string.
        assert Lowercased(301) == "301"

        # A trailing period is removed.
        l = Lowercased("A string.")
        assert l == "a string"

        # The original value is still available.
        assert l.original == "A string."


class TestGenreData:
    def test_fiction_default(self):
        # In general, genres are restricted to either fiction or
        # nonfiction.
        assert Science_Fiction.is_fiction == True
        assert Science.is_fiction == False


class TestClassifier:
    def test_default_target_age_for_audience(self):
        assert SubjectClassifier.default_target_age_for_audience(
            SubjectClassifier.AUDIENCE_CHILDREN
        ) == (0, 12)
        assert SubjectClassifier.default_target_age_for_audience(
            SubjectClassifier.AUDIENCE_YOUNG_ADULT
        ) == (13, 17)
        assert SubjectClassifier.default_target_age_for_audience(
            SubjectClassifier.AUDIENCE_ADULT
        ) == (18, None)
        assert SubjectClassifier.default_target_age_for_audience(
            SubjectClassifier.AUDIENCE_ADULTS_ONLY
        ) == (18, None)
        assert SubjectClassifier.default_target_age_for_audience(
            SubjectClassifier.AUDIENCE_ALL_AGES
        ) == (8, None)

    def test_default_audience_for_target_age(self):
        def aud(low, high, expect):
            assert expect == SubjectClassifier.default_audience_for_target_age(
                (low, high)
            )

        assert SubjectClassifier.default_audience_for_target_age(None) == None
        aud(None, None, None)
        aud(None, 17, SubjectClassifier.AUDIENCE_YOUNG_ADULT)
        aud(None, 4, SubjectClassifier.AUDIENCE_CHILDREN)
        aud(None, 44, SubjectClassifier.AUDIENCE_ADULT)
        aud(18, 44, SubjectClassifier.AUDIENCE_ADULT)
        aud(14, 14, SubjectClassifier.AUDIENCE_YOUNG_ADULT)
        aud(14, 19, SubjectClassifier.AUDIENCE_YOUNG_ADULT)
        aud(2, 14, SubjectClassifier.AUDIENCE_CHILDREN)
        aud(2, 8, SubjectClassifier.AUDIENCE_CHILDREN)

        # We treat this as YA because its target age range overlaps
        # our YA age range, and many external sources consider books
        # for twelve-year-olds to be "YA".
        aud(12, 15, SubjectClassifier.AUDIENCE_YOUNG_ADULT)

        # Whereas this is unambiguously 'Children' as far as we're concerned.
        aud(12, 13, SubjectClassifier.AUDIENCE_CHILDREN)

        # All ages for audiences that are younger than the "all ages
        # age cutoff" and older than the "adult age cutoff".
        aud(5, 18, SubjectClassifier.AUDIENCE_ALL_AGES)
        aud(5, 25, SubjectClassifier.AUDIENCE_ALL_AGES)

    def test_and_up(self):
        """Test the code that determines what "x and up" actually means."""

        def u(young, keyword):
            return SubjectClassifier.and_up(young, keyword)

        assert u(None, None) == None
        assert u(6, "6 years old only") == None
        assert u(3, "3 and up") == 5
        assert u(6, "6+") == 8
        assert u(8, "8+") == 12
        assert u(10, "10+") == 14
        assert u(12, "12 and up") == 17
        assert u(14, "14+.") == 17
        assert u(18, "18+") == 18

    def test_scrub_identifier_can_override_name(self):
        """Test the ability of scrub_identifier to override the name
        of the subject for classification purposes.

        This is used e.g. in the BISACClassifier to ensure that a known BISAC
        code is always mapped to its canonical name.
        """

        class SetsNameForOneIdentifier(SubjectClassifier):
            "A Classifier that insists on a certain name for one specific identifier"

            @classmethod
            def scrub_identifier(self, identifier):
                if identifier == "A":
                    return ("A", "Use this name!")
                else:
                    return identifier

            @classmethod
            def scrub_name(self, name):
                """This verifies that the override name still gets passed
                into scrub_name.
                """
                return name.upper()

        m = SetsNameForOneIdentifier.scrub_identifier_and_name
        assert m("A", "name a") == ("A", "USE THIS NAME!")
        assert m("B", "name b") == ("B", "NAME B")

    def test_scrub_identifier(self):
        m = SubjectClassifier.scrub_identifier
        assert m(None) == None
        assert m("Foo") == Lowercased("Foo")

    def test_scrub_name(self):
        m = SubjectClassifier.scrub_name
        assert m(None) == None
        assert m("Foo") == Lowercased("Foo")


class TestClassifierLookup:
    def test_lookup(self):
        assert (
            SubjectClassifier.lookup(SubjectClassifier.GRADE_LEVEL)
            == GradeLevelClassifier
        )
        assert (
            SubjectClassifier.lookup(SubjectClassifier.SCHEMA_AGE_RANGE)
            == AgeClassifier
        )
        assert (
            SubjectClassifier.lookup(SubjectClassifier.SCHEMA_AUDIENCE)
            == SchemaAudienceClassifier
        )
        assert (
            SubjectClassifier.lookup(SubjectClassifier.DEMARQUE) == DeMarqueClassifier
        )
        assert SubjectClassifier.lookup("no-such-key") == None


class TestNestedSubgenres:
    def test_parents(self):
        assert list(classifier.Romantic_Suspense.parents) == [classifier.Romance]

        # eq_([classifier.Crime_Thrillers_Mystery, classifier.Mystery],
        #    list(classifier.Police_Procedurals.parents))

    def test_self_and_subgenres(self):
        assert set(list(classifier.Fantasy.self_and_subgenres)) == {
            classifier.Fantasy,
            classifier.Epic_Fantasy,
            classifier.Historical_Fantasy,
            classifier.Urban_Fantasy,
        }


class TestSchemaAudienceClassifier:
    def test_audience(self):
        def audience(aud):
            # The second param, `name`, is not used in the audience method
            return SchemaAudienceClassifier.audience(aud, None)

        for val in ["children", "pre-adolescent", "beginning reader"]:
            assert SubjectClassifier.AUDIENCE_CHILDREN == audience(val)

        for val in [
            "young adult",
            "ya",
            "teenagers",
            "adolescent",
            "early adolescents",
        ]:
            assert SubjectClassifier.AUDIENCE_YOUNG_ADULT == audience(val)

        assert audience("adult") == SubjectClassifier.AUDIENCE_ADULT
        assert audience("adults only") == SubjectClassifier.AUDIENCE_ADULTS_ONLY
        assert audience("all ages") == SubjectClassifier.AUDIENCE_ALL_AGES

        assert audience("books for all ages") == None

    def test_target_age(self):
        def target_age(age):
            return SchemaAudienceClassifier.target_age(age, None)

        assert target_age("beginning reader") == (5, 8)
        assert target_age("pre-adolescent") == (9, 12)
        assert target_age("all ages") == (SubjectClassifier.ALL_AGES_AGE_CUTOFF, None)

        assert target_age("babies") == (None, None)


class TestWorkClassifierFixture:
    work: Work
    identifier: Identifier
    classifier: WorkClassifier
    transaction: DatabaseTransactionFixture


@pytest.fixture()
def work_classifier_fixture(
    db,
) -> TestWorkClassifierFixture:
    fix = TestWorkClassifierFixture()
    fix.transaction = db
    fix.work = db.work(with_license_pool=True)
    fix.identifier = fix.work.presentation_edition.primary_identifier
    fix.classifier = WorkClassifier(fix.work, test_session=db.session)
    return fix


class TestWorkClassifier:
    def _genre(self, db, genre_data):
        expected_genre, ignore = Genre.lookup(db, genre_data.name)
        return expected_genre

    def test_workclassifier_no_assumptions(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        """If we have no data whatsoever, we make no assumptions
        about a work's classification.
        """
        work = work_classifier_fixture
        assert work.classifier._fiction() == None
        assert work.classifier._genres(None) == ([], None)
        assert work.classifier._audience() == None
        assert work.classifier._target_age(None) == (None, None)

    def test_prepare_classification_audience(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        source = DataSource.lookup(session, DataSource.OVERDRIVE)

        # 1. schema:audience and READ is Children
        c1 = work.identifier.identifier_to_subject(
            source, Subject.SCHEMA_AUDIENCE, "Children"
        )
        work.classifier.prepare_classification(c1)
        assert work.classifier.audience_counts[SubjectClassifier.AUDIENCE_CHILDREN] == 1
        c2 = work.identifier.identifier_to_subject(source, Subject.DEMARQUE, "READ0001")
        work.classifier.prepare_classification(c2)
        assert work.classifier.audience_counts[SubjectClassifier.AUDIENCE_CHILDREN] == 2
        # BISAC adds to its own counting
        c3 = work.identifier.identifier_to_subject(
            source, Subject.BISAC, "JUVENILE FICTION / General"
        )
        work.classifier.prepare_classification(c3)
        assert work.classifier.audience_counts["BISAC Children"] == 1

        # 2. schema:audience and READ is Young Adult

        c4 = work.identifier.identifier_to_subject(
            source, Subject.SCHEMA_AUDIENCE, "Young Adult"
        )
        work.classifier.prepare_classification(c4)
        assert (
            work.classifier.audience_counts[SubjectClassifier.AUDIENCE_YOUNG_ADULT] == 1
        )

        c5 = work.identifier.identifier_to_subject(source, Subject.DEMARQUE, "READ0004")
        work.classifier.prepare_classification(c5)
        assert (
            work.classifier.audience_counts[SubjectClassifier.AUDIENCE_YOUNG_ADULT] == 2
        )

        # BISAC adds to its own counting

        c6 = work.identifier.identifier_to_subject(
            source, Subject.BISAC, "YOUNG ADULT FICTION / Fantasy / General"
        )
        work.classifier.prepare_classification(c6)
        assert work.classifier.audience_counts["BISAC ya"] == 1

        # 3. schema:audience, READ, or BISAC is Adult

        c7 = work.identifier.identifier_to_subject(
            source, Subject.SCHEMA_AUDIENCE, "Adult"
        )
        work.classifier.prepare_classification(c7)
        assert work.classifier.audience_counts[SubjectClassifier.AUDIENCE_ADULT] == 1

        c8 = work.identifier.identifier_to_subject(source, Subject.DEMARQUE, "READ0000")
        work.classifier.prepare_classification(c8)
        assert work.classifier.audience_counts[SubjectClassifier.AUDIENCE_ADULT] == 2

        # BISAC adds to its own counting

        c9 = work.identifier.identifier_to_subject(
            source, Subject.BISAC, "MEDICAL / Dermatology"
        )
        work.classifier.prepare_classification(c9)
        assert work.classifier.audience_counts["BISAC Adult"] == 1

        # Any other subject does not add to audience counting

        c10 = work.identifier.identifier_to_subject(source, Subject.TAG, "Adult")
        work.classifier.prepare_classification(c10)
        assert work.classifier.audience_counts[SubjectClassifier.AUDIENCE_ADULT] == 2

    def test_prepare_classification_genres(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        source = DataSource.lookup(session, DataSource.OVERDRIVE)

        # BISAC adds to genres
        c1 = work.identifier.identifier_to_subject(
            source, Subject.BISAC, "MEDICAL / Dermatology"
        )
        work.classifier.prepare_classification(c1)
        assert len(work.classifier.genre_list) == 1

        # Any other subject does not
        c2 = work.identifier.identifier_to_subject(
            source, Subject.SCHEMA_AUDIENCE, "Children"
        )
        work.classifier.prepare_classification(c2)
        assert len(work.classifier.genre_list) == 1

        c3 = work.identifier.identifier_to_subject(
            source, Subject.TAG, "Juvenile Whatever Fiction"
        )
        work.classifier.prepare_classification(c3)
        assert len(work.classifier.genre_list) == 1

    def test_prepare_classification_target_age(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        source = DataSource.lookup(session, DataSource.OVERDRIVE)

        # schema:typicalAgeRange does not set target age
        c1 = work.identifier.identifier_to_subject(
            source, Subject.SCHEMA_AGE_RANGE, "0-12"
        )
        work.classifier.prepare_classification(c1)
        assert not work.classifier.target_age_lower
        assert not work.classifier.target_age_upper == 12

        c2 = work.identifier.identifier_to_subject(source, Subject.DEMARQUE, "READ0001")
        work.classifier.prepare_classification(c2)
        assert work.classifier.target_age_lower == 0
        assert work.classifier.target_age_upper == 3

        # ...or BISAC (only a few of them)
        c3 = work.identifier.identifier_to_subject(
            source, Subject.BISAC, "JUVENILE FICTION / General"
        )
        work.classifier.prepare_classification(c3)
        assert work.classifier.target_age_lower == 0
        assert work.classifier.target_age_upper == 3

    def test_prepare_classification_duplicate_classification_ignored(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        """A given classification is only used once from
        a given data source.
        """
        work = work_classifier_fixture
        session = work.transaction.session
        history = self._genre(session, classifier.History)
        i = work.identifier
        source = DataSource.lookup(session, DataSource.AMAZON)

        c1 = i.identifier_to_subject(source, Subject.BISAC, "HISTORY / General")
        work.classifier.prepare_classification(c1)
        assert len(work.classifier.genre_list) == 1

        c2 = i.identifier_to_subject(source, Subject.BISAC, "HISTORY / General")
        work.classifier.prepare_classification(c2)
        assert len(work.classifier.genre_list) == 1

        # The same classification can come in from another data source and
        # it will be taken into consideration.

        source2 = DataSource.lookup(session, DataSource.OCLC_LINKED_DATA)
        c3 = i.identifier_to_subject(source2, Subject.BISAC, "HISTORY / General")
        work.classifier.prepare_classification(c3)
        assert len(work.classifier.genre_list) == 2

    def test_prepare_classification_staff_genre_overrides_others(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        """Staff classification should remove any other classifications."""
        work = work_classifier_fixture
        session = work.transaction.session
        genre1, is_new = Genre.lookup(session, "Psychology")
        genre2, is_new = Genre.lookup(session, "Cooking")
        subject1 = work.transaction.subject(
            type=SubjectClassifier.BISAC, identifier="PSY000000"
        )
        subject1.genre = genre1
        subject2 = work.transaction.subject(
            type=Subject.SIMPLIFIED_GENRE, identifier="Cooking"
        )
        subject2.genre = genre2
        source = DataSource.lookup(session, DataSource.AXIS_360)
        staff_source = DataSource.lookup(session, DataSource.LIBRARY_STAFF)
        classification1 = work.transaction.classification(
            identifier=work.identifier, subject=subject1, data_source=source
        )
        classification2 = work.transaction.classification(
            identifier=work.identifier,
            subject=subject2,
            data_source=staff_source,
        )
        work.classifier.prepare_classification(classification1)
        work.classifier.prepare_classification(classification2)
        (genres, fiction, audience, target_age) = work.classifier.classify_work()
        assert [genre.name for genre in genres] == [genre2.name]

    def test_prepare_classification_staff_none_genre_overrides_others(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        """If staff has removed a classification from the admin UI, it or any other genresshouldn't be counted in anymore."""
        work = work_classifier_fixture
        session = work.transaction.session
        source = DataSource.lookup(session, DataSource.AXIS_360)
        staff_source = DataSource.lookup(session, DataSource.LIBRARY_STAFF)
        genre1, is_new = Genre.lookup(session, "Poetry")
        subject1 = work.transaction.subject(
            type=Subject.SIMPLIFIED_GENRE, identifier="Poetry"
        )
        subject1.genre = genre1
        classification1 = work.transaction.classification(
            identifier=work.identifier, subject=subject1, data_source=source
        )
        subject2 = work.transaction.subject(
            type=Subject.SIMPLIFIED_GENRE, identifier=SimplifiedGenreClassifier.NONE
        )
        classification2 = work.transaction.classification(
            identifier=work.identifier,
            subject=subject2,
            data_source=staff_source,
        )
        work.classifier.prepare_classification(classification1)
        work.classifier.prepare_classification(classification2)
        (genres, fiction, audience, target_age) = work.classifier.classify_work()
        assert len(genres) == 0

    def test_prepare_classification_staff_fiction_overrides_others(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        source = DataSource.lookup(session, DataSource.AXIS_360)
        staff_source = DataSource.lookup(session, DataSource.LIBRARY_STAFF)
        subject1 = work.transaction.subject(type="type1", identifier="Cooking")
        subject1.fiction = False
        subject2 = work.transaction.subject(type="type2", identifier="Psychology")
        subject2.fiction = False
        subject3 = work.transaction.subject(
            type=Subject.SIMPLIFIED_FICTION_STATUS, identifier="Fiction"
        )
        classification1 = work.transaction.classification(
            identifier=work.identifier, subject=subject1, data_source=source
        )
        classification2 = work.transaction.classification(
            identifier=work.identifier, subject=subject2, data_source=source
        )
        classification3 = work.transaction.classification(
            identifier=work.identifier,
            subject=subject3,
            data_source=staff_source,
        )
        work.classifier.prepare_classification(classification1)
        work.classifier.prepare_classification(classification2)
        work.classifier.prepare_classification(classification3)
        (genres, fiction, audience, target_age) = work.classifier.classify_work()
        assert fiction == True

    def test_prepare_classification_staff_audience_overrides_others(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        pool = work.transaction.licensepool(None, data_source_name=DataSource.AXIS_360)
        license_source = pool.data_source
        staff_source = DataSource.lookup(session, DataSource.LIBRARY_STAFF)
        subject1 = work.transaction.subject(type="type1", identifier="subject1")
        subject1.audience = "Adult"
        subject2 = work.transaction.subject(type="type2", identifier="subject2")
        subject2.audience = "Adult"
        subject3 = work.transaction.subject(
            type=Subject.SCHEMA_AUDIENCE, identifier="Children"
        )
        classification1 = work.transaction.classification(
            identifier=pool.identifier,
            subject=subject1,
            data_source=license_source,
        )
        classification2 = work.transaction.classification(
            identifier=pool.identifier,
            subject=subject2,
            data_source=license_source,
        )
        classification3 = work.transaction.classification(
            identifier=pool.identifier,
            subject=subject3,
            data_source=staff_source,
        )
        work.classifier.prepare_classification(classification1)
        work.classifier.prepare_classification(classification2)
        work.classifier.prepare_classification(classification3)
        (genres, fiction, audience, target_age) = work.classifier.classify_work()
        assert audience == "Children"

    def test_prepare_classification_staff_target_age_overrides_others(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        source = DataSource.lookup(session, DataSource.AXIS_360)
        staff_source = DataSource.lookup(session, DataSource.LIBRARY_STAFF)
        source1 = DataSource.lookup(session, DataSource.AXIS_360)
        # There's always an audience
        subject = work.transaction.subject(
            type=Subject.SCHEMA_AUDIENCE, identifier="Children"
        )
        classification1 = work.transaction.classification(
            identifier=work.identifier,
            subject=subject,
            data_source=source1,
        )
        subject1 = work.transaction.subject(
            type=Subject.SCHEMA_AGE_RANGE, identifier="6-8"
        )
        subject2 = work.transaction.subject(
            type=Subject.SCHEMA_AGE_RANGE, identifier="10-13"
        )
        classification2 = work.transaction.classification(
            identifier=work.identifier, subject=subject1, data_source=source
        )
        classification3 = work.transaction.classification(
            identifier=work.identifier,
            subject=subject2,
            data_source=staff_source,
        )
        work.classifier.prepare_classification(classification1)
        work.classifier.prepare_classification(classification2)
        work.classifier.prepare_classification(classification3)

        (genres, fiction, audience, target_age) = work.classifier.classify_work()
        assert target_age == (10, 13)

    def test_prepare_classification_staff_not_inclusive_target_age(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        staff_source = DataSource.lookup(session, DataSource.LIBRARY_STAFF)
        subject = work.transaction.subject(
            type=Subject.SCHEMA_AGE_RANGE, identifier="10-12"
        )
        subject.target_age = NumericRange(9, 13, "()")
        classification = work.transaction.classification(
            identifier=work.identifier,
            subject=subject,
            data_source=staff_source,
        )
        work.classifier.prepare_classification(classification)

        # There's also an audience
        source1 = DataSource.lookup(session, DataSource.AXIS_360)
        subject1 = work.transaction.subject(
            type=Subject.SCHEMA_AUDIENCE, identifier="Young Adult"
        )
        classification1 = work.transaction.classification(
            identifier=work.identifier,
            subject=subject1,
            data_source=source1,
        )
        work.classifier.prepare_classification(classification1)

        work.classifier.prepare_classification(classification)
        (genres, fiction, audience, target_age) = work.classifier.classify_work()
        assert target_age == (10, 12)

    def test_audience_childrens_book_when_more_than_ya(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture

        # This is most like from BISACs and no schema:audience was available.
        # There's a bit more children's than YA counts.
        work.classifier.audience_counts = {
            SubjectClassifier.AUDIENCE_YOUNG_ADULT: 1,
            SubjectClassifier.AUDIENCE_CHILDREN: 2,
        }
        assert work.classifier._audience() == SubjectClassifier.AUDIENCE_CHILDREN

    def test_audience_ya_book_when_childrens_and_ya(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        # Ellibs often contain both audiences in their children+ya books.
        work.classifier.audience_counts = {
            SubjectClassifier.AUDIENCE_CHILDREN: 1,
            SubjectClassifier.AUDIENCE_YOUNG_ADULT: 1,
        }
        assert work.classifier._audience() == SubjectClassifier.AUDIENCE_YOUNG_ADULT

    def test_audience_genre_may_restrict_audience(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        # The audience info says this is a YA book.
        work.classifier.audience_counts = {SubjectClassifier.AUDIENCE_YOUNG_ADULT: 1}

        # Without any genre information, it's classified as YA.
        assert work.classifier._audience() == SubjectClassifier.AUDIENCE_YOUNG_ADULT

        # But if it's Erotica, it is always classified as Adults Only.
        genres = {classifier.Erotica: 1, classifier.Science_Fiction: 2}
        assert (
            work.classifier._audience(genres) == SubjectClassifier.AUDIENCE_ADULTS_ONLY
        )

    def test_audience_all_ages(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        # There are counts for all audiences, most likely these have come from BISACs
        # and no schema:audience info was provided.
        work.classifier.audience_counts = {
            SubjectClassifier.AUDIENCE_ADULT: 1,
            SubjectClassifier.AUDIENCE_CHILDREN: 3,
            SubjectClassifier.AUDIENCE_YOUNG_ADULT: 2,
        }
        assert work.classifier._audience() == SubjectClassifier.AUDIENCE_ALL_AGES

    def test_audience_adults(self, work_classifier_fixture: TestWorkClassifierFixture):
        work = work_classifier_fixture
        work.classifier.audience_counts = {
            SubjectClassifier.AUDIENCE_ADULT: 1,
        }
        assert work.classifier._audience() == SubjectClassifier.AUDIENCE_ADULT

    def test_audience_no_information_results_in_none(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        work.classifier.audience_counts = {}
        assert work.classifier._audience() == None

    def test_target_age_from_bisac_children(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session

        # Children's book
        overdrive = DataSource.lookup(session, DataSource.OVERDRIVE)
        c1 = work.identifier.identifier_to_subject(
            overdrive, Subject.SCHEMA_AUDIENCE, "Children"
        )
        work.classifier.prepare_classification(c1)

        # And there's also an age range defined
        oclc = DataSource.lookup(session, DataSource.OCLC)
        c2 = work.identifier.identifier_to_subject(
            oclc, Subject.BISAC, "JUV043000", "JUVENILE FICTION / Readers / Beginner"
        )
        work.classifier.prepare_classification(c2)
        audience = work.classifier._audience()
        assert audience == SubjectClassifier.AUDIENCE_CHILDREN
        assert work.classifier._target_age(audience) == (0, 4)

    def test_target_age_children(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session

        # Children's book
        overdrive = DataSource.lookup(session, DataSource.OVERDRIVE)
        c1 = work.identifier.identifier_to_subject(
            overdrive, Subject.DEMARQUE, "READ0001"
        )
        work.classifier.prepare_classification(c1)

        audience = work.classifier._audience()
        assert audience == SubjectClassifier.AUDIENCE_CHILDREN
        assert work.classifier._target_age(audience) == (0, 3)

    def test_target_age_children_and_adult_subjects(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        overdrive = DataSource.lookup(session, DataSource.OVERDRIVE)

        c2 = work.identifier.identifier_to_subject(
            overdrive, Subject.DEMARQUE, "READ0001"
        )
        work.classifier.prepare_classification(c2)
        c3 = work.identifier.identifier_to_subject(
            overdrive, Subject.BISAC, "FIC000000", "Fiction / General"
        )
        work.classifier.prepare_classification(c3)

        audience = work.classifier._audience()
        assert audience == SubjectClassifier.AUDIENCE_CHILDREN
        assert work.classifier._target_age(audience) == (0, 3)

    def test_target_age_two_reads(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        overdrive = DataSource.lookup(session, DataSource.OVERDRIVE)

        c5 = work.identifier.identifier_to_subject(
            overdrive, Subject.DEMARQUE, "READ0002"
        )
        work.classifier.prepare_classification(c5)

        c4 = work.identifier.identifier_to_subject(
            overdrive, Subject.DEMARQUE, "READ0001"
        )
        work.classifier.prepare_classification(c4)

        audience = work.classifier._audience()
        assert audience == SubjectClassifier.AUDIENCE_CHILDREN
        assert work.classifier._target_age(audience) == (0, 7)

    def test_target_age_ya(self, work_classifier_fixture: TestWorkClassifierFixture):
        work = work_classifier_fixture
        session = work.transaction.session

        # De Marque YA book
        overdrive = DataSource.lookup(session, DataSource.OVERDRIVE)
        c1 = work.identifier.identifier_to_subject(
            overdrive, Subject.DEMARQUE, "READ0004"
        )
        work.classifier.prepare_classification(c1)

        audience = work.classifier._audience()
        assert audience == SubjectClassifier.AUDIENCE_YOUNG_ADULT
        age = work.classifier._target_age(audience)
        assert age == (13, 17)

    def test_target_age_ya_no_upper(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        overdrive = DataSource.lookup(session, DataSource.OVERDRIVE)

        c2 = work.identifier.identifier_to_subject(
            overdrive, Subject.DEMARQUE, "READ0005"
        )
        work.classifier.prepare_classification(c2)

        audience = work.classifier._audience()
        assert audience == SubjectClassifier.AUDIENCE_YOUNG_ADULT
        age = work.classifier._target_age(audience)
        assert age == (17, None)

    def test_target_age_adult(self, work_classifier_fixture: TestWorkClassifierFixture):
        work = work_classifier_fixture
        session = work.transaction.session

        # Adult
        overdrive = DataSource.lookup(session, DataSource.OVERDRIVE)
        c1 = work.identifier.identifier_to_subject(
            overdrive, Subject.SCHEMA_AUDIENCE, "Adult"
        )
        work.classifier.prepare_classification(c1)
        audience = work.classifier._audience()
        assert audience == SubjectClassifier.AUDIENCE_ADULT
        # No target age information results in default adult age range
        assert work.classifier._target_age(audience) == (18, None)

    def test_fiction_status_restricts_genre(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session

        fiction_genre = self._genre(session, classifier.Science_Fiction)
        nonfiction_genre = self._genre(session, classifier.History)
        work.classifier.genre_list = [fiction_genre, nonfiction_genre]
        # Fiction counts have also been accumulated. It's a tie.
        work.classifier.fiction_counts[True] = 1
        work.classifier.fiction_counts[False] = 1

        # But any given book is either fiction or nonfiction. We lean towards
        # fiction so any other genres are deleted.
        genres, fiction = work.classifier._genres(True)
        assert fiction == True
        assert genres == [fiction_genre]

        # Even if we have more than one nonfiction genre, we still classify
        # the book as fiction.
        fiction_genre = self._genre(session, classifier.Science_Fiction)
        nonfiction_genre = self._genre(session, classifier.History)
        nonfiction_genre2 = self._genre(session, classifier.Education)
        work.classifier.genre_list = [
            nonfiction_genre2,
            nonfiction_genre,
            fiction_genre,
        ]
        genres, fiction = work.classifier._genres(True)
        assert fiction == True
        assert genres == [fiction_genre]

    def test_fiction_genre_changes_fiction_status(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        # There's two nonfiction genres
        nonfiction_genre = self._genre(session, classifier.History)
        nonfiction_genre2 = self._genre(session, classifier.Education)
        work.classifier.genre_list = [nonfiction_genre2, nonfiction_genre]
        # This should not really happen anymore but, theoretically, for some reason, fiction()
        # has returned True.
        genres, fiction = work.classifier._genres(True)
        # Because both genres conflicted with the fiction status, the first
        # was removed but the second one stayed and it changed the fiction status.
        assert fiction == False
        assert len(genres) == 1

    def test_classify_work_juvenile_classification_is_split_between_children_and_ya(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        # schema:audience can have both Children and YA. At the moment,
        # we classify them as Children's.
        i = work.identifier
        source = DataSource.lookup(session, DataSource.OCLC)
        c = i.identifier_to_subject(source, Subject.SCHEMA_AUDIENCE, "Children")
        work.classifier.prepare_classification(c)
        c2 = i.identifier_to_subject(source, Subject.SCHEMA_AUDIENCE, "Young Adult")
        work.classifier.prepare_classification(c2)

        # (This classification has no bearing on audience and its
        # weight will be ignored.)
        c3 = i.identifier_to_subject(source, Subject.BISAC, "Pets")
        work.classifier.prepare_classification(c3)
        genres, fiction, audience, target_age = work.classifier.classify_work()

        # Young Adult wins because we err on the side of showing books
        # to kids who are a bit too young, rather than too old.
        assert SubjectClassifier.AUDIENCE_YOUNG_ADULT == audience

        # But behind the scenes, more is going on. The count of the
        # classifier has been added up with 1 for both.
        counts = work.classifier.audience_counts
        assert counts[SubjectClassifier.AUDIENCE_YOUNG_ADULT] == 1
        assert counts[SubjectClassifier.AUDIENCE_CHILDREN] == 1

    def test_classify_work_uses_default_fiction_status(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        genres, fiction, audience, target_age = work.classifier.classify_work(
            default_fiction=True
        )
        assert fiction == True

        genres, fiction, audience, target_age = work.classifier.classify_work(
            default_fiction=False
        )
        assert fiction == False

        genres, fiction, audience, target_age = work.classifier.classify_work(
            default_fiction=None
        )
        assert fiction == None

        # The default isn't used if there's any information about the fiction status.
        work.classifier.fiction_counts[False] = 1
        genres, fiction, audience, target_age = work.classifier.classify_work(
            default_fiction=None
        )
        assert fiction == False

    def test_classify_work_uses_default_audience(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        genres, fiction, audience, target_age = work.classifier.classify_work()
        assert audience == None

        genres, fiction, audience, target_age = work.classifier.classify_work(
            default_audience=SubjectClassifier.AUDIENCE_ADULT
        )
        assert audience == SubjectClassifier.AUDIENCE_ADULT

        genres, fiction, audience, target_age = work.classifier.classify_work(
            default_audience=SubjectClassifier.AUDIENCE_CHILDREN
        )
        assert audience == SubjectClassifier.AUDIENCE_CHILDREN

        # The default isn't used if there's any information about the audience.
        work.classifier.audience_counts[SubjectClassifier.AUDIENCE_ADULT] = 1
        genres, fiction, audience, target_age = work.classifier.classify_work(
            default_audience=None
        )
        assert audience == SubjectClassifier.AUDIENCE_ADULT

    def test_classify_work(self, work_classifier_fixture: TestWorkClassifierFixture):
        work = work_classifier_fixture
        session = work.transaction.session
        # At this point we've tested all the components of classify, so just
        # do an overall test to verify that classify_work() returns a 4-tuple
        # (genres, fiction, audience, target_age)

        work.work.presentation_edition.title = (
            "Science Fiction: A Comprehensive History"
        )
        i = work.identifier
        source = DataSource.lookup(session, DataSource.OVERDRIVE)
        c1 = i.identifier_to_subject(source, Subject.BISAC, "HISTORY / General")
        c2 = i.identifier_to_subject(
            source, Subject.BISAC, "FICTION / Science Fiction / General"
        )
        c3 = i.identifier_to_subject(source, Subject.SCHEMA_AUDIENCE, "Young Adult")
        for classification in i.classifications:
            work.classifier.prepare_classification(classification)

        genres, fiction, audience, target_age = work.classifier.classify_work()

        assert genres[0].name == "Science Fiction"
        assert fiction == True
        assert audience == SubjectClassifier.AUDIENCE_YOUNG_ADULT
        assert target_age == (13, 17)

    def test_classify_work_no_genre_when_not_ekirjasto_known_subject(
        self, work_classifier_fixture: TestWorkClassifierFixture
    ):
        work = work_classifier_fixture
        session = work.transaction.session
        # At this point we've tested all the components of classify, so just
        # do an overall test to verify that classify_work() returns a 4-tuple
        # (genres, fiction, audience, target_age)

        work.work.presentation_edition.title = (
            "Science Fiction: A Comprehensive History"
        )
        i = work.identifier
        source = DataSource.lookup(session, DataSource.OVERDRIVE)
        c1 = i.identifier_to_subject(source, Subject.TAG, "HISTORY")
        for classification in i.classifications:
            work.classifier.prepare_classification(classification)

        genres, fiction, audience, target_age = work.classifier.classify_work()

        assert len(genres) == 0
        assert fiction == None
        assert audience == None
        assert target_age == (None, None)
