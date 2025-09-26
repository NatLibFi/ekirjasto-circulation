from sqlalchemy import Column, Integer, Unicode
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, relationship

from core.model import Base
from core.model.edition import Edition


class Accessibility(Base):
    __tablename__ = "accessibility"

    id = Column(Integer, primary_key=True)

    # https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/guidelines/#accessibility-summary
    summary = Column(Unicode, nullable=True)

    # https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/guidelines/#ways-of-reading
    ways_of_reading = Column(ARRAY(Unicode), nullable=True)

    # https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/guidelines/#additional-accessibility-information
    additional_accessibility_information = Column(ARRAY(Unicode), nullable=True)

    # https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/guidelines/#navigation
    navigation = Column(ARRAY(Unicode), nullable=True)

    # https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/guidelines/#rich-content
    rich_content = Column(ARRAY(Unicode), nullable=True)

    # https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/guidelines/#hazards
    hazards = Column(ARRAY(Unicode), nullable=True)

    # https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/guidelines/#conformance-group
    conforms_to = Column(ARRAY(Unicode), nullable=True)
    certifier = Column(Unicode, nullable=True)

    # Conformance also includes "credentials" and "report" but these are left out atm because our feed does not carry that data.
    # credentials
    # report

    # https://w3c.github.io/publ-a11y/a11y-meta-display-guide/2.0/guidelines/#legal-considerations
    legal_considerations = Column(Unicode, nullable=True)

    edition: Mapped[Edition] = relationship(
        "Edition", back_populates="accessibility", uselist=False
    )

    def __repr__(self):
        return f"Accessibility data: Conforms_to: {self.conforms_to}, Hazards: {self.hazards}, Ways of reading: {self.ways_of_reading}"
