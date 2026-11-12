from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, Unicode, UniqueConstraint

from core.model import Base


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True)
    work_id = Column(Integer, ForeignKey("works.id"), nullable=False, index=True)
    name = Column(Unicode, nullable=False)

    __table_args__ = (UniqueConstraint("work_id", "name"),)
