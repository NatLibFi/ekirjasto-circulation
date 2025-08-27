from collections.abc import Sequence
from functools import cached_property

from pydantic import AwareDatetime, Field, NonNegativeInt

from api.opds.base import BaseOpdsModel
from api.opds.odl.protection import Protection
from api.opds.odl.terms import Terms
from api.opds.opds2 import Price
from api.opds.util import StrOrTuple, obj_or_tuple_to_tuple
from core.model.licensing import LicenseStatus


class Loan(BaseOpdsModel):
    """
    https://drafts.opds.io/odl-1.0.html#41-syntax
    """

    href: str
    id: str
    # We alias 'patron' here because the ODL documentation
    # requires the field to be named `patron_id` but
    # DeMarque returns a field named `patron`.
    patron_id: str = Field(validation_alias="patron")
    expires: AwareDatetime


class Checkouts(BaseOpdsModel):
    """
    https://drafts.opds.io/odl-1.0.html#41-syntax
    """

    left: NonNegativeInt | None = None
    available: NonNegativeInt
    active: list[Loan] = Field(default_factory=list)


class LicenseInfo(BaseOpdsModel):
    """
    This document is defined in the ODL specification:
    https://drafts.opds.io/odl-1.0.html#4-license-info-document
    """

    @staticmethod
    def content_type() -> str:
        return "application/vnd.odl.info+json"

    identifier: str
    status: LicenseStatus
    checkouts: Checkouts
    format: StrOrTuple[str] = tuple()

    @cached_property
    def formats(self) -> Sequence[str]:
        return obj_or_tuple_to_tuple(self.format)

    created: AwareDatetime | None = None
    terms: Terms = Field(default_factory=Terms)
    protection: Protection = Field(default_factory=Protection)
    price: Price | None = None
    source: str | None = None

    @cached_property
    def active(self) -> bool:
        return self.status == LicenseStatus.available
