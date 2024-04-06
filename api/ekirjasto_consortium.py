import logging
from dataclasses import dataclass
from typing import Literal, TypeVar

import requests
from pydantic import BaseModel

from api.circulation_exceptions import RemoteInitiatedServerError
from core.configuration.library import LibrarySettings
from core.metadata_layer import TimestampData
from core.model.library import Library
from core.monitor import Monitor


class KirkantaConsortium(BaseModel):
    id: str
    name: str
    slug: str


class KirkantaConsortiums(BaseModel):
    items: list[KirkantaConsortium]
    type: Literal["consortium"]


class KirkantaCity(BaseModel):
    id: str
    consortium: str | None
    name: str


class KirkantaCities(BaseModel):
    items: list[KirkantaCity]
    type: Literal["city"]


class KoodistoConceptCodeAttribute(BaseModel):
    attributeName: str
    attributeValue: list[str]


class KoodistoConceptCode(BaseModel):
    attributes: list[KoodistoConceptCodeAttribute]
    status: Literal["ACTIVE", "PROPOSAL", "DELETED"]
    conceptCodeId: str

    def findAttribute(self, attributeName: str) -> str | None:
        for attribute in self.attributes:
            if attribute.attributeName == attributeName and attribute.attributeValue:
                return attribute.attributeValue[0]
        return None


class KoodistoConceptCodes(BaseModel):
    conceptCodes: list[KoodistoConceptCode]
    page: int
    totalItems: int
    totalPages: int


@dataclass(frozen=True)
class EkirjastoConsortium:
    name: str
    kirkanta_slug: str
    municipality_codes: list[str]


# Finland
class EkirjastoConsortiumMonitor(Monitor):
    """For E-kirjasto, each library represents a consortium, aka "Kimppa".
    Each consortium has multiple member municipalities.

    This utility updates the mapping between municipality codes (received
    from authentication app) and Consortiums.

    Two 3rd party public APIs are utilized for this: Kirkanta and Kansallinen
    Koodistopalvelu.
    """

    SERVICE_NAME: str = "E-Kirjasto Consortium Monitor"

    # Kirkanta provides two endpoints, one that lists all consortiums and
    # another that lists all cities.
    _KIRKANTA_API_URL: str = "https://api.kirjastot.fi/v4"

    # Kansallinen koodistopalvelu provides the list of municipality codes. These
    # are the codes used by the authentication application.
    # The ids of "cities" in Kirkanta do not match the municipality codes in
    # Koodistopalvelu, and need to be matched by name.
    _KOODISTO_API_URL: str = "https://koodistopalvelu.kanta.fi/codeserver/csapi/v6"
    _KOODISTO_CLASSIFICATION_ID: str = "1.2.246.537.6.21"
    _KOODISTO_MAX_ALLOWED_PAGESIZE: int = 500

    # City names that we want to skip in Kirkanta.
    _KIRKANTA_JUNK_CITIES: list[str] = ["Äävekaupunki"]

    # Manually managed list of Kirkanta city names don't match with Koodistopalvelu
    KIRKANTA_CITY_NAME_ALIASES: dict[str, str] = {"Pedersöre": "Pedersören kunta"}

    def run_once(self, progress):
        kirkanta_consortiums = self._fetch_kirkanta_consortiums()
        kirkanta_cities = self._fetch_kirkanta_cities()
        kirkanta_cities = self._filter_out_junk_data(kirkanta_cities)
        koodisto_concept_codes = self._fetch_koodisto_concept_codes()

        missing_cities = self._verify_all_kirkanta_cities_found_in_koodisto(
            kirkanta_cities, koodisto_concept_codes
        )

        logging.info(
            f"The following cities do not belong to any consortium: "
            f"{[city.name for city in kirkanta_cities if not city.consortium]}. "
            f"Unless a library is created manually, patrons from these cities "
            f"will be assigned to the default library."
        )

        libraries = self._db.query(Library).all()

        for library in libraries:
            self._synchronize_library(
                library, kirkanta_consortiums, kirkanta_cities, koodisto_concept_codes
            )

        if missing_cities:
            return TimestampData(
                achievements=f"Kirkanta synchronization done! NOTE: "
                f"The following Kirkanta city names were "
                f"not found in Koodistopalvelu: {missing_cities}. "
                f"Please add the missing city names manually to "
                f"KIRKANTA_CITY_NAME_ALIASES."
            )
        return TimestampData(
            achievements=f"Kirkanta synchronization done!",
        )

    def _fetch_kirkanta_cities(self) -> list[KirkantaCity]:
        return self._get(
            KirkantaCities, f"{self._KIRKANTA_API_URL}/city", {"limit": 9999}
        ).items

    def _fetch_kirkanta_consortiums(self) -> list[KirkantaConsortium]:
        return self._get(
            KirkantaConsortiums,
            f"{self._KIRKANTA_API_URL}/consortium",
            {"status": "ACTIVE", "limit": 9999},
        ).items

    def _fetch_koodisto_concept_codes(self, page: int = 1) -> list[KoodistoConceptCode]:
        response: KoodistoConceptCodes = self._get(
            KoodistoConceptCodes,
            f"{self._KOODISTO_API_URL}/classifications/{self._KOODISTO_CLASSIFICATION_ID}/conceptcodes",
            {"pageSize": self._KOODISTO_MAX_ALLOWED_PAGESIZE, "page": page},
        )
        if response.page < response.totalPages:
            return response.conceptCodes + self._fetch_koodisto_concept_codes(
                page=page + 1
            )

        return response.conceptCodes

    def _build_circulation_consortium(
        self,
        kirkanta_consortium: KirkantaConsortium,
        kirkanta_cities: list[KirkantaCity],
        koodisto_concept_codes: list[KoodistoConceptCode],
    ) -> EkirjastoConsortium:
        """Combines the the Kirkanta and Koodistopalvelu data into a model usable within Circulation"""

        cities_in_consortium = [
            city
            for city in kirkanta_cities
            if city.consortium == kirkanta_consortium.id
        ]
        koodisto_codes = [
            self._to_koodisto_code(city, koodisto_concept_codes)
            for city in cities_in_consortium
        ]

        return EkirjastoConsortium(
            name=kirkanta_consortium.name,
            kirkanta_slug=kirkanta_consortium.slug,
            municipality_codes=[code.conceptCodeId for code in koodisto_codes if code],
        )

    def _synchronize_library(
        self,
        library: Library,
        kirkanta_consortiums: list[KirkantaConsortium],
        kirkanta_cities: list[KirkantaCity],
        koodisto_codes: list[KoodistoConceptCode],
    ) -> None:
        if library.is_default:
            logging.info(
                'Skipping default library "%s".',
                library.name,
            )
            return

        slug = library.settings_dict.get(
            "kirkanta_consortium_slug",
            "disabled",
        )
        if not slug or slug == "disabled":
            logging.info(
                'Skipping library "%s": not configured for automatic sync.',
                library.name,
            )
            return

        matched_consortiums = [
            consortium for consortium in kirkanta_consortiums if consortium.slug == slug
        ]
        if not matched_consortiums:
            logging.error(
                "A consortium with slug %s was not found in Kirkanta. Can't update library. "
                "This can happen if the library slug selection does not match Kirkanta value.",
                slug,
            )
            return

        logging.info('Synchronizing library "%s"', library.name)

        circulation_consortium: EkirjastoConsortium = (
            self._build_circulation_consortium(
                matched_consortiums[0], kirkanta_cities, koodisto_codes
            )
        )

        library.update_settings(
            LibrarySettings.construct(
                municipalities=circulation_consortium.municipality_codes,
            )
        )

    def _filter_out_junk_data(self, cities: list[KirkantaCity]) -> list[KirkantaCity]:
        return [city for city in cities if city.name not in self._KIRKANTA_JUNK_CITIES]

    def _verify_all_kirkanta_cities_found_in_koodisto(
        self, cities: list[KirkantaCity], concept_codes: list[KoodistoConceptCode]
    ) -> list[str] | None:
        """Compares the city names in Kirkanta and Koodistopalvelu. Logs an
        error if some names are.

        :returns: List of city names found in Kirkanta but not in koodisto
        """
        missing_names = [
            city.name
            for city in cities
            if not self._to_koodisto_code(city, concept_codes)
        ]

        if missing_names:
            logging.warning(
                "The following Kirkanta city names are not found in Koodistopalvelu: %s. "
                "Please add the missing city name manually to KIRKANTA_CITY_NAME_ALIASES.",
                str(missing_names),
            )
            return missing_names
        else:
            logging.info("Nice! All Kirkanta city names found in Koodistopalvelu.")
            return None

    def _to_koodisto_code(
        self, kirkanta_city: KirkantaCity, concept_codes: list[KoodistoConceptCode]
    ) -> KoodistoConceptCode | None:
        """Finds a matching Koodistopalvelu code for a kirkanta city"""

        for code in concept_codes:
            koodisto_city_name = code.findAttribute("ShortName")
            if koodisto_city_name == kirkanta_city.name:
                return code

            alias: str | None = self.KIRKANTA_CITY_NAME_ALIASES.get(
                kirkanta_city.name,
            )
            if alias and koodisto_city_name == alias:
                return code

        return None

    R = TypeVar("R", bound=BaseModel)

    def _get(self, response_type: type[R], url: str, params=None) -> R:
        """Perform HTTP GET request and parse the response into a pydantic model of type R"""
        try:
            response = requests.get(url, params)
        except requests.exceptions.ConnectionError as e:
            raise RemoteInitiatedServerError(str(e), url)

        if response.status_code != 200:
            content: str = "No content"
            if response.content:
                content = response.content.decode("utf-8", errors="replace")

            raise RemoteInitiatedServerError(
                f"Unexpected response status {response.status_code}, url={url}, content={content}",
                url,
            )
        else:
            try:
                return response_type(**response.json())
            except requests.exceptions.JSONDecodeError as e:
                raise RemoteInitiatedServerError(str(e), url)
