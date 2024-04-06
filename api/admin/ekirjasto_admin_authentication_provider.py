import logging
from urllib.parse import quote

import flask
import requests
from flask import url_for
from pydantic import BaseModel

from api.admin.admin_authentication_provider import AdminAuthenticationProvider
from api.admin.config import Configuration
from api.admin.template_styles import button_style, input_style, label_style
from api.admin.templates import ekirjasto_sign_in_template
from api.circulation_exceptions import RemoteInitiatedServerError
from api.problem_details import (
    EKIRJASTO_REMOTE_AUTHENTICATION_FAILED,
    INVALID_EKIRJASTO_TOKEN,
)
from core.util.problem_detail import ProblemDetail


class EkirjastoUserInfo(BaseModel):
    exp: int
    family_name: str = ""
    given_name: str = ""
    role: str
    sub: str
    sid: str
    # Municipality of residence, used to link patrons to a consortium
    municipality: str
    # Municipalities the admin/librarian is allowed to manage. For admins and librarians only.
    municipalities: list[str] = []
    verified: bool = False
    passkeys: list[dict] = []


# Finland
class EkirjastoAdminAuthenticationProvider(AdminAuthenticationProvider):
    NAME = "Ekirjasto Auth"

    SIGN_IN_TEMPLATE = ekirjasto_sign_in_template.format(
        label=label_style, input=input_style, button=button_style
    )

    _ekirjasto_api_url = Configuration.ekirjasto_authentication_url()

    def sign_in_template(self, redirect):
        redirect_uri = quote(
            url_for("ekirjasto_auth_finish", redirect_uri=redirect, _external=True),
        )

        # Allow passing authentication test state with query parameter. For
        # example, http://localhost:6500/admin/sign_in?state=:T0008 will result
        # in authentication with "orgadmin" role.
        state = flask.request.args.get("state", "")
        ekirjasto_auth_url = (
            f"{self._ekirjasto_api_url}/v1/auth/tunnistus/start"
            f"?locale=fi"
            f"&state={state}"
            f"&redirect_uri={redirect_uri}"
        )
        return self.SIGN_IN_TEMPLATE % dict(
            ekirjasto_auth_url=ekirjasto_auth_url,
        )

    def active_credentials(self, admin):
        # This is not called anywhere, not sure what this is for.
        return True

    def ekirjasto_authenticate(
        self, ekirjasto_token
    ) -> EkirjastoUserInfo | ProblemDetail:
        return self._get_user_info(ekirjasto_token)

    def _get_user_info(self, ekirjasto_token: str) -> EkirjastoUserInfo | ProblemDetail:
        userinfo_url = self._ekirjasto_api_url + "/v1/auth/userinfo"
        try:
            response = requests.get(
                userinfo_url, headers={"Authorization": f"Bearer {ekirjasto_token}"}
            )
        except requests.exceptions.ConnectionError as e:
            raise RemoteInitiatedServerError(str(e), self.__class__.__name__)

        if response.status_code == 401:
            return INVALID_EKIRJASTO_TOKEN
        elif response.status_code != 200:
            logging.error(
                "Got unexpected response code %d, content=%s",
                response.status_code,
                (response.content or b"No content").decode("utf-8", errors="replace"),
            )
            return EKIRJASTO_REMOTE_AUTHENTICATION_FAILED
        else:
            try:
                return EkirjastoUserInfo(**response.json())
            except requests.exceptions.JSONDecodeError as e:
                raise RemoteInitiatedServerError(str(e), self.__class__.__name__)

    def try_revoke_ekirjasto_session(self, ekirjasto_token: str) -> None:
        revoke_url = self._ekirjasto_api_url + "/v1/auth/revoke"

        try:
            response = requests.post(
                revoke_url, headers={"Authorization": f"Bearer {ekirjasto_token}"}
            )
        except requests.exceptions.ConnectionError as e:
            logging.exception(
                "Failed to revoke ekirjasto session due to connection error."
            )
            # Ignore connection error, we tried our best

        # Response codes in 4xx range mean that session is already expired, thus ok.
        # For 5xx range, we want to log the response
        if 500 <= response.status_code < 600:
            logging.error(
                "Failed to revoke ekirjasto session due to server error, status=%s, content=%s",
                response.status_code,
                (response.content or b"No content").decode("utf-8", errors="replace"),
            )
            # Ignore the error response, we tried our best
