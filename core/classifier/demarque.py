"""Classifier to extract classifications from De Marque data.
"""
from core.classifier import *


class DeMarqueClassifier(Classifier):

    @classmethod
    def scrub_identifier(cls, identifier):
        """
        Make sure that the identifier matches with De Marque codes.

        :param identifier: The identifier to be scrubbed.
        :return: The scrubbed identifier.
        """
        if identifier.startswith("READ"):
            return identifier
    
    @classmethod
    def scrub_name(cls, name):
        """
        Read in the De Marque name of the subject code.
        :param name: The name of the subject.
        """
        if name:
            return name

    @classmethod
    def audience(cls, identifier, name):
        """
        Function to determine the audience based on the given identifier.
        
        :param identifier: The identifier to check for audience classification.
        :param name: The name associated with the identifier.
        :return: The audience classification based on the identifier.
        """
        if identifier in ["READ0001","READ0002", "READ0003"]:
            return cls.AUDIENCE_CHILDREN
        elif identifier in ["READ0004", "READ0005"]:
            return cls.AUDIENCE_YOUNG_ADULT
        return cls.AUDIENCE_ADULT

    @classmethod
    def target_age(cls, identifier, name):
        """
        Function that determines the target age range based on the given identifier.

        :param identifier: The identifier to check for target age classification.
        :return: A tuple representing the target age range.
        """
        if identifier == "READ0001":
            return (0, 3)
        if identifier == "READ0002":
            return (4, 7)
        if identifier == "READ0003":
            return (8, 12)
        if identifier == "READ0004":
            return (13, 18)
        if identifier == "READ0005":
            return (17, None)
                

Classifier.classifiers[Classifier.DEMARQUE] = DeMarqueClassifier
