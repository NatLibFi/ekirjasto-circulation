from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import pytest
from flask.ctx import RequestContext
from flask_babel import Babel
from werkzeug.datastructures import ImmutableMultiDict

from api.util.flask import PalaceFlask
from core.model import Admin, AdminRole, Library, get_one_or_create
from core.model.patron import Patron
from tests.fixtures.database import DatabaseTransactionFixture


class FlaskAppFixture:
    def __init__(self, db: DatabaseTransactionFixture) -> None:
        self.app = PalaceFlask(__name__)
        self.db = db
        Babel(self.app)

    def admin_user(
        self,
        email: str = "admin@admin.org",
        role: str = AdminRole.SYSTEM_ADMIN,
        library: Library | None = None,
    ) -> Admin:
        admin, _ = get_one_or_create(self.db.session, Admin, email=email)
        admin.add_role(role, library)
        return admin

    @contextmanager
    def test_request_context(
        self,
        *args: Any,
        admin: Admin | None = None,
        library: Library | None = None,
        patron: Patron | None = None,
        **kwargs: Any,
    ) -> Generator[RequestContext, None, None]:
        with self.app.test_request_context(*args, **kwargs) as c:
            self.db.session.begin_nested()
            setattr(c.request, "library", library)
            setattr(c.request, "admin", admin)
            setattr(c.request, "patron", patron)
            setattr(c.request, "form", ImmutableMultiDict())
            setattr(c.request, "files", ImmutableMultiDict())
            try:
                yield c
            finally:
                # Flush any changes that may have occurred during the request, then
                # expire all objects to ensure that the next request will see the
                # changes.
                self.db.session.commit()
                self.db.session.expire_all()

    @contextmanager
    def test_request_context_system_admin(
        self, *args: Any, **kwargs: Any
    ) -> Generator[RequestContext]:  # type: ignore[type-arg]
        admin = self.admin_user()
        with self.test_request_context(*args, **kwargs, admin=admin) as c:
            yield c


@pytest.fixture
def flask_app_fixture(db: DatabaseTransactionFixture) -> FlaskAppFixture:
    return FlaskAppFixture(db)
