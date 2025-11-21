"""
A classifier module that classifies books and subjects into various categories. This module is called when importing
collections to a library or updating classifications. It's called by the core/model/classification.py.
"""

# If the genre classification does not match the fiction classification, throw
# away the genre classifications.
#
# E.g. "Investigations -- nonfiction" maps to Mystery, but Mystery
# conflicts with Nonfiction.

# SQL to find commonly used DDC classifications
# select count(editions.id) as c, subjects.identifier from editions join identifiers on workrecords.primary_identifier_id=workidentifiers.id join classifications on workidentifiers.id=classifications.work_identifier_id join subjects on classifications.subject_id=subjects.id where subjects.type = 'DDC' and not subjects.identifier like '8%' group by subjects.identifier order by c desc;

# SQL to find commonly used classifications not assigned to a genre
# select count(identifiers.id) as c, subjects.type, substr(subjects.identifier, 0, 20) as i, substr(subjects.name, 0, 20) as n from workidentifiers join classifications on workidentifiers.id=classifications.work_identifier_id join subjects on classifications.subject_id=subjects.id where subjects.genre_id is null and subjects.fiction is null group by subjects.type, i, n order by c desc;

import json
import logging
import os
import pkgutil
import re
from collections import Counter, defaultdict
from urllib.parse import urlparse

from sqlalchemy.orm.session import Session
from sqlalchemy.sql.expression import and_

base_dir = os.path.split(__file__)[0]
resource_dir = os.path.join(base_dir, "..", "resources")

NO_VALUE = "NONE"
NO_NUMBER = -1


class ClassifierConstants:
    # Subject related constants
    BISAC = "BISAC"
    TAG = "tag"  # Folksonomic tags.
    DEMARQUE = "De Marque"
    SCHEMA_AGE_RANGE = "schema:typicalAgeRange"  # "0-2", etc.
    SCHEMA_AUDIENCE = "schema:audience"

    GRADE_LEVEL = "Grade level"  # "1-2", "Grade 4", "Kindergarten", etc.

    # Audience related constants
    AUDIENCE_ADULT = "Adult"
    AUDIENCE_ADULTS_ONLY = "Adults Only"
    AUDIENCE_YOUNG_ADULT = "Young Adult"
    AUDIENCE_CHILDREN = "Children"
    AUDIENCE_ALL_AGES = "All Ages"
    AUDIENCE_RESEARCH = "Research"

    # A book for a child younger than 14 is a children's book.
    # A book for a child 14 or older is a young adult book.
    YOUNG_ADULT_AGE_CUTOFF = 14

    ADULT_AGE_CUTOFF = 18

    # "All ages" actually means "all ages with reading fluency".
    ALL_AGES_AGE_CUTOFF = 8

    AUDIENCES_YOUNG_CHILDREN = [AUDIENCE_CHILDREN, AUDIENCE_ALL_AGES]
    AUDIENCES_JUVENILE = AUDIENCES_YOUNG_CHILDREN + [AUDIENCE_YOUNG_ADULT]
    AUDIENCES_ADULT = [AUDIENCE_ADULT, AUDIENCE_ADULTS_ONLY, AUDIENCE_ALL_AGES]
    AUDIENCES = {
        AUDIENCE_ADULT,
        AUDIENCE_ADULTS_ONLY,
        AUDIENCE_YOUNG_ADULT,
        AUDIENCE_CHILDREN,
        AUDIENCE_ALL_AGES,
    }

    # Subjects used when changed in the admin UI and what goes into our OPDS feed.
    SIMPLIFIED_GENRE = "http://librarysimplified.org/terms/genres/Simplified/"
    SIMPLIFIED_FICTION_STATUS = "http://librarysimplified.org/terms/fiction/"


class SubjectClassifier(ClassifierConstants):
    """
    Turn an external classification into an internal genre, an
    audience, an age level, and a fiction status.

    The appropriate classifier is based on the subject's type which
    is mapped to one of the Subject scheme constants.
    """

    AUDIENCES_NO_RESEARCH = [
        x
        for x in ClassifierConstants.AUDIENCES
        if x != ClassifierConstants.AUDIENCE_RESEARCH
    ]

    classifiers = dict()

    @classmethod
    def range_tuple(cls, lower, upper):
        """
        Turn a pair of ages into a tuple that represents an age range.
        This may be turned into an inclusive postgres NumericRange later,
        but this code should not depend on postgres.
        """
        # Just in case the upper and lower ranges are mixed up,
        # and no prior code caught this, un-mix them.
        if lower and upper and lower > upper:
            lower, upper = upper, lower
        return (lower, upper)

    @classmethod
    def lookup(cls, scheme):
        """
        Look up a classifier for a classification scheme.
        """
        return cls.classifiers.get(scheme, None)

    @classmethod
    def name_for(cls, identifier):
        """
        Look up a human-readable name for the given identifier.
        """
        return None

    @classmethod
    def classify_subject(cls, subject):
        """
        Try to determine genre, audience, target age, and fiction status
        for the given Subject.
        """
        identifier, name = cls.scrub_identifier_and_name(
            subject.identifier, subject.name
        )
        fiction = cls.is_fiction(identifier, name)
        audience = cls.audience(identifier, name)

        target_age = cls.target_age(identifier, name)
        if target_age == cls.range_tuple(None, None):
            target_age = cls.default_target_age_for_audience(audience)

        return (
            cls.genre(identifier, name, fiction, audience),
            audience,
            target_age,
            fiction,
        )

    @classmethod
    def scrub_identifier_and_name(cls, identifier, name):
        """
        Prepare identifier and name from within a call to classify_subject().
        """
        identifier = cls.scrub_identifier(identifier)
        if isinstance(identifier, tuple):
            # scrub_identifier returned a canonical value for name as
            # well. Use it in preference to any name associated with
            # the subject.
            identifier, name = identifier
        elif not name:
            name = identifier
        name = cls.scrub_name(name)
        return identifier, name

    @classmethod
    def scrub_identifier(cls, identifier):
        """
        Prepare an identifier from within a call to classify_subject().

        This may involve data normalization, conversion to lowercase,
        etc.
        """
        if identifier is None:
            return None
        return Lowercased(identifier)

    @classmethod
    def scrub_name(cls, name):
        """
        Prepare a name from within a call to classify().
        """
        if name is None:
            return None
        return Lowercased(name)

    @classmethod
    def genre(cls, identifier, name, fiction=None, audience=None):
        """
        Is this identifier associated with a particular Genre?
        """
        return None

    @classmethod
    def genre_match(cls, query):
        """
        Does this query string match a particular Genre, and which part
        of the query matches?
        """
        return None, None

    @classmethod
    def is_fiction(cls, identifier, name):
        """
        Is this identifier+name particularly indicative of fiction?
        How about nonfiction?
        """
        if "nonfiction" in name:
            return False
        if "fiction" in name:
            return True
        return None

    @classmethod
    def audience(cls, identifier, name):
        """
        What does this identifier+name say about the audience for
        this book?
        """
        if "juvenile" in name:
            return cls.AUDIENCE_CHILDREN
        elif "young adult" in name or "YA" in name.original:
            return cls.AUDIENCE_YOUNG_ADULT
        return None

    @classmethod
    def audience_match(cls, query):
        """Does this query string match a particular Audience, and which
        part of the query matches?"""
        return (None, None)

    @classmethod
    def target_age(cls, identifier, name):
        """
        For children's books, what does this identifier+name say
        about the target age for this book?
        """
        return cls.range_tuple(None, None)

    @classmethod
    def default_target_age_for_audience(cls, audience):
        """
        The default target age for a given audience.
        """
        if audience == SubjectClassifier.AUDIENCE_YOUNG_ADULT:
            return cls.range_tuple(13, 17)  # 13, 18 in db
        elif audience in (
            SubjectClassifier.AUDIENCE_ADULT,
            SubjectClassifier.AUDIENCE_ADULTS_ONLY,
        ):
            return cls.range_tuple(18, None)
        elif audience == SubjectClassifier.AUDIENCE_CHILDREN:
            return cls.range_tuple(0, 12)  # 0, 13 in db
        elif audience == SubjectClassifier.AUDIENCE_ALL_AGES:
            return cls.range_tuple(8, None)
        return cls.range_tuple(None, None)

    @classmethod
    def default_audience_for_target_age(cls, range):
        if range is None:
            return None
        lower = range[0]
        upper = range[1]
        if not lower and not upper:
            # You could interpret this as 'all ages' but it's more
            # likely the data is simply missing.
            return None
        if not lower:
            if upper >= cls.ADULT_AGE_CUTOFF:
                # e.g. "up to 20 years", though this doesn't
                # really make sense.
                #
                # The 'all ages' interpretation is more plausible here
                # but it's still more likely that this is simply a
                # book for grown-ups and no lower bound was provided.
                return cls.AUDIENCE_ADULT
            elif upper > cls.YOUNG_ADULT_AGE_CUTOFF:
                # e.g. "up to 15 years"
                return cls.AUDIENCE_YOUNG_ADULT
            else:
                # e.g. "up to 14 years"
                return cls.AUDIENCE_CHILDREN

        # At this point we can assume that lower is not None.
        if lower >= 18:
            return cls.AUDIENCE_ADULT
        elif lower >= cls.YOUNG_ADULT_AGE_CUTOFF:
            return cls.AUDIENCE_YOUNG_ADULT
        elif lower <= cls.ALL_AGES_AGE_CUTOFF and (
            upper is not None and upper >= cls.ADULT_AGE_CUTOFF
        ):
            # e.g. "for children ages 7-77". The 'all ages' reading
            # is here the most plausible.
            return cls.AUDIENCE_ALL_AGES
        elif lower >= 12 and (not upper or upper >= cls.YOUNG_ADULT_AGE_CUTOFF):
            # Although we treat "Young Adult" as starting at 14, many
            # outside sources treat it as starting at 12. As such we
            # treat "12 and up" or "12-14" as an indicator of a Young
            # Adult audience, with a target age that overlaps what we
            # consider a Children audience.
            return cls.AUDIENCE_YOUNG_ADULT
        else:
            return cls.AUDIENCE_CHILDREN

    @classmethod
    def and_up(cls, young, keyword):
        """
        Encapsulates the logic of what "[x] and up" actually means.

        Given the lower end of an age range, tries to determine the
        upper end of the range.
        """
        if young is None:
            return None
        if not any([keyword.endswith(x) for x in ("and up", "and up.", "+", "+.")]):
            return None

        if young >= 18:
            old = young
        elif young >= 12:
            # "12 and up", "14 and up", etc.  are
            # generally intended to cover the entire
            # YA span.
            old = 17
        elif young >= 8:
            # "8 and up" means something like "8-12"
            old = young + 4
        else:
            # Whereas "3 and up" really means more
            # like "3 to 5".
            old = young + 2
        return old


class GradeLevelClassifier(SubjectClassifier):
    # How old a kid is when they start grade N in the US.
    american_grade_to_age = {
        # Preschool: 3-4 years
        "preschool": 3,
        "pre-school": 3,
        "p": 3,
        "pk": 4,
        # Early readers
        "kindergarten": 5,
        "k": 5,
        "0": 5,
        "first": 6,
        "1": 6,
        "second": 7,
        "2": 7,
        # Chapter Books
        "third": 8,
        "3": 8,
        "fourth": 9,
        "4": 9,
        "fifth": 10,
        "5": 10,
        "sixth": 11,
        "6": 11,
        "7": 12,
        "8": 13,
        # YA
        "9": 14,
        "10": 15,
        "11": 16,
        "12": 17,
    }

    # Regular expressions that match common ways of expressing grade
    # levels.
    # TODO: Is this code duplicated in core/classifier/age.py?
    grade_res = [
        re.compile(x, re.I)
        for x in [
            "grades? ([kp0-9]+) to ([kp0-9]+)?",
            "grades? ([kp0-9]+) ?-? ?([kp0-9]+)?",
            r"gr\.? ([kp0-9]+) ?-? ?([kp0-9]+)?",
            "grades?: ([kp0-9]+) to ([kp0-9]+)",
            "grades?: ([kp0-9]+) ?-? ?([kp0-9]+)?",
            r"gr\.? ([kp0-9]+)",
            "([0-9]+)[tnsr][hdt] grade",
            "([a-z]+) grade",
            r"\b(kindergarten|preschool)\b",
        ]
    ]

    generic_grade_res = [
        re.compile(r"([kp0-9]+) ?- ?([0-9]+)", re.I),
        re.compile(r"([kp0-9]+) ?to ?([0-9]+)", re.I),
        re.compile(r"^([0-9]+)\b", re.I),
        re.compile(r"^([kp])\b", re.I),
    ]

    @classmethod
    def audience(cls, identifier, name, require_explicit_age_marker=False):
        target_age = cls.target_age(identifier, name, require_explicit_age_marker)
        return cls.default_audience_for_target_age(target_age)

    @classmethod
    def target_age(cls, identifier, name, require_explicit_grade_marker=False):
        if (identifier and "education" in identifier) or (name and "education" in name):
            # This is a book about teaching, e.g. fifth grade.
            return cls.range_tuple(None, None)

        if (identifier and "grader" in identifier) or (name and "grader" in name):
            # This is a book about, e.g. fifth graders.
            return cls.range_tuple(None, None)

        if require_explicit_grade_marker:
            res = cls.grade_res
        else:
            res = cls.grade_res + cls.generic_grade_res

        for r in res:
            for k in identifier, name:
                if not k:
                    continue
                m = r.search(k)
                if m:
                    gr = m.groups()
                    if len(gr) == 1:
                        young = gr[0]
                        old = None
                    else:
                        young, old = gr

                    # Strip leading zeros
                    if young and young.lstrip("0"):
                        young = young.lstrip("0")
                    if old and old.lstrip("0"):
                        old = old.lstrip("0")

                    young = cls.american_grade_to_age.get(young)
                    old = cls.american_grade_to_age.get(old)

                    if not young and not old:
                        return cls.range_tuple(None, None)

                    if young:
                        young = int(young)
                    if old:
                        old = int(old)
                    if old is None:
                        old = cls.and_up(young, k)
                    if old is None and young is not None:
                        old = young
                    if young is None and old is not None:
                        young = old
                    if old and young and old < young:
                        young, old = old, young
                    return cls.range_tuple(young, old)
        return cls.range_tuple(None, None)

    @classmethod
    def target_age_match(cls, query):
        target_age = None
        grade_words = None
        target_age = cls.target_age(None, query, require_explicit_grade_marker=True)
        if target_age:
            for r in cls.grade_res:
                match = r.search(query)
                if match:
                    grade_words = match.group()
                    break
        return (target_age, grade_words)


class AgeClassifier(SubjectClassifier):
    # Regular expressions that match common ways of expressing ages.
    age_res = [
        re.compile(x, re.I)
        for x in [
            "age ([0-9]+) ?-? ?([0-9]+)?",
            "age: ([0-9]+) ?-? ?([0-9]+)?",
            "age: ([0-9]+) to ([0-9]+)",
            "ages ([0-9]+) ?- ?([0-9]+)",
            "([0-9]+) ?- ?([0-9]+) years?",
            "([0-9]+) years?",
            "ages ([0-9]+)+",
            "([0-9]+) and up",
            "([0-9]+) years? and up",
        ]
    ]

    generic_age_res = [
        re.compile("([0-9]+) ?- ?([0-9]+)", re.I),
        re.compile(r"^([0-9]+)\b", re.I),
    ]

    baby_re = re.compile("^baby ?- ?([0-9]+) year", re.I)

    @classmethod
    def audience(cls, identifier, name, require_explicit_age_marker=False):
        target_age = cls.target_age(identifier, name, require_explicit_age_marker)
        return cls.default_audience_for_target_age(target_age)

    @classmethod
    def target_age(cls, identifier, name, require_explicit_age_marker=False):
        if require_explicit_age_marker:
            res = cls.age_res
        else:
            res = cls.age_res + cls.generic_age_res
        if identifier:
            match = cls.baby_re.search(identifier)
            if match:
                # This is for babies.
                upper_bound = int(match.groups()[0])
                return cls.range_tuple(0, upper_bound)

        for r in res:
            for k in identifier, name:
                if not k:
                    continue
                m = r.search(k)
                if m:
                    groups = m.groups()
                    young = old = None
                    if groups:
                        young = int(groups[0])
                        if len(groups) > 1 and groups[1] != None:
                            old = int(groups[1])
                    if old is None:
                        old = cls.and_up(young, k)
                    if old is None and young is not None:
                        old = young
                    if young is None and old is not None:
                        young = old
                    if old > 99:
                        # This is not an age at all.
                        old = None
                    if young > 99:
                        # This is not an age at all.
                        young = None
                    if young > old:
                        young, old = old, young
                    return cls.range_tuple(young, old)

        return cls.range_tuple(None, None)

    @classmethod
    def target_age_match(cls, query):
        target_age = None
        age_words = None
        target_age = cls.target_age(None, query, require_explicit_age_marker=True)
        if target_age:
            for r in cls.age_res:
                match = r.search(query)
                if match:
                    age_words = match.group()
                    break
        return (target_age, age_words)


# This is the large-scale structure of our classification system.
#
# If the name of a genre is a string, it's the name of the genre
# and there are no subgenres.
#
# If the name of a genre is a dictionary, the 'name' argument is the
# name of the genre, and the 'subgenres' argument is the list of the
# subgenres.

COMICS_AND_GRAPHIC_NOVELS = "Comics & Graphic Novels"

fiction_genres = [
    "Adventure",
    "Classics",
    COMICS_AND_GRAPHIC_NOVELS,
    "Drama",
    dict(name="Erotica", audiences=SubjectClassifier.AUDIENCE_ADULTS_ONLY),
    dict(
        name="Fantasy",
        subgenres=[
            "Epic Fantasy",
            "Historical Fantasy",
            "Urban Fantasy",
        ],
    ),
    "Folklore",
    "Historical Fiction",
    dict(
        name="Horror",
        subgenres=[
            "Gothic Horror",
            "Ghost Stories",
            "Vampires",
            "Werewolves",
            "Occult Horror",
        ],
    ),
    "Humorous Fiction",
    "General Fiction",
    "LGBTQ Fiction",
    dict(
        name="Mystery",
        subgenres=[
            "Crime & Detective Stories",
            "Hard-Boiled Mystery",
            "Police Procedural",
            "Cozy Mystery",
            "Historical Mystery",
            "Paranormal Mystery",
            "Women Detectives",
        ],
    ),
    "Poetry",
    "Religious Fiction",
    dict(
        name="Romance",
        subgenres=[
            "Contemporary Romance",
            "Gothic Romance",
            "Historical Romance",
            "Paranormal Romance",
            "Western Romance",
            "Romantic Suspense",
        ],
    ),
    dict(
        name="Science Fiction",
        subgenres=[
            "Dystopian SF",
            "Space Opera",
            "Cyberpunk",
            "Military SF",
            "Alternative History",
            "Steampunk",
            "Romantic SF",
            "Media Tie-in SF",
        ],
    ),
    "Short Stories",
    dict(
        name="Suspense/Thriller",
        subgenres=[
            "Historical Thriller",
            "Espionage",
            "Supernatural Thriller",
            "Medical Thriller",
            "Political Thriller",
            "Psychological Thriller",
            "Technothriller",
            "Legal Thriller",
            "Military Thriller",
        ],
    ),
    "Urban Fiction",
    "Westerns",
    "Women's Fiction",
]

nonfiction_genres = [
    dict(
        name="Art & Design",
        subgenres=[
            "Architecture",
            "Art",
            "Art Criticism & Theory",
            "Art History",
            "Design",
            "Fashion",
            "Photography",
        ],
    ),
    "Biography & Memoir",
    "Education",
    dict(
        name="Personal Finance & Business",
        subgenres=[
            "Business",
            "Economics",
            "Management & Leadership",
            "Personal Finance & Investing",
            "Real Estate",
        ],
    ),
    dict(
        name="Parenting & Family",
        subgenres=[
            "Family & Relationships",
            "Parenting",
        ],
    ),
    dict(
        name="Food & Health",
        subgenres=[
            "Bartending & Cocktails",
            "Cooking",
            "Health & Diet",
            "Vegetarian & Vegan",
        ],
    ),
    dict(
        name="History",
        subgenres=[
            "African History",
            "Ancient History",
            "Asian History",
            "Civil War History",
            "European History",
            "Latin American History",
            "Medieval History",
            "Middle East History",
            "Military History",
            "Modern History",
            "Renaissance & Early Modern History",
            "United States History",
            "World History",
        ],
    ),
    dict(
        name="Hobbies & Home",
        subgenres=[
            "Antiques & Collectibles",
            "Crafts & Hobbies",
            "Gardening",
            "Games",
            "House & Home",
            "Pets",
        ],
    ),
    "Humorous Nonfiction",
    dict(
        name="Entertainment",
        subgenres=[
            "Film & TV",
            "Music",
            "Performing Arts",
        ],
    ),
    "Life Strategies",
    "Literary Criticism",
    "Periodicals",
    "Philosophy",
    "Political Science",
    dict(
        name="Reference & Study Aids",
        subgenres=[
            "Dictionaries",
            "Foreign Language Study",
            "Law",
            "Study Aids",
        ],
    ),
    dict(
        name="Religion & Spirituality",
        subgenres=[
            "Body, Mind & Spirit",
            "Buddhism",
            "Christianity",
            "Hinduism",
            "Islam",
            "Judaism",
        ],
    ),
    dict(
        name="Science & Technology",
        subgenres=[
            "Computers",
            "Mathematics",
            "Medical",
            "Nature",
            "Psychology",
            "Science",
            "Social Sciences",
            "Technology",
        ],
    ),
    "Self-Help",
    "Sports",
    "Travel",
    "True Crime",
]


class GenreData:
    def __init__(self, name, is_fiction, parent=None, audience_restriction=None):
        self.name = name
        self.parent = parent
        self.is_fiction = is_fiction
        self.subgenres = []
        if isinstance(audience_restriction, str):
            audience_restriction = [audience_restriction]
        self.audience_restriction = audience_restriction

    def __repr__(self):
        return f"<GenreData: {self.name} fiction={self.is_fiction}>"

    @property
    def self_and_subgenres(self):
        yield self
        yield from self.all_subgenres

    @property
    def all_subgenres(self):
        for child in self.subgenres:
            yield from child.self_and_subgenres

    @property
    def parents(self):
        parents = []
        p = self.parent
        while p:
            parents.append(p)
            p = p.parent
        return reversed(parents)

    def has_subgenre(self, subgenre):
        for s in self.subgenres:
            if s == subgenre or s.has_subgenre(subgenre):
                return True
        return False

    @property
    def variable_name(self):
        return (
            self.name.replace("-", "_")
            .replace(", & ", "_")
            .replace(", ", "_")
            .replace(" & ", "_")
            .replace(" ", "_")
            .replace("/", "_")
            .replace("'", "")
        )

    @classmethod
    def populate(cls, namespace, genres, fiction_source, nonfiction_source):
        """
        Create a GenreData object for every genre and subgenre in the given
        list of fiction and nonfiction genres.
        """
        for source, default_fiction in (
            (fiction_source, True),
            (nonfiction_source, False),
        ):
            for item in source:
                subgenres = []
                audience_restriction = None
                name = item
                fiction = default_fiction
                if isinstance(item, dict):
                    name = item["name"]
                    subgenres = item.get("subgenres", [])
                    audience_restriction = item.get("audience_restriction")
                    fiction = item.get("fiction", default_fiction)

                cls.add_genre(
                    namespace,
                    genres,
                    name,
                    subgenres,
                    fiction,
                    None,
                    audience_restriction,
                )

    @classmethod
    def add_genre(
        cls, namespace, genres, name, subgenres, fiction, parent, audience_restriction
    ):
        """
        Create a GenreData object. Add it to a dictionary and a namespace.
        """
        if isinstance(name, tuple):
            name, default_fiction = name
        default_fiction = None
        default_audience = None
        if parent:
            default_fiction = parent.is_fiction
            default_audience = parent.audience_restriction
        if isinstance(name, dict):
            data = name
            subgenres = data.get("subgenres", [])
            name = data["name"]
            fiction = data.get("fiction", default_fiction)
            audience_restriction = data.get("audience", default_audience)
        if name in genres:
            raise ValueError("Duplicate genre name! %s" % name)

        # Create the GenreData object.
        genre_data = GenreData(name, fiction, parent, audience_restriction)
        if parent:
            parent.subgenres.append(genre_data)

        # Add the genre to the given dictionary, keyed on name.
        genres[genre_data.name] = genre_data

        # Convert the name to a Python-safe variable name,
        # and add it to the given namespace.
        namespace[genre_data.variable_name] = genre_data

        # Do the same for subgenres.
        for sub in subgenres:
            cls.add_genre(
                namespace, genres, sub, [], fiction, genre_data, audience_restriction
            )


Fantasy: GenreData
Romance: GenreData
Science_Fiction: GenreData
Contemporary_Romance: GenreData
Epic_Fantasy: GenreData

genres = dict()
GenreData.populate(globals(), genres, fiction_genres, nonfiction_genres)


class Lowercased(str):
    """
    A lowercased string that remembers its original value.
    """

    def __new__(cls, value):
        if isinstance(value, Lowercased):
            # Nothing to do.
            return value
        if not isinstance(value, str):
            value = str(value)
        new_value = value.lower()
        if new_value.endswith("."):
            new_value = new_value[:-1]
        o = super().__new__(cls, new_value)
        o.original = value
        return o

    @classmethod
    def scrub_identifier(cls, identifier):
        if not identifier:
            return identifier


class AgeOrGradeClassifier(SubjectClassifier):
    @classmethod
    def audience(cls, identifier, name):
        audience = AgeClassifier.audience(identifier, name)
        if audience == None:
            audience = GradeLevelClassifier.audience(identifier, name)
        return audience

    @classmethod
    def target_age(cls, identifier, name):
        """
        This tag might contain a grade level, an age in years, or nothing.
        We will try both a grade level and an age in years, but we
        will require that the tag indicate what's being measured. A
        tag like "9-12" will not match anything because we don't know if it's
        age 9-12 or grade 9-12.
        """
        age = AgeClassifier.target_age(identifier, name, True)
        if age == cls.range_tuple(None, None):
            age = GradeLevelClassifier.target_age(identifier, name, True)
        return age


class SchemaAudienceClassifier(AgeOrGradeClassifier):
    """
    E-kirjasto gets its audience information from schema:audience.
    """

    @classmethod
    def audience(cls, identifier, name):
        if identifier in (
            "children",  # This is the only children's identifier we actually get
            "juvenile",
            "juvenile-fiction",
            "juvenile-nonfiction",
            "pre-adolescent",
            "beginning reader",
        ):
            return cls.AUDIENCE_CHILDREN
        elif identifier in (
            "young adult",  # This is the only YA identifier we actually get
            "ya",
            "teenagers",
            "adolescent",
            "early adolescents",
        ):
            return cls.AUDIENCE_YOUNG_ADULT
        elif identifier == "adult":  # This is the only adult identifier we actually get
            return cls.AUDIENCE_ADULT
        elif identifier == "adults only":
            return cls.AUDIENCE_ADULTS_ONLY
        elif identifier == "all ages":
            return cls.AUDIENCE_ALL_AGES
        return AgeOrGradeClassifier.audience(identifier, name)

    @classmethod
    def target_age(cls, identifier, name):
        if identifier == "beginning reader":
            return cls.range_tuple(5, 8)
        if identifier == "pre-adolescent":
            return cls.range_tuple(9, 12)
        if identifier == "early adolescents":
            return cls.range_tuple(13, 15)
        if identifier == "all ages":
            return cls.range_tuple(cls.ALL_AGES_AGE_CUTOFF, None)
        strict_age = AgeClassifier.target_age(identifier, name, True)
        if strict_age[0] or strict_age[1]:
            return strict_age

        strict_grade = GradeLevelClassifier.target_age(identifier, name, True)
        if strict_grade[0] or strict_grade[1]:
            return strict_grade

        # Default to assuming it's an unmarked age.
        return AgeClassifier.target_age(identifier, name, False)


class WorkClassifier:
    """
    Boil down a bunch of Subject objects into a few values.
    """

    def __init__(self, work, test_session=None, debug=False):
        self._db = Session.object_session(work)
        if test_session:
            self._db = test_session
        self.work = work
        self.fiction_counts = Counter()
        self.genre_list = list()
        self.audience_counts = Counter()
        self.target_age_lower = None
        self.target_age_upper = None
        self.bisac_target_age_lower = None
        self.bisac_target_age_upper = None
        self.log = logging.getLogger("Classifier (workid=%d)" % self.work.id)
        # For tracking whether classifications have been changed manually
        self.using_staff_genres = False
        self.using_staff_fiction_status = False
        self.using_staff_audience = False
        self.using_staff_target_age = False
        self.seen_classifications = set()

    def prepare_classification(self, classification):
        """
        Prepare and process the classification information for a given subject.

        This method extracts and categorizes genre, fiction/nonfiction, audience,
        and target age information based on the subject type of the provided
        classification (~subject). We know our subjects to be reliable so we only need
        to keep count of the most relevant information.

        - If the subject type is BISAC, it appends the genre to the genre list
        and increments the fiction count.
        - If the subject type is De Marque, it increments audience counts based
        on the audience type and extracts target age range information.
        - If the subject type is Schema Audience, it similarly increments audience
        counts.
        - If the subject type is Schema Age Range, it extracts the target age
        range.
        Args:
            classification: Classification: An instance of a Classification object containing
                            subject information.
        Returns:
            None
        """
        try:
            from core.model import DataSource, Subject
        except ValueError:
            from model import DataSource, Subject

        # We only need to classify a subject once per work.
        key = (classification.subject, classification.data_source)
        if key in self.seen_classifications:
            return
        self.seen_classifications.add(key)

        subject = classification.subject

        # Prepare the subject by extracting its data.
        subject.extract_subject_data()

        # Changed in admin UI by someone.
        from_staff = classification.data_source.name == DataSource.LIBRARY_STAFF

        is_genre = subject.genre != None
        # The genre has been deleted by someone manually. In work_editor, a manually
        # defined genre is type SIMPLIFIED_GENRE and id NONE.
        is_none = (
            from_staff
            and subject.type == Subject.SIMPLIFIED_GENRE
            and subject.identifier == SimplifiedGenreClassifier.NONE
        )

        # Collect information about genre.
        if is_genre or is_none:
            self._add_genres(from_staff, is_genre, subject)

        # Collect information about fiction.
        if not self.using_staff_fiction_status:
            self._add_fiction_count(from_staff, subject)

        # Collect information about audience.
        if not self.using_staff_audience:
            self._add_audience_count(from_staff, subject)

        # Collect information about target age.
        if not self.using_staff_target_age:
            self._add_target_age(from_staff, subject)

    def _add_genres(self, from_staff, is_genre, subject):
        """
        Append a genre to the classifier's genres if it's BISAC or from staff.
        Args:
            from_staff: Boolean: Indicates if the classification has been modified in
            the admin UI.
        Returns:
            None
        """
        try:
            from core.model import Genre, Subject
        except ValueError:
            from model import Genre, Subject

        if not from_staff and self.using_staff_genres:
            return
        if from_staff and not self.using_staff_genres:
            self.using_staff_genres = True
            # first encounter with staff genre, so throw out existing genres
            self.genre_list = []
        if is_genre:
            # De Marque has some of its own subjects that we don't want to use for any
            # classifications at the moment.
            if (
                subject.type == SubjectClassifier.BISAC
                or subject.type == Subject.SIMPLIFIED_GENRE
            ):
                # Ensure it's a Genre, not GenreData object.
                genre, ignore = Genre.lookup(self._db, subject.genre.name)
                self.genre_list.append(genre)

    def _add_fiction_count(self, from_staff, subject):
        """
        Increment the classifier's fiction count if it's BISAC or from staff.
        Args:
            from_staff: Boolean: Indicates if the classification has been modified in
            the admin UI.
        Returns:
            None
        """

        try:
            from core.model import Subject
        except ValueError:
            from model import Subject

        # A manually defined fiction status in work_editor is type SIMPLIFIED_FICTION_STATUS.
        if from_staff and subject.type == Subject.SIMPLIFIED_FICTION_STATUS:
            self.using_staff_fiction_status = True
            # first encounter with staff fiction, so throw out existing fiction status
            self.fiction_counts = Counter()
        # De Marque has some of its own subjects that we don't want to use for any
        # classifications at the moment.
        if (
            subject.type == SubjectClassifier.BISAC
            or subject.type == Subject.SIMPLIFIED_FICTION_STATUS
        ):
            self.fiction_counts[subject.fiction] += 1

    def _add_audience_count(self, from_staff, subject):
        """
        Increment the classifier's approppriate audience count if it's schema, BISAC
        or from staff.
        Args:
            from_staff: Boolean: Indicates if the classification has been modified in
            the admin UI.
        Returns:
            None
        """
        try:
            from core.model import Subject
        except ValueError:
            from model import Subject

        # A manually defined audience in work_editor is type SCHEMA_AUDIENCE.
        if from_staff and subject.type == Subject.SCHEMA_AUDIENCE:
            self.using_staff_audience = True
            # first encounter with staff audience, so throw out existing audience counts
            self.audience_counts = Counter()
            self.audience_counts[subject.audience] += 1
        else:
            # De Marque READ.
            if subject.type == SubjectClassifier.DEMARQUE:
                if subject.audience == SubjectClassifier.AUDIENCE_CHILDREN:
                    self.audience_counts[subject.audience] += 1
                elif subject.audience == SubjectClassifier.AUDIENCE_YOUNG_ADULT:
                    self.audience_counts[subject.audience] += 1
                else:
                    self.audience_counts[subject.audience] += 1

            # Ellibs schema:audience.
            elif subject.type == SubjectClassifier.SCHEMA_AUDIENCE:
                if subject.audience == SubjectClassifier.AUDIENCE_CHILDREN:
                    self.audience_counts[subject.audience] += 1
                elif subject.audience == SubjectClassifier.AUDIENCE_YOUNG_ADULT:
                    self.audience_counts[subject.audience] += 1
                else:
                    self.audience_counts[subject.audience] += 1

            elif subject.type == SubjectClassifier.BISAC:
                # Though audience information, in most cases, is provided to us by schema,
                # it's found to be missing in some cases. Save audience information from BISAC
                # just in case.
                if subject.audience == SubjectClassifier.AUDIENCE_CHILDREN:
                    self.audience_counts["BISAC Children"] += 1
                elif subject.audience == SubjectClassifier.AUDIENCE_YOUNG_ADULT:
                    self.audience_counts["BISAC ya"] += 1
                else:
                    self.audience_counts["BISAC Adult"] += 1

    def _add_target_age(self, from_staff, subject):
        """
        Modify the classifier's target age if it's schema or from staff.
        Args:
            from_staff: Boolean: Indicates if the classification has been modified in
            the admin UI.
        Returns:
            None
        """

        try:
            from core.model import Subject
        except ValueError:
            from model import Subject

        # A manually defined audience in work_editor is type AGE_RANGE.
        if from_staff and subject.type == Subject.SCHEMA_AGE_RANGE:
            self.using_staff_target_age = True
            # first encounter with staff target age, so set those
            self.target_age_lower = subject.target_age.lower
            self.target_age_upper = subject.target_age.upper
        else:
            # De Marque children's subjects contain more granular target age information
            # and a work can have more than one subject. We want to grab the largest age
            # range possible based on the subjects.
            if (
                subject.audience == SubjectClassifier.AUDIENCE_CHILDREN
                or subject.audience == SubjectClassifier.AUDIENCE_YOUNG_ADULT
            ) and subject.target_age:
                if subject.type == SubjectClassifier.DEMARQUE:
                    # Set ages if not set
                    if self.target_age_lower is None:
                        self.target_age_lower = subject.target_age.lower
                    else:
                        self.target_age_lower = min(
                            self.target_age_lower, subject.target_age.lower
                        )

    def classify_work(self, default_fiction=None, default_audience=None):
                    if self.target_age_upper is None:
                        self.target_age_upper = subject.target_age.upper
                    else:
                        self.target_age_upper = max(
                            self.target_age_upper, subject.target_age.upper
                        )
                # BISACs do not need such handling.
                elif subject.type == SubjectClassifier.BISAC:
                    self.bisac_target_age_lower = subject.target_age.lower
                    self.bisac_target_age_upper = subject.target_age.upper

        """
        Determine the audience, target age, fiction status and genres of a work.

        Args:
            default_fiction: Boolean| None: A default fiction status.
            default_audience: Any of the audience constants in ClassifierConstants
            or None.
        Returns:
            genres: List: A list of Genre objects.
            fiction: Boolean: A boolean indicating fictions status.
            audience: Any of the audience constants in ClassifierConstants indicating
            the target audience.
            target_age: Tuple: A tuple indicating a target age range.

        """
        fiction = self._fiction(default_fiction=default_fiction)
        genres, fiction = self._genres(fiction)
        audience = self._audience(genres, default_audience=default_audience)
        target_age = self._target_age(audience)
        self.log.info(
            f"Work {self.work} classified: Fiction: {fiction}, Genres: {genres}, Audience: {audience}, Target age: {target_age}"
        )
        return genres, fiction, audience, target_age

    def _fiction(self, default_fiction=None):
        """
        Is it more likely this is a fiction or nonfiction book?

        In E-kirjasto, a fiction book can have nonfiction BISACs (~genres) that
        expand the book's related subjects so we classify the book as fiction rather
        than nonfiction. If, some time in the future, the tendency to get incorrect
        nonfiction BISACs change, we'll need to adjust this classification.
        Args:
            default_fiction: Boolean| None: A default fiction status.
        Returns:
            is_fiction: Boolean or None
        """
        if not self.fiction_counts:
            # We have absolutely no idea one way or the other, and it
            # would be irresponsible to guess.
            return default_fiction
        is_fiction = default_fiction

        self.log.info(f"Fiction counts: {self.fiction_counts}")

        if self.fiction_counts[True] > 0:
            is_fiction = True
        else:
            is_fiction = False

        self.log.info(f"Fiction: {is_fiction}")

        return is_fiction

    def _audience(self, genres=[], default_audience=None):
        """
        What's the most likely audience for this book?
        Since we get reliable schema audience information from our feeds,
        we should always know exactly who the book is targeted to. If not,
        we can use audience information collected from BISACs.

        Args:
            default_audience: It's better to state that we have no information, so
            default_audience can be set to None.
        Returns:
            audience: Any of the audience constants in ClassifierConstants indicating
            the target audience.
        """
        # If we determined that Erotica was a significant enough
        # component of the classification to count as a genre, the
        # audience will always be 'Adults Only', even if the audience
        # counts would indicate something else.
        is_erotica = [genre.name for genre in genres if genre.name == "Erotica"]
        if is_erotica:
            return SubjectClassifier.AUDIENCE_ADULTS_ONLY

        counts = self.audience_counts
        if not counts:
            return default_audience

        self.log.info(f"Audience counts: {counts}")

        children_counts = counts.get(SubjectClassifier.AUDIENCE_CHILDREN, 0)
        ya_counts = counts.get(SubjectClassifier.AUDIENCE_YOUNG_ADULT, 0)
        adult_counts = counts.get(SubjectClassifier.AUDIENCE_ADULT, 0)
        if children_counts == 0 and ya_counts == 0 and adult_counts == 0:
            # We saved BISAC audience information in case the prefered schema audience info is missing.
            children_counts = counts.get("BISAC Children", 0)
            ya_counts = counts.get("BISAC ya", 0)
            adult_counts = counts.get("BISAC Adult", 0)

        audience = default_audience

        # There were subjects (most likely BISACs) from all audience categories.
        if children_counts > 0 and ya_counts > 0 and adult_counts > 0:
            audience = SubjectClassifier.AUDIENCE_ALL_AGES
        # For now, if there were both ya and children's BISACs, make the audience YA
        # until we create a new audience that suits both.
        elif ya_counts >= children_counts and ya_counts > adult_counts:
            audience = SubjectClassifier.AUDIENCE_YOUNG_ADULT
        # It's a children's book if there's more indication towards that than YA. There
        # might be adult, but as long as there's more children, we go with that.
        elif children_counts > ya_counts and children_counts > adult_counts:
            audience = SubjectClassifier.AUDIENCE_CHILDREN
        else:
            audience = SubjectClassifier.AUDIENCE_ADULT

        return audience

    def _target_age(self, audience):
        """
        Derive a target age from the gathered data.

        Args:
            audience: Any of the audience constants in ClassifierConstants.
        Returns:
            Tuple: Indicating the age range.

        """
        target_age = tuple()

        if audience not in (
            SubjectClassifier.AUDIENCE_CHILDREN,
            SubjectClassifier.AUDIENCE_YOUNG_ADULT,
        ):
            # This is not a children's or YA book. Assertions about
            # target age are irrelevant and the default value rules.
            target_age = SubjectClassifier.default_target_age_for_audience(audience)
        # There is specific target age information.
        elif self.target_age_lower or self.target_age_upper:
            target_age = SubjectClassifier.range_tuple(
                self.target_age_lower, self.target_age_upper
            )
        elif audience == SubjectClassifier.AUDIENCE_CHILDREN and (
            self.bisac_target_age_lower or self.bisac_target_age_upper
        ):
            # It's one of the juvenile BISAC subjects that we set to have a target age
            target_age = SubjectClassifier.range_tuple(
                self.bisac_target_age_lower, self.bisac_target_age_upper
            )
        # There was no target age.
        else:
            target_age = SubjectClassifier.default_target_age_for_audience(audience)

        self.log.info(f"Target age: {target_age}")

        return target_age

    def _genres(self, fiction):
        """
        The function compares all current genres to the fiction status and
        removes any if they differ from it. If there's only one genre and it
        conflicts with the fiction status, the fiction status will change
        to the genre's fiction status.

        Args:
            fiction (Boolean): A derived fiction status of a work.
        Returns:
            list: List of genres.
            boolean: Fiction status.
        """
        genres = self.genre_list

        if not genres:
            # We have absolutely no idea, and it would be
            # irresponsible to guess.
            return [], fiction

        self.log.info(f"Collected genres: {genres} Initial fiction: {fiction}")

        # copy the list because the original might have genres removed
        for genre in list(genres):
            # If we have a fiction determination, that lets us eliminate
            # possible genres that conflict with that determination.
            if fiction is not None and (genre.default_fiction != fiction):
                if len(genres) > 1:
                    genres.remove(genre)
                # If there's only one genre, we don't want to lose it or its
                # fiction status.
                else:
                    fiction = genre.default_fiction

        self.log.info(f"Final genres: {genres} Final fiction: {fiction}")

        return genres, fiction


# Make a dictionary of classification schemes to classifiers.

SubjectClassifier.classifiers[
    SubjectClassifier.SCHEMA_AUDIENCE
] = SchemaAudienceClassifier

# Finally, import classifiers described in submodules.
from core.classifier.age import AgeClassifier, GradeLevelClassifier
from core.classifier.bisac import BISACClassifier
from core.classifier.demarque import DeMarqueClassifier
from core.classifier.keyword import Eg, KeywordBasedClassifier, TAGClassifier
from core.classifier.simplified import (
    SimplifiedFictionClassifier,
    SimplifiedGenreClassifier,
)
