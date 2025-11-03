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
        ) = BISACClassifier.classify_subject(subject)
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
        genre_is(
            "FICTION / African American & Black / Urban & Street Lit", "Urban Fiction"
        )
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
        genre_is(
            "FICTION / Indigenous / General (see also Indigenous Peoples of Turtle Island or Native American)",
            "General Fiction",
        )
        genre_is("FICTION / Indigenous / City Life", "Urban Fiction")
        genre_is("FICTION / Indigenous / Elders", "General Fiction")
        genre_is("FICTION / Indigenous / Family Life", "General Fiction")
        genre_is("FICTION / Indigenous / Indigenous Futurism", "Science Fiction")
        genre_is("FICTION / Indigenous / Life Stories", "General Fiction")
        genre_is("FICTION / Indigenous / Oral Storytelling & Teachings", "Folklore")
        genre_is("FICTION / Indigenous / Women", "General Fiction")
        genre_is(
            "FICTION / Indigenous / Indigenous Peoples of Turtle Island",
            "General Fiction",
        )
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
        genre_is(
            "FICTION / Performing Arts / Dance, Theater & Musicals", "General Fiction"
        )
        genre_is(
            "FICTION / Performing Arts / Film, Television & Radio", "General Fiction"
        )
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
        genre_is(
            "FICTION / World Literature / Africa / Southern Africa", "General Fiction"
        )
        genre_is("FICTION / World Literature / Africa / West Africa", "General Fiction")
        genre_is(
            "FICTION / World Literature / American / 19th Century", "Historical Fiction"
        )
        genre_is(
            "FICTION / World Literature / American / 20th Century", "General Fiction"
        )
        genre_is(
            "FICTION / World Literature / American / 21st Century", "General Fiction"
        )
        genre_is(
            "FICTION / World Literature / American / Colonial & Revolutionary Periods",
            "Historical Fiction",
        )
        genre_is("FICTION / World Literature / American / General", "General Fiction")
        genre_is("FICTION / World Literature / Argentina", "General Fiction")
        genre_is("FICTION / World Literature / Asia (General)", "General Fiction")
        genre_is("FICTION / World Literature / Australia", "General Fiction")
        genre_is("FICTION / World Literature / Austria", "General Fiction")
        genre_is("FICTION / World Literature / Brazil", "General Fiction")
        genre_is(
            "FICTION / World Literature / Canada / 20th Century", "General Fiction"
        )
        genre_is(
            "FICTION / World Literature / Canada / 21st Century", "General Fiction"
        )
        genre_is(
            "FICTION / World Literature / Canada / Colonial & 19th Century",
            "Historical Fiction",
        )
        genre_is("FICTION / World Literature / Canada / General", "General Fiction")
        genre_is(
            "FICTION / World Literature / Caribbean & West Indies", "General Fiction"
        )
        genre_is("FICTION / World Literature / Central America", "General Fiction")
        genre_is("FICTION / World Literature / Central Asia", "General Fiction")
        genre_is("FICTION / World Literature / Chile", "General Fiction")
        genre_is(
            "FICTION / World Literature / China / 19th Century", "Historical Fiction"
        )
        genre_is("FICTION / World Literature / China / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / China / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / China / General", "General Fiction")
        genre_is("FICTION / World Literature / Colombia", "General Fiction")
        genre_is("FICTION / World Literature / Czech Republic", "General Fiction")
        genre_is("FICTION / World Literature / Denmark", "General Fiction")
        genre_is(
            "FICTION / World Literature / England / 16th & 17th Century",
            "Historical Fiction",
        )
        genre_is(
            "FICTION / World Literature / England / 18th Century", "Historical Fiction"
        )
        genre_is(
            "FICTION / World Literature / England / 19th Century", "Historical Fiction"
        )
        genre_is(
            "FICTION / World Literature / England / 20th Century", "General Fiction"
        )
        genre_is(
            "FICTION / World Literature / England / 21st Century", "General Fiction"
        )
        genre_is(
            "FICTION / World Literature / England / Early & Medieval Periods",
            "General Fiction",
        )
        genre_is("FICTION / World Literature / England / General", "General Fiction")
        genre_is("FICTION / World Literature / Europe (General)", "General Fiction")
        genre_is("FICTION / World Literature / Finland", "General Fiction")
        genre_is(
            "FICTION / World Literature / France / 18th Century", "Historical Fiction"
        )
        genre_is(
            "FICTION / World Literature / France / 19th Century", "Historical Fiction"
        )
        genre_is(
            "FICTION / World Literature / France / 20th Century", "General Fiction"
        )
        genre_is(
            "FICTION / World Literature / France / 21st Century", "General Fiction"
        )
        genre_is("FICTION / World Literature / France / General", "General Fiction")
        genre_is(
            "FICTION / World Literature / Germany / 20th Century", "General Fiction"
        )
        genre_is(
            "FICTION / World Literature / Germany / 21st Century", "General Fiction"
        )
        genre_is("FICTION / World Literature / Germany / General", "General Fiction")
        genre_is("FICTION / World Literature / Greece", "General Fiction")
        genre_is("FICTION / World Literature / Hungary", "General Fiction")
        genre_is(
            "FICTION / World Literature / India / 19th Century", "Historical Fiction"
        )
        genre_is("FICTION / World Literature / India / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / India / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / India / General", "General Fiction")
        genre_is(
            "FICTION / World Literature / Ireland / 19th Century", "Historical Fiction"
        )
        genre_is(
            "FICTION / World Literature / Ireland / 20th Century", "General Fiction"
        )
        genre_is(
            "FICTION / World Literature / Ireland / 21st Century", "General Fiction"
        )
        genre_is("FICTION / World Literature / Ireland / General", "General Fiction")
        genre_is("FICTION / World Literature / Italy", "General Fiction")
        genre_is("FICTION / World Literature / Japan", "General Fiction")
        genre_is("FICTION / World Literature / Korea", "General Fiction")
        genre_is("FICTION / World Literature / Mexico", "General Fiction")
        genre_is(
            "FICTION / World Literature / Middle East / Arabian Peninsula",
            "General Fiction",
        )
        genre_is(
            "FICTION / World Literature / Middle East / Egypt & North Africa",
            "General Fiction",
        )
        genre_is(
            "FICTION / World Literature / Middle East / General", "General Fiction"
        )
        genre_is("FICTION / World Literature / Middle East / Israel", "General Fiction")
        genre_is("FICTION / World Literature / Netherlands", "General Fiction")
        genre_is("FICTION / World Literature / New Zealand", "General Fiction")
        genre_is("FICTION / World Literature / Norway", "General Fiction")
        genre_is("FICTION / World Literature / Oceania", "General Fiction")
        genre_is("FICTION / World Literature / Pakistan", "General Fiction")
        genre_is("FICTION / World Literature / Peru", "General Fiction")
        genre_is("FICTION / World Literature / Poland", "General Fiction")
        genre_is("FICTION / World Literature / Portugal", "General Fiction")
        genre_is(
            "FICTION / World Literature / Russia / 19th Century", "Historical Fiction"
        )
        genre_is(
            "FICTION / World Literature / Russia / 20th Century", "General Fiction"
        )
        genre_is(
            "FICTION / World Literature / Russia / 21st Century", "General Fiction"
        )
        genre_is("FICTION / World Literature / Russia / General", "General Fiction")
        genre_is(
            "FICTION / World Literature / Scotland / 19th Century", "Historical Fiction"
        )
        genre_is(
            "FICTION / World Literature / Scotland / 20th Century", "General Fiction"
        )
        genre_is(
            "FICTION / World Literature / Scotland / 21st Century", "General Fiction"
        )
        genre_is("FICTION / World Literature / Scotland / General", "General Fiction")
        genre_is(
            "FICTION / World Literature / South America (General)", "General Fiction"
        )
        genre_is("FICTION / World Literature / Southeast Asia", "General Fiction")
        genre_is(
            "FICTION / World Literature / Spain / 19th Century", "Historical Fiction"
        )
        genre_is("FICTION / World Literature / Spain / 20th Century", "General Fiction")
        genre_is("FICTION / World Literature / Spain / 21st Century", "General Fiction")
        genre_is("FICTION / World Literature / Spain / General", "General Fiction")
        genre_is("FICTION / World Literature / Sweden", "General Fiction")
        genre_is("FICTION / World Literature / Switzerland", "General Fiction")
        genre_is("FICTION / World Literature / Turkey", "General Fiction")
        genre_is("FICTION / World Literature / Uruguay", "General Fiction")
        genre_is("FICTION / World Literature / Wales", "General Fiction")
        # Juvenile and YA subjects do not yet all have genres.
        # Tests added to track them anyway.
        genre_is("JUVENILE FICTION / Activity Books / Coloring", None)
        genre_is("JUVENILE FICTION / Activity Books / General", None)
        genre_is("JUVENILE FICTION / Activity Books / Sticker", None)
        genre_is("JUVENILE FICTION / African American & Black", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Animals / Alligators & Crocodiles", "General Fiction"
        )
        genre_is("JUVENILE FICTION / Animals / Apes, Monkeys, etc.", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Baby Animals", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Bears", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Birds", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Animals / Butterflies, Moths & Caterpillars",
            "General Fiction",
        )
        genre_is("JUVENILE FICTION / Animals / Cats", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Cows", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Animals / Deer, Moose & Caribou", "General Fiction"
        )
        genre_is(
            "JUVENILE FICTION / Animals / Dinosaurs & Prehistoric Creatures",
            "General Fiction",
        )
        genre_is("JUVENILE FICTION / Animals / Dogs", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Ducks, Geese, etc.", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Elephants", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Farm Animals", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Fish", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Foxes", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Frogs & Toads", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / General", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Giraffes", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Hippos & Rhinos", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Horses", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Animals / Insects, Spiders, etc.", "General Fiction"
        )
        genre_is("JUVENILE FICTION / Animals / Jungle Animals", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Kangaroos", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Animals / Lions, Tigers, Leopards, etc.",
            "General Fiction",
        )
        genre_is("JUVENILE FICTION / Animals / Mammals", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Marine Life", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Animals / Mice, Hamsters, Guinea Pigs, etc.",
            "General Fiction",
        )
        genre_is("JUVENILE FICTION / Animals / Nocturnal", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Penguins", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Pets", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Pigs", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Rabbits", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Animals / Reptiles & Amphibians", "General Fiction"
        )
        genre_is("JUVENILE FICTION / Animals / Squirrels", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Turtles & Tortoises", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Animals / Wolves, Coyotes & Wild Dogs",
            "General Fiction",
        )
        genre_is("JUVENILE FICTION / Animals / Worms", "General Fiction")
        genre_is("JUVENILE FICTION / Animals / Zoos", "General Fiction")
        genre_is("JUVENILE FICTION / Architecture", None)
        genre_is("JUVENILE FICTION / Art", None)
        genre_is(
            "JUVENILE FICTION / Asian American & Pacific Islander", "General Fiction"
        )
        genre_is("JUVENILE FICTION / Bedtime & Dreams", None)
        genre_is("JUVENILE FICTION / Biographical / Africa", "General Fiction")
        genre_is("JUVENILE FICTION / Biographical / Asia", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Biographical / Australia & Oceania", "General Fiction"
        )
        genre_is("JUVENILE FICTION / Biographical / Canada", "General Fiction")
        genre_is("JUVENILE FICTION / Biographical / Europe", "General Fiction")
        genre_is("JUVENILE FICTION / Biographical / General", "General Fiction")
        genre_is("JUVENILE FICTION / Biographical / Latin America", "General Fiction")
        genre_is("JUVENILE FICTION / Biographical / United States", "General Fiction")
        genre_is("JUVENILE FICTION / Biracial & Multiracial", None)
        genre_is("JUVENILE FICTION / Books & Libraries", None)
        genre_is("JUVENILE FICTION / Boys & Men", None)
        genre_is("JUVENILE FICTION / Business, Careers, Occupations", None)
        genre_is("JUVENILE FICTION / Clothing & Dress", None)
        genre_is("JUVENILE FICTION / Computers & Digital Media", None)
        genre_is("JUVENILE FICTION / Concepts / Alphabet", None)
        genre_is("JUVENILE FICTION / Concepts / Body", None)
        genre_is("JUVENILE FICTION / Concepts / Colors", None)
        genre_is("JUVENILE FICTION / Concepts / Counting & Numbers", None)
        genre_is("JUVENILE FICTION / Concepts / Date & Time", None)
        genre_is("JUVENILE FICTION / Concepts / General", None)
        genre_is("JUVENILE FICTION / Concepts / Language", None)
        genre_is("JUVENILE FICTION / Concepts / Money", None)
        genre_is("JUVENILE FICTION / Concepts / Opposites", None)
        genre_is("JUVENILE FICTION / Concepts / Seasons", None)
        genre_is("JUVENILE FICTION / Concepts / Senses & Sensation", None)
        genre_is("JUVENILE FICTION / Concepts / Size & Shape", None)
        genre_is("JUVENILE FICTION / Concepts / Sounds", None)
        genre_is("JUVENILE FICTION / Concepts / Words", None)
        genre_is("JUVENILE FICTION / Cooking & Food", None)
        genre_is("JUVENILE FICTION / Disabilities", "General Fiction")
        genre_is("JUVENILE FICTION / Diversity & Multicultural", "General Fiction")
        genre_is("JUVENILE FICTION / Family / Adoption", None)
        genre_is("JUVENILE FICTION / Family / Alternative Family", None)
        genre_is("JUVENILE FICTION / Family / Blended Families", None)
        genre_is(
            "JUVENILE FICTION / Family / General (see also headings under Social Themes)",
            None,
        )
        genre_is("JUVENILE FICTION / Family / Grandparents", None)
        genre_is("JUVENILE FICTION / Family / Marriage & Divorce", None)
        genre_is("JUVENILE FICTION / Family / Multigenerational", None)
        genre_is("JUVENILE FICTION / Family / New Baby", None)
        genre_is("JUVENILE FICTION / Family / Orphans & Foster Homes", None)
        genre_is("JUVENILE FICTION / Family / Parents", None)
        genre_is("JUVENILE FICTION / Family / Siblings", None)
        genre_is("JUVENILE FICTION / First Nations", None)
        genre_is("JUVENILE FICTION / Girls & Women", None)
        genre_is("JUVENILE FICTION / Health & Daily Living / Daily Activities", None)
        genre_is(
            "JUVENILE FICTION / Health & Daily Living / Diseases, Illnesses & Injuries",
            None,
        )
        genre_is("JUVENILE FICTION / Health & Daily Living / General", None)
        genre_is("JUVENILE FICTION / Health & Daily Living / Mental Health", None)
        genre_is(
            "JUVENILE FICTION / Health & Daily Living / Mindfulness & Meditation", None
        )
        genre_is("JUVENILE FICTION / Health & Daily Living / Toilet Training", None)
        genre_is("JUVENILE FICTION / Hispanic & Latino", "General Fiction")
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Birthdays", None)
        genre_is(
            "JUVENILE FICTION / Holidays & Celebrations / Christmas & Advent", None
        )
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Day of the Dead", None)
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Diwali", None)
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Easter & Lent", None)
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Father's Day", None)
        genre_is(
            "JUVENILE FICTION / Holidays & Celebrations / General (see also Religious / Christian / Holidays & Celebrations)",
            None,
        )
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Halloween", None)
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Hanukkah", None)
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Juneteenth", None)
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Kwanzaa", None)
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Lunar New Year", None)
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Mother's Day", None)
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Passover", None)
        genre_is(
            "JUVENILE FICTION / Holidays & Celebrations / Patriotic Holidays", None
        )
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Ramadan", None)
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Thanksgiving", None)
        genre_is("JUVENILE FICTION / Holidays & Celebrations / Valentine's Day", None)
        genre_is("JUVENILE FICTION / Imagination & Play", None)
        genre_is("JUVENILE FICTION / Indigenous / Animal Stories", "General Fiction")
        genre_is("JUVENILE FICTION / Indigenous / Cautionary Tales", "General Fiction")
        genre_is("JUVENILE FICTION / Indigenous / Elders", "General Fiction")
        genre_is("JUVENILE FICTION / Indigenous / Family Life", "General Fiction")
        genre_is("JUVENILE FICTION / Indigenous / General", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Indigenous / Life Stories (see also headings under Biographical)",
            "General Fiction",
        )
        genre_is("JUVENILE FICTION / Indigenous / Oral Stories", "General Fiction")
        genre_is("JUVENILE FICTION / Indigenous / Retellings", "General Fiction")
        genre_is("JUVENILE FICTION / Indigenous / Teachings", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Indigenous Peoples of Turtle Island", "General Fiction"
        )
        genre_is("JUVENILE FICTION / Interactive Adventures", None)
        genre_is("JUVENILE FICTION / Inuit", None)
        genre_is("JUVENILE FICTION / Lifestyles / City & Town Life", None)
        genre_is("JUVENILE FICTION / Lifestyles / Country Life", None)
        genre_is("JUVENILE FICTION / Lifestyles / Farm & Ranch Life", None)
        genre_is("JUVENILE FICTION / Mathematics", None)
        genre_is("JUVENILE FICTION / Media Tie-In", "General Fiction")
        genre_is("JUVENILE FICTION / Mermaids & Mermen", "Fantasy")
        genre_is("JUVENILE FICTION / Middle Eastern & Arab American", "General Fiction")
        genre_is("JUVENILE FICTION / M�tis", None)
        genre_is("JUVENILE FICTION / Native American", "General Fiction")
        genre_is("JUVENILE FICTION / Neurodiversity", "General Fiction")
        genre_is("JUVENILE FICTION / Performing Arts / Circus", "General Fiction")
        genre_is("JUVENILE FICTION / Performing Arts / Dance", "General Fiction")
        genre_is("JUVENILE FICTION / Performing Arts / Film", "General Fiction")
        genre_is("JUVENILE FICTION / Performing Arts / General", "General Fiction")
        genre_is("JUVENILE FICTION / Performing Arts / Music", "General Fiction")
        genre_is(
            "JUVENILE FICTION / Performing Arts / Television & Radio", "General Fiction"
        )
        genre_is(
            "JUVENILE FICTION / Performing Arts / Theater & Musicals", "General Fiction"
        )
        genre_is("JUVENILE FICTION / Places / Africa", None)
        genre_is("JUVENILE FICTION / Places / Asia", None)
        genre_is("JUVENILE FICTION / Places / Australia & Oceania", None)
        genre_is("JUVENILE FICTION / Places / Canada", None)
        genre_is("JUVENILE FICTION / Places / Caribbean & Latin America", None)
        genre_is("JUVENILE FICTION / Places / Europe", None)
        genre_is("JUVENILE FICTION / Places / General", None)
        genre_is("JUVENILE FICTION / Places / Mexico", None)
        genre_is("JUVENILE FICTION / Places / Middle East", None)
        genre_is("JUVENILE FICTION / Places / Polar Regions", None)
        genre_is("JUVENILE FICTION / Places / United States", None)
        genre_is("JUVENILE FICTION / Politics & Government", None)
        genre_is("JUVENILE FICTION / Readers / Beginner", None)
        genre_is("JUVENILE FICTION / Readers / Chapter Books", None)
        genre_is("JUVENILE FICTION / Readers / Hi-Lo", None)
        genre_is("JUVENILE FICTION / Readers / Intermediate", None)
        genre_is("JUVENILE FICTION / Recycling & Green Living", None)
        genre_is("JUVENILE FICTION / Royalty", None)
        genre_is("JUVENILE FICTION / School & Education", None)
        genre_is("JUVENILE FICTION / Science & Nature / Disasters", None)
        genre_is("JUVENILE FICTION / Science & Nature / Environment", None)
        genre_is("JUVENILE FICTION / Science & Nature / Flowers & Plants", None)
        genre_is("JUVENILE FICTION / Science & Nature / General", None)
        genre_is("JUVENILE FICTION / Science & Nature / Trees & Forests", None)
        genre_is("JUVENILE FICTION / Science & Nature / Weather", None)
        genre_is("JUVENILE FICTION / Social Themes / Activism & Social Justice", None)
        genre_is("JUVENILE FICTION / Social Themes / Adolescence & Coming of Age", None)
        genre_is("JUVENILE FICTION / Social Themes / Bullying", None)
        genre_is("JUVENILE FICTION / Social Themes / Dating & Relationships", None)
        genre_is("JUVENILE FICTION / Social Themes / Death, Grief, Bereavement", None)
        genre_is("JUVENILE FICTION / Social Themes / Depression & Mental Illness", None)
        genre_is(
            "JUVENILE FICTION / Social Themes / Drugs, Alcohol, Substance Abuse", None
        )
        genre_is("JUVENILE FICTION / Social Themes / Emigration & Immigration", None)
        genre_is("JUVENILE FICTION / Social Themes / Emotions & Feelings", None)
        genre_is("JUVENILE FICTION / Social Themes / Friendship", None)
        genre_is(
            "JUVENILE FICTION / Social Themes / General (see also headings under Family)",
            None,
        )
        genre_is("JUVENILE FICTION / Social Themes / Manners & Etiquette", None)
        genre_is("JUVENILE FICTION / Social Themes / New Experience", None)
        genre_is("JUVENILE FICTION / Social Themes / Peer Pressure", None)
        genre_is(
            "JUVENILE FICTION / Social Themes / Physical & Emotional Abuse (see also Social Themes / Sexual Abuse)",
            None,
        )
        genre_is("JUVENILE FICTION / Social Themes / Poverty & Homelessness", None)
        genre_is("JUVENILE FICTION / Social Themes / Prejudice & Racism", None)
        genre_is("JUVENILE FICTION / Social Themes / Religion & Faith", None)
        genre_is("JUVENILE FICTION / Social Themes / Runaways", None)
        genre_is("JUVENILE FICTION / Social Themes / Self-Esteem & Self-Reliance", None)
        genre_is("JUVENILE FICTION / Social Themes / Sexual Abuse", None)
        genre_is("JUVENILE FICTION / Social Themes / Strangers", None)
        genre_is("JUVENILE FICTION / Social Themes / Values & Virtues", None)
        genre_is("JUVENILE FICTION / Social Themes / Violence", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Baseball & Softball", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Basketball", None)
        genre_is(
            "JUVENILE FICTION / Sports & Recreation / Camping & Outdoor Activities",
            None,
        )
        genre_is("JUVENILE FICTION / Sports & Recreation / Cheerleading", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Cycling", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Equestrian", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Extreme Sports", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Football", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Games", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / General", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Golf", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Gymnastics", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Hockey", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Ice Skating", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Martial Arts", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Motor Sports", None)
        genre_is(
            "JUVENILE FICTION / Sports & Recreation / Olympics & Paralympics", None
        )
        genre_is("JUVENILE FICTION / Sports & Recreation / Skateboarding", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Soccer", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Track & Field", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Water Sports", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Winter Sports", None)
        genre_is("JUVENILE FICTION / Sports & Recreation / Wrestling", None)
        genre_is("JUVENILE FICTION / Technology / Astronauts & Space", None)
        genre_is("JUVENILE FICTION / Technology / General", None)
        genre_is("JUVENILE FICTION / Technology / Inventions", None)
        genre_is("JUVENILE FICTION / Toys, Dolls & Puppets", None)
        genre_is("JUVENILE FICTION / Transportation / Aviation", None)
        genre_is(
            "JUVENILE FICTION / Transportation / Boats, Ships & Underwater Craft", None
        )
        genre_is("JUVENILE FICTION / Transportation / Cars & Trucks", None)
        genre_is("JUVENILE FICTION / Transportation / General", None)
        genre_is("JUVENILE FICTION / Transportation / Railroads & Trains", None)
        genre_is("JUVENILE FICTION / Travel", None)
        genre_is("JUVENILE FICTION / Trickster Tales", None)
        genre_is("JUVENILE NONFICTION / Activism & Social Justice", None)
        genre_is("JUVENILE NONFICTION / Activity Books / Coloring", None)
        genre_is("JUVENILE NONFICTION / Activity Books / General", None)
        genre_is("JUVENILE NONFICTION / Activity Books / Sticker", None)
        genre_is("JUVENILE NONFICTION / Adventure & Adventurers", None)
        genre_is("JUVENILE NONFICTION / African American & Black", None)
        genre_is("JUVENILE NONFICTION / Asian American & Pacific Islander", None)
        genre_is("JUVENILE NONFICTION / Bedtime & Dreams", None)
        genre_is("JUVENILE NONFICTION / Biracial & Multiracial", None)
        genre_is("JUVENILE NONFICTION / Books & Libraries", None)
        genre_is("JUVENILE NONFICTION / Boys & Men", None)
        genre_is("JUVENILE NONFICTION / Clothing & Dress", None)
        genre_is("JUVENILE NONFICTION / Concepts / Alphabet", None)
        genre_is("JUVENILE NONFICTION / Concepts / Body", None)
        genre_is("JUVENILE NONFICTION / Concepts / Colors", None)
        genre_is("JUVENILE NONFICTION / Concepts / Counting & Numbers", None)
        genre_is("JUVENILE NONFICTION / Concepts / Date & Time", None)
        genre_is("JUVENILE NONFICTION / Concepts / General", None)
        genre_is("JUVENILE NONFICTION / Concepts / Money", None)
        genre_is("JUVENILE NONFICTION / Concepts / Opposites", None)
        genre_is("JUVENILE NONFICTION / Concepts / Seasons", None)
        genre_is("JUVENILE NONFICTION / Concepts / Senses & Sensation", None)
        genre_is("JUVENILE NONFICTION / Concepts / Size & Shape", None)
        genre_is("JUVENILE NONFICTION / Concepts / Sounds", None)
        genre_is(
            "JUVENILE NONFICTION / Concepts / Words (see also headings under Language Arts)",
            None,
        )
        genre_is("JUVENILE NONFICTION / Curiosities & Wonders", None)
        genre_is("JUVENILE NONFICTION / Disabilities", None)
        genre_is("JUVENILE NONFICTION / Diversity & Multicultural", None)
        genre_is("JUVENILE NONFICTION / First Nations", None)
        genre_is("JUVENILE NONFICTION / General", None)
        genre_is("JUVENILE NONFICTION / Girls & Women", None)
        genre_is("JUVENILE NONFICTION / Hispanic & Latino", None)
        genre_is("JUVENILE NONFICTION / Holidays & Celebrations / Birthdays", None)
        genre_is(
            "JUVENILE NONFICTION / Holidays & Celebrations / Day of the Dead", None
        )
        genre_is("JUVENILE NONFICTION / Holidays & Celebrations / Diwali", None)
        genre_is("JUVENILE NONFICTION / Holidays & Celebrations / Easter & Lent", None)
        genre_is(
            "JUVENILE NONFICTION / Holidays & Celebrations / General (see also Religious / Christian / Holidays & Celebrations)",
            None,
        )
        genre_is("JUVENILE NONFICTION / Holidays & Celebrations / Halloween", None)
        genre_is("JUVENILE NONFICTION / Holidays & Celebrations / Hanukkah", None)
        genre_is("JUVENILE NONFICTION / Holidays & Celebrations / Juneteenth", None)
        genre_is("JUVENILE NONFICTION / Holidays & Celebrations / Kwanzaa", None)
        genre_is("JUVENILE NONFICTION / Holidays & Celebrations / Lunar New Year", None)
        genre_is("JUVENILE NONFICTION / Holidays & Celebrations / Passover", None)
        genre_is(
            "JUVENILE NONFICTION / Holidays & Celebrations / Patriotic Holidays", None
        )
        genre_is("JUVENILE NONFICTION / Holidays & Celebrations / Ramadan", None)
        genre_is("JUVENILE NONFICTION / Holidays & Celebrations / Thanksgiving", None)
        genre_is(
            "JUVENILE NONFICTION / Holidays & Celebrations / Valentine's Day", None
        )
        genre_is("JUVENILE NONFICTION / Indigenous / Animal Stories", None)
        genre_is("JUVENILE NONFICTION / Indigenous / Elders", None)
        genre_is("JUVENILE NONFICTION / Indigenous / Family Life", None)
        genre_is("JUVENILE NONFICTION / Indigenous / General", None)
        genre_is("JUVENILE NONFICTION / Indigenous / Land-Based Knowledge", None)
        genre_is("JUVENILE NONFICTION / Indigenous / Reconciliation", None)
        genre_is("JUVENILE NONFICTION / Indigenous Peoples of Turtle Island", None)
        genre_is("JUVENILE NONFICTION / Inspirational & Personal Growth", None)
        genre_is("JUVENILE NONFICTION / Inuit", None)
        genre_is("JUVENILE NONFICTION / LGBTQ+", None)
        genre_is("JUVENILE NONFICTION / Language Study / French", None)
        genre_is("JUVENILE NONFICTION / Language Study / General", None)
        genre_is(
            "JUVENILE NONFICTION / Language Study / Indigenous Languages in the Americas",
            None,
        )
        genre_is("JUVENILE NONFICTION / Language Study / Spanish", None)
        genre_is("JUVENILE NONFICTION / Lifestyles / City & Town Life", None)
        genre_is("JUVENILE NONFICTION / Lifestyles / Country Life", None)
        genre_is("JUVENILE NONFICTION / Lifestyles / Farm & Ranch Life", None)
        genre_is("JUVENILE NONFICTION / Media Tie-In", None)
        genre_is("JUVENILE NONFICTION / Middle Eastern & Arab American", None)
        genre_is("JUVENILE NONFICTION / M�tis", None)
        genre_is("JUVENILE NONFICTION / Native American", None)
        genre_is("JUVENILE NONFICTION / Neurodiversity", None)
        genre_is("JUVENILE NONFICTION / Paranormal & Supernatural", None)
        genre_is("JUVENILE NONFICTION / Pirates", None)
        genre_is("JUVENILE NONFICTION / Places / Africa", None)
        genre_is("JUVENILE NONFICTION / Places / Asia", None)
        genre_is("JUVENILE NONFICTION / Places / Australia & Oceania", None)
        genre_is("JUVENILE NONFICTION / Places / Canada", None)
        genre_is("JUVENILE NONFICTION / Places / Caribbean & Latin America", None)
        genre_is("JUVENILE NONFICTION / Places / Europe", None)
        genre_is("JUVENILE NONFICTION / Places / General", None)
        genre_is("JUVENILE NONFICTION / Places / Mexico", None)
        genre_is("JUVENILE NONFICTION / Places / Middle East", None)
        genre_is("JUVENILE NONFICTION / Places / Polar Regions", None)
        genre_is("JUVENILE NONFICTION / Places / United States", None)
        genre_is("JUVENILE NONFICTION / Readers / Beginner", None)
        genre_is("JUVENILE NONFICTION / Readers / Chapter Books", None)
        genre_is("JUVENILE NONFICTION / Readers / Hi-Lo", None)
        genre_is("JUVENILE NONFICTION / Readers / Intermediate", None)
        genre_is("JUVENILE NONFICTION / Recycling & Green Living", None)
        genre_is("JUVENILE NONFICTION / Spies & Spying", None)
        genre_is("JUVENILE NONFICTION / Toys, Dolls & Puppets", None)
        genre_is("JUVENILE NONFICTION / Volunteering", None)
        genre_is("NON-CLASSIFIABLE", None)
        genre_is("YOUNG ADULT FICTION / African American & Black", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Animals / General", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Animals / Horses", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Animals / Marine Life", "General Fiction")
        genre_is(
            "YOUNG ADULT FICTION / Animals / Mythical Creatures", "General Fiction"
        )
        genre_is("YOUNG ADULT FICTION / Animals / Pets", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Art", None)
        genre_is(
            "YOUNG ADULT FICTION / Asian American & Pacific Islander", "General Fiction"
        )
        genre_is("YOUNG ADULT FICTION / Biographical", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Biracial & Multiracial", None)
        genre_is("YOUNG ADULT FICTION / Books & Libraries", None)
        genre_is("YOUNG ADULT FICTION / Boys & Men", None)
        genre_is("YOUNG ADULT FICTION / Careers, Occupations, Internships", None)
        genre_is("YOUNG ADULT FICTION / Clean & Nonviolent", None)
        genre_is("YOUNG ADULT FICTION / Coming of Age", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Computers & Digital Media", None)
        genre_is("YOUNG ADULT FICTION / Cooking & Food", None)
        genre_is("YOUNG ADULT FICTION / Disabilities", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Diversity & Multicultural", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Epistolary (Letters & Diaries)", None)
        genre_is("YOUNG ADULT FICTION / Family / Adoption", None)
        genre_is("YOUNG ADULT FICTION / Family / Alternative Family", None)
        genre_is("YOUNG ADULT FICTION / Family / Blended Families", None)
        genre_is(
            "YOUNG ADULT FICTION / Family / General (see also headings under Social Themes)",
            None,
        )
        genre_is("YOUNG ADULT FICTION / Family / Marriage & Divorce", None)
        genre_is("YOUNG ADULT FICTION / Family / Multigenerational", None)
        genre_is("YOUNG ADULT FICTION / Family / Orphans & Foster Homes", None)
        genre_is("YOUNG ADULT FICTION / Family / Parents", None)
        genre_is("YOUNG ADULT FICTION / Family / Siblings", None)
        genre_is("YOUNG ADULT FICTION / Fashion & Beauty", None)
        genre_is("YOUNG ADULT FICTION / First Nations", None)
        genre_is("YOUNG ADULT FICTION / Girls & Women", None)
        genre_is(
            "YOUNG ADULT FICTION / Health & Daily Living / Diseases, Illnesses & Injuries",
            None,
        )
        genre_is("YOUNG ADULT FICTION / Health & Daily Living / General", None)
        genre_is("YOUNG ADULT FICTION / Hispanic & Latino", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Holidays & Celebrations", None)
        genre_is(
            "YOUNG ADULT FICTION / Indigenous / Cautionary Tales", "General Fiction"
        )
        genre_is("YOUNG ADULT FICTION / Indigenous / City Life", "Urban Fiction")
        genre_is("YOUNG ADULT FICTION / Indigenous / Family Life", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Indigenous / General", "General Fiction")
        genre_is(
            "YOUNG ADULT FICTION / Indigenous / Life Stories (see also Biographical)",
            "General Fiction",
        )
        genre_is("YOUNG ADULT FICTION / Indigenous / Oral Stories", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Indigenous / Retellings", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Indigenous / Teachings", "General Fiction")
        genre_is(
            "YOUNG ADULT FICTION / Indigenous Peoples of Turtle Island",
            "General Fiction",
        )
        genre_is("YOUNG ADULT FICTION / Interactive Adventures", None)
        genre_is("YOUNG ADULT FICTION / Inuit", None)
        genre_is("YOUNG ADULT FICTION / Lifestyles / City & Town Life", None)
        genre_is("YOUNG ADULT FICTION / Lifestyles / Country Life", None)
        genre_is("YOUNG ADULT FICTION / Lifestyles / Farm & Ranch Life", None)
        genre_is("YOUNG ADULT FICTION / Loners & Outcasts", None)
        genre_is("YOUNG ADULT FICTION / Media Tie-In", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Mermaids & Mermen", "Fantasy")
        genre_is(
            "YOUNG ADULT FICTION / Middle Eastern & Arab American", "General Fiction"
        )
        genre_is("YOUNG ADULT FICTION / M�tis", None)
        genre_is("YOUNG ADULT FICTION / Native American", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Neurodiversity", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Own Voices", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Performing Arts / Dance", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Performing Arts / Film", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Performing Arts / General", "General Fiction")
        genre_is("YOUNG ADULT FICTION / Performing Arts / Music", "General Fiction")
        genre_is(
            "YOUNG ADULT FICTION / Performing Arts / Television & Radio",
            "General Fiction",
        )
        genre_is(
            "YOUNG ADULT FICTION / Performing Arts / Theater & Musicals",
            "General Fiction",
        )
        genre_is("YOUNG ADULT FICTION / Places / Africa", None)
        genre_is("YOUNG ADULT FICTION / Places / Asia", None)
        genre_is("YOUNG ADULT FICTION / Places / Australia & Oceania", None)
        genre_is("YOUNG ADULT FICTION / Places / Canada", None)
        genre_is("YOUNG ADULT FICTION / Places / Caribbean & Latin America", None)
        genre_is("YOUNG ADULT FICTION / Places / Europe", None)
        genre_is("YOUNG ADULT FICTION / Places / General", None)
        genre_is("YOUNG ADULT FICTION / Places / Mexico", None)
        genre_is("YOUNG ADULT FICTION / Places / Middle East", None)
        genre_is("YOUNG ADULT FICTION / Places / Polar Regions", None)
        genre_is("YOUNG ADULT FICTION / Places / United States", None)
        genre_is("YOUNG ADULT FICTION / Politics & Government", None)
        genre_is("YOUNG ADULT FICTION / Recycling & Green Living", None)
        genre_is("YOUNG ADULT FICTION / Royalty", None)
        genre_is(
            "YOUNG ADULT FICTION / School & Education / Boarding School & Prep School",
            None,
        )
        genre_is(
            "YOUNG ADULT FICTION / School & Education / College & University", None
        )
        genre_is("YOUNG ADULT FICTION / School & Education / General", None)
        genre_is("YOUNG ADULT FICTION / Science & Nature / Environment", None)
        genre_is(
            "YOUNG ADULT FICTION / Science & Nature / General (see also headings under Animals)",
            None,
        )
        genre_is(
            "YOUNG ADULT FICTION / Social Themes / Activism & Social Justice", None
        )
        genre_is("YOUNG ADULT FICTION / Social Themes / Assimilation", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Bullying", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Class Differences", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Cutting & Self-Harm", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Dating & Sex", None)
        genre_is(
            "YOUNG ADULT FICTION / Social Themes / Death, Grief, Bereavement", None
        )
        genre_is("YOUNG ADULT FICTION / Social Themes / Depression", None)
        genre_is(
            "YOUNG ADULT FICTION / Social Themes / Drugs, Alcohol, Substance Abuse",
            None,
        )
        genre_is(
            "YOUNG ADULT FICTION / Social Themes / Eating Disorders & Body Image", None
        )
        genre_is("YOUNG ADULT FICTION / Social Themes / Emigration & Immigration", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Emotions & Feelings", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Friendship", None)
        genre_is(
            "YOUNG ADULT FICTION / Social Themes / General (see also headings under Family)",
            None,
        )
        genre_is("YOUNG ADULT FICTION / Social Themes / Mental Illness", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / New Experience", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Peer Pressure", None)
        genre_is(
            "YOUNG ADULT FICTION / Social Themes / Physical & Emotional Abuse (see also Social Themes / Sexual Abuse)",
            None,
        )
        genre_is("YOUNG ADULT FICTION / Social Themes / Poverty & Homelessness", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Pregnancy", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Prejudice & Racism", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Religion & Faith", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Runaways", None)
        genre_is(
            "YOUNG ADULT FICTION / Social Themes / Self-Esteem & Self-Reliance", None
        )
        genre_is("YOUNG ADULT FICTION / Social Themes / Sexual Abuse", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Suicide", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Values & Virtues", None)
        genre_is("YOUNG ADULT FICTION / Social Themes / Violence", None)
        genre_is(
            "YOUNG ADULT FICTION / Sports & Recreation / Baseball & Softball", None
        )
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Basketball", None)
        genre_is(
            "YOUNG ADULT FICTION / Sports & Recreation / Camping & Outdoor Activities",
            None,
        )
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Equestrian", None)
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Extreme Sports", None)
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Football", None)
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / General", None)
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Gymnastics", None)
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Hockey", None)
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Martial Arts", None)
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Skateboarding", None)
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Soccer", None)
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Track & Field", None)
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Water Sports", None)
        genre_is("YOUNG ADULT FICTION / Sports & Recreation / Winter Sports", None)
        genre_is("YOUNG ADULT FICTION / Technology", None)
        genre_is(
            "YOUNG ADULT FICTION / Travel & Transportation / Car & Road Trips", None
        )
        genre_is("YOUNG ADULT FICTION / Travel & Transportation / General", None)
        genre_is("YOUNG ADULT FICTION / Urban & Street Lit", "Urban Fiction")
        genre_is("YOUNG ADULT NONFICTION / Activism & Social Justice", None)
        genre_is("YOUNG ADULT NONFICTION / Activity Books", None)
        genre_is("YOUNG ADULT NONFICTION / Adventure & Adventurers", None)
        genre_is("YOUNG ADULT NONFICTION / African American & Black", None)
        genre_is("YOUNG ADULT NONFICTION / Asian American & Pacific Islander", None)
        genre_is("YOUNG ADULT NONFICTION / Biracial & Multiracial", None)
        genre_is("YOUNG ADULT NONFICTION / Books & Libraries", None)
        genre_is("YOUNG ADULT NONFICTION / Boys & Men", None)
        genre_is("YOUNG ADULT NONFICTION / Curiosities & Wonders", None)
        genre_is("YOUNG ADULT NONFICTION / Disabilities", None)
        genre_is("YOUNG ADULT NONFICTION / Diversity & Multicultural", None)
        genre_is("YOUNG ADULT NONFICTION / First Nations", None)
        genre_is("YOUNG ADULT NONFICTION / General", None)
        genre_is("YOUNG ADULT NONFICTION / Girls & Women", None)
        genre_is("YOUNG ADULT NONFICTION / Hispanic & Latino", None)
        genre_is("YOUNG ADULT NONFICTION / Holidays & Celebrations", None)
        genre_is("YOUNG ADULT NONFICTION / Indigenous / Elders", None)
        genre_is("YOUNG ADULT NONFICTION / Indigenous / Family Life", None)
        genre_is("YOUNG ADULT NONFICTION / Indigenous / General", None)
        genre_is("YOUNG ADULT NONFICTION / Indigenous / Land-Based Knowledge", None)
        genre_is("YOUNG ADULT NONFICTION / Indigenous / Reconciliation", None)
        genre_is("YOUNG ADULT NONFICTION / Indigenous Peoples of Turtle Island", None)
        genre_is("YOUNG ADULT NONFICTION / Inspirational & Personal Growth", None)
        genre_is("YOUNG ADULT NONFICTION / Inuit", None)
        genre_is("YOUNG ADULT NONFICTION / LGBTQ+", None)
        genre_is("YOUNG ADULT NONFICTION / Language Study / French", None)
        genre_is("YOUNG ADULT NONFICTION / Language Study / General", None)
        genre_is(
            "YOUNG ADULT NONFICTION / Language Study / Indigenous Languages in the Americas",
            None,
        )
        genre_is("YOUNG ADULT NONFICTION / Language Study / Spanish", None)
        genre_is("YOUNG ADULT NONFICTION / Media Tie-In", None)
        genre_is("YOUNG ADULT NONFICTION / Middle Eastern & Arab American", None)
        genre_is("YOUNG ADULT NONFICTION / M�tis", None)
        genre_is("YOUNG ADULT NONFICTION / Native American", None)
        genre_is("YOUNG ADULT NONFICTION / Neurodiversity", None)
        genre_is("YOUNG ADULT NONFICTION / Paranormal & Supernatural", None)
        genre_is("YOUNG ADULT NONFICTION / Places / Africa", None)
        genre_is("YOUNG ADULT NONFICTION / Places / Asia", None)
        genre_is("YOUNG ADULT NONFICTION / Places / Australia & Oceania", None)
        genre_is("YOUNG ADULT NONFICTION / Places / Canada", None)
        genre_is("YOUNG ADULT NONFICTION / Places / Caribbean & Latin America", None)
        genre_is("YOUNG ADULT NONFICTION / Places / Europe", None)
        genre_is("YOUNG ADULT NONFICTION / Places / General", None)
        genre_is("YOUNG ADULT NONFICTION / Places / Mexico", None)
        genre_is("YOUNG ADULT NONFICTION / Places / Middle East", None)
        genre_is("YOUNG ADULT NONFICTION / Places / United States", None)
        genre_is("YOUNG ADULT NONFICTION / Recycling & Green Living", None)
        genre_is("FICTION / Mystery & Detective / Amateur Sleuth", "Mystery")
        genre_is("BIOGRAPHY & AUTOBIOGRAPHY / Historical", "Biography & Memoir")
        genre_is("FICTION / General", "General Fiction")

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

        # This is not an official BISAC classification, so we're not
        # expecting to find anything
        subject = self._subject("FSHUM000000N", "Human Science")
        if subject.genre is None:
            assert subject.genre is None

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
