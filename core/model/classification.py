# Subject, Classification, Genre
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Column,
    Enum,
    ForeignKey,
    Integer,
    Unicode,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INT4RANGE
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.orm.session import Session
from sqlalchemy.sql.functions import func

from core import classifier
from core.classifier import (  # type: ignore[attr-defined]
    COMICS_AND_GRAPHIC_NOVELS,
    Erotica,
    GenreData,
    SubjectClassifier,
)
from core.model import (
    Base,
    get_one,
    get_one_or_create,
    numericrange_to_string,
    numericrange_to_tuple,
    tuple_to_numericrange,
)
from core.model.hassessioncache import HasSessionCache

if TYPE_CHECKING:
    # This is needed during type checking so we have the
    # types of related models.
    from core.model import WorkGenre  # noqa: autoflake
    from core.model.datasource import DataSource  # noqa: autoflake
    from core.model.identifier import Identifier  # noqa: autoflake


class Subject(Base):
    """A subject under which books might be classified."""

    # Types of subjects.
    BISAC = SubjectClassifier.BISAC
    TAG: str = SubjectClassifier.TAG  # Folksonomic tags.
    SCHEMA_AUDIENCE: str = SubjectClassifier.SCHEMA_AUDIENCE
    DEMARQUE = SubjectClassifier.DEMARQUE
    SCHEMA_AGE_RANGE: str = SubjectClassifier.SCHEMA_AGE_RANGE

    # Types with terms that are suitable for search.
    TYPES_FOR_SEARCH = [BISAC]

    SIMPLIFIED_GENRE = SubjectClassifier.SIMPLIFIED_GENRE
    SIMPLIFIED_FICTION_STATUS = SubjectClassifier.SIMPLIFIED_FICTION_STATUS

    # SUbject schemes that define which classifier is used to classify the subject.
    by_uri = {
        SIMPLIFIED_GENRE: SIMPLIFIED_GENRE,
        SIMPLIFIED_FICTION_STATUS: SIMPLIFIED_FICTION_STATUS,
        "http://schema.org/typicalAgeRange": SCHEMA_AGE_RANGE,
        "http://schema.org/audience": SCHEMA_AUDIENCE,
        "http://www.bisg.org/standards/bisac_subject/": BISAC,
        # Feedbooks uses a modified BISAC which we know how to handle.
        "http://www.feedbooks.com/categories": BISAC,
        "http://schema.org/Audience": DEMARQUE,
    }

    uri_lookup = dict()
    for k, v in list(by_uri.items()):
        uri_lookup[v] = k

    __tablename__ = "subjects"
    id = Column(Integer, primary_key=True)
    # Type should be one of the constants in this class.
    type = Column(Unicode, index=True)

    # Formal identifier for the subject (e.g. "300" for Dewey Decimal
    # System's Social Sciences subject.)
    identifier = Column(Unicode, index=True)

    # Human-readable name, if different from the
    # identifier. (e.g. "Social Sciences" for DDC 300)
    name = Column(Unicode, default=None, index=True)

    # Whether classification under this subject implies anything about
    # the fiction/nonfiction status of a book.
    fiction = Column(Boolean, default=None)

    # Whether classification under this subject implies anything about
    # the book's audience.
    audience = Column(
        Enum(
            "Adult",
            "Young Adult",
            "Children",
            "Adults Only",
            "All Ages",
            "Research",
            name="audience",
        ),
        default=None,
        index=True,
    )

    # For children's books, the target age implied by this subject.
    target_age = Column(INT4RANGE, default=None, index=True)

    # Each Subject may claim affinity with one Genre.
    genre_id = Column(Integer, ForeignKey("genres.id"), index=True)

    # A locked Subject has been reviewed by a human and software will
    # not mess with it without permission.
    locked = Column(Boolean, default=False, index=True)

    # A checked Subject has been reviewed by software and will
    # not be checked again unless forced.
    checked = Column(Boolean, default=False, index=True)

    # One Subject may participate in many Classifications.
    classifications: Mapped[list[Classification]] = relationship(
        "Classification", back_populates="subject"
    )

    # Type + identifier must be unique.
    __table_args__ = (UniqueConstraint("type", "identifier"),)

    def __repr__(self):
        if self.name:
            name = ' ("%s")' % self.name
        else:
            name = ""
        if self.audience:
            audience = " audience=%s" % self.audience
        else:
            audience = ""
        if self.fiction:
            fiction = " (Fiction)"
        elif self.fiction == False:
            fiction = " (Nonfiction)"
        else:
            fiction = ""
        if self.genre:
            genre = ' genre="%s"' % self.genre.name
        else:
            genre = ""
        if self.target_age is not None and (
            self.target_age.lower or self.target_age.upper
        ):
            age_range = " " + self.target_age_string
        else:
            age_range = ""
        a = "[{}:{}{}{}{}{}{}]".format(
            self.type,
            self.identifier,
            name,
            fiction,
            audience,
            genre,
            age_range,
        )
        return str(a)

    @property
    def target_age_string(self):
        return numericrange_to_string(self.target_age)

    @property
    def describes_format(self):
        """Does this Subject describe a format of book rather than
        subject matter, audience, etc?
        If so, there are limitations on when we believe this Subject
        actually applies to a given book--it may describe a very
        different adaptation of the same underlying work.
        TODO: See note in assign_genres about the hacky way this is used.
        """
        if self.genre and self.genre.name == COMICS_AND_GRAPHIC_NOVELS:
            return True
        return False

    @classmethod
    def lookup(cls, _db, type, identifier, name, autocreate=True):
        """Turn a subject type and identifier into a Subject."""
        classifier = SubjectClassifier.lookup(type)
        if not type:
            raise ValueError("Cannot look up Subject with no type.")
        if not identifier and not name:
            raise ValueError(
                "Cannot look up Subject when neither identifier nor name is provided."
            )

        # An identifier is more reliable than a name, so we would rather
        # search based on identifier. But if we only have a name, we'll
        # search based on name.
        if identifier:
            find_with = dict(identifier=identifier)
            create_with = dict(name=name)
        else:
            # Type + identifier is unique, but type + name is not
            # (though maybe it should be). So we need to provide
            # on_multiple.
            find_with = dict(name=name, on_multiple="interchangeable")
            create_with = dict()

        if autocreate:
            subject, new = get_one_or_create(
                _db, Subject, type=type, create_method_kwargs=create_with, **find_with
            )
        else:
            subject = get_one(_db, Subject, type=type, **find_with)
            new = False

        if name and not subject.name or name and subject.name != name:
            # We just discovered the name of a subject that previously
            # had only an ID OR the name we had has changed. Let's go with the new name.
            subject.name = name
        return subject, new

    @classmethod
    def common_but_not_assigned_to_genre(
        cls, _db, min_occurances=1000, type_restriction=None
    ):
        q = _db.query(Subject).join(Classification).filter(Subject.genre == None)

        if type_restriction:
            q = q.filter(Subject.type == type_restriction)
        q = (
            q.group_by(Subject.id)
            .having(func.count(Subject.id) > min_occurances)
            .order_by(func.count(Classification.id).desc())
        )
        return q

    @classmethod
    def assign_to_genres(cls, _db, type_restriction=None, force=False, batch_size=1000):
        """Find subjects that have not been checked yet, assign each a
        genre/audience/fiction status if possible, and mark each as checked.

        :param type_restriction: Only consider subjects of the given type.
        :param force: Assign a genre to all subjects not just the ones that
            have been checked.
        :param batch_size: Perform a database commit every time this many
            subjects have been checked.

        """
        q = _db.query(Subject).filter(Subject.locked == False)

        if type_restriction:
            q = q.filter(Subject.type == type_restriction)

        if not force:
            q = q.filter(Subject.checked == False)

        counter = 0
        for subject in q:
            subject.extract_subject_data()
            counter += 1
            if not counter % batch_size:
                _db.commit()
        _db.commit()

    # Called by WorkClassifier
    def extract_subject_data(self):
        """
        Maps the subject with a genre but also defines a fiction status, audience
        and target age when possible.
        """
        classifier = SubjectClassifier.classifiers.get(self.type, None)
        if not classifier:
            return
        self.checked = True
        log = logging.getLogger(f"Subject-genre assignment: {self.type} / {classifier}")

        genredata, audience, target_age, fiction = classifier.classify_subject(self)
        # If the genre is erotica, the audience will always be ADULTS_ONLY,
        # no matter what the classifier says.
        if genredata == Erotica:
            audience = SubjectClassifier.AUDIENCE_ADULTS_ONLY

        if audience in SubjectClassifier.AUDIENCES_ADULT:
            target_age = SubjectClassifier.default_target_age_for_audience(audience)
        if not audience:
            # We have no audience but some target age information.
            # Try to determine an audience based on that.
            audience = SubjectClassifier.default_audience_for_target_age(target_age)

        if genredata:
            _db = Session.object_session(self)
            genre, was_new = Genre.lookup(_db, genredata.name, True)
        else:
            genre = None

        # Create a shorthand way of referring to this Subject in log
        # messages.
        parts = [self.type, self.identifier, self.name]
        shorthand = ":".join(x for x in parts if x)

        if genre != self.genre:
            log.info("%s genre %r=>%r", shorthand, self.genre, genre)
        self.genre = genre

        if audience:
            if self.audience != audience:
                log.info("%s audience %s=>%s", shorthand, self.audience, audience)
        self.audience = audience

        if fiction is not None:
            if self.fiction != fiction:
                log.info("%s fiction %s=>%s", shorthand, self.fiction, fiction)
        self.fiction = fiction

        if numericrange_to_tuple(self.target_age) != target_age and not (
            not self.target_age and not target_age
        ):
            log.info(
                "%s target_age %r=>%r",
                shorthand,
                self.target_age,
                tuple_to_numericrange(target_age),
            )
        self.target_age = tuple_to_numericrange(target_age)


class Classification(Base):
    """The assignment of a Identifier to a Subject."""

    __tablename__ = "classifications"
    id = Column(Integer, primary_key=True)
    identifier_id = Column(Integer, ForeignKey("identifiers.id"), index=True)
    identifier: Mapped[Identifier | None]
    subject_id = Column(Integer, ForeignKey("subjects.id"), index=True)
    subject: Mapped[Subject] = relationship("Subject", back_populates="classifications")
    data_source_id = Column(Integer, ForeignKey("datasources.id"), index=True)
    data_source: Mapped[DataSource | None]

    # We don't count weight anymore but leaving this in due to so many dependencies, e.g. search.
    weight = Column(Integer)


class Genre(Base, HasSessionCache):
    """A subject-matter classification for a book.
    Much, much more general than Classification.
    """

    __tablename__ = "genres"
    id = Column(Integer, primary_key=True)
    name = Column(Unicode, unique=True, index=True)

    # One Genre may have affinity with many Subjects.
    subjects: Mapped[list[Subject]] = relationship("Subject", backref="genre")

    # One Genre may participate in many WorkGenre assignments.
    works = association_proxy("work_genres", "work")

    work_genres: Mapped[list[WorkGenre]] = relationship(
        "WorkGenre", backref="genre", cascade="all, delete-orphan"
    )

    def __repr__(self):
        if classifier.genres.get(self.name):
            length = len(classifier.genres[self.name].subgenres)
        else:
            length = 0
        genre_data = "<Genre %s (%d subjects, %d works, %d subcategories)>" % (
            self.name,
            len(self.subjects),
            len(self.works),
            length,
        )
        # genre_data += "\n  <Subjects: %s>" % (
        #     ", ".join(
        #         [
        #             f"{subject.name} ({subject.type}, {subject.identifier})"
        #             for subject in self.subjects
        #         ]
        #     )
        # )

        return genre_data

    def cache_key(self):
        return self.name

    @classmethod
    def lookup(cls, _db, name, autocreate=False, use_cache=True):
        if isinstance(name, GenreData):
            name = name.name

        def create():
            """Function called when a Genre is not found in cache and must be
            created."""
            new = False
            args = (_db, Genre)
            if autocreate:
                genre, new = get_one_or_create(*args, name=name)
            else:
                genre = get_one(*args, name=name)
                if genre is None:
                    logging.getLogger().error('"%s" is not a recognized genre.', name)
                    return None, False
            return genre, new

        if use_cache:
            return cls.by_cache_key(_db, name, create)
        else:
            return create()

    @property
    def genredata(self):
        if classifier.genres.get(self.name):
            return classifier.genres[self.name]
        else:
            return GenreData(self.name, False)

    @property
    def subgenres(self):
        for genre in self.self_and_subgenres:
            if genre != self:
                yield genre

    @property
    def self_and_subgenres(self):
        _db = Session.object_session(self)
        genres = []
        for genre_data in self.genredata.self_and_subgenres:
            genres.append(self.lookup(_db, genre_data.name)[0])
        return genres

    @property
    def default_fiction(self):
        if self.name not in classifier.genres:
            return None
        return classifier.genres[self.name].is_fiction
