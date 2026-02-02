from core.classifier import *
from core.classifier.bisac_mappings import GENRES


class BISACClassifier(SubjectClassifier):
    """Handle real, genuine, according-to-Hoyle BISAC classifications.

    The identifier is used as a means to map to a genre, and the identifier's
    initial characters are used to determine fiction status and audience. Finally,
    there are certain juvenile BISACs that we want to define a target age for.
    These identifiers are defined exactly.
    """

    FICTION = ("FIC", "HUM", "DRA", "POE", "LCO", "CGN", "JUV", "YAF")

    JUVENILE = ("JUV", "JNF")

    YA = ("YAF", "YAN")

    TARGET_AGES = {
        "JUV043000": (0, 4),  # Beginner
        "JUV044000": (5, 7),  # Intermediate
        "JUV045000": (8, 13),  # Chapter Books
        "JNF045000": (0, 4),  # Beginner
        "JNF046000": (5, 7),  # Intermediate
        "JNF047000": (8, 13),  # Chapter Books
    }

    @classmethod
    def is_fiction(cls, identifier, name):
        """
        Sets a fiction status accroding to a known set of BISAC fiction first characters.

        Returns:
            fiction: Boolean or None
        """
        if identifier in GENRES.keys():
            if identifier.startswith(cls.FICTION):
                return True
            return False
        return None

    @classmethod
    def audience(cls, identifier, name):
        """
        Matches audience according to certain BISAC code characters.

        Returns:
            audience: One of the SubjectClassifier audiences.
        """
        if identifier in GENRES.keys():
            if identifier.startswith(cls.JUVENILE):
                return SubjectClassifier.AUDIENCE_CHILDREN
            elif identifier.startswith(cls.YA):
                return SubjectClassifier.AUDIENCE_YOUNG_ADULT
            return SubjectClassifier.AUDIENCE_ADULT
        return None

    @classmethod
    def target_age(cls, identifier, name):
        """
        Looks up the target age if it's one of the defined set of juvenile
        BISACs.

        Returns:
            tuple: A tuple representing the age range.
        """
        if identifier in GENRES.keys():
            if identifier in cls.TARGET_AGES:
                return cls.TARGET_AGES.get(identifier)
        return None

    @classmethod
    def genre(cls, identifier, name, fiction, audience):
        """
        Looks up the genre according to the given identifier.

        Returns:
            genre: GenreData object
        """
        if identifier in GENRES.keys():
            genre = GENRES[identifier].get("genre")
            return GenreData(genre, fiction)
        return None

    @classmethod
    def scrub_identifier(cls, identifier):
        """
        Confirm the identifier is a BISAC identifier.

        Returns:
            identifier: String or  tuple(identifier, name): If an official
            BISAC identifier, returns the identifier and name. Else returns
            the identifier.
        """
        if not identifier:
            return identifier
        # Feedbooks can use prefix FB
        if identifier.startswith("FB"):
            identifier = identifier[2:]
        # And Feedbooks can use postfix N
        if identifier.endswith("N"):
            identifier = identifier[:-1]
        if identifier in GENRES.keys():
            return (identifier, GENRES[identifier]["name"])
        return identifier

    @classmethod
    def scrub_name(cls, name):
        """
        Confirm the name matches with our BISAC code names.

        Returns:
            name: String: BISAC subject name.
        """
        for identifier, mappings in GENRES.items():
            # If the name does not match our list of codes, we don't want to classify it.
            if mappings["name"].lower() == name.lower():
                return name
        return None


SubjectClassifier.classifiers[SubjectClassifier.BISAC] = BISACClassifier
