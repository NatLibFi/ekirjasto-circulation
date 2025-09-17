from enum import Enum
from typing import List, Optional, Union
from core.util.log import LoggerMixin

class ConformanceLevel(Enum):
    """
    https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/techniques/epub-metadata/#conformance-group
    """
    a = "This publication meets minimum accessibility standards"
    aa = "This publication meets accepted accessibility standards"
    aaa = "This publication exceeds accepted accessibility standards"

    @classmethod
    def get(cls, value: str) -> Optional[str]:
        """Get the description for a given conformance level string."""
        try:
            level = cls[value.lower()]  # Access the enum member by name
            return level.value  # Return the description
        except KeyError:
            return None 


class AccessibilityDataMapper(LoggerMixin):
    """Maps Schema.org data to w3c display data."""
    def __init__(self, edition):
        self.edition = edition

    def map_conformance(self, conformance: Union[str, List[str]]) -> Union[str, List[str], None]:
        """ Map the conformance level to a display category. """

        def get_level_description(level_str: str) -> Optional[str]:
            # A helper function to extract the level from AccessibilityData.conforms_to
            level = level_str.split('_')[-1]
            return ConformanceLevel.get(level)

        if isinstance(conformance, str):
            description = get_level_description(conformance)
            return [description] if description else None

        if isinstance(conformance, list):
            results = [get_level_description(item) for item in conformance]
            return [result for result in results if result] or None

        return None