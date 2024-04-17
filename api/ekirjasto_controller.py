from __future__ import annotations

import datetime
import json
import logging

import jwt
from flask import Response

from api.problem_details import (
    EKIRJASTO_PROVIDER_NOT_CONFIGURED,
    EKIRJASTO_REMOTE_AUTHENTICATION_FAILED,
    INVALID_EKIRJASTO_DELEGATE_TOKEN,
)
from core.model import Patron
from core.util.datetime_helpers import from_timestamp, utc_now
from core.util.problem_detail import ProblemDetail


class EkirjastoController:
    """Controller used for handing Ekirjasto authentication requests"""

    def __init__(self, circulation_manager, authenticator):
        """Initializes a new instance of EkirjastoController class

        :param circulation_manager: Circulation Manager
        :type circulation_manager: CirculationManager

        :param authenticator: Authenticator object used to route requests to the appropriate LibraryAuthenticator
        :type authenticator: Authenticator
        """
        self._circulation_manager = circulation_manager
        self._authenticator = authenticator

        self._logger = logging.getLogger(__name__)

    def _get_delegate_expire_timestamp(self, ekirjasto_expire_millis: int) -> int:
        """Get the expire time to use for delegate token, it is calculated based on
        expire time of the ekirjasto token.

        :param ekirjasto_expire_millis: Ekirjasto token expiration timestamp in milliseconds.

        :return: Timestamp for the delegate token expiration in seconds.
        """

        delegate_expire_seconds = (
            utc_now()
            + datetime.timedelta(
                seconds=self._authenticator.ekirjasto_provider.delegate_expire_timemestamp
            )
        ).timestamp()

        now_seconds = utc_now().timestamp()

        # Use ekirjasto expire time at 70 % of the remaining duration, so we have some time to refresh it.
        ekirjasto_expire_seconds = (
            (ekirjasto_expire_millis / 1000) - now_seconds
        ) * 0.7 + now_seconds

        return int(min(ekirjasto_expire_seconds, delegate_expire_seconds))

    def get_tokens(self, authorization, validate_expire=False):
        """Extract possible delegate and ekirjasto tokens from the authorization header."""
        if self.is_configured != True:
            return EKIRJASTO_PROVIDER_NOT_CONFIGURED, None, None, None

        if (
            authorization is None
            or authorization.token is None
            or len(authorization.token) == 0
        ):
            return INVALID_EKIRJASTO_DELEGATE_TOKEN, None, None, None

        token = authorization.token

        ekirjasto_token = None
        delegate_token = None
        delegate_sub = None
        delegate_expired = True
        try:
            # We may attempt to refresh ekirjasto token in any case, so we don't validate
            # delegate token expiration by default and we need the decrypted ekirjasto token.
            delegate_payload = self._authenticator.ekirjasto_provider.validate_ekirjasto_delegate_token(
                token, validate_expire=validate_expire, decrypt_ekirjasto_token=True
            )
            if isinstance(delegate_payload, ProblemDetail):
                # The delegate_payload might be ProblemDetail, indicating that the token
                # is not valid. Still thetoken might be ekirjasto_token (which is not JWT or
                # at least not signed by us), so we can continue.
                ekirjasto_token = token
            else:
                # Successful validation of a delegate token for circulation manager.
                ekirjasto_token = delegate_payload["token"]
                delegate_expired = from_timestamp(delegate_payload["exp"]) < utc_now()
                delegate_sub = delegate_payload["sub"]
                delegate_token = token
        except jwt.exceptions.DecodeError as e:
            # It might be just an ekirjasto_token, it will be used to authenticate
            # with the remote ekirjasto API. We don't do anything further validation
            # for it as it is valdiated with successful authentication.
            ekirjasto_token = token

        if delegate_token == None:
            return INVALID_EKIRJASTO_DELEGATE_TOKEN, None, None, None

        return delegate_token, ekirjasto_token, delegate_sub, delegate_expired

    @property
    def is_configured(self):
        if self._authenticator.ekirjasto_provider:
            return True
        return False

    def refresh_tokens_if_needed(self, authorization, _db, sync_patron):
        """Refresh delegate and ekirjasto tokens if delegate is expired."""
        if self.is_configured != True:
            return EKIRJASTO_PROVIDER_NOT_CONFIGURED, None, None

        ekirjasto_provider = self._authenticator.ekirjasto_provider

        (
            delegate_token,
            ekirjasto_token,
            delegate_sub,
            delegate_expired,
        ) = self.get_tokens(authorization)
        if isinstance(delegate_token, ProblemDetail):
            return delegate_token, None, None

        ekirjasto_token_expires = None
        if delegate_expired:
            (
                ekirjasto_token,
                ekirjasto_token_expires,
            ) = ekirjasto_provider.remote_refresh_token(ekirjasto_token)

            if isinstance(ekirjasto_token, ProblemDetail):
                return ekirjasto_token, None, None
            if ekirjasto_token == None or ekirjasto_token_expires == None:
                return EKIRJASTO_REMOTE_AUTHENTICATION_FAILED, None, None

        is_patron_new = False
        patron = None
        if sync_patron:
            # Synchronize or create patron
            patron, is_patron_new = ekirjasto_provider.ekirjasto_authenticate(
                _db, ekirjasto_token
            )
            if not isinstance(patron, Patron):
                # Authentication was failed.
                if patron == None:
                    return INVALID_EKIRJASTO_DELEGATE_TOKEN, None, None
                # Return ProblemDetail
                return patron, None, None
        elif delegate_token != None:
            patron = ekirjasto_provider.get_patron_with_delegate_id(_db, delegate_sub)
            if patron == None:
                # Causes to return 401 error
                return INVALID_EKIRJASTO_DELEGATE_TOKEN, None, None
        else:
            return INVALID_EKIRJASTO_DELEGATE_TOKEN, None, None

        if ekirjasto_token_expires != None:
            # We have new ekirjasto token.
            # Create a delegate token which we can give to the patron.
            delegate_expires = self._get_delegate_expire_timestamp(
                ekirjasto_token_expires
            )
            delegate_token = ekirjasto_provider.create_ekirjasto_delegate_token(
                ekirjasto_token,
                ekirjasto_provider.get_patron_delegate_id(_db, patron),
                delegate_expires,
            )

        return delegate_token, ekirjasto_token, is_patron_new

    def authenticate(self, request, _db):
        """Authenticate patron with ekirjasto API and return delegate token for
        circulation manager API access.

        New Patron is created to database if ekirjasto authentication was succesfull
        and no patron for it was found. Token for ekirjasto API is stored for later usage.
        """
        if self.is_configured != True:
            return EKIRJASTO_PROVIDER_NOT_CONFIGURED

        delegate_token, ekirjasto_token, is_patron_new = self.refresh_tokens_if_needed(
            request.authorization, _db, sync_patron=True
        )
        if delegate_token == None or isinstance(delegate_token, ProblemDetail):
            return delegate_token

        patron_info = None
        patrondata = self._authenticator.ekirjasto_provider.remote_patron_lookup(
            ekirjasto_token
        )
        if patrondata:
            patron_info = json.dumps(patrondata.to_dict)

        response_json = json.dumps(
            {"access_token": delegate_token, "patron_info": patron_info}
        )
        response_code = 201 if is_patron_new else 200
        return Response(response_json, response_code, mimetype="application/json")

    def get_decrypted_ekirjasto_token(self, request):
        """Call E-kirjasto API's passkey register endpoints on behalf of the user."""
        if self.is_configured != True:
            return EKIRJASTO_PROVIDER_NOT_CONFIGURED

        (
            delegate_token,
            ekirjasto_token,
            delegate_sub,
            delegate_expired,
        ) = self.get_tokens(request.authorization, validate_expire=True)
        if isinstance(delegate_token, ProblemDetail):
            return delegate_token

        return Response(
            json.dumps({"token": ekirjasto_token}), 200, mimetype="application/json"
        )

    def call_remote_endpoint(self, remote_path, request):
        """Call E-kirjasto API's passkey register endpoints on behalf of the user."""
        if self.is_configured != True:
            return EKIRJASTO_PROVIDER_NOT_CONFIGURED

        (
            delegate_token,
            ekirjasto_token,
            delegate_sub,
            delegate_expired,
        ) = self.get_tokens(request.authorization)
        if isinstance(delegate_token, ProblemDetail):
            return delegate_token

        (
            response_json,
            response_code,
        ) = self._authenticator.ekirjasto_provider.remote_endpoint(
            remote_path, ekirjasto_token, request.method, request.json
        )
        if isinstance(response_json, ProblemDetail):
            return response_json
        elif isinstance(response_json, dict):
            response_json = json.dumps(response_json)
        else:
            response_json = None

        return Response(response_json, response_code, mimetype="application/json")
