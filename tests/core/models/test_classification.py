import pytest
from psycopg2.extras import NumericRange
from sqlalchemy.exc import IntegrityError

from core.classifier import SubjectClassifier
from core.model import create
from core.model.classification import Genre, Subject
from tests.fixtures.database import DatabaseTransactionFixture


class TestSubject:
    def test_lookup_errors(self, db: DatabaseTransactionFixture):
        """Subject.lookup will complain if you don't give it
        enough information to find a Subject.
        """
        with pytest.raises(ValueError) as excinfo:
            Subject.lookup(db.session, None, "identifier", "name")
        assert "Cannot look up Subject with no type." in str(excinfo.value)
        with pytest.raises(ValueError) as excinfo:
            Subject.lookup(db.session, Subject.TAG, None, None)
        assert (
            "Cannot look up Subject when neither identifier nor name is provided."
            in str(excinfo.value)
        )

    def test_lookup_autocreate(self, db: DatabaseTransactionFixture):
        # By default, Subject.lookup creates a Subject that doesn't exist.
        identifier = db.fresh_str()
        name = db.fresh_str()
        subject, was_new = Subject.lookup(db.session, Subject.TAG, identifier, name)
        assert was_new == True
        assert subject.identifier == identifier
        assert subject.name == name

        # But you can tell it not to autocreate.
        identifier2 = db.fresh_str()
        subject, was_new = Subject.lookup(
            db.session, Subject.TAG, identifier2, None, autocreate=False
        )
        assert was_new == False
        assert subject == None

    def test_lookup_by_name(self, db: DatabaseTransactionFixture):
        """We can look up a subject by its name, without providing an
        identifier."""
        s1 = db.subject(Subject.TAG, "i1")
        s1.name = "A tag"
        assert Subject.lookup(db.session, Subject.TAG, None, "A tag") == (s1, False)

        # If we somehow get into a state where there are two Subjects
        # with the same name, Subject.lookup treats them as interchangeable.
        s2 = db.subject(Subject.TAG, "i2")
        s2.name = "A tag"

        subject, is_new = Subject.lookup(db.session, Subject.TAG, None, "A tag")
        assert subject in [s1, s2]
        assert is_new == False

    def test_lookup_name_has_changed(self, db: DatabaseTransactionFixture):
        """Check that when a subject's name has changed, we update the name."""
        s1 = db.subject(Subject.TAG, "id_1")
        s1.name = "A name"
        assert (s1, False) == Subject.lookup(
            db.session, Subject.TAG, "id_1", "A new name"
        )
        assert s1.name == "A new name"

    def test_extract_subject_data_can_remove_genre(
        self, db: DatabaseTransactionFixture
    ):
        # Here's a Subject that identifies children's books.
        subject, was_new = Subject.lookup(
            db.session, Subject.SCHEMA_AUDIENCE, "Children", None
        )

        # The genre and audience data for this Subject is totally wrong.
        subject.audience = SubjectClassifier.AUDIENCE_ADULT
        subject.target_age = NumericRange(1, 10)
        subject.fiction = False
        sf, ignore = Genre.lookup(db.session, "Science Fiction")
        subject.genre = sf

        # But calling extract_subject_data() will fix it.
        subject.extract_subject_data()
        assert subject.audience == SubjectClassifier.AUDIENCE_CHILDREN
        assert subject.target_age == NumericRange(0, 12, "[]")
        # This type of subject has no idea of genre or fiction status.
        assert subject.genre == None
        assert subject.fiction == None

    def test_extract_subject_data_returns_genre_when_bisac(
        self, db: DatabaseTransactionFixture
    ):
        # Here's a Subject that identifies children's books.
        subject, was_new = Subject.lookup(
            db.session, Subject.BISAC, "History / General", None
        )
        history, ignore = Genre.lookup(db.session, "History")
        subject.genre = history

        subject.extract_subject_data()
        assert subject.audience == SubjectClassifier.AUDIENCE_ADULT
        assert subject.target_age == NumericRange(18, None, "[]")
        assert subject.genre.name == "History"
        assert subject.fiction == False


class TestGenre:
    def test_name_is_unique(self, db: DatabaseTransactionFixture):
        g1, ignore = Genre.lookup(db.session, "A Genre", autocreate=True)
        g2, ignore = Genre.lookup(db.session, "A Genre", autocreate=True)
        assert g1 == g2

        pytest.raises(IntegrityError, create, db.session, Genre, name="A Genre")

    def test_default_fiction(self, db: DatabaseTransactionFixture):
        sf, ignore = Genre.lookup(db.session, "Science Fiction")
        nonfiction, ignore = Genre.lookup(db.session, "History")
        assert sf.default_fiction == True
        assert nonfiction.default_fiction == False

        # Create a previously unknown genre.
        genre, ignore = Genre.lookup(db.session, "Some Weird Genre", autocreate=True)

        # We don't know its default fiction status.
        assert genre.default_fiction == None
