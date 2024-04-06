from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

import flask
from flask import Response, redirect, url_for
from flask_babel import lazy_gettext as _
from sqlalchemy.orm import Session
from werkzeug import Response as WerkzeugResponse

from api.admin.config import Configuration as AdminClientConfig
from api.admin.controller.base import AdminController
from api.admin.ekirjasto_admin_authentication_provider import (
    EkirjastoAdminAuthenticationProvider,
    EkirjastoUserInfo,
)
from api.admin.password_admin_authentication_provider import (
    PasswordAdminAuthenticationProvider,
)
from api.admin.problem_details import (
    ADMIN_AUTH_MECHANISM_NOT_CONFIGURED,
    ADMIN_AUTH_NOT_CONFIGURED,
    ADMIN_NOT_AUTHORIZED,
    INVALID_ADMIN_CREDENTIALS,
)
from api.admin.template_styles import (
    body_style,
    error_style,
    hr_style,
    logo_style,
    section_style,
    small_link_style,
)
from api.problem_details import EKIRJASTO_REMOTE_AUTHENTICATION_FAILED
from core.model import get_one, get_one_or_create
from core.model.admin import Admin, AdminCredential, AdminRole
from core.model.library import Library
from core.util.problem_detail import ProblemDetail


class SignInController(AdminController):
    HEAD_TEMPLATE = """<head>
<meta charset="utf8">
<title>{app_name}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;700&display=swap');
</style>
</head>
""".format(
        app_name=AdminClientConfig.APP_NAME
    )

    ERROR_RESPONSE_TEMPLATE = """<!DOCTYPE HTML>
<html lang="en">
{head_html}
<body style="{error}">
<p><strong>%(status_code)d ERROR:</strong> %(message)s</p>
<hr style="{hr}">
<a href="/admin/sign_in" style="{link}">Try again</a>
</body>
</html>""".format(
        head_html=HEAD_TEMPLATE, error=error_style, hr=hr_style, link=small_link_style
    )

    SIGN_IN_TEMPLATE = """<!DOCTYPE HTML>
<html lang="en">
{head_html}
<body style="{body}">
<img src="%(logo_url)s" alt="{app_name}" style="{logo}">
%(auth_provider_html)s
</body>
</html>""".format(
        head_html=HEAD_TEMPLATE,
        body=body_style,
        app_name=AdminClientConfig.APP_NAME,
        logo=logo_style,
    )

    # Finland
    def ekirjasto_auth_finish(self):
        auth: EkirjastoAdminAuthenticationProvider = self.admin_auth_provider(
            EkirjastoAdminAuthenticationProvider.NAME
        )
        if not auth:
            return ADMIN_AUTH_MECHANISM_NOT_CONFIGURED

        result = flask.request.form.get("result")
        if result != "success":
            logging.error("Ekirjasto authentication failed, result = %s", result)
            return self.error_response(EKIRJASTO_REMOTE_AUTHENTICATION_FAILED)

        ekirjasto_token = flask.request.form.get("token")

        user_info = auth.ekirjasto_authenticate(ekirjasto_token)
        if isinstance(user_info, ProblemDetail):
            return user_info

        circulation_roles = self._to_circulation_roles(
            self._db, user_info.role, user_info.municipalities
        )
        if not circulation_roles:
            return self.error_response(ADMIN_NOT_AUTHORIZED)

        try:
            credentials = get_one(self._db, AdminCredential, external_id=user_info.sub)
            if credentials:
                credentials.last_signed_in = datetime.now(timezone.utc)
                admin = credentials.admin
            else:
                admin = self._create_admin_with_external_credentials(user_info)

            self._update_roles_if_changed(admin, circulation_roles)
        except Exception as e:
            logging.exception("Internal error during signup")
            self._db.rollback()
            return EKIRJASTO_REMOTE_AUTHENTICATION_FAILED

        self._setup_admin_flask_session(admin, user_info, auth, ekirjasto_token)

        redirect_uri = flask.request.args.get("redirect_uri", "/admin/web")
        return SanitizedRedirections.redirect(redirect_uri)

    def _create_admin_with_external_credentials(self, user_info: EkirjastoUserInfo):
        admin, _ = get_one_or_create(
            self._db,
            Admin,
            email=user_info.sub,
        )
        get_one_or_create(
            self._db,
            AdminCredential,
            external_id=user_info.sub,
            admin_id=admin.id,
        )
        return admin

    @staticmethod
    def _update_roles_if_changed(
        admin: Admin, new_roles: list[tuple[str, Library | None]]
    ):
        existing_roles = [(role.role, role.library) for role in admin.roles]
        if new_roles != existing_roles:
            for role in admin.roles:
                admin.remove_role(role.role, role.library)
            for name, library in new_roles:
                admin.add_role(name, library)

    @staticmethod
    def _setup_admin_flask_session(
        admin: Admin,
        user_info: EkirjastoUserInfo,
        auth: EkirjastoAdminAuthenticationProvider,
        ekirjasto_token: str,
    ):
        # Set up the admin's flask session.
        flask.session["admin_email"] = admin.email
        flask.session["auth_type"] = auth.NAME

        if user_info.given_name:
            flask.session["admin_given_name"] = user_info.given_name

        # This one is extra compared to password auth provider
        flask.session["ekirjasto_token"] = ekirjasto_token

        # A permanent session expires after a fixed time, rather than
        # when the user closes the browser.
        flask.session.permanent = True

    @staticmethod
    def _to_circulation_roles(
        db: Session, ekirjasto_role: str, municipalities: list[str]
    ) -> list[tuple[str, Library | None]]:
        if ekirjasto_role == "orgadmin":
            return [(AdminRole.SYSTEM_ADMIN, None)]

        libraries = {
            library
            for municipality_code in municipalities
            if (library := Library.lookup_by_municipality(db, municipality_code))
        }

        if ekirjasto_role == "admin":
            return [(AdminRole.LIBRARY_MANAGER, library) for library in libraries]

        if ekirjasto_role == "librarian":
            return [(AdminRole.LIBRARIAN, library) for library in libraries]

        # other possible values are "sysadmin", "registrant" and "customer",
        # these are not allowed as circulation admins
        return []

    def sign_in(self):
        """Redirects admin if they're signed in, or shows the sign in page."""
        if not self.admin_auth_providers:
            return ADMIN_AUTH_NOT_CONFIGURED

        admin = self.authenticated_admin_from_request()

        if isinstance(admin, ProblemDetail):
            redirect_url = flask.request.args.get("redirect")
            auth_provider_html = [
                auth.sign_in_template(redirect_url)
                for auth in self.admin_auth_providers
            ]
            auth_provider_html = """
                <section style="{section}">
                <hr style="{hr}">or<hr style="{hr}">
                </section>
            """.format(
                section=section_style, hr=hr_style
            ).join(
                auth_provider_html
            )

            html = self.SIGN_IN_TEMPLATE % dict(
                auth_provider_html=auth_provider_html,
                logo_url=AdminClientConfig.lookup_asset_url(key="admin_logo"),
            )
            headers = dict()
            headers["Content-Type"] = "text/html"
            return Response(html, 200, headers)
        elif admin:
            return SanitizedRedirections.redirect(
                flask.request.args.get("redirect", "/admin/web")
            )

    def password_sign_in(self):
        if not self.admin_auth_providers:
            return ADMIN_AUTH_NOT_CONFIGURED

        auth = self.admin_auth_provider(PasswordAdminAuthenticationProvider.NAME)
        if not auth:
            return ADMIN_AUTH_MECHANISM_NOT_CONFIGURED

        admin_details, redirect_url = auth.sign_in(self._db, flask.request.form)
        if isinstance(admin_details, ProblemDetail):
            return self.error_response(INVALID_ADMIN_CREDENTIALS)

        admin = self.authenticated_admin(admin_details)
        return SanitizedRedirections.redirect(redirect_url)

    def change_password(self):
        admin = flask.request.admin

        if admin.is_authenticated_externally():
            return ADMIN_NOT_AUTHORIZED

        new_password = flask.request.form.get("password")
        if new_password:
            admin.password = new_password
        return Response(_("Success"), 200)

    def sign_out(self):
        # Clear out the admin's flask session.
        flask.session.pop("admin_email", None)
        flask.session.pop("auth_type", None)
        flask.session.pop("admin_given_name", None)

        # Finland, revoke ekirjasto session
        self._try_revoke_ekirjasto_session()

        redirect_url = url_for(
            "admin_sign_in",
            redirect=url_for("admin_view", _external=True),
            _external=True,
        )
        return SanitizedRedirections.redirect(redirect_url)

    # Finland
    def _try_revoke_ekirjasto_session(self):
        ekirjasto_token = flask.session.pop("ekirjasto_token", None)
        auth: EkirjastoAdminAuthenticationProvider = self.admin_auth_provider(
            EkirjastoAdminAuthenticationProvider.NAME
        )
        if ekirjasto_token and auth:
            auth.try_revoke_ekirjasto_session(ekirjasto_token)

    def error_response(self, problem_detail):
        """Returns a problem detail as an HTML response"""
        html = self.ERROR_RESPONSE_TEMPLATE % dict(
            status_code=problem_detail.status_code, message=problem_detail.detail
        )
        return Response(html, problem_detail.status_code)


class SanitizedRedirections:
    """Functions to sanitize redirects."""

    @staticmethod
    def _check_redirect(target: str) -> tuple[bool, str]:
        """Check that a redirect is allowed.
        Because the URL redirect is assumed to be untrusted user input,
        we extract the URL path and forbid redirecting to external
        hosts.
        """
        redirect_url = urlsplit(target)

        # If the redirect isn't asking for a particular host, then it's safe.
        if redirect_url.netloc in (None, ""):
            return True, ""

        # Otherwise, if the redirect is asking for a different host, it's unsafe.
        if redirect_url.netloc != flask.request.host:
            logging.warning(f"Redirecting to {redirect_url.netloc} is not permitted")
            return False, _("Redirecting to an external domain is not allowed.")

        return True, ""

    @staticmethod
    def redirect(target: str) -> WerkzeugResponse:
        """Check that a redirect is allowed before performing it."""
        ok, message = SanitizedRedirections._check_redirect(target)
        if ok:
            return redirect(target, Response=Response)
        else:
            return Response(message, 400)
