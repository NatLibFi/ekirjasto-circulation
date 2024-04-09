from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, relationship

from core.model import Base
from core.model.collection import Collection


class ApiToken(Base):
    __tablename__ = "apitokens"

    id = Column(Integer, primary_key=True)
    token = Column(String, unique=True, nullable=False)
    label = Column(String, unique=True, nullable=False)
    collection_id = Column(Integer, ForeignKey("collections.id", ondelete="CASCADE"))
    created = Column(DateTime(timezone=True), nullable=False, default=datetime.now())

    collection: Mapped[list[Collection]] = relationship("Collection")

    def __repr__(self):
        return f"<ApiToken id={self.id} token={self.token} label={self.label} collection_id={self.collection_id}>"
