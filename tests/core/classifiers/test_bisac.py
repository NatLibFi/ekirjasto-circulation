import pytest

from core.classifier import BISACClassifier, Classifier
from core.classifier.bisac import (
    RE,
    MatchingRule,
    anything,
    fiction,
    juvenile,
    nonfiction,
    something,
    ya,
)


class TestMatchingRule:
    def test_registered_object_returned_on_match(self):
        o = object()
        rule = MatchingRule(o, "Fiction")
        assert o == rule.match("fiction")
        assert None == rule.match("nonfiction")

        # You can't create a MatchingRule that returns None on
        # match, since that's the value returned on non-match.
        pytest.raises(ValueError, MatchingRule, None, "Fiction")

    def test_string_match(self):
        rule = MatchingRule(True, "Fiction")
        assert True == rule.match("fiction", "westerns")
        assert None == rule.match("nonfiction", "westerns")
        assert None == rule.match("all books", "fiction")

    def test_regular_expression_match(self):
        rule = MatchingRule(True, RE("F.*O"))
        assert True == rule.match("food")
        assert True == rule.match("flapjacks and oatmeal")
        assert None == rule.match("good", "food")
        assert None == rule.match("fads")

    def test_special_tokens_must_be_first(self):
        # In general, special tokens can only appear in the first
        # slot of a ruleset.
        for special in (juvenile, fiction, nonfiction):
            pytest.raises(ValueError, MatchingRule, True, "first item", special)

        # This rule doesn't apply to the 'anything' token.
        MatchingRule(True, "first item", anything)

    def test_juvenile_match(self):
        rule = MatchingRule(True, juvenile, "western")
        assert True == rule.match("juvenile fiction", "western")
        assert None == rule.match("juvenile nonfiction", "western civilization")
        assert None == rule.match("juvenile nonfiction", "penguins")
        assert None == rule.match("young adult nonfiction", "western")
        assert None == rule.match("fiction", "western")

    def test_ya_match(self):
        rule = MatchingRule(True, ya, "western")
        assert True == rule.match("young adult fiction", "western")
        assert True == rule.match("young adult nonfiction", "western")
        assert None == rule.match("juvenile fiction", "western")
        assert None == rule.match("fiction", "western")

    def test_nonfiction_match(self):
        rule = MatchingRule(True, nonfiction, "art")
        assert True == rule.match("juvenile nonfiction", "art")
        assert True == rule.match("art")
        assert None == rule.match("juvenile fiction", "art")
        assert None == rule.match("fiction", "art")

    def test_fiction_match(self):
        rule = MatchingRule(True, fiction, "art")
        assert None == rule.match("juvenile nonfiction", "art")
        assert None == rule.match("art")
        assert True == rule.match("juvenile fiction", "art")
        assert True == rule.match("fiction", "art")

    def test_anything_match(self):
        # 'anything' can go up front.
        rule = MatchingRule(True, anything, "Penguins")
        assert True == rule.match(
            "juvenile fiction", "science fiction", "antarctica", "animals", "penguins"
        )
        assert True == rule.match("fiction", "penguins")
        assert True == rule.match("nonfiction", "penguins")
        assert True == rule.match("penguins")
        assert None == rule.match("geese")

        # 'anything' can go in the middle, even after another special
        # match rule.
        rule = MatchingRule(True, fiction, anything, "Penguins")
        assert True == rule.match(
            "juvenile fiction", "science fiction", "antarctica", "animals", "penguins"
        )
        assert True == rule.match("fiction", "penguins")
        assert None == rule.match("fiction", "geese")

        # It's redundant, but 'anything' can go last.
        rule = MatchingRule(True, anything, "Penguins", anything)
        assert True == rule.match(
            "juvenile fiction", "science fiction", "antarctica", "animals", "penguins"
        )
        assert True == rule.match("fiction", "penguins", "more penguins")
        assert True == rule.match("penguins")
        assert None == rule.match("geese")

    def test_something_match(self):
        # 'something' can go anywhere.
        rule = MatchingRule(True, something, "Penguins", something, something)

        assert True == rule.match("juvenile fiction", "penguins", "are", "great")
        assert True == rule.match("penguins", "penguins", "i said", "penguins")
        assert None == rule.match("penguins", "what?", "i said", "penguins")

        # unlike 'anything', 'something' must match a specific token.
        assert None == rule.match("penguins")
        assert None == rule.match("juvenile fiction", "penguins", "and seals")


class MockSubject:
    def __init__(self, identifier, name):
        self.identifier = identifier
        self.name = name


class TestBISACClassifier:
    def _subject(self, identifier, name):
        subject = MockSubject(identifier, name)
        (
            subject.genre,
            subject.audience,
            subject.target_age,
            subject.fiction,
        ) = BISACClassifier.classify(subject)
        return subject

    def genre_is(self, name, expect):
        subject = self._subject("", name)
        if expect and subject.genre:
            assert expect == subject.genre.name
        else:
            assert expect == subject.genre

    def test_every_rule_fires(self):
        """There's no point in having a rule that doesn't catch any real BISAC
        subjects. The presence of such a rule generally indicates a
        bug -- usually a typo, or a rule is completely 'shadowed' by another
        rule above it.
        """
        subjects = []
        for identifier, name in sorted(BISACClassifier.NAMES.items()):
            subjects.append(self._subject(identifier, name))

        # caught_fiction_rules = []
        for i in BISACClassifier.FICTION:
            if i.caught == []:
                # caught_fiction_rules.append(i)
                raise Exception("Fiction rule %s didn't catch anything!" % i.ruleset)
        # print("Caught fiction rules: ", len(caught_fiction_rules))
        # for rule in caught_fiction_rules:
        #     print(rule)

        # caught_genre_rules = []
        for i in BISACClassifier.GENRE:
            if i.caught == []:
                # caught_genre_rules.append(i)
                raise Exception("Genre rule %s didn't catch anything!" % i.ruleset)
        # print("Caught genre rules: ", len(caught_genre_rules))
        # for rule in caught_genre_rules:
        #     print(rule)

        need_fiction = []
        need_audience = []
        for subject in subjects:
            if subject.fiction is None:  # == humor, drama
                need_fiction.append(subject)
            if subject.audience is None:
                need_audience.append(subject)

        # We determined fiction/nonfiction status for every BISAC
        # subject except for humor, drama, and poetry.
        for subject in need_fiction:
            assert any(subject.name.lower().startswith(x) for x in ["humor", "drama"])

        # We determined the target audience for every BISAC subject.
        assert [] == need_audience

        # At this point, you can also create a list of subjects that
        # were not classified in some way. The old Bisac had about
        # 400 such subjects, most of them under Juvenile and Young
        # Adult. The new Bisac has almost 650 such subjects.

        # Not every subject has to be classified under a genre, but
        # if it's possible for one to be, it should be. This is the place
        # to check how well the current rules are operating.

        # DEBUGGING:
        # need_genre = sorted(x.name for x in subjects if x.genre is None)
        # print("Bisac subjects without a genre: ", len(need_genre))
        # print("Subjects without a genre: ")
        # for genre in need_genre:
        #     print(genre)

    def test_genre_spot_checks(self):
        """Test some unusual cases with respect to how BISAC
        classifications are turned into genres.
        """
        genre_is = self.genre_is

        genre_is("Fiction / Science Fiction / Erotica", "Erotica")
        genre_is("Literary Criticism / Science Fiction", "Literary Criticism")
        genre_is("Fiction / Christian / Science Fiction", "Religious Fiction")
        genre_is("Fiction / Science Fiction / Short Stories", "Short Stories")
        genre_is("Fiction / Steampunk", "Steampunk")
        genre_is("Fiction / Science Fiction / Steampunk", "Steampunk")
        genre_is("Fiction / African American / Urban", "Urban Fiction")
        genre_is("Fiction / Urban", None)
        genre_is("History / Native American", "United States History")
        genre_is(
            "History / Modern / 17th Century", "Renaissance & Early Modern History"
        )
        genre_is("Biography & Autobiography / Music", "Music"),
        genre_is(
            "Biography & Autobiography / Entertainment & Performing Arts",
            "Entertainment",
        ),
        genre_is("Fiction / Christian", "Religious Fiction"),
        genre_is("Juvenile Nonfiction / Science & Nature / Fossils", "Nature")
        genre_is("Juvenile Nonfiction / Science & Nature / Physics", "Science")
        genre_is("Juvenile Nonfiction / Science & Nature / General", "Science")
        genre_is(
            "Juvenile Nonfiction / Religious / Christian / Social Issues",
            "Christianity",
        )

        genre_is("Young Adult Fiction / Zombies", "Horror")
        genre_is("Young Adult Fiction / Superheroes", "Suspense/Thriller")
        genre_is("Young Adult Nonfiction / Social Topics", "Life Strategies")
        genre_is("Young Adult Fiction / Social Themes", None)

        genre_is("Young Adult Fiction / Poetry", "Poetry")
        genre_is("Poetry / General", "Poetry")
        # Making sure we classify Poetry as Poetry
        genre_is("Poetry / European / General", "Poetry")
        # Making sure we classify Literary Criticism as such, not Poetry
        genre_is("Literary Criticism / Poetry", "Literary Criticism")

        # Grandfathered in from an older test to validate that the new
        # BISAC algorithm gives the same results as the old one.
        genre_is("JUVENILE FICTION / Dystopian", "Dystopian SF")
        genre_is("JUVENILE FICTION / Stories in Verse (see also Poetry)", "Poetry")

        # These tests cover the missing rules for new BISAC codes
        genre_is("FICTION / Absurdist", "Humorous Fiction")
        genre_is("FICTION / Adaptations & Pastiche", "General Fiction")
        genre_is("FICTION / African American & Black / General", "General Fiction")
        genre_is("FICTION / African American & Black / Women", "General Fiction")
        genre_is("FICTION / Amish & Mennonite", "General Fiction")
        genre_is("FICTION / Animals", "General Fiction")
        genre_is("FICTION / Asian American & Pacific Islander", "General Fiction")
        genre_is("FICTION / Biographical", "General Fiction")
        genre_is("FICTION / Buddhist", "General Fiction")
        genre_is("FICTION / City Life", "Urban Fiction")
        genre_is("FICTION / Coming of Age", "General Fiction")
        genre_is("FICTION / Cultural Heritage", "Folklore")
        genre_is("FICTION / Disabilities", "General Fiction")
        genre_is("FICTION / Disaster", "General Fiction")
        genre_is("FICTION / Diversity & Multicultural", "General Fiction")
        genre_is("FICTION / Epistolary", "General Fiction")
        genre_is("FICTION / Family Life / General", "General Fiction")
        genre_is("FICTION / Family Life / Marriage & Divorce", "General Fiction")
        genre_is("FICTION / Family Life / Siblings", "General Fiction")
        genre_is("FICTION / Feminist", "General Fiction")
        genre_is("FICTION / Friendship", "General Fiction")
        genre_is("FICTION / Hispanic & Latino / Family Life", "General Fiction")
        genre_is("FICTION / Hispanic & Latino / General", "General Fiction")
        genre_is("FICTION / Hispanic & Latino / Inspirational", "General Fiction")
        genre_is("FICTION / Hispanic & Latino / Urban & Street Lit", "Urban Fiction")
        genre_is("FICTION / Hispanic & Latino / Women", "General Fiction")
        genre_is("FICTION / Holidays", "General Fiction")
        genre_is("FICTION / Immigration", "General Fiction")
        genre_is("FICTION / Indigenous / General (see also Indigenous Peoples of Turtle Island or Native American)", "General Fiction")
        genre_is("FICTION / Indigenous / City Life", "Urban Fiction")
        genre_is("FICTION / Indigenous / Elders", "General Fiction")
        genre_is("FICTION / Indigenous / Family Life", "General Fiction")
        genre_is("FICTION / Indigenous / Indigenous Futurism", "Science Fiction")
        genre_is("FICTION / Indigenous / Life Stories", "General Fiction")
        genre_is("FICTION / Indigenous / Oral Storytelling & Teachings", "Folklore")
        genre_is("FICTION / Indigenous / Women", "General Fiction")
        genre_is("FICTION / Indigenous / Indigenous Peoples of Turtle Island", "General Fiction")
        genre_is("FICTION / Legal", "General Fiction")
        genre_is("FICTION / Mashups", "General Fiction")
        genre_is("FICTION / Media Tie-In", "General Fiction")
        genre_is("FICTION / Medical", "General Fiction")
        genre_is("FICTION / Middle Eastern & Arab American", "General Fiction")
        genre_is("FICTION / Multiple Timelines", "General Fiction")
        genre_is("FICTION / Muslim", "General Fiction")
        genre_is("FICTION / Native American", "General Fiction")
        genre_is("FICTION / Nature & the Environment", "General Fiction")
        genre_is("FICTION / Neurodiversity", "General Fiction")
        genre_is("FICTION / Own Voices", "General Fiction")
        genre_is("FICTION / Performing Arts / General", "General Fiction")
        genre_is("FICTION / Performing Arts / Dance, Theater & Musicals", "General Fiction")
        genre_is("FICTION / Performing Arts / Film, Television & Radio", "General Fiction")
        genre_is("FICTION / Performing Arts / Music", "General Fiction")
        genre_is("FICTION / Political", "General Fiction")
        genre_is("FICTION / Psychological", "General Fiction")
        genre_is("FICTION / Small Town & Rural", "General Fiction")
        genre_is("FICTION / Southern", "General Fiction")
        genre_is("FICTION / Sports", "General Fiction")
        genre_is("FICTION / Suburban", "General Fiction")
        genre_is("FICTION / Urban & Street Lit", "Urban Fiction")
        genre_is("FICTION / Women", "General Fiction")
        genre_is("FICTION / World Literature / Africa / East Africa", "General Fiction")
        genre_is("FICTION / World Literature / Africa / General", "General Fiction")
        genre_is("FICTION / World Literature / Africa / Nigeria", "General Fiction")
        genre_is("FICTION / World Literature / Africa / Southern Africa", "General Fiction")
        genre_is("FICTION / World Literature / Africa / West Africa", "General Fiction")
        genre_is("FICTION / World Literature / American / 19th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / American / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / American / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / American / Colonial & Revolutionary Periods", "Historical Fiction")
        genre_is("FICTION / World Literature / American / General", "General Fiction")
        genre_is("FICTION / World Literature / Argentina", "General Fiction")
        genre_is("FICTION / World Literature / Asia (General)", "General Fiction")
        genre_is("FICTION / World Literature / Australia", "General Fiction")
        genre_is("FICTION / World Literature / Austria", "General Fiction")
        genre_is("FICTION / World Literature / Brazil", "General Fiction")
        genre_is("FICTION / World Literature / Canada / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / Canada / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / Canada / Colonial & 19th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / Canada / General", "General Fiction")
        genre_is("FICTION / World Literature / Caribbean & West Indies", "General Fiction")
        genre_is("FICTION / World Literature / Central America", "General Fiction")
        genre_is("FICTION / World Literature / Central Asia", "General Fiction")
        genre_is("FICTION / World Literature / Chile", "General Fiction")
        genre_is("FICTION / World Literature / China / 19th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / China / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / China / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / China / General", "General Fiction")
        genre_is("FICTION / World Literature / Colombia", "General Fiction")
        genre_is("FICTION / World Literature / Czech Republic", "General Fiction")
        genre_is("FICTION / World Literature / Denmark", "General Fiction")
        genre_is("FICTION / World Literature / England / 16th & 17th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / England / 18th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / England / 19th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / England / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / England / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / England / Early & Medieval Periods", "General Fiction")
        genre_is("FICTION / World Literature / England / General", "General Fiction")
        genre_is("FICTION / World Literature / Europe (General)", "General Fiction")
        genre_is("FICTION / World Literature / Finland", "General Fiction")
        genre_is("FICTION / World Literature / France / 18th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / France / 19th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / France / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / France / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / France / General", "General Fiction")
        genre_is("FICTION / World Literature / Germany / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / Germany / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / Germany / General", "General Fiction")
        genre_is("FICTION / World Literature / Greece", "General Fiction")
        genre_is("FICTION / World Literature / Hungary", "General Fiction")
        genre_is("FICTION / World Literature / India / 19th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / India / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / India / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / India / General", "General Fiction")
        genre_is("FICTION / World Literature / Ireland / 19th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / Ireland / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / Ireland / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / Ireland / General", "General Fiction")
        genre_is("FICTION / World Literature / Italy", "General Fiction")
        genre_is("FICTION / World Literature / Japan", "General Fiction")
        genre_is("FICTION / World Literature / Korea", "General Fiction")
        genre_is("FICTION / World Literature / Mexico", "General Fiction")
        genre_is("FICTION / World Literature / Middle East / Arabian Peninsula", "General Fiction")
        genre_is("FICTION / World Literature / Middle East / Egypt & North Africa", "General Fiction")
        genre_is("FICTION / World Literature / Middle East / General", "General Fiction")
        genre_is("FICTION / World Literature / Middle East / Israel", "General Fiction")
        genre_is("FICTION / World Literature / Netherlands", "General Fiction")
        genre_is("FICTION / World Literature / New Zealand", "General Fiction")
        genre_is("FICTION / World Literature / Norway", "General Fiction")
        genre_is("FICTION / World Literature / Oceania", "General Fiction")
        genre_is("FICTION / World Literature / Pakistan", "General Fiction")
        genre_is("FICTION / World Literature / Peru", "General Fiction")
        genre_is("FICTION / World Literature / Poland", "General Fiction")
        genre_is("FICTION / World Literature / Portugal", "General Fiction")
        genre_is("FICTION / World Literature / Russia / 19th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / Russia / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / Russia / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / Russia / General", "General Fiction")
        genre_is("FICTION / World Literature / Scotland / 19th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / Scotland / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / Scotland / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / Scotland / General", "General Fiction")
        genre_is("FICTION / World Literature / South America (General)", "General Fiction")
        genre_is("FICTION / World Literature / Southeast Asia", "General Fiction")
        genre_is("FICTION / World Literature / Spain / 19th Century", "Historical Fiction")
        genre_is("FICTION / World Literature / Spain / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / Spain / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / Spain / General", "General Fiction")
        genre_is("FICTION / World Literature / Sweden", "General Fiction")
        genre_is("FICTION / World Literature / Switzerland", "General Fiction")
        genre_is("FICTION / World Literature / Turkey", "General Fiction")
        genre_is("FICTION / World Literature / Uruguay", "General Fiction")
        genre_is("FICTION / World Literature / Wales", "General Fiction")
        

    def test_deprecated_bisac_terms(self):
        """These BISAC terms have been deprecated. We classify them
        the same as the new terms.
        """
        self.genre_is("Psychology & Psychiatry / Jungian", "Psychology")
        self.genre_is("Mind & Spirit / Crystals, Man", "Body, Mind & Spirit")
        self.genre_is("Technology / Fire", "Technology")
        self.genre_is(
            "Young Adult Nonfiction / Social Situations / Junior Prom",
            "Life Strategies",
        )

    def test_non_bisac_classified_as_keywords(self):
        """Categories that are not official BISAC categories (and any official
        BISAC categories our rules didn't catch) are classified as
        though they were free-text keywords.
        """
        self.genre_is("Fiction / Unicorns", "Fantasy")

    def test_fiction_spot_checks(self):
        def fiction_is(name, expect):
            subject = self._subject("", name)
            assert expect == subject.fiction

        # Some easy tests.
        fiction_is("Fiction / Science Fiction", True)
        fiction_is("Antiques & Collectibles / Kitchenware", False)

        # Humor and drama do not have fiction classifications
        # unless the fiction classification comes from elsewhere in the
        # subject. Poetry used y´to be in this category but as changed in
        # e-kirjasto.
        fiction_is("Humor", None)
        fiction_is("Drama", None)
        fiction_is("Poetry / Russian & Former Soviet Union", True)
        fiction_is("Young Adult Fiction / Poetry", True)
        fiction_is("Poetry / General", True)

        # When Poetry is a subclass, fiction status is based on the upper class.
        fiction_is("Literary Criticism / Poetry", False)

        fiction_is("Young Adult Nonfiction / Humor", False)
        fiction_is("Juvenile Fiction / Humorous Stories", True)

        # Literary collections in general are presumed to be
        # collections of short fiction, but letters and essays are
        # definitely nonfiction.
        fiction_is("Literary Collections / General", True)
        fiction_is("Literary Collections / Letters", False)
        fiction_is("Literary Collections / Essays", False)

        # Grandfathered in from an older test to validate that the new
        # BISAC algorithm gives the same results as the old one.
        fiction_is("FICTION / Classics", True)
        fiction_is("JUVENILE FICTION / Concepts / Date & Time", True)
        fiction_is("YOUNG ADULT FICTION / Lifestyles / Country Life", True)
        fiction_is("HISTORY / General", False)

        fiction_is("JUVENILE FICTION / General", True)

    def test_audience_spot_checks(self):
        def audience_is(name, expect):
            subject = self._subject("", name)
            assert expect == subject.audience

        adult = Classifier.AUDIENCE_ADULT
        adults_only = Classifier.AUDIENCE_ADULTS_ONLY
        ya = Classifier.AUDIENCE_YOUNG_ADULT
        children = Classifier.AUDIENCE_CHILDREN

        audience_is("Fiction / Science Fiction", adult)
        audience_is("Fiction / Science Fiction / Erotica", adults_only)
        audience_is("Juvenile Fiction / Science Fiction", children)
        audience_is("Young Adult Fiction / Science Fiction / General", ya)

        # Grandfathered in from an older test to validate that the new
        # BISAC algorithm gives the same results as the old one.
        audience_is("FAMILY & RELATIONSHIPS / Love & Romance", adult)
        audience_is("JUVENILE FICTION / Action & Adventure / General", children)
        audience_is("YOUNG ADULT FICTION / Action & Adventure / General", ya)

    def test_target_age_spot_checks(self):
        def target_age_is(name, expect):
            subject = self._subject("", name)
            assert expect == subject.target_age

        # These are the only BISAC classifications with implied target
        # ages.
        for check in ("Fiction", "Nonfiction"):
            target_age_is("Juvenile %s / Readers / Beginner" % check, (0, 4))
            target_age_is("Juvenile %s / Readers / Intermediate" % check, (5, 7))
            target_age_is("Juvenile %s / Readers / Chapter Books" % check, (8, 13))
            target_age_is(
                "Juvenile %s / Religious / Christian / Early Readers" % check, (5, 7)
            )

        # In all other cases, the classifier will fall back to the
        # default for the target audience.
        target_age_is("Fiction / Science Fiction / Erotica", (18, None))
        target_age_is("Fiction / Science Fiction", (18, None))
        target_age_is("Juvenile Fiction / Science Fiction", (None, None))
        target_age_is("Young Adult Fiction / Science Fiction / General", (14, 17))

    def test_feedbooks_bisac(self):
        """Feedbooks uses a system based on BISAC but with different
        identifiers, different names, and some additions. This is all
        handled transparently by the default BISAC classifier.
        """
        subject = self._subject("FBFIC022000", "Mystery & Detective")
        assert "Mystery" == subject.genre.name

        # This is not an official BISAC classification, so we'll
        # end up running it through the keyword classifier.
        subject = self._subject("FSHUM000000N", "Human Science")
        assert "Social Sciences" == subject.genre.name

    def test_scrub_identifier(self):
        # FeedBooks prefixes are removed.
        assert "abc" == BISACClassifier.scrub_identifier("FBabc")

        # Otherwise, the identifier is left alone.
        assert "abc" == BISACClassifier.scrub_identifier("abc")

        # If the identifier is recognized as an official BISAC identifier,
        # the canonical name is also returned. This will override
        # any other name associated with the subject for classification
        # purposes.
        assert ("FIC015000", "FICTION / Horror") == BISACClassifier.scrub_identifier(
            "FBFIC015000"
        )

    def test_scrub_name(self):
        """Sometimes a data provider sends BISAC names that contain extra or
        nonstandard characters. We store the data as it was provided to us,
        but when it's time to classify things, we normalize it.
        """

        def scrubbed(before, after):
            assert after == BISACClassifier.scrub_name(before)

        scrubbed(
            "ART/Collections  Catalogs  Exhibitions/",
            ["art", "collections, catalogs, exhibitions"],
        )
        scrubbed(
            "ARCHITECTURE|History|Contemporary|",
            ["architecture", "history", "contemporary"],
        )
        scrubbed(
            "BIOGRAPHY & AUTOBIOGRAPHY / Editors, Journalists, Publishers",
            ["biography & autobiography", "editors, journalists, publishers"],
        )
        scrubbed(
            "EDUCATION/Teaching Methods & Materials/Arts & Humanities */",
            ["education", "teaching methods & materials", "arts & humanities"],
        )
        scrubbed(
            "JUVENILE FICTION / Family / General (see also headings under Social Issues)",
            ["juvenile fiction", "family", "general"],
        )
