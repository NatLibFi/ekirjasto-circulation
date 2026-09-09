from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Integer, Numeric, Unicode, UniqueConstraint
from sqlalchemy.orm import Mapped, relationship

from core.model import Base

if TYPE_CHECKING:
    from core.model.work import Work


class AnnifSubject(Base):
    """A subject suggestion returned by the Annif/Finto AI service."""

    __tablename__ = "annifsubjects"
    id = Column(Integer, primary_key=True)
    work_id = Column(Integer, ForeignKey("works.id"), nullable=False, index=True)
    uri = Column(Unicode, nullable=False)
    label = Column(Unicode, nullable=False)
    score: Mapped[Decimal | None] = Column(Numeric(6, 5), nullable=True)
    notation = Column(Unicode, nullable=True)

    work: Mapped[Work] = relationship("Work", back_populates="annif_subjects")

    __table_args__ = (UniqueConstraint("work_id", "uri"),)
