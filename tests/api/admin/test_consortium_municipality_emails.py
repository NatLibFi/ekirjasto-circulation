import pytest

from api.admin.consortium_municipality_emails import (
    get_consortium_municipality_emails,
    save_consortium_municipality_email,
)
from tests.fixtures.database import DatabaseTransactionFixture


class TestConsortiumMunicipalityEmails:
    def test_email_settings_are_saved_and_removed(self, db: DatabaseTransactionFixture):
        assert {"consortiums": {}, "municipalities": {}} == (
            get_consortium_municipality_emails(db.session)
        )

        save_consortium_municipality_email(
            db.session, "consortiums", "vaski-kirjastot", "team@example.fi"
        )
        save_consortium_municipality_email(
            db.session, "municipalities", "853", "turku@example.fi"
        )

        assert {
            "consortiums": {"vaski-kirjastot": "team@example.fi"},
            "municipalities": {"853": "turku@example.fi"},
        } == get_consortium_municipality_emails(db.session)

        save_consortium_municipality_email(
            db.session, "municipalities", "853", "  "
        )

        assert {
            "consortiums": {"vaski-kirjastot": "team@example.fi"},
            "municipalities": {},
        } == get_consortium_municipality_emails(db.session)

    @pytest.mark.parametrize(
        "kind,key,email",
        [
            ("other", "key", "team@example.fi"),
            ("consortiums", "", "team@example.fi"),
            ("municipalities", "853", "not-an-email"),
        ],
    )
    def test_invalid_email_settings_are_rejected(
        self, db: DatabaseTransactionFixture, kind: str, key: str, email: str
    ):
        with pytest.raises(ValueError):
            save_consortium_municipality_email(db.session, kind, key, email)
