from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.metadata.base import MetadataService, MetadataServiceSettings
from core.integration.base import HasLibraryIntegrationConfiguration
from core.integration.goals import Goals
from core.integration.settings import (
    BaseSettings,
    ConfigurationFormItem,
    ConfigurationFormItemType,
    FormField,
)
from core.model import IntegrationConfiguration


class FintoAISettings(MetadataServiceSettings):
    """Settings for the Finto AI keyword suggestion service."""

    work_languages: list[Literal["fin", "swe", "eng"]] = FormField(
        ["fin", "swe", "eng"],
        form=ConfigurationFormItem(
            label="Work languages",
            type=ConfigurationFormItemType.MENU,
            description="Extract keywords from summaries of works in these languages.",
            options={
                "fin": "Finnish",
                "swe": "Swedish",
                "eng": "English",
            },
        ),
    )
    keyword_language: Literal["fin", "book"] = FormField(
        "fin",
        form=ConfigurationFormItem(
            label="Keyword language",
            type=ConfigurationFormItemType.SELECT,
            description="Language used for the extracted keyword labels.",
            options={"fin": "Finnish", "book": "Book language"},
        ),
    )

    limit: int = FormField(
        10,
        ge=1,
        form=ConfigurationFormItem(
            label="Maximum keywords",
            type=ConfigurationFormItemType.NUMBER,
            description="Maximum number of keyword suggestions to retain per work.",
        ),
    )
    threshold: float = FormField(
        0.1,
        ge=0,
        le=1,
        form=ConfigurationFormItem(
            label="Keyword score threshold",
            type=ConfigurationFormItemType.NUMBER,
            description="Minimum score required for a keyword suggestion.",
        ),
    )


class FintoAILibrarySettings(BaseSettings):
    """Library-specific settings for Finto AI."""

    ...


class FintoAI(
    MetadataService[FintoAISettings],
    HasLibraryIntegrationConfiguration[FintoAISettings, FintoAILibrarySettings],
):
    """Configuration entry for the Finto AI keyword suggestion service."""

    @classmethod
    def label(cls) -> str:
        return "Finto AI"

    @classmethod
    def description(cls) -> str:
        return "Suggest keywords from work summaries using Finto AI."

    @classmethod
    def settings_class(cls) -> type[FintoAISettings]:
        return FintoAISettings

    @classmethod
    def library_settings_class(cls) -> type[FintoAILibrarySettings]:
        return FintoAILibrarySettings

    @classmethod
    def integration(cls, db: Session) -> IntegrationConfiguration | None:
        protocol = cls.__name__
        query = select(IntegrationConfiguration).where(
            IntegrationConfiguration.goal == Goals.METADATA_GOAL,
            IntegrationConfiguration.protocol == protocol,
        )
        return db.execute(query).scalar_one_or_none()

    @classmethod
    def multiple_services_allowed(cls) -> bool:
        return False
