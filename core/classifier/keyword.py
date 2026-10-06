from core.classifier import *


def match_kw(*l):
    """Turn a list of strings into a function which uses a regular expression
    to match any of those strings, so long as there's a word boundary on both ends.
    The function will match all the strings by default, or can exclude the strings
    that are examples of the classification.
    """

    def match_term(term, exclude_examples=False):
        if not l:
            return None
        if exclude_examples:
            keywords = [keyword for keyword in l if not isinstance(keyword, Eg)]
        else:
            keywords = [str(keyword) for keyword in l]

        if not keywords:
            return None
        any_keyword = "|".join(keywords)
        with_boundaries = r"\b(%s)\b" % any_keyword
        return re.compile(with_boundaries, re.I).search(term)

    # This is a dictionary so it can be used as a class variable
    return {"search": match_term}


class Eg:
    """Mark this string as an example of a classification, rather than
    an exact identifier for that classification. For example, basketball
    is an an example of a sport, but athletics is an identifier for the sports
    classification.
    """

    def __init__(self, term):
        self.term = term

    def __str__(self):
        return self.term


class KeywordBasedClassifier(AgeOrGradeClassifier):
    """Classify a book based on keywords."""

    # We have to handle these first because otherwise '\bfiction\b'
    # will match it.
    LEVEL_1_NONFICTION_INDICATORS = match_kw("non-fiction", "non fiction")

    LEVEL_2_FICTION_INDICATORS = match_kw(
        "fiction",
        Eg("stories"),
        Eg("tales"),
        Eg("literature"),
        Eg("bildungsromans"),
        Eg("poetry"),  # Finland
        "fictitious",
    )
    LEVEL_2_NONFICTION_INDICATORS = match_kw(
        Eg("history"),
        Eg("biography"),
        Eg("histories"),
        Eg("biographies"),
        Eg("autobiography"),
        Eg("autobiographies"),
        "nonfiction",
        Eg("essays"),
        Eg("letters"),
        Eg("true story"),
        Eg("personal memoirs"),
    )
    JUVENILE_INDICATORS = match_kw(
        "for children", "children's", "juvenile", Eg("nursery rhymes"), Eg("9-12")
    )
    YOUNG_ADULT_INDICATORS = match_kw(
        "young adult",
        "ya",
        "12-Up",
        "teenage .*fiction",
        "teens .*fiction",
        "teen books",
        Eg("teenage romance"),
    )

    # Children's books don't generally deal with romance, so although
    # "Juvenile Fiction" generally refers to children's fiction,
    # "Juvenile Fiction / Love & Romance" is almost certainly YA.
    JUVENILE_TERMS_THAT_IMPLY_YOUNG_ADULT = {
        "love & romance",
        "romance",
        "romantic",
    }

    # These identifiers indicate that the string "children" or
    # "juvenile" in the identifier does not actually mean the work is
    # _for_ children.
    JUVENILE_BLACKLIST = {
        "military participation",
        "services",
        "children's accidents",
        "children's voices",
        "juvenile delinquency",
        "children's television workshop",
        "missing children",
    }

    CATCHALL_KEYWORDS = {
        Adventure: match_kw(
            "adventure",
            "adventurers",
            "adventure stories",
            "adventure fiction",
            "adventurers",
            Eg("sea stories"),
            Eg("war stories"),
            Eg("men's adventure"),
        ),
        Antiques_Collectibles: match_kw(
            "antiques",
            "collectibles",
            "collectors",
            "collecting",
        ),
        Architecture: match_kw(
            "architecture",
            "architectural",
            "architect",
            "architects",
        ),
        Art: match_kw(
            "art",
            "arts",
            "artist",
            "artists",
            "artistic",
        ),
        Biography_Memoir: match_kw(
            "autobiographies",
            "autobiography",
            "biographies",
            "biography",
            "biographical",
            "personal memoirs",
        ),
        Body_Mind_Spirit: match_kw(
            "body, mind & spirit",
        ),
        Classics: match_kw(
            "classics",
        ),
        Computers: match_kw(
            "computer",
            "computer science",
            "computational",
            "computers",
            "computing",
            Eg("data"),
            Eg("database"),
            Eg("hardware"),
            Eg("software"),
            Eg("software development"),
            Eg("information technology"),
            Eg("web"),
            Eg("world wide web"),
        ),
        Contemporary_Romance: match_kw(
            "contemporary romance",
            "romance--contemporary",
            "romance / contemporary",
            "romance - contemporary",
        ),
        Crafts_Hobbies: match_kw(
            "arts & crafts",
            "arts, crafts",
            Eg("beadwork"),
            Eg("candle crafts"),
            Eg("candle making"),
            Eg("carving"),
            Eg("ceramics"),
            "crafts & hobbies",
            "crafts",
            Eg("crochet"),
            Eg("crocheting"),
            Eg("cross-stitch"),
            "decorative arts",
            Eg("flower arranging"),
            "folkcrafts",
            "handicrafts",
            "hobbies",
            "hobby",
            "hobbyist",
            "hobbyists",
            Eg("jewelry"),
            Eg("knitting"),
            Eg("metal work"),
            Eg("needlework"),
            Eg("origami"),
            Eg("paper crafts"),
            Eg("pottery"),
            Eg("quilting"),
            Eg("quilts"),
            Eg("scrapbooking"),
            Eg("sewing"),
            Eg("soap making"),
            Eg("stamping"),
            Eg("stenciling"),
            Eg("textile crafts"),
            Eg("toymaking"),
            Eg("weaving"),
            Eg("woodwork"),
        ),
        Design: match_kw(
            "design", "designer", "designers", Eg("graphic design"), Eg("typography")
        ),
        Dictionaries: match_kw(
            "dictionaries",
            "dictionary",
        ),
        Drama: match_kw(
            Eg("comedies"),
            "drama",
            "dramatist",
            "dramatists",
            Eg("operas"),
            Eg("plays"),
            Eg("shakespeare"),
            Eg("tragedies"),
            Eg("tragedy"),
        ),
        Economics: match_kw(
            Eg("banking"),
            "economy",
            "economies",
            "economic",
            "economics",
        ),
        Education: match_kw(
            # TODO: a lot of these don't work well because of
            # the huge amount of fiction about students. This
            # will be fixed when we institute the
            # fiction/nonfiction split.
            "education",
            "educational",
            "educator",
            "educators",
            Eg("principals"),
            "teacher",
            "teachers",
            "teaching",
            # "schools",
            # "high school",
            "schooling",
            # "student",
            # "students",
            # "college",
            Eg("university"),
            Eg("universities"),
        ),
        Epic_Fantasy: match_kw(
            "epic fantasy",
            "fantasy - epic",
            "fantasy / epic",
            "fantasy--epic",
            "fantasy/epic",
        ),
        Erotica: match_kw(
            "erotic",
            "erotica",
        ),
        Fantasy: match_kw(
            "fantasy",
            Eg("magic"),
            Eg("wizards"),
            Eg("fairies"),
            Eg("witches"),
            Eg("dragons"),
            Eg("sorcery"),
            Eg("witchcraft"),
            Eg("wizardry"),
            Eg("unicorns"),
        ),
        Fashion: match_kw(
            "fashion",
            "fashion design",
            "fashion designers",
        ),
        Film_TV: match_kw(
            Eg("director"),
            Eg("directors"),
            "film",
            "films",
            "movies",
            "movie",
            "motion picture",
            "motion pictures",
            "moviemaker",
            "moviemakers",
            Eg("producer"),
            Eg("producers"),
            "television",
            "tv",
            "video",
        ),
        Foreign_Language_Study: match_kw(
            Eg("english as a foreign language"),
            Eg("english as a second language"),
            Eg("esl"),
            "foreign language study",
            Eg("multi-language dictionaries"),
        ),
        Games: match_kw(
            "games",
            Eg("video games"),
            "gaming",
            Eg("gambling"),
        ),
        Gardening: match_kw(
            "gardening",
            "horticulture",
        ),
        Comics_Graphic_Novels: match_kw(
            "comics",
            "comic strip",
            "comic strips",
            "comic book",
            "comic books",
            "graphic novel",
            "graphic novels",
            # Formerly in 'Superhero'
            Eg("superhero"),
            Eg("superheroes"),
            # Formerly in 'Manga'
            Eg("japanese comic books"),
            Eg("japanese comics"),
            Eg("manga"),
            Eg("yaoi"),
        ),
        Historical_Fiction: match_kw(
            "historical fiction",
            "fiction.*historical",
            "^historical$",
        ),
        Historical_Romance: match_kw(
            "historical romance",
            Eg("regency romance"),
            Eg("romance.*regency"),
        ),
        History: match_kw(
            "histories",
            "history",
            "historiography",
            "historical period",
            Eg("pre-confederation"),
        ),
        Horror: match_kw(
            "horror",
            Eg("occult"),
            Eg("ghost"),
            Eg("ghost stories"),
            Eg("vampires"),
            Eg("paranormal fiction"),
            Eg("occult fiction"),
            Eg("supernatural"),
            "scary",
        ),
        Humorous_Fiction: match_kw(
            "comedy",
            "funny",
            "humor",
            "humorous",
            "humourous",
            "humour",
            Eg("satire"),
            "wit",
        ),
        Humorous_Nonfiction: match_kw(
            "comedy",
            "funny",
            "humor",
            "humorous",
            "humour",
            "humourous",
            "wit",
        ),
        LGBTQ_Fiction: match_kw(
            "lgbt",
            "lgbtq",
            Eg("lesbian"),
            Eg("lesbians"),
            "gay",
            Eg("bisexual"),
            Eg("transgender"),
            Eg("transsexual"),
            Eg("transsexuals"),
            "homosexual",
            "homosexuals",
            "homosexuality",
            "queer",
        ),
        Law: match_kw(
            "court",
            "judicial",
            "law",
            "laws",
            "legislation",
            "legal",
        ),
        General_Fiction: match_kw(
            "literary",
            "literary fiction",
            "general fiction",
            "fiction[^a-z]+general",
            "fiction[^a-z]+literary",
        ),
        Management_Leadership: match_kw(
            "management",
            "business & economics / leadership",
            "business & economics -- leadership",
            "management science",
        ),
        Mathematics: match_kw(
            Eg("algebra"),
            Eg("arithmetic"),
            Eg("calculus"),
            Eg("chaos theory"),
            Eg("game theory"),
            Eg("geometry"),
            Eg("group theory"),
            Eg("logic"),
            "math",
            "mathematical",
            "mathematician",
            "mathematicians",
            "mathematics",
            Eg("probability"),
            Eg("statistical"),
            Eg("statistics"),
            Eg("trigonometry"),
        ),
        Medical: match_kw(
            Eg("anatomy"),
            Eg("disease"),
            Eg("diseases"),
            Eg("disorders"),
            Eg("epidemiology"),
            Eg("illness"),
            Eg("illnesses"),
            "medical",
            "medicine",
            Eg("neuroscience"),
            Eg("ophthalmology"),
            Eg("physiology"),
            Eg("vaccines"),
            Eg("virus"),
        ),
        Music: match_kw(
            "music",
            "musician",
            "musicians",
            "musical",
            Eg("genres & styles"),
            Eg("blues"),
            Eg("jazz"),
            Eg("rap"),
            Eg("hip-hop"),
            Eg("rock.*roll"),
            Eg("rock music"),
            Eg("punk rock"),
        ),
        Mystery: match_kw(
            Eg("crime"),
            Eg("detective"),
            Eg("murder"),
            "mystery",
            "mysteries",
            Eg("private investigators"),
            Eg("holmes, sherlock"),
            Eg("poirot, hercule"),
            Eg("schema:person:holmes, sherlock"),
        ),
        Nature: match_kw(
            # TODO: not sure about this one
            "nature",
        ),
        Body_Mind_Spirit: match_kw(
            "new age",
        ),
        Parenting_Family: match_kw(
            # Pure top-level category
        ),
        Performing_Arts: match_kw(
            "theatre",
            "theatrical",
            "performing arts",
            "entertainers",
            Eg("farce"),
            Eg("tragicomedy"),
        ),
        Pets: match_kw(
            "pets",
            Eg("dogs"),
            Eg("cats"),
        ),
        Philosophy: match_kw(
            "philosophy",
            "philosophical",
            "philosopher",
            "philosophers",
            Eg("epistemology"),
            Eg("metaphysics"),
        ),
        Photography: match_kw(
            "photography",
            "photographer",
            "photographers",
            "photographic",
        ),
        Poetry: match_kw(
            "poetry",
            "poet",
            "poets",
            "poem",
            "poems",
            Eg("sonnet"),
            Eg("sonnets"),
        ),
        Political_Science: match_kw(
            Eg("american government"),
            Eg("anarchism"),
            Eg("censorship"),
            Eg("citizenship"),
            Eg("civics"),
            Eg("communism"),
            Eg("corruption"),
            Eg("corrupt practices"),
            Eg("democracy"),
            Eg("geopolitics"),
            "government",
            Eg("human rights"),
            Eg("international relations"),
            Eg("political economy"),
            "political ideologies",
            "political process",
            "political science",
            Eg("public affairs"),
            Eg("public policy"),
            "politics",
            "political",
            Eg("current events"),
        ),
        Psychology: match_kw(
            "psychology",
            Eg("psychiatry"),
            "psychological aspects",
            Eg("psychiatric"),
            Eg("psychoanalysis"),
        ),
        Reference_Study_Aids: match_kw(
            Eg("catalogs"),
            Eg("handbooks"),
            Eg("manuals"),
            Eg("reference"),
            # Formerly in 'Encyclopedias'
            Eg("encyclopaedias"),
            Eg("encyclopaedia"),
            Eg("encyclopedias"),
            Eg("encyclopedia"),
            # Formerly in 'Language Arts & Disciplines'
            Eg("alphabets"),
            Eg("communication studies"),
            Eg("composition"),
            Eg("creative writing"),
            Eg("grammar"),
            Eg("handwriting"),
            Eg("information sciences"),
            Eg("journalism"),
            Eg("library & information sciences"),
            Eg("linguistics"),
            Eg("literacy"),
            Eg("public speaking"),
            Eg("rhetoric"),
            Eg("sign language"),
            Eg("speech"),
            Eg("spelling"),
            Eg("style manuals"),
            Eg("syntax"),
            Eg("vocabulary"),
            Eg("writing systems"),
        ),
        Religion_Spirituality: match_kw(
            "religion",
            "religious",
            Eg("taoism"),
            Eg("taoist"),
            Eg("confucianism"),
            Eg("inspirational nonfiction"),
        ),
        Romance: match_kw(
            "love stories",
            "romance",
            "love & romance",
            "romances",
        ),
        Science: match_kw(
            Eg("aeronautics"),
            Eg("astronomy"),
            Eg("biology"),
            Eg("biophysics"),
            Eg("biochemistry"),
            Eg("botany"),
            Eg("chemistry"),
            Eg("earth sciences"),
            Eg("ecology"),
            Eg("entomology"),
            Eg("evolution"),
            Eg("geology"),
            Eg("genetics"),
            Eg("genetic engineering"),
            Eg("genomics"),
            Eg("ichthyology"),
            Eg("herpetology"),
            Eg("life sciences"),
            Eg("microbiology"),
            Eg("microscopy"),
            Eg("mycology"),
            Eg("ornithology"),
            Eg("natural history"),
            Eg("natural history"),
            Eg("physics"),
            "science",
            "scientist",
            "scientists",
            Eg("zoology"),
            Eg("virology"),
            Eg("cytology"),
        ),
        Science_Fiction: match_kw(
            "speculative fiction",
            "sci-fi",
            "sci fi",
            Eg("time travel"),
        ),
        # Science_Fiction_Fantasy: match_kw(
        #    "science fiction.*fantasy",
        # ),
        Self_Help: match_kw(
            "self help",
            "self-help",
            "self improvement",
            "self-improvement",
        ),
        Folklore: match_kw(
            "fables",
            "folklore",
            "folktales",
            "folk tales",
            "myth",
            "legends",
        ),
        Short_Stories: match_kw(
            "short stories",
            Eg("literary collections"),
        ),
        Sports: match_kw(
            # Ton of specific sports here since 'players'
            # doesn't work. TODO: Why? I don't remember.
            "sports",
            Eg("baseball"),
            Eg("football"),
            Eg("hockey"),
            Eg("soccer"),
            Eg("skating"),
        ),
        Technology: match_kw(
            "technology",
            Eg("engineering"),
            Eg("bioengineering"),
            Eg("mechanics"),
            # Formerly in 'Transportation'
            Eg("transportation"),
            Eg("railroads"),
            Eg("trains"),
            Eg("automotive"),
            Eg("ships & shipbuilding"),
            Eg("cars & trucks"),
        ),
        Suspense_Thriller: match_kw(
            "thriller",
            "thrillers",
            "suspense",
        ),
        Travel: match_kw(
            Eg("discovery"),
            "exploration",
            "travel",
            "travels.*voyages",
            "voyage.*travels",
            "voyages",
            "travelers",
            "description.*travel",
        ),
    }

    LEVEL_2_KEYWORDS = {
        Reference_Study_Aids: match_kw(
            # Formerly in 'Language Arts & Disciplines'
            Eg("language arts & disciplines"),
            Eg("language arts and disciplines"),
            Eg("language arts"),
        ),
        Design: match_kw(
            "arts and crafts movement",
        ),
        Drama: match_kw(
            Eg("opera"),
        ),
        Erotica: match_kw(
            Eg("erotic poetry"),
            Eg("gay erotica"),
            Eg("lesbian erotica"),
            Eg("erotic photography"),
        ),
        Games: match_kw(Eg("games.*fantasy")),
        Historical_Fiction: match_kw(
            Eg("arthurian romance.*"),  # This is "romance" in the old
            # sense of a story.
        ),
        Pets: match_kw(
            "human-animal relationships",
        ),
        Political_Science: match_kw(
            Eg("health care reform"),
        ),
        # Stop the 'religious' from matching Religion/Spirituality.
        Religious_Fiction: match_kw(
            Eg("christian fiction"),
            Eg("inspirational fiction"),
            Eg("fiction.*christian"),
            "religious fiction",
            "fiction.*religious",
            Eg("Oriental religions and wisdom"),
        ),
        Science_Fiction: match_kw(
            "science fiction",
            "science fiction.*general",
        ),
        # Stop from going into Mystery due to 'crime'
        True_Crime: match_kw(
            "true crime",
        ),
        # Stop the 'children' in 'children of' from matching Parenting.
        None: match_kw(
            "children of",
        ),
    }

    LEVEL_3_KEYWORDS = {
        Space_Opera: match_kw(
            "space opera",
        ),
    }

    @classmethod
    def is_fiction(cls, identifier, name, exclude_examples=False):
        if not name:
            return None
        if cls.LEVEL_1_NONFICTION_INDICATORS["search"](name, exclude_examples):
            return False
        if cls.LEVEL_2_FICTION_INDICATORS["search"](name, exclude_examples):
            return True
        if cls.LEVEL_2_NONFICTION_INDICATORS["search"](name, exclude_examples):
            return False
        return None

    @classmethod
    def audience(cls, identifier, name, exclude_examples=False):
        if name is None:
            return None
        if cls.YOUNG_ADULT_INDICATORS["search"](name, exclude_examples):
            use = cls.AUDIENCE_YOUNG_ADULT
        elif cls.JUVENILE_INDICATORS["search"](name, exclude_examples):
            use = cls.AUDIENCE_CHILDREN
        else:
            return None

        if use == cls.AUDIENCE_CHILDREN:
            for i in cls.JUVENILE_TERMS_THAT_IMPLY_YOUNG_ADULT:
                if i in name:
                    use = cls.AUDIENCE_YOUNG_ADULT

        # It may be for kids, or it may be about kids, e.g. "juvenile
        # delinquency".
        for i in cls.JUVENILE_BLACKLIST:
            if i in name:
                return None
        return use

    @classmethod
    def audience_match(cls, query):
        audience = None
        audience_words = None
        audience = cls.audience(None, query, exclude_examples=True)
        if audience:
            for audience_keywords in [
                cls.JUVENILE_INDICATORS,
                cls.YOUNG_ADULT_INDICATORS,
            ]:
                match = audience_keywords["search"](query, exclude_examples=True)
                if match:
                    audience_words = match.group()
                    break
        return (audience, audience_words)

    @classmethod
    def genre(
        cls, identifier, name, fiction=None, audience=None, exclude_examples=False
    ):
        matches = Counter()
        match_against = [name]
        for l in [cls.LEVEL_3_KEYWORDS, cls.LEVEL_2_KEYWORDS, cls.CATCHALL_KEYWORDS]:
            for genre, keywords in list(l.items()):
                if genre and fiction is not None and genre.is_fiction != fiction:
                    continue
                if (
                    genre
                    and audience
                    and genre.audience_restriction
                    and audience not in genre.audience_restriction
                ):
                    continue
                if keywords and keywords["search"](name, exclude_examples):
                    matches[genre] += 1
            most_specific_genre = None
            most_specific_count = 0
            # The genre with the most regex matches wins.
            #
            # If a genre and a subgenre are tied, then the subgenre wins
            # because it's more specific.
            for genre, count in matches.most_common():
                if not most_specific_genre or (
                    most_specific_genre.has_subgenre(genre)
                    and count >= most_specific_count
                ):
                    most_specific_genre = genre
                    most_specific_count = count
            if most_specific_genre:
                break
        return most_specific_genre

    @classmethod
    def genre_match(cls, query):
        genre = None
        genre_words = None
        genre = cls.genre(None, query, exclude_examples=True)
        if genre:
            for kwlist in [
                cls.LEVEL_3_KEYWORDS,
                cls.LEVEL_2_KEYWORDS,
                cls.CATCHALL_KEYWORDS,
            ]:
                if genre in list(kwlist.keys()):
                    genre_keywords = kwlist[genre]
                    match = genre_keywords["search"](query, exclude_examples=True)
                    if match:
                        genre_words = match.group()
                        break
        return (genre, genre_words)


class TAGClassifier(KeywordBasedClassifier):
    pass


SubjectClassifier.classifiers[SubjectClassifier.TAG] = TAGClassifier
