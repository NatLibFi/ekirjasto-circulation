import logging

from flask_babel import gettext as _

import core.classifier as genres
from api.config import CannotLoadConfiguration, Configuration
from api.metadata.novelist import NoveListAPI
from core import classifier
from core.classifier import GenreData, SubjectClassifier
from core.lane import (
    DatabaseBackedWorkList,
    DefaultSortOrderFacets,
    Facets,
    Lane,
    WorkList,
)
from core.model import Contributor, Edition, Library, Session, create
from core.util import LanguageCodes


def load_lanes(_db, library):
    """Return a WorkList that reflects the current lane structure of the
    Library.

    If no top-level visible lanes are configured, the WorkList will be
    configured to show every book in the collection.

    If a single top-level Lane is configured, it will returned as the
    WorkList.

    Otherwise, a WorkList containing the visible top-level lanes is
    returned.
    """
    top_level = WorkList.top_level_for_library(_db, library)

    # It's likely this WorkList will be used across sessions, so
    # expunge any data model objects from the database session.
    #
    # TODO: This is the cause of a lot of problems in the cached OPDS
    # feed generator. There, these Lanes are used in a normal database
    # session and we end up needing hacks to merge them back into the
    # session.
    if isinstance(top_level, Lane):
        to_expunge = [top_level]
    else:
        to_expunge = [x for x in top_level.children if isinstance(x, Lane)]
    list(map(_db.expunge, to_expunge))
    return top_level


def _lane_configuration_from_collection_sizes(estimates):
    """Sort a library's collections into 'large' and 'small'
    subcollections based on language.

    :param estimates: A Counter.

    :return: A list.
    """
    if not estimates:
        # There are no holdings. Assume we have a large Finnish
        # collection and nothing else.
        return ["fin"], []

    large = []
    small = []

    # E-kirjasto: We don't actually care about the amounts, we're just
    # interested in the languages in the estimates so that we get all the
    # smaller languages to make up the "Other Languages" lanes.
    for language, _ in estimates.most_common():
        print(language)
        # We want to be exact with our large languages because we know
        # what they are.
        if language in {"fin", "swe", "eng"}:
            large.append(language)
        else:
            small.append(language)
    # Return lists for each category
    print(large, small)
    return large, small


def create_default_lanes(_db, library):
    """Reset the lanes for the given library to the default.

    This method will create a set of default lanes. For E-kirjasto,
    they will always be the same: Finnish, Swedish and English as
    "large" languages and any others as "small" languages ending up
    in Books in Other Languages lane.

    When run on a Library that already has Lane configuration, this is
    an extremely destructive method. All new Lanes will be visible
    and all Lanes based on CustomLists (but not the CustomLists
    themselves) will be destroyed.
    """
    # Delete existing lanes. This includes custom lanes as well.
    for lane in _db.query(Lane).filter(Lane.library_id == library.id):
        _db.delete(lane)

    # Finland: Lanes from default library are shown for all other
    # libraries. For other libraries this reset just means deleting
    # all custom lanes.
    if not library.is_default:
        return

    # E-kirjasto does not configure languages. Keeping it for tests.
    large = Configuration.large_collection_languages(library) or []
    small = Configuration.small_collection_languages(library) or []
    tiny = Configuration.tiny_collection_languages(library) or []

    # E-kirjasto will always have a Finnish, Swedish and English collection but
    # others may change over time. Get the current languages.
    if not large and not small and not tiny:
        estimates = library.estimated_holdings_by_language()
        large, small = _lane_configuration_from_collection_sizes(estimates)

    if "fin" in large:
        create_lanes_for_finnish_collection(_db, library)
    if "swe" in large:
        create_lanes_for_swedish_collection(_db, library)
    if "eng" in large:
        create_lanes_for_english_collection(_db, library)
    create_world_languages_lane(_db, library, small)


def lane_from_genres(
    _db,
    library,
    genres,
    display_name=None,
    exclude_genres=None,
    priority=0,
    audiences=None,
    **extra_args
):
    """Turn genre info into a Lane object."""

    # Create sublanes first.
    sublanes = []
    for genre in genres:
        if isinstance(genre, dict):
            sublane_priority = 0
            for subgenre in genre.get("subgenres", []):
                sublanes.append(
                    lane_from_genres(
                        _db,
                        library,
                        [subgenre],
                        priority=sublane_priority,
                        **extra_args
                    )
                )
                sublane_priority += 1

    # Now that we have sublanes we don't care about subgenres anymore.
    genres = [
        genre.get("name")
        if isinstance(genre, dict)
        else genre.name
        if isinstance(genre, GenreData)
        else genre
        for genre in genres
    ]

    exclude_genres = [
        genre.get("name")
        if isinstance(genre, dict)
        else genre.name
        if isinstance(genre, GenreData)
        else genre
        for genre in exclude_genres or []
    ]

    fiction = None
    visible = True
    if len(genres) == 1:
        if classifier.genres.get(genres[0]):
            genredata = classifier.genres[genres[0]]
        else:
            genredata = GenreData(genres[0], False)
        fiction = genredata.is_fiction

    if not display_name:
        display_name = ", ".join(sorted(genres))

    lane, ignore = create(
        _db,
        Lane,
        library_id=library.id,
        display_name=display_name,
        fiction=fiction,
        audiences=audiences,
        sublanes=sublanes,
        priority=priority,
        **extra_args
    )
    lane.visible = visible
    for genre in genres:
        lane.add_genre(genre)
    for genre in exclude_genres:
        lane.add_genre(genre, inclusive=False)
    return lane


def create_lanes_for_finnish_collection(_db, library, language="fin", priority=1000):
    """
    Defines the lanes of the Finnish collection.

    :param library: Newly created lanes will be associated with this
        library.
    :param language: Newly created lanes will contain only books
        in these languages.
    :param priority: An integer that defines the order of main lanes on the main view.

    :return: A list of top-level Lane objects.

    """
    if isinstance(language, str):
        language = [language]

    ADULT = SubjectClassifier.AUDIENCES_ADULT
    YA = [SubjectClassifier.AUDIENCE_YOUNG_ADULT]

    common_args = dict(languages=language, media=None)
    adult_common_args = dict(common_args)
    adult_common_args["audiences"] = ADULT

    # The main lanes shown on the main view.
    main_lanes = []

    adult_fiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Aikuisten kaunokirjat",
        genres=[],
        sublanes=[],
        fiction=True,
        priority=priority,
        **adult_common_args
    )
    priority += 1
    main_lanes.append(adult_fiction)

    # Order of fiction lanes.
    adult_fiction_priority = 0

    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Yleinen kaunokirjallisuus",
            genres=[
                genres.General_Fiction,
                genres.LGBTQ_Fiction,
                genres.Urban_Fiction,
                genres.Religious_Fiction,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction_suspense, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Jännitys",
        genres=[],
        fiction=True,
        sublanes=[],
        priority=adult_fiction_priority,
        **adult_common_args
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(adult_fiction_suspense)
    adult_fiction_suspense.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Jännitys",
            genres=[
                genres.Suspense_Thriller,
                genres.Historical_Thriller,
                genres.Espionage,
                genres.Supernatural_Thriller,
                genres.Medical_Thriller,
                genres.Political_Thriller,
                genres.Technothriller,
                genres.Legal_Thriller,
                genres.Military_Thriller,
            ],
            priority=1,
            **adult_common_args
        )
    )
    adult_fiction_suspense.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Dekkarit",
            genres=[
                genres.Mystery,
                genres.Crime_Detective_Stories,
                genres.Hard_Boiled_Mystery,
                genres.Police_Procedural,
                genres.Cozy_Mystery,
                genres.Historical_Mystery,
                genres.Paranormal_Mystery,
                genres.Women_Detectives,
            ],
            priority=2,
            **adult_common_args
        )
    )
    adult_fiction_suspense.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Hyvän mielen dekkarit",
            genres=[genres.Cozy_Mystery],
            priority=3,
            **adult_common_args
        )
    )
    adult_fiction_suspense.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Seikkailu",
            genres=[genres.Adventure],
            priority=4,
            **adult_common_args
        )
    )
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Fantasia",
            genres=[
                genres.Fantasy,
                genres.Epic_Fantasy,
                genres.Historical_Fantasy,
                genres.Urban_Fantasy,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Kauhu",
            genres=[
                genres.Horror,
                genres.Gothic_Horror,
                genres.Ghost_Stories,
                genres.Vampires,
                genres.Werewolves,
                genres.Occult_Horror,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Scifi",
            genres=[
                genres.Science_Fiction,
                genres.Dystopian_SF,
                genres.Space_Opera,
                genres.Cyberpunk,
                genres.Military_SF,
                genres.Alternative_History,
                genres.Steampunk,
                genres.Romantic_SF,
                genres.Media_Tie_in_SF,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Historialliset romaanit",
            genres=[
                genres.Historical_Fiction,
                genres.Westerns,
                genres.Historical_Fantasy,
                genres.Historical_Mystery,
                genres.Historical_Romance,
                genres.Historical_Thriller,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Romantiikka",
            genres=[
                genres.Romance,
                genres.Contemporary_Romance,
                genres.Gothic_Romance,
                genres.Historical_Romance,
                genres.Paranormal_Romance,
                genres.Western_Romance,
                genres.Romantic_Suspense,
                genres.Erotica,
                genres.Romantic_SF,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Sarjakuvat",
            genres=[genres.Comics_Graphic_Novels],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Huumori",
            genres=[genres.Humor, genres.Humorous_Fiction],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Klassikot ja kansantarinat",
            genres=[genres.Classics, genres.Folklore_Stories],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Novellit",
            genres=[genres.Short_Stories],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Runot ja näytelmät",
            genres=[genres.Poetry, genres.Drama],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Kaikki aikuisten kaunokirjat",
        fiction=True,
        priority=adult_fiction_priority,
        **adult_common_args
    )
    adult_fiction.sublanes.append(adult_fiction_all)

    # Adult nonfiction.
    adult_nonfiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Aikuisten tietokirjat",
        genres=[],
        sublanes=[],
        fiction=False,
        priority=priority,
        **adult_common_args
    )
    priority += 1
    main_lanes.append(adult_fiction)

    # Order of nonfiction lanes.
    adult_nonfiction_priority = 0

    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Elämäkerrat ja muistelmat",
            genres=[genres.Biography_Memoir],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Historia",
            genres=[
                genres.History,
                genres.African_History,
                genres.Ancient_History,
                genres.Asian_History,
                genres.Civil_War_History,
                genres.European_History,
                genres.Latin_American_History,
                genres.Medieval_History,
                genres.Middle_East_History,
                genres.Military_History,
                genres.Modern_History,
                genres.Renaissance_Early_Modern_History,
                genres.United_States_History,
                genres.World_History,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_society, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Yhteiskunta",
        genres=[],
        sublanes=[],
        priority=adult_nonfiction_priority,
        **adult_common_args
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(adult_society)
    adult_society.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Filosofia",
            genres=[genres.Philosophy],
            priority=1,
            **adult_common_args
        )
    )
    adult_society.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Yhteiskunta",
            genres=[
                genres.Social_Sciences,
                genres.Political_Science,
                genres.Law,
                genres.Study_Aids,
            ],
            priority=2,
            **adult_common_args
        )
    )
    adult_economics, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Talous ja johtaminen",
        genres=[],
        sublanes=[],
        priority=adult_nonfiction_priority,
        **adult_common_args
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(adult_economics)
    adult_economics.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Sijoittaminen",
            genres=[
                genres.Personal_Finance_Business,
                genres.Personal_Finance_Investing,
                genres.Real_Estate,
            ],
            priority=1,
            **adult_common_args
        )
    )
    adult_economics.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Liiketalous ja johtaminen",
            genres=[genres.Business, genres.Management_Leadership],
            priority=2,
            **adult_common_args
        )
    )
    adult_economics.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Taloustiede",
            genres=[genres.Economics],
            priority=3,
            **adult_common_args
        )
    )
    adult_psychology, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Psykologia ja elämäntaito",
        genres=[],
        sublanes=[],
        priority=adult_nonfiction_priority,
        **adult_common_args
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(adult_psychology)
    adult_psychology.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Elämäntaito",
            genres=[genres.Self_Help, genres.Life_Strategies],
            priority=1,
            **adult_common_args
        )
    )
    adult_psychology.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Psykologia",
            genres=[genres.Psychology],
            priority=2,
            **adult_common_args
        )
    )
    adult_psychology.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Filosofia",
            genres=[genres.Philosophy],
            priority=3,
            **adult_common_args
        )
    )
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Matkailu",
            genres=[genres.Travel],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_spirit, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Uskonto ja hengellisyys",
        genres=[],
        sublanes=[],
        priority=adult_nonfiction_priority,
        **adult_common_args
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(adult_spirit)
    adult_spirit.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Keho ja mieli",
            genres=[genres.Body_Mind_Spirit],
            priority=1,
            **adult_common_args
        )
    )
    adult_spirit.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Uskonnot",
            genres=[
                genres.Religion_Spirituality,
                genres.Buddhism,
                genres.Christianity,
                genres.Hinduism,
                genres.Islam,
                genres.Judaism,
            ],
            priority=2,
            **adult_common_args
        )
    )
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Vanhemmuus ja perhe",
            genres=[
                genres.Parenting_Family,
                genres.Family_Relationships,
                genres.Parenting,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Ruoka ja terveys",
            genres=[
                genres.Food_Health,
                genres.Cooking,
                genres.Health_Diet,
                genres.Vegetarian_Vegan,
                genres.Bartending_Cocktails,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Harrastukset ja koti",
            genres=[
                genres.Hobbies_Home,
                genres.Antiques_Collectibles,
                genres.Crafts_Hobbies,
                genres.Gardening,
                genres.Games_Activities,
                genres.House_Home,
                genres.Pets,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Urheilu",
            genres=[genres.Sports],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Luonto ja eläimet",
            genres=[genres.Nature],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_science, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Tiede ja teknologia",
        genres=[],
        sublanes=[],
        priority=adult_nonfiction_priority,
        **adult_common_args
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(adult_science)
    adult_science.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Teknologia",
            genres=[genres.Technology, genres.Computers],
            priority=1,
            **adult_common_args
        )
    )
    adult_science.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Luonnontieteet",
            genres=[genres.Mathematics, genres.Science],
            priority=2,
            **adult_common_args
        )
    )
    adult_science.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Lääketiede",
            genres=[genres.Medical],
            priority=3,
            **adult_common_args
        )
    )
    adult_science.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Kielet ja kirjallisuus",
            genres=[
                genres.Dictionaries,
                genres.Foreign_Language_Study,
                genres.Literary_Criticism,
            ],
            priority=4,
            **adult_common_args
        )
    )
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="True Crime",
            genres=[genres.True_Crime],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Taiteet",
            genres=[
                genres.Art_Culture,
                genres.Film_TV,
                genres.Music,
                genres.Performing_Arts,
                genres.Architecture,
                genres.Art,
                genres.Art_Criticism_Theory,
                genres.Art_History,
                genres.Design,
                genres.Fashion,
                genres.Photography,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Muut tietokirjat",
            genres=[
                genres.Humorous_Nonfiction,
                genres.Reference_Study_Aids,
                genres.Folklore,
                genres.Education,
                genres.Periodicals,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Kaikki aikuisten tietokirjat",
        fiction=False,
        priority=adult_nonfiction_priority,
        **adult_common_args
    )
    adult_nonfiction.sublanes.append(adult_nonfiction_all)

    # YA lanes
    ya_common_args = dict(common_args)
    ya_common_args["audiences"] = YA

    ya_books, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Nuortenkirjat",
        genres=[],
        sublanes=[],
        priority=priority,
        **ya_common_args
    )
    priority += 1
    main_lanes.append(ya_books)

    # Order of YA lanes.
    ya_priority = 0

    ya_general, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Yleinen kaunokirjallisuus",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_general.add_genres(
        [
            genres.General_Fiction.name,
            genres.Diary_Stories.name,
            genres.Family_Stories.name,
            genres.Festivities_Holidays.name,
            genres.Easter_Stories.name,
            genres.Christmas_Stories.name,
            genres.Halloween_Stories.name,
            genres.Classics.name,
            genres.School_Study.name,
            genres.Drama.name,
            genres.Folklore_Stories.name,
            genres.LGBTQ_Fiction.name,
            genres.Historical_Fiction.name,
            genres.Growing_Up.name,
        ]
    )
    ya_books.sublanes.append(ya_general)
    ya_suspense, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Jännitys",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_suspense.add_genres(
        [
            genres.Mystery.name,
            genres.Crime_Detective_Stories.name,
            genres.Suspense_Thriller.name,
            genres.Adventure.name,
        ]
    )
    ya_books.sublanes.append(ya_suspense)
    ya_fantasy, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Fantasia",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_fantasy.add_genre(genres.Fantasy.name)
    ya_books.sublanes.append(ya_fantasy)
    ya_scifi, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Scifi",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_scifi.add_genre(genres.Science_Fiction.name)
    ya_books.sublanes.append(ya_scifi)
    ya_horror, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Kauhu",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_horror.add_genre(genres.Horror.name)
    ya_books.sublanes.append(ya_horror)
    ya_romance, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Romantiikka",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_romance.add_genre(genres.Romance.name)
    ya_books.sublanes.append(ya_romance)
    ya_sports, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Urheilu",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_sports.add_genres(
        [
            genres.Sports_Stories.name,
            genres.Football_Stories.name,
            genres.Hockey_Stories.name,
            genres.Dance_Stories.name,
            genres.Riding_Stories.name,
            genres.Skating_Stories.name,
        ]
    )
    ya_books.sublanes.append(ya_sports)
    ya_animals, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Eläimet",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_animals.add_genres(
        [genres.Animal_Stories.name, genres.Horse_Stories.name, genres.Pet_Stories.name]
    )
    ya_books.sublanes.append(ya_animals)
    ya_humor, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Huumori",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_humor.add_genres([genres.Humor.name, genres.Humorous_Fiction.name])
    ya_books.sublanes.append(ya_humor)
    ya_comics, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Sarjakuvat",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_comics.add_genre(genres.Comics_Graphic_Novels.name)
    ya_books.sublanes.append(ya_comics)
    ya_poetry, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Runot",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_poetry.add_genre(genres.Poetry.name)
    ya_books.sublanes.append(ya_poetry)
    ya_difficult, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Vaikeat aiheet",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_difficult.add_genres(
        [
            genres.Difficult_Topics.name,
            genres.Bullying.name,
            genres.Death.name,
            genres.Multicultural_Stories.name,
            genres.Disabilities.name,
            genres.War.name,
            genres.Drugs.name,
            genres.Eating_Disorders_Self_Image.name,
            genres.Mental_Health.name,
        ]
    )
    ya_books.sublanes.append(ya_difficult)
    ya_nonfiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Tietokirjat",
        genres=[],
        fiction=False,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_books.sublanes.append(ya_nonfiction)
    ya_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Kaikki nuortenkirjat",
        genres=[],
        priority=ya_priority,
        **ya_common_args
    )
    ya_books.sublanes.append(ya_all)

    # Children's lanes
    children_common_args = dict(common_args)
    children_common_args["target_age"] = SubjectClassifier.range_tuple(
        0, SubjectClassifier.YOUNG_ADULT_AGE_CUTOFF - 2
    )

    childrens_books, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Lastenkirjat",
        genres=[],
        sublanes=[],
        priority=priority,
        **children_common_args
    )
    priority += 1
    main_lanes.append(childrens_books)

    # Order of children's lanes.
    children_priority = 0

    children_general, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Yleinen kaunokirjallisuus",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_general.add_genres(
        [
            genres.General_Fiction.name,
            genres.Diary_Stories.name,
            genres.Family_Stories.name,
            genres.Festivities_Holidays.name,
            genres.Easter_Stories.name,
            genres.Christmas_Stories.name,
            genres.Halloween_Stories.name,
            genres.Classics.name,
            genres.School_Study.name,
            genres.Drama.name,
            genres.Folklore_Stories.name,
            genres.LGBTQ_Fiction.name,
            genres.Sports_Stories.name,
            genres.Vehicles_Technology.name,
            genres.Cars.name,
            genres.Trains.name,
            genres.Airplanes.name,
        ]
    )
    childrens_books.sublanes.append(children_general)
    children_animals, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Eläimet",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_animals.add_genres(
        [genres.Animal_Stories.name, genres.Horse_Stories.name, genres.Pet_Stories.name]
    )
    childrens_books.sublanes.append(children_animals)
    children_humor, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Huumori",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_humor.add_genres([genres.Humor.name, genres.Humorous_Fiction.name])
    childrens_books.sublanes.append(children_humor)
    children_suspense, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Jännitys",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_suspense.add_genres(
        [
            genres.Mystery.name,
            genres.Crime_Detective_Stories.name,
            genres.Suspense_Thriller.name,
            genres.Historical_Thriller.name,
            genres.Adventure.name,
            genres.Superheroes.name,
        ]
    )
    childrens_books.sublanes.append(children_suspense)
    children_scifi, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Scifi ja fantasia",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_scifi.add_genres([genres.Science_Fiction.name, genres.Fantasy.name])
    childrens_books.sublanes.append(children_scifi)
    children_horror, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Kauhu",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_horror.add_genre(genres.Horror.name)
    childrens_books.sublanes.append(children_horror)
    picture_books, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Kuvakirjat",
        target_age=(0, 4),
        genres=[],
        fiction=None,
        priority=children_priority,
    )
    children_priority += 1
    childrens_books.sublanes.append(picture_books)
    children_comics, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Sarjakuvat",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_comics.add_genre(genres.Comics_Graphic_Novels.name)
    childrens_books.sublanes.append(children_comics)
    children_poetry, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Runot",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_poetry.add_genre(genres.Poetry.name)
    childrens_books.sublanes.append(children_poetry)
    children_difficult, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Vaikeat aiheet",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_difficult.add_genres(
        [
            genres.Difficult_Topics.name,
            genres.Bullying.name,
            genres.Death.name,
            genres.Multicultural_Stories.name,
            genres.Disabilities.name,
            genres.War.name,
            genres.Drugs.name,
            genres.Eating_Disorders_Self_Image.name,
            genres.Mental_Health.name,
        ]
    )
    childrens_books.sublanes.append(children_difficult)
    children_nonfiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Tietokirjat",
        genres=[],
        fiction=False,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    childrens_books.sublanes.append(children_nonfiction)
    children_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Kaikki lastenkirjat",
        genres=[],
        priority=children_priority,
        **children_common_args
    )
    childrens_books.sublanes.append(children_all)
    return priority


def create_lanes_for_swedish_collection(_db, library, language="swe", priority=2000):
    """
    Defines the lanes of the Swedish collection.

    :param library: Newly created lanes will be associated with this
        library.
    :param language: Newly created lanes will contain only books
        in these languages.
    :param priority: An integer that defines the order of main lanes on the main view.

    :return: A list of top-level Lane objects.

    """
    if isinstance(language, str):
        language = [language]

    ADULT = SubjectClassifier.AUDIENCES_ADULT
    YA = [SubjectClassifier.AUDIENCE_YOUNG_ADULT]

    common_args = dict(languages=language, media=None)
    adult_common_args = dict(common_args)
    adult_common_args["audiences"] = ADULT

    main_lanes = []

    all_swedish, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Böcker på svenska",
        genres=[],
        sublanes=[],
        priority=priority,
        **adult_common_args
    )
    priority += 1
    main_lanes.append(all_swedish)

    adult_fiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Skönlitteratur för vuxen",
        genres=[],
        fiction=True,
        sublanes=[],
        priority=1,
        **adult_common_args
    )
    all_swedish.sublanes.append(adult_fiction)

    # Order of fiction lanes.
    adult_fiction_priority = 0

    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Skönlitteratur allmänt",
            genres=[
                genres.General_Fiction,
                genres.LGBTQ_Fiction,
                genres.Urban_Fiction,
                genres.Religious_Fiction,
                genres.Humor,
                genres.Humorous_Fiction,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Historiska romaner",
            genres=[
                genres.Historical_Fiction,
                genres.Westerns,
                genres.Historical_Fantasy,
                genres.Historical_Mystery,
                genres.Historical_Romance,
                genres.Historical_Thriller,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Deckare",
            genres=[
                genres.Mystery,
                genres.Crime_Detective_Stories,
                genres.Hard_Boiled_Mystery,
                genres.Police_Procedural,
                genres.Cozy_Mystery,
                genres.Historical_Mystery,
                genres.Paranormal_Mystery,
                genres.Women_Detectives,
                genres.Cozy_Mystery,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Spänning",
            genres=[
                genres.Suspense_Thriller,
                genres.Historical_Thriller,
                genres.Espionage,
                genres.Supernatural_Thriller,
                genres.Medical_Thriller,
                genres.Political_Thriller,
                genres.Technothriller,
                genres.Legal_Thriller,
                genres.Military_Thriller,
                genres.Adventure,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Romantik",
            genres=[
                genres.Romance,
                genres.Contemporary_Romance,
                genres.Gothic_Romance,
                genres.Historical_Romance,
                genres.Paranormal_Romance,
                genres.Western_Romance,
                genres.Romantic_Suspense,
                genres.Erotica,
                genres.Romantic_SF,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1

    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Fantasi",
            genres=[
                genres.Fantasy,
                genres.Epic_Fantasy,
                genres.Historical_Fantasy,
                genres.Urban_Fantasy,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Skräck",
            genres=[
                genres.Horror,
                genres.Gothic_Horror,
                genres.Ghost_Stories,
                genres.Vampires,
                genres.Werewolves,
                genres.Occult_Horror,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Scifi",
            genres=[
                genres.Science_Fiction,
                genres.Dystopian_SF,
                genres.Space_Opera,
                genres.Cyberpunk,
                genres.Military_SF,
                genres.Alternative_History,
                genres.Steampunk,
                genres.Romantic_SF,
                genres.Media_Tie_in_SF,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Klassiker och folkdikter",
            genres=[genres.Classics, genres.Folklore_Stories],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Dikter och skådespel",
            genres=[genres.Poetry, genres.Drama],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Tecknade serier",
            genres=[genres.Comics_Graphic_Novels],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Noveller",
            genres=[genres.Short_Stories],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Alla skönlitteratur för vuxen",
        genres=[],
        priority=adult_fiction_priority,
        **adult_common_args
    )
    adult_fiction.sublanes.append(adult_fiction_all)

    # Adult nonfiction
    adult_nonfiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Facklitteratur för vuxen",
        genres=[],
        sublanes=[],
        fiction=False,
        priority=2,
        **adult_common_args
    )
    all_swedish.sublanes.append(adult_nonfiction)

    # Order of nonfiction lanes.
    adult_nonfiction_priority = 0

    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Biografier och memoarer",
            genres=[genres.Biography_Memoir],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Historia",
            genres=[
                genres.History,
                genres.African_History,
                genres.Ancient_History,
                genres.Asian_History,
                genres.Civil_War_History,
                genres.European_History,
                genres.Latin_American_History,
                genres.Medieval_History,
                genres.Middle_East_History,
                genres.Military_History,
                genres.Modern_History,
                genres.Renaissance_Early_Modern_History,
                genres.United_States_History,
                genres.World_History,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Samhälle",
            genres=[
                genres.Social_Sciences,
                genres.Political_Science,
                genres.Law,
                genres.Study_Aids,
                genres.Philosophy,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Ekonomi och management",
            genres=[
                genres.Personal_Finance_Business,
                genres.Personal_Finance_Investing,
                genres.Real_Estate,
                genres.Business,
                genres.Management_Leadership,
                genres.Economics,
            ],
            priority=1,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Psykologi och självhjälpsböcker",
            genres=[genres.Self_Help, genres.Life_Strategies, genres.Psychology],
            priority=1,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Hem och hobbyer",
            genres=[
                genres.Hobbies_Home,
                genres.Antiques_Collectibles,
                genres.Crafts_Hobbies,
                genres.Gardening,
                genres.Games_Activities,
                genres.House_Home,
                genres.Pets,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Mat och hälsa",
            genres=[
                genres.Food_Health,
                genres.Cooking,
                genres.Health_Diet,
                genres.Vegetarian_Vegan,
                genres.Bartending_Cocktails,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Vetenskap och teknologi",
            genres=[
                genres.Technology,
                genres.Computers,
                genres.Mathematics,
                genres.Science,
                genres.Medical,
                genres.Nature,
                genres.Social_Sciences,
            ],
            priority=1,
            **adult_common_args
        )
    )
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Idrott",
            genres=[genres.Sports],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Resor",
            genres=[genres.Travel],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Andra faktaböcker",
            genres=[
                genres.Dictionaries,
                genres.Foreign_Language_Study,
                genres.Literary_Criticism,
                genres.Body_Mind_Spirit,
                genres.Religion_Spirituality,
                genres.Buddhism,
                genres.Christianity,
                genres.Hinduism,
                genres.Islam,
                genres.Judaism,
                genres.Parenting_Family,
                genres.Family_Relationships,
                genres.Parenting,
                genres.True_Crime,
                genres.Art_Culture,
                genres.Film_TV,
                genres.Music,
                genres.Performing_Arts,
                genres.Architecture,
                genres.Art,
                genres.Art_Criticism_Theory,
                genres.Art_History,
                genres.Design,
                genres.Fashion,
                genres.Photography,
                genres.Humorous_Nonfiction,
                genres.Reference_Study_Aids,
                genres.Folklore,
                genres.Education,
                genres.Periodicals,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Alla facklitteratur för vuxen",
        fiction=False,
        priority=adult_nonfiction_priority,
        **adult_common_args
    )
    adult_nonfiction.sublanes.append(adult_nonfiction_all)

    # # YA lanes
    ya_common_args = dict(common_args)
    ya_common_args["audiences"] = YA

    ya_books, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Böcker för ungdomar",
        genres=[],
        sublanes=[],
        priority=3,
        **ya_common_args
    )
    all_swedish.sublanes.append(ya_books)

    # Order of YA lanes.
    ya_priority = 0

    ya_general, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Skönlitteratur allmänt",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_general.add_genres(
        [
            genres.General_Fiction.name,
            genres.Diary_Stories.name,
            genres.Family_Stories.name,
            genres.Festivities_Holidays.name,
            genres.Easter_Stories.name,
            genres.Christmas_Stories.name,
            genres.Halloween_Stories.name,
            genres.Classics.name,
            genres.School_Study.name,
            genres.Drama.name,
            genres.Folklore_Stories.name,
            genres.Westerns.name,
            genres.LGBTQ_Fiction.name,
            genres.Sports_Stories.name,
            genres.Football_Stories.name,
            genres.Hockey_Stories.name,
            genres.Dance_Stories.name,
            genres.Riding_Stories.name,
            genres.Skating_Stories.name,
            genres.Animal_Stories.name,
            genres.Horse_Stories.name,
            genres.Pet_Stories.name,
            genres.Humor.name,
            genres.Humorous_Fiction.name,
            genres.Comics_Graphic_Novels.name,
            genres.Difficult_Topics.name,
            genres.Bullying.name,
            genres.Death.name,
            genres.Multicultural_Stories.name,
            genres.Disabilities.name,
            genres.War.name,
            genres.Drugs.name,
            genres.Eating_Disorders_Self_Image.name,
            genres.Mental_Health.name,
            genres.Growing_Up.name,
        ]
    )
    ya_books.sublanes.append(ya_general)
    ya_fantasy, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Fantasi",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_fantasy.add_genre(genres.Fantasy.name)
    ya_books.sublanes.append(ya_fantasy)
    ya_adventure, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Äventyr",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_adventure.add_genre(genres.Adventure.name)
    ya_books.sublanes.append(ya_adventure)
    ya_suspense, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Spänning",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_suspense.add_genres(
        [
            genres.Mystery.name,
            genres.Crime_Detective_Stories.name,
            genres.Suspense_Thriller.name,
        ]
    )
    ya_books.sublanes.append(ya_suspense)
    ya_scifi, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Scifi",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_scifi.add_genre(genres.Science_Fiction.name)
    ya_books.sublanes.append(ya_scifi)
    ya_horror, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Skräk",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_horror.add_genre(genres.Horror.name)
    ya_books.sublanes.append(ya_horror)
    ya_historical, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Historiska romaner",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_historical.add_genre(genres.Historical_Fiction.name)
    ya_books.sublanes.append(ya_historical)
    ya_romance, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Romantik",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_romance.add_genre(genres.Romance.name)
    ya_books.sublanes.append(ya_romance)
    ya_poetry, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Dikter och skådespel",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_poetry.add_genres([genres.Poetry.name, genres.Drama.name])
    ya_books.sublanes.append(ya_poetry)
    ya_nonfiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Facklitteratur",
        genres=[],
        fiction=False,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_books.sublanes.append(ya_nonfiction)
    ya_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Alla böcker för ungdomar",
        genres=[],
        priority=ya_priority,
        **ya_common_args
    )
    ya_books.sublanes.append(ya_all)

    # Children's lanes
    children_common_args = dict(common_args)
    children_common_args["target_age"] = SubjectClassifier.range_tuple(
        0, SubjectClassifier.YOUNG_ADULT_AGE_CUTOFF - 2
    )

    childrens_books, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Böcker för barn",
        genres=[],
        sublanes=[],
        priority=4,
        **children_common_args
    )
    all_swedish.sublanes.append(childrens_books)

    # Order of children's lanes.
    children_priority = 0

    children_general, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Skönlitteratur allmänt",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_general.add_genres(
        [
            genres.General_Fiction.name,
            genres.Diary_Stories.name,
            genres.Family_Stories.name,
            genres.Festivities_Holidays.name,
            genres.Classics.name,
            genres.School_Study.name,
            genres.Drama.name,
            genres.Folklore_Stories.name,
            genres.LGBTQ_Fiction.name,
            genres.Sports_Stories.name,
            genres.Football_Stories.name,
            genres.Hockey_Stories.name,
            genres.Dance_Stories.name,
            genres.Riding_Stories.name,
            genres.Skating_Stories.name,
            genres.Animal_Stories.name,
            genres.Horse_Stories.name,
            genres.Pet_Stories.name,
            genres.Humor.name,
            genres.Humorous_Fiction.name,
            genres.Comics_Graphic_Novels.name,
            genres.Difficult_Topics.name,
            genres.Bullying.name,
            genres.Death.name,
            genres.Multicultural_Stories.name,
            genres.Disabilities.name,
            genres.War.name,
            genres.Drugs.name,
            genres.Eating_Disorders_Self_Image.name,
            genres.Mental_Health.name,
            genres.Vehicles_Technology.name,
            genres.Cars.name,
            genres.Trains.name,
            genres.Airplanes.name,
        ]
    )
    childrens_books.sublanes.append(children_general)
    picture_books, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Bilderböcker",
        target_age=(0, 4),
        genres=[],
        fiction=None,
        priority=children_priority,
    )
    children_priority += 1
    childrens_books.sublanes.append(picture_books)
    children_suspense, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Spänning",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_suspense.add_genres(
        [
            genres.Mystery.name,
            genres.Crime_Detective_Stories.name,
            genres.Suspense_Thriller.name,
            genres.Adventure.name,
            genres.Superheroes.name,
        ]
    )
    childrens_books.sublanes.append(children_suspense)
    children_adventure, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Äventyr",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_adventure.add_genre(genres.Adventure.name)
    childrens_books.sublanes.append(children_adventure)
    children_humor, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Humor",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_humor.add_genres([genres.Humor.name, genres.Humorous_Fiction.name])
    childrens_books.sublanes.append(children_humor)
    children_horror, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Skräk",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_horror.add_genre(genres.Horror.name)
    childrens_books.sublanes.append(children_horror)
    children_scifi, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Scifi och fantasi",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_scifi.add_genres([genres.Science_Fiction.name, genres.Fantasy.name])
    childrens_books.sublanes.append(children_scifi)
    children_historical, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Historiska romaner",
        fiction=True,
        priority=children_priority,
        **ya_common_args
    )
    children_priority += 1
    children_historical.add_genre(genres.Historical_Fiction.name)
    childrens_books.sublanes.append(children_historical)
    children_poetry, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Dikter och skådespel",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_poetry.add_genres([genres.Poetry.name, genres.Drama.name])
    childrens_books.sublanes.append(children_poetry)
    children_nonfiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Facklitteratur",
        genres=[],
        fiction=False,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    childrens_books.sublanes.append(children_nonfiction)
    children_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Alla böcker för barn",
        genres=[],
        priority=children_priority,
        **children_common_args
    )
    childrens_books.sublanes.append(children_all)

    return priority


def create_lanes_for_english_collection(_db, library, language="eng", priority=3000):
    """
    Defines the lanes of the English collection.

    :param library: Newly created lanes will be associated with this
        library.
    :param language: Newly created lanes will contain only books
        in these languages.
    :param priority: An integer that defines the order of main lanes on the main view.

    :return: A list of top-level Lane objects.

    """
    if isinstance(language, str):
        language = [language]

    ADULT = SubjectClassifier.AUDIENCES_ADULT
    YA = [SubjectClassifier.AUDIENCE_YOUNG_ADULT]

    common_args = dict(languages=language, media=None)
    adult_common_args = dict(common_args)
    adult_common_args["audiences"] = ADULT
    main_lanes = []

    all_english, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Books in English",
        genres=[],
        sublanes=[],
        priority=priority,
        **adult_common_args
    )
    priority += 1
    main_lanes.append(all_english)

    adult_fiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Fiction for Adults",
        genres=[],
        fiction=True,
        sublanes=[],
        priority=1,
        **adult_common_args
    )
    all_english.sublanes.append(adult_fiction)

    # Order of fiction lanes.
    adult_fiction_priority = 0

    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="General Fiction",
            genres=[
                genres.General_Fiction,
                genres.LGBTQ_Fiction,
                genres.Urban_Fiction,
                genres.Religious_Fiction,
                genres.Humor,
                genres.Humorous_Fiction,
                genres.Comics_Graphic_Novels,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_suspense, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Suspense",
        genres=[],
        fiction=True,
        sublanes=[],
        priority=adult_fiction_priority,
        **adult_common_args
    )
    adult_fiction_priority += 1
    adult_suspense.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Suspense",
            genres=[
                genres.Suspense_Thriller,
                genres.Historical_Thriller,
                genres.Espionage,
                genres.Supernatural_Thriller,
                genres.Medical_Thriller,
                genres.Political_Thriller,
                genres.Technothriller,
                genres.Legal_Thriller,
                genres.Military_Thriller,
            ],
            priority=1,
            **adult_common_args
        )
    )
    adult_fiction.sublanes.append(adult_suspense)
    adult_suspense.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Crime & Detective Stories",
            genres=[
                genres.Mystery,
                genres.Crime_Detective_Stories,
                genres.Hard_Boiled_Mystery,
                genres.Police_Procedural,
                genres.Cozy_Mystery,
                genres.Historical_Mystery,
                genres.Paranormal_Mystery,
                genres.Women_Detectives,
            ],
            priority=2,
            **adult_common_args
        )
    )
    adult_suspense.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Cozy Crime",
            genres=[genres.Cozy_Mystery],
            priority=3,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_suspense.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Adventure",
            genres=[genres.Adventure],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Romance",
            genres=[
                genres.Romance,
                genres.Contemporary_Romance,
                genres.Gothic_Romance,
                genres.Historical_Romance,
                genres.Paranormal_Romance,
                genres.Western_Romance,
                genres.Romantic_Suspense,
                genres.Erotica,
                genres.Romantic_SF,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="LGBTQ+",
            genres=[
                genres.LGBTQ_Fiction,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Science Fiction",
            genres=[
                genres.Science_Fiction,
                genres.Dystopian_SF,
                genres.Space_Opera,
                genres.Cyberpunk,
                genres.Military_SF,
                genres.Alternative_History,
                genres.Steampunk,
                genres.Romantic_SF,
                genres.Media_Tie_in_SF,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Fantasy",
            genres=[
                genres.Fantasy,
                genres.Epic_Fantasy,
                genres.Historical_Fantasy,
                genres.Urban_Fantasy,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Horror",
            genres=[
                genres.Horror,
                genres.Gothic_Horror,
                genres.Ghost_Stories,
                genres.Vampires,
                genres.Werewolves,
                genres.Occult_Horror,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Historical Fiction",
            genres=[
                genres.Historical_Fiction,
                genres.Westerns,
                genres.Historical_Fantasy,
                genres.Historical_Mystery,
                genres.Historical_Romance,
                genres.Historical_Thriller,
            ],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1

    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Short Stories",
            genres=[genres.Short_Stories],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Classics and Folklore",
            genres=[genres.Classics, genres.Folklore_Stories],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Poetry and Drama",
            genres=[genres.Poetry, genres.Drama],
            priority=adult_fiction_priority,
            **adult_common_args
        )
    )
    adult_fiction_priority += 1
    adult_fiction_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="All Fiction for Adults",
        genres=[],
        priority=adult_fiction_priority,
        **adult_common_args
    )
    adult_fiction.sublanes.append(adult_fiction_all)

    # Nonfiction lanes.
    adult_nonfiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Nonfiction for Adults",
        genres=[],
        sublanes=[],
        fiction=False,
        priority=2,
        **adult_common_args
    )
    all_english.sublanes.append(adult_nonfiction)

    # Order of nonfiction lanes.
    adult_nonfiction_priority = 0

    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Biography & Memoir",
            genres=[genres.Biography_Memoir],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="History",
            genres=[
                genres.History,
                genres.African_History,
                genres.Ancient_History,
                genres.Asian_History,
                genres.Civil_War_History,
                genres.European_History,
                genres.Latin_American_History,
                genres.Medieval_History,
                genres.Middle_East_History,
                genres.Military_History,
                genres.Modern_History,
                genres.Renaissance_Early_Modern_History,
                genres.United_States_History,
                genres.World_History,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Society",
            genres=[
                genres.Social_Sciences,
                genres.Political_Science,
                genres.Law,
                genres.Study_Aids,
                genres.Philosophy,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Economics & Management",
            genres=[
                genres.Personal_Finance_Business,
                genres.Personal_Finance_Investing,
                genres.Real_Estate,
                genres.Business,
                genres.Management_Leadership,
                genres.Economics,
            ],
            priority=1,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Psychology & Self-Help",
            genres=[genres.Self_Help, genres.Life_Strategies, genres.Psychology],
            priority=1,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Home & Hobbies",
            genres=[
                genres.Hobbies_Home,
                genres.Antiques_Collectibles,
                genres.Crafts_Hobbies,
                genres.Gardening,
                genres.Games_Activities,
                genres.House_Home,
                genres.Pets,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Health & Diet",
            genres=[
                genres.Food_Health,
                genres.Cooking,
                genres.Health_Diet,
                genres.Vegetarian_Vegan,
                genres.Bartending_Cocktails,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Science & Technology",
            genres=[
                genres.Technology,
                genres.Computers,
                genres.Mathematics,
                genres.Science,
                genres.Medical,
                genres.Nature,
                genres.Social_Sciences,
            ],
            priority=1,
            **adult_common_args
        )
    )
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Sports",
            genres=[genres.Sports],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Travel",
            genres=[genres.Travel],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Art",
            genres=[
                genres.Art_Culture,
                genres.Film_TV,
                genres.Music,
                genres.Performing_Arts,
                genres.Architecture,
                genres.Art,
                genres.Art_Criticism_Theory,
                genres.Art_History,
                genres.Design,
                genres.Fashion,
                genres.Photography,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction.sublanes.append(
        lane_from_genres(
            _db,
            library,
            display_name="Other Nonfiction",
            genres=[
                genres.Dictionaries,
                genres.Foreign_Language_Study,
                genres.Literary_Criticism,
                genres.Body_Mind_Spirit,
                genres.Religion_Spirituality,
                genres.Buddhism,
                genres.Christianity,
                genres.Hinduism,
                genres.Islam,
                genres.Judaism,
                genres.Parenting_Family,
                genres.Family_Relationships,
                genres.Parenting,
                genres.True_Crime,
                genres.Humorous_Nonfiction,
                genres.Reference_Study_Aids,
                genres.Folklore,
                genres.Education,
                genres.Periodicals,
            ],
            priority=adult_nonfiction_priority,
            **adult_common_args
        )
    )
    adult_nonfiction_priority += 1
    adult_nonfiction_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="All Nonfiction for Adults",
        fiction=False,
        priority=adult_nonfiction_priority,
        **adult_common_args
    )
    adult_nonfiction.sublanes.append(adult_nonfiction_all)

    # # YA lanes
    ya_common_args = dict(common_args)
    ya_common_args["audiences"] = YA

    ya_books, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Books for Young Adults",
        genres=[],
        sublanes=[],
        priority=3,
        **ya_common_args
    )
    all_english.sublanes.append(ya_books)

    # Order of YA lanes.
    ya_priority = 0
    ya_general, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="General Fiction",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_general.add_genres(
        [
            genres.General_Fiction.name,
            genres.Diary_Stories.name,
            genres.Family_Stories.name,
            genres.Festivities_Holidays.name,
            genres.Easter_Stories.name,
            genres.Christmas_Stories.name,
            genres.Halloween_Stories.name,
            genres.Classics.name,
            genres.School_Study.name,
            genres.Drama.name,
            genres.Folklore_Stories.name,
            genres.LGBTQ_Fiction.name,
            genres.Sports_Stories.name,
            genres.Football_Stories.name,
            genres.Hockey_Stories.name,
            genres.Dance_Stories.name,
            genres.Riding_Stories.name,
            genres.Skating_Stories.name,
            genres.Animal_Stories.name,
            genres.Horse_Stories.name,
            genres.Pet_Stories.name,
            genres.Humor.name,
            genres.Humorous_Fiction.name,
            genres.Comics_Graphic_Novels.name,
            genres.Difficult_Topics.name,
            genres.Bullying.name,
            genres.Death.name,
            genres.Multicultural_Stories.name,
            genres.Disabilities.name,
            genres.War.name,
            genres.Drugs.name,
            genres.Eating_Disorders_Self_Image.name,
            genres.Mental_Health.name,
            genres.Historical_Fiction.name,
            genres.Romance.name,
            genres.Poetry.name,
            genres.Growing_Up.name,
        ]
    )
    ya_books.sublanes.append(ya_general)
    ya_adventure, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Adventure",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_adventure.add_genre(genres.Adventure.name)
    ya_books.sublanes.append(ya_adventure)
    ya_fantasy, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Fantasy",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_fantasy.add_genre(genres.Fantasy.name)
    ya_books.sublanes.append(ya_fantasy)
    ya_suspense, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Suspense",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_suspense.add_genres(
        [
            genres.Mystery.name,
            genres.Crime_Detective_Stories.name,
            genres.Suspense_Thriller.name,
            genres.Horror.name,
        ]
    )
    ya_books.sublanes.append(ya_suspense)
    ya_scifi, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Science Fiction",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_scifi.add_genre(genres.Science_Fiction.name)
    ya_books.sublanes.append(ya_scifi)
    ya_nonfiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Nonfiction",
        genres=[],
        fiction=False,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    ya_books.sublanes.append(ya_nonfiction)
    ya_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="All Books for Young Adults",
        genres=[],
        priority=ya_priority,
        **ya_common_args
    )
    ya_books.sublanes.append(ya_all)

    # Children's lanes
    children_common_args = dict(common_args)
    children_common_args["target_age"] = SubjectClassifier.range_tuple(
        0, SubjectClassifier.YOUNG_ADULT_AGE_CUTOFF - 2
    )
    childrens_books, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Books for Children",
        genres=[],
        sublanes=[],
        priority=4,
        **children_common_args
    )
    all_english.sublanes.append(childrens_books)

    # Order of children's lanes.
    children_priority = 0

    children_general, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="General Fiction",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_general.add_genres(
        [
            genres.General_Fiction.name,
            genres.Diary_Stories.name,
            genres.Family_Stories.name,
            genres.Festivities_Holidays.name,
            genres.Classics.name,
            genres.School_Study.name,
            genres.Drama.name,
            genres.Folklore_Stories.name,
            genres.LGBTQ_Fiction.name,
            genres.Sports_Stories.name,
            genres.Football_Stories.name,
            genres.Hockey_Stories.name,
            genres.Dance_Stories.name,
            genres.Riding_Stories.name,
            genres.Skating_Stories.name,
            genres.Animal_Stories.name,
            genres.Horse_Stories.name,
            genres.Pet_Stories.name,
            genres.Humor.name,
            genres.Humorous_Fiction.name,
            genres.Comics_Graphic_Novels.name,
            genres.Difficult_Topics.name,
            genres.Bullying.name,
            genres.Death.name,
            genres.Multicultural_Stories.name,
            genres.Disabilities.name,
            genres.War.name,
            genres.Drugs.name,
            genres.Eating_Disorders_Self_Image.name,
            genres.Mental_Health.name,
            genres.Historical_Fiction.name,
            genres.Poetry.name,
            genres.Science_Fiction.name,
            genres.Fantasy.name,
            genres.Vehicles_Technology.name,
            genres.Cars.name,
            genres.Trains.name,
            genres.Airplanes.name,
        ]
    )
    childrens_books.sublanes.append(children_general)
    children_adventure, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Adventure",
        fiction=True,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    children_adventure.add_genre(genres.Adventure.name)
    childrens_books.sublanes.append(children_adventure)
    children_fantasy, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Fantasy",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    children_fantasy.add_genre(genres.Fantasy.name)
    childrens_books.sublanes.append(children_fantasy)
    children_suspense, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Suspense",
        fiction=True,
        priority=ya_priority,
        **ya_common_args
    )
    ya_priority += 1
    children_suspense.add_genres(
        [
            genres.Mystery.name,
            genres.Crime_Detective_Stories.name,
            genres.Suspense_Thriller.name,
            genres.Horror.name,
        ]
    )
    childrens_books.sublanes.append(children_suspense)
    picture_books, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Picture Books",
        target_age=(0, 4),
        genres=[],
        fiction=None,
        priority=children_priority,
    )
    children_priority += 1
    childrens_books.sublanes.append(picture_books)
    children_nonfiction, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="Nonfiction for Children",
        genres=[],
        fiction=False,
        priority=children_priority,
        **children_common_args
    )
    children_priority += 1
    childrens_books.sublanes.append(children_nonfiction)
    children_all, ignore = create(
        _db,
        Lane,
        library=library,
        display_name="All Books for Children",
        genres=[],
        priority=ya_priority,
        **ya_common_args
    )
    childrens_books.sublanes.append(children_all)

    return priority


def create_world_languages_lane(
    _db,
    library,
    small_languages,
    priority=4000,
):
    """Create a lane called 'Books in Other Languages' whose sublanes represent
    the non-large language collections available to this library.
    """
    if not small_languages:
        # All the languages on this system have large collections, so
        # there is no need for a 'World Languages' lane.
        return priority
    world_languages, ignore = create(
        _db,
        Lane,
        library=library,
        display_name=_("Books in Other Languages"),
        fiction=None,
        priority=priority,
        languages=small_languages,
        genres=[],
    )
    priority += 1

    language_priority = 0
    for small in small_languages:
        # Create a lane (with sublanes) for each small collection.
        language_priority = create_lane_for_small_collection(
            _db, library, world_languages, small, language_priority
        )
    return priority


def create_lane_for_small_collection(_db, library, parent, languages, priority=0):
    """Create a lane (with sublanes) for a small collection based on language,
    if the language exists in the lookup table. In E-kirjasto, this consists mainly of Sami language.

    :param parent: The parent of the new lane.
    """
    if isinstance(languages, str):
        languages = [languages]
    ADULT = SubjectClassifier.AUDIENCES_ADULT
    YA = (SubjectClassifier.AUDIENCE_YOUNG_ADULT,)
    CHILDREN = (SubjectClassifier.AUDIENCE_CHILDREN,)

    common_args = dict(
        languages=languages,
        genres=[],
    )

    try:
        language_identifier = LanguageCodes.name_for_languageset(languages)
    except ValueError as e:
        logging.getLogger().warning(
            "Could not create a lane for small collection with languages %s", languages
        )
        return 0

    sublane_priority = 0

    adults, ignore = create(
        _db,
        Lane,
        library=library,
        display_name=_("Books for Adults"),
        audiences=ADULT,
        priority=sublane_priority,
        **common_args
    )
    sublane_priority += 1

    ya, ignore = create(
        _db,
        Lane,
        library=library,
        display_name=_("Books for Young Adults"),
        audiences=YA,
        priority=sublane_priority,
        **common_args
    )
    sublane_priority += 1

    children, ignore = create(
        _db,
        Lane,
        library=library,
        display_name=_("Books for Children"),
        audiences=CHILDREN,
        priority=sublane_priority,
        **common_args
    )
    sublane_priority += 1

    lane, ignore = create(
        _db,
        Lane,
        library=library,
        display_name=language_identifier,
        parent=parent,
        sublanes=[adults, ya, children],
        priority=priority,
        **common_args
    )
    priority += 1
    return priority


class DynamicLane(WorkList):
    """A WorkList that's used to from an OPDS lane, but isn't a Lane
    in the database."""


class DatabaseExclusiveWorkList(DatabaseBackedWorkList):
    """A DatabaseBackedWorkList that can _only_ get Works through the database."""

    def works(self, *args, **kwargs):
        return self.works_from_database(*args, **kwargs)


class WorkBasedLane(DynamicLane):
    """A lane that shows works related to one particular Work."""

    DISPLAY_NAME: str | None = None
    ROUTE: str | None = None

    def __init__(self, library, work, display_name=None, children=None, **kwargs):
        self.work = work
        self.edition = work.presentation_edition

        # To avoid showing the same book in other languages, the value
        # of this lane's .languages is always derived from the
        # language of the work.  All children of this lane will be put
        # under a similar restriction.
        self.source_language = self.edition.language
        kwargs["languages"] = [self.source_language]

        # To avoid showing inappropriate material, the value of this
        # lane's .audiences setting is always derived from the
        # audience of the work. All children of this lane will be
        # under a similar restriction.
        self.source_audience = self.work.audience
        kwargs["audiences"] = self.audiences_list_from_source()

        display_name = display_name or self.DISPLAY_NAME

        children = children or list()

        super().initialize(
            library, display_name=display_name, children=children, **kwargs
        )

    @property
    def url_arguments(self):
        if not self.ROUTE:
            raise NotImplementedError()
        identifier = self.edition.primary_identifier
        kwargs = dict(identifier_type=identifier.type, identifier=identifier.identifier)
        return self.ROUTE, kwargs

    def audiences_list_from_source(self):
        if (
            not self.source_audience
            or self.source_audience in SubjectClassifier.AUDIENCES_ADULT
        ):
            return SubjectClassifier.AUDIENCES
        if self.source_audience == SubjectClassifier.AUDIENCE_YOUNG_ADULT:
            return SubjectClassifier.AUDIENCES_JUVENILE
        else:
            return [SubjectClassifier.AUDIENCE_CHILDREN]

    def append_child(self, worklist):
        """Add another Worklist as a child of this one and change its
        configuration to make sure its results fit in with this lane.
        """
        super().append_child(worklist)
        worklist.languages = self.languages
        worklist.audiences = self.audiences

    def accessible_to(self, patron):
        """In addition to the restrictions imposed by the superclass, a lane
        based on a specific Work is accessible to a Patron only if the
        Work itself is age-appropriate for the patron.

        :param patron: A Patron
        :return: A boolean
        """
        superclass_ok = super().accessible_to(patron)
        return superclass_ok and (
            not self.work or self.work.age_appropriate_for_patron(patron)
        )


class RecommendationLane(WorkBasedLane):
    """A lane of recommended Works based on a particular Work"""

    DISPLAY_NAME = "Titles recommended by NoveList"
    ROUTE = "recommendations"

    # Cache for 24 hours -- would ideally be much longer but availability
    # information goes stale.
    MAX_CACHE_AGE = 24 * 60 * 60

    def __init__(
        self, library, work, display_name=None, novelist_api=None, parent=None
    ):
        """Constructor.

        :raises: CannotLoadConfiguration if `novelist_api` is not provided
        and no Novelist integration is configured for this library.
        """
        super().__init__(
            library,
            work,
            display_name=display_name,
        )
        self.novelist_api = novelist_api or NoveListAPI.from_config(library)
        if parent:
            parent.append_child(self)
        _db = Session.object_session(library)
        self.recommendations = self.fetch_recommendations(_db)

    def fetch_recommendations(self, _db):
        """Get identifiers of recommendations for this LicensePool"""
        metadata = self.novelist_api.lookup(self.edition.primary_identifier)
        if metadata:
            metadata.filter_recommendations(_db)
            return metadata.recommendations
        return []

    def overview_facets(self, _db, facets):
        """Convert a generic FeaturedFacets to some other faceting object,
        suitable for showing an overview of this WorkList in a grouped
        feed.
        """
        # TODO: Since the purpose of the recommendation feed is to
        # suggest books that can be borrowed immediately, it would be
        # better to set availability=AVAILABLE_NOW. However, this feed
        # is cached for so long that we can't rely on the availability
        # information staying accurate. It would be especially bad if
        # people borrowed all of the recommendations that were
        # available at the time this feed was generated, and then
        # recommendations that were unavailable when the feed was
        # generated became available.
        #
        # For now, it's better to show all books and let people put
        # the unavailable ones on hold if they want.
        #
        # TODO: It would be better to order works in the same order
        # they come from the recommendation engine, since presumably
        # the best recommendations are in the front.
        return Facets.default(
            self.get_library(_db),
            collection=facets.COLLECTION_FULL,
            availability=facets.AVAILABLE_ALL,
            entrypoint=facets.entrypoint,
        )

    def modify_search_filter_hook(self, filter):
        """Find Works whose Identifiers include the ISBNs returned
        by an external recommendation engine.

        :param filter: A Filter object.
        """
        if not self.recommendations:
            # There are no recommendations. The search should not even
            # be executed.
            filter.match_nothing = True
        else:
            filter.identifiers = self.recommendations
        return filter


class SeriesFacets(DefaultSortOrderFacets):
    """A list with a series restriction is ordered by series position by
    default.
    """

    DEFAULT_SORT_ORDER = Facets.ORDER_SERIES_POSITION


class SeriesLane(DynamicLane):
    """A lane of Works in a particular series."""

    ROUTE = "series"
    # Cache for 24 hours -- would ideally be longer but availability
    # information goes stale.
    MAX_CACHE_AGE = 24 * 60 * 60

    def __init__(self, library, series_name, parent=None, **kwargs):
        if not series_name:
            raise ValueError("SeriesLane can't be created without series")
        super().initialize(library, display_name=series_name, **kwargs)
        self.series = series_name
        if parent:
            parent.append_child(self)
            if isinstance(parent, WorkBasedLane) and parent.source_audience:
                # WorkBasedLane forces self.audiences to values
                # compatible with the work in the WorkBasedLane, but
                # that's not enough for us. We want to force
                # self.audiences to *the specific audience* of the
                # work in the WorkBasedLane. If we're looking at a YA
                # series, we don't want to see books in a children's
                # series with the same name, even if it would be
                # appropriate to show those books.
                self.audiences = [parent.source_audience]

    @property
    def url_arguments(self):
        kwargs = dict(series_name=self.series)
        if self.language_key:
            kwargs["languages"] = self.language_key
        if self.audience_key:
            kwargs["audiences"] = self.audience_key
        return self.ROUTE, kwargs

    def overview_facets(self, _db, facets):
        """Convert a FeaturedFacets to a SeriesFacets suitable for
        use in a grouped feed. Our contribution to a grouped feed will
        be ordered by series position.
        """
        return SeriesFacets.default(
            self.get_library(_db),
            collection=facets.COLLECTION_FULL,
            availability=facets.AVAILABLE_ALL,
            entrypoint=facets.entrypoint,
        )

    def modify_search_filter_hook(self, filter):
        filter.series = self.series
        return filter


class ContributorFacets(DefaultSortOrderFacets):
    """A list with a contributor restriction is, by default, sorted by
    title.
    """

    DEFAULT_SORT_ORDER = Facets.ORDER_TITLE


class ContributorLane(DynamicLane):
    """A lane of Works written by a particular contributor"""

    ROUTE = "contributor"
    # Cache for 24 hours -- would ideally be longer but availability
    # information goes stale.
    MAX_CACHE_AGE = 24 * 60 * 60

    def __init__(
        self, library, contributor, parent=None, languages=None, audiences=None
    ):
        """Constructor.

        :param library: A Library.
        :param contributor: A Contributor or ContributorData object.
        :param parent: A WorkList.
        :param languages: An extra restriction on the languages of Works.
        :param audiences: An extra restriction on the audience for Works.
        """
        if not contributor:
            raise ValueError("ContributorLane can't be created without contributor")

        self.contributor = contributor
        self.contributor_key = (
            self.contributor.display_name or self.contributor.sort_name
        )
        super().initialize(
            library,
            display_name=self.contributor_key,
            audiences=audiences,
            languages=languages,
        )
        if parent:
            parent.append_child(self)

    @property
    def url_arguments(self):
        kwargs = dict(
            contributor_name=self.contributor_key,
            languages=self.language_key,
            audiences=self.audience_key,
        )
        return self.ROUTE, kwargs

    def overview_facets(self, _db, facets):
        """Convert a FeaturedFacets to a ContributorFacets suitable for
        use in a grouped feed.
        """
        return ContributorFacets.default(
            self.get_library(_db),
            collection=facets.COLLECTION_FULL,
            availability=facets.AVAILABLE_ALL,
            entrypoint=facets.entrypoint,
        )

    def modify_search_filter_hook(self, filter):
        filter.author = self.contributor
        return filter


class RelatedBooksLane(WorkBasedLane):
    """A lane of Works all related to a given Work by various criteria.

    Each criterion is represented by another WorkBaseLane class:

    * ContributorLane: Works by one of the contributors to this work.
    * SeriesLane: Works in the same series.
    * RecommendationLane: Works provided by a third-party recommendation
      service.
    """

    DISPLAY_NAME = "Related Books"
    ROUTE = "related_books"

    # Cache this lane for the shortest amount of time any of its
    # component lane should be cached.
    MAX_CACHE_AGE = min(
        ContributorLane.MAX_CACHE_AGE,
        SeriesLane.MAX_CACHE_AGE,
        RecommendationLane.MAX_CACHE_AGE,
    )

    def __init__(self, library, work, display_name=None, novelist_api=None):
        super().__init__(
            library,
            work,
            display_name=display_name,
        )
        _db = Session.object_session(library)
        sublanes = self._get_sublanes(_db, novelist_api)
        if not sublanes:
            raise ValueError(
                "No related books for {} by {}".format(
                    self.work.title, self.work.author
                )
            )
        self.children = sublanes

    def works(self, _db, *args, **kwargs):
        """This lane never has works of its own.

        Only its sublanes have works.
        """
        return []

    def _get_sublanes(self, _db, novelist_api):
        sublanes = list()

        for contributor_lane in self._contributor_sublanes(_db):
            sublanes.append(contributor_lane)

        for recommendation_lane in self._recommendation_sublane(_db, novelist_api):
            sublanes.append(recommendation_lane)

        # Create a series sublane.
        series_name = self.edition.series
        if series_name:
            sublanes.append(
                SeriesLane(
                    self.get_library(_db),
                    series_name,
                    parent=self,
                    languages=self.languages,
                )
            )

        return sublanes

    def _contributor_sublanes(self, _db):
        """Create contributor sublanes"""
        viable_contributors = list()
        roles_by_priority = list(Contributor.author_contributor_tiers())[1:]

        while roles_by_priority and not viable_contributors:
            author_roles = roles_by_priority.pop(0)
            viable_contributors = [
                c.contributor
                for c in self.edition.contributions
                if c.role in author_roles
            ]

        library = self.get_library(_db)
        for contributor in viable_contributors:
            contributor_lane = ContributorLane(
                library,
                contributor,
                parent=self,
                languages=self.languages,
                audiences=self.audiences,
            )
            yield contributor_lane

    def _recommendation_sublane(self, _db, novelist_api):
        """Create a recommendations sublane."""
        lane_name = "Similar titles recommended by NoveList"
        try:
            recommendation_lane = RecommendationLane(
                library=self.get_library(_db),
                work=self.work,
                display_name=lane_name,
                novelist_api=novelist_api,
                parent=self,
            )
            if recommendation_lane.recommendations:
                yield recommendation_lane
        except CannotLoadConfiguration as e:
            # NoveList isn't configured. This isn't fatal -- we just won't
            # use this sublane.
            pass


class CrawlableFacets(Facets):
    """A special Facets class for crawlable feeds."""

    # These facet settings are definitive of a crawlable feed.
    # Library configuration settings don't matter.
    SETTINGS = {
        Facets.ORDER_FACET_GROUP_NAME: Facets.ORDER_LAST_UPDATE,
        Facets.AVAILABILITY_FACET_GROUP_NAME: Facets.AVAILABLE_ALL,
        Facets.COLLECTION_FACET_GROUP_NAME: Facets.COLLECTION_FULL,
        Facets.DISTRIBUTOR_FACETS_GROUP_NAME: Facets.DISTRIBUTOR_ALL,
        Facets.COLLECTION_NAME_FACETS_GROUP_NAME: Facets.COLLECTION_NAME_ALL,
        # Facets.LANGUAGE_FACET_GROUP_NAME: Facets.LANGUAGE_ALL,
    }

    @classmethod
    def available_facets(cls, config, facet_group_name):
        facets = [cls.SETTINGS[facet_group_name]]

        if (
            facet_group_name == Facets.DISTRIBUTOR_FACETS_GROUP_NAME
            or facet_group_name == Facets.COLLECTION_NAME_FACETS_GROUP_NAME
        ) and config is not None:
            facets.extend(config.enabled_facets(facet_group_name))

        return facets

    @classmethod
    def default_facet(cls, config, facet_group_name):
        return cls.SETTINGS[facet_group_name]


class CrawlableLane(DynamicLane):
    # By default, crawlable feeds are cached for 12 hours.
    MAX_CACHE_AGE = 12 * 60 * 60


class CrawlableCollectionBasedLane(CrawlableLane):
    # Since these collections may be shared collections, for which
    # recent information is very important, these feeds are only
    # cached for 2 hours.
    MAX_CACHE_AGE = 2 * 60 * 60

    LIBRARY_ROUTE = "crawlable_library_feed"
    COLLECTION_ROUTE = "crawlable_collection_feed"

    def initialize(self, library_or_collections):
        self.collection_feed = False

        if isinstance(library_or_collections, Library):
            # We're looking at all the collections in a given library.
            library = library_or_collections
            collections = library.collections
            identifier = library.name
        else:
            # We're looking at collections directly, without respect
            # to the libraries that might use them.
            library = None
            collections = library_or_collections
            identifier = " / ".join(sorted(x.name for x in collections))
            if len(collections) == 1:
                self.collection_feed = True
                self.collection_name = collections[0].name

        super().initialize(
            library,
            "Crawlable feed: %s" % identifier,
        )
        if collections is not None:
            # initialize() set the collection IDs to all collections
            # associated with the library. We may want to restrict that
            # further.
            self.collection_ids = [x.id for x in collections]

    @property
    def url_arguments(self):
        if not self.collection_feed:
            return self.LIBRARY_ROUTE, dict()
        else:
            kwargs = dict(
                collection_name=self.collection_name,
            )
            return self.COLLECTION_ROUTE, kwargs


class CrawlableCustomListBasedLane(CrawlableLane):
    """A lane that consists of all works in a single CustomList."""

    ROUTE = "crawlable_list_feed"

    uses_customlists = True

    def initialize(self, library, customlist):
        self.customlist_name = customlist.name
        super().initialize(
            library,
            "Crawlable feed: %s" % self.customlist_name,
            customlists=[customlist],
        )

    @property
    def url_arguments(self):
        kwargs = dict(list_name=self.customlist_name)
        return self.ROUTE, kwargs


class KnownOverviewFacetsWorkList(WorkList):
    """A WorkList whose defining feature is that the Facets object
    to be used when generating a grouped feed is known in advance.
    """

    def __init__(self, facets, *args, **kwargs):
        """Constructor.

        :param facets: A Facets object to be used when generating a grouped
           feed.
        """
        super().__init__(*args, **kwargs)
        self.facets = facets

    def overview_facets(self, _db, facets):
        """Return the faceting object to be used when generating a grouped
        feed.

        :param _db: Ignored -- only present for API compatibility.
        :param facets: Ignored -- only present for API compatibility.
        """
        return self.facets


class JackpotFacets(Facets):
    """A faceting object for a jackpot feed.

    Unlike other faceting objects, AVAILABLE_NOT_NOW is an acceptable
    option for the availability facet.
    """

    @classmethod
    def default_facet(cls, config, facet_group_name):
        if facet_group_name != cls.AVAILABILITY_FACET_GROUP_NAME:
            return super().default_facet(config, facet_group_name)
        return cls.AVAILABLE_NOW

    @classmethod
    def available_facets(cls, config, facet_group_name):
        if facet_group_name != cls.AVAILABILITY_FACET_GROUP_NAME:
            return super().available_facets(config, facet_group_name)

        return [
            cls.AVAILABLE_NOW,
            cls.AVAILABLE_NOT_NOW,
            cls.AVAILABLE_ALL,
            cls.AVAILABLE_OPEN_ACCESS,
        ]


class HasSeriesFacets(Facets):
    """A faceting object for a feed containg books guaranteed
    to belong to _some_ series.
    """

    def modify_search_filter(self, filter):
        filter.series = True


class JackpotWorkList(WorkList):
    """A WorkList guaranteed to, so far as possible, contain the exact
    selection of books necessary to perform common QA tasks.

    This makes it easy to write integration tests that work on real
    circulation managers and real books.
    """

    def __init__(self, library, facets):
        """Constructor.

        :param library: A Library
        :param facets: A Facets object.
        """
        super().initialize(library)

        # Initialize a list of child Worklists; one for each test that
        # a client might need to run.
        self.children = []

        # Add one or more WorkLists for every collection in the
        # system, so that a client can test borrowing a book from
        # every collection.
        for collection in sorted(library.collections, key=lambda x: x.name):
            for medium in Edition.FULFILLABLE_MEDIA:
                # Give each Worklist a name that is distinctive
                # and easy for a client to parse.
                if collection.data_source:
                    data_source_name = collection.data_source.name
                else:
                    data_source_name = "[Unknown]"
                display_name = (
                    "License source {%s} - Medium {%s} - Collection name {%s}"
                    % (data_source_name, medium, collection.name)
                )
                child = KnownOverviewFacetsWorkList(facets)
                child.initialize(library, media=[medium], display_name=display_name)
                child.collection_ids = [collection.id]
                self.children.append(child)

    def works(self, _db, *args, **kwargs):
        """This worklist never has works of its own.

        Only its children have works.
        """
        return []
