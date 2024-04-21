from __future__ import annotations

import datetime
import uuid
from abc import ABC
from base64 import b64decode, b64encode
from enum import Enum
from typing import Any

import flask_babel
import jwt
import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import url_for
from flask_babel import lazy_gettext as _
from sqlalchemy.orm import Session
from werkzeug.datastructures import Authorization

from api.authentication.base import (
    AuthenticationProvider,
    AuthProviderLibrarySettings,
    AuthProviderSettings,
    PatronData,
)
from api.circulation_exceptions import (
    InternalServerError,
    PatronNotFoundOnRemote,
    RemoteInitiatedServerError,
    RemotePatronCreationFailedException,
)
from api.config import Configuration
from api.problem_details import (
    EKIRJASTO_REMOTE_AUTHENTICATION_FAILED,
    EKIRJASTO_REMOTE_ENDPOINT_FAILED,
    EKIRJASTO_REMOTE_METHOD_NOT_SUPPORTED,
    INVALID_EKIRJASTO_DELEGATE_TOKEN,
    INVALID_EKIRJASTO_TOKEN,
    UNSUPPORTED_AUTHENTICATION_MECHANISM,
)
from api.util.patron import PatronUtility
from core.analytics import Analytics
from core.integration.settings import (
    ConfigurationFormItem,
    ConfigurationFormItemType,
    FormField,
)
from core.model import ConfigurationSetting, Credential, DataSource, Patron, get_one
from core.model.library import Library
from core.util.datetime_helpers import from_timestamp, utc_now
from core.util.log import elapsed_time_logging
from core.util.problem_detail import ProblemDetail


class EkirjastoEnvironment(Enum):
    FAKE = "http://localhost"
    DEVELOPMENT = "https://e-kirjasto.loikka.dev"
    PRODUCTION = "https://tunnistus.e-kirjasto.fi"


class MagazineEnvironment(Enum):
    DEVELOPMENT = "https://e-kirjasto-playground.epaper.fi/"
    PRODUCTION = "https://e-kirjasto.ewl.epress.fi/"


class EkirjastoAuthAPISettings(AuthProviderSettings):
    """Settings for the EkirjastoAuthenticationAPI."""

    _DEFAULT_DELEGATE_EXPIRE_SECONDS = datetime.timedelta(hours=12).seconds

    # API environment form field, choose between dev and prod.
    ekirjasto_environment: EkirjastoEnvironment = FormField(
        EkirjastoEnvironment.FAKE,
        form=ConfigurationFormItem(
            label=_("E-kirjasto API environment"),
            description=_(
                "Select what environment of E-kirjasto accounts should be used."
            ),
            type=ConfigurationFormItemType.SELECT,
            options={
                EkirjastoEnvironment.FAKE: "Fake",
                EkirjastoEnvironment.DEVELOPMENT: "Development",
                EkirjastoEnvironment.PRODUCTION: "Production",
            },
            required=True,
            weight=10,
        ),
    )

    magazine_service: MagazineEnvironment = FormField(
        MagazineEnvironment.DEVELOPMENT,
        form=ConfigurationFormItem(
            label=_("E-magazines environment"),
            description=_(
                "Select what environment of e-magazines service should be used."
            ),
            type=ConfigurationFormItemType.SELECT,
            options={
                MagazineEnvironment.DEVELOPMENT: "Development",
                MagazineEnvironment.PRODUCTION: "Production",
            },
            required=True,
            weight=10,
        ),
    )

    delegate_expire_time: int = FormField(
        _DEFAULT_DELEGATE_EXPIRE_SECONDS,
        form=ConfigurationFormItem(
            label=_("Delegate token expire time in seconds"),
            description=_(
                "Expire time for a delegate token to authorize in behalf of a ekirjasto token. This should be less than the expire time for ekirjasto token, so it can be refreshed."
            ),
            required=True,
        ),
    )


class EkirjastoAuthAPILibrarySettings(AuthProviderLibrarySettings):
    ...


class EkirjastoAuthenticationAPI(AuthenticationProvider, ABC):
    """Verify a token for E-kirjasto login, with a remote source of truth."""

    def __init__(
        self,
        library_id: int,
        integration_id: int,
        settings: EkirjastoAuthAPISettings,
        library_settings: EkirjastoAuthAPILibrarySettings,
        analytics: Analytics | None = None,
    ):
        """Create a EkirjastoAuthenticationAPI."""
        super().__init__(
            library_id, integration_id, settings, library_settings, analytics
        )

        self.ekirjasto_environment = settings.ekirjasto_environment
        self.magazine_service = settings.magazine_service
        self.delegate_expire_timemestamp = settings.delegate_expire_time

        self.delegate_token_signing_secret: str | None = None
        self.delegate_token_encrypting_secret: bytes | None = None

        self.analytics = analytics

        self.fake_ekirjasto_token = (
            "4d2i2w3o1f6t3e1y0d46655q114q4d37200o3s6q5f1z2r4i1z0q1o5d3f695g1g"
        )

        self._ekirjasto_api_url = self.ekirjasto_environment.value
        if self.ekirjasto_environment == EkirjastoEnvironment.FAKE:
            self._ekirjasto_api_url = EkirjastoEnvironment.DEVELOPMENT.value

        self._magazine_service_url = self.magazine_service.value

    @property
    def flow_type(self) -> str:
        return "http://e-kirjasto.fi/authtype/ekirjasto"

    @classmethod
    def label(cls) -> str:
        return "E-kirjasto provider for circulation manager"

    @classmethod
    def patron_delegate_id_credential_key(cls) -> str:
        return "E-kirjasto patron uuid"

    @classmethod
    def description(cls) -> str:
        return "Authenticate patrons with E-kirjasto accounts service."

    @property
    def identifies_individuals(self):
        return True

    @classmethod
    def settings_class(cls) -> type[EkirjastoAuthAPISettings]:
        return EkirjastoAuthAPISettings

    @classmethod
    def library_settings_class(
        cls,
    ) -> type[EkirjastoAuthAPILibrarySettings]:
        return EkirjastoAuthAPILibrarySettings

    def _authentication_flow_document(self, _db: Session) -> dict[str, Any]:
        """Create a Authentication Flow object for use in an Authentication for
        OPDS document.

        This follows loosely the specification for OPDS authentication document (https://drafts.opds.io/authentication-for-opds-1.0.html#24-authentication-provider).

        Example:
        {
            "type": "http://e-kirjasto.fi/authtype/ekirjasto",
            "description": "E-kirjasto",
            "links": [
                {
                    "rel": "authenticate",
                    "href": "http://localhost:6500/ellibs-test/ekirjasto_authenticate?provider=E-kirjasto"
                },
                {
                    "rel": "api",
                    "href": "https://e-kirjasto.loikka.dev"
                },

                ...
            ]
        }
        """

        flow_doc = {
            "type": self.flow_type,
            "description": self.label(),
            "links": [
                {
                    "rel": "authenticate",
                    "href": self._create_circulation_url("ekirjasto_authenticate", _db),
                },
                {
                    "rel": "ekirjasto_token",
                    "href": self._create_circulation_url("ekirjasto_token", _db),
                },
                {"rel": "magazine_service", "href": self._magazine_service_url},
                {"rel": "api", "href": self._ekirjasto_api_url},
                {
                    "rel": "tunnistus_start",
                    "href": f"{self._ekirjasto_api_url}/v1/auth/tunnistus/start?locale={flask_babel.get_locale()}",
                },
                {
                    "rel": "tunnistus_finish",
                    "href": f"{self._ekirjasto_api_url}/v1/auth/tunnistus/finish",
                },
                {
                    "rel": "passkey_login_start",
                    "href": f"{self._ekirjasto_api_url}/v1/auth/passkey/login/start",
                },
                {
                    "rel": "passkey_login_finish",
                    "href": f"{self._ekirjasto_api_url}/v1/auth/passkey/login/finish",
                },
                {
                    "rel": "passkey_register_start",
                    "href": self._create_circulation_url(
                        "ekirjasto_passkey_register_start", _db
                    ),
                },
                {
                    "rel": "passkey_register_finish",
                    "href": self._create_circulation_url(
                        "ekirjasto_passkey_register_finish", _db
                    ),
                },
            ],
        }

        return flow_doc

    def _create_circulation_url(self, endpoint, db):
        """Returns an authentication link used by clients to authenticate patrons

        :param db: Database session
        :type db: sqlalchemy.orm.session.Session

        :return: URL for authentication using the chosen IdP
        :rtype: string
        """

        library = self.library(db)

        return url_for(
            endpoint,
            _external=True,
            library_short_name=library.short_name,
            provider=self.label(),
        )

    def _run_self_tests(self, _db):
        pass

    def _userinfo_to_patrondata(self, userinfo_json: dict) -> PatronData:
        """Convert user info JSON received from the ekirjasto API to PatronData.

        Example of userinfo_json
        {
            'exp': 1703141144518,
            'family_name': 'Testi',
            'given_name': 'Testi',
            'name': 'Testi Testi',
            'role': 'customer',
            'sub': '1bf3c6ea-0502-45fc-a785-0113d8f78a51',
            'municipality': 'Helsinki',
            'verified': True,
            'passkeys': []
        }
        """

        def _get_key_or_none(userinfo_json, key):
            if key in userinfo_json:
                return userinfo_json[key]
            return None

        patrondata = PatronData(
            permanent_id=_get_key_or_none(userinfo_json, "sub"),
            authorization_identifier=_get_key_or_none(
                userinfo_json, "sub"
            ),  # TODO: We don't know exactly what this should be.
            external_type=_get_key_or_none(userinfo_json, "role"),
            personal_name=_get_key_or_none(userinfo_json, "name"),
            email_address=_get_key_or_none(userinfo_json, "email"),
            username=_get_key_or_none(userinfo_json, "username"),
            cached_neighborhood=_get_key_or_none(userinfo_json, "municipality"),
            complete=True,
        )

        if patrondata.permanent_id == None:
            # permanent_id is used to get the local Patron, we cannot proceed
            # if it is missing.
            message = "Value for permanent_id is missing in remote user info."
            raise RemotePatronCreationFailedException(message, self.__class__.__name__)

        return patrondata

    def get_credential_from_header(self, auth: Authorization) -> str | None:
        # We cannot extract the credential from the header, so we just return None.
        # This is only needed for authentication providers where the external
        # circulation API needs additional authentication.
        return None

    def get_patron_delegate_id(self, _db: Session, patron: Patron) -> str | None:
        """Find or randomly create an identifier to use when identifying
        this patron from delegate token.
        """

        def refresher_method(credential):
            credential.credential = str(uuid.uuid4())

        data_source = DataSource.lookup(_db, self.label(), autocreate=True)
        if data_source == None:
            raise InternalServerError(
                "Ekirjasto authenticator failed to create DataSource for itself."
            )

        credential = Credential.lookup(
            _db,
            data_source,
            self.patron_delegate_id_credential_key(),
            patron,
            refresher_method,
            allow_persistent_token=True,
        )
        return credential.credential

    def get_patron_with_delegate_id(
        self, _db: Session, patron_delegate_id: str
    ) -> Patron | None:
        """Find patron based on its delegate id."""
        data_source = DataSource.lookup(_db, self.label())
        if data_source == None:
            return None

        credential = Credential.lookup_by_token(
            _db,
            data_source,
            self.patron_delegate_id_credential_key(),
            patron_delegate_id,
            allow_persistent_token=True,
        )

        if credential == None:
            return None

        return credential.patron

    def set_secrets(self, _db):
        self.delegate_token_signing_secret = ConfigurationSetting.sitewide_secret(
            _db, Configuration.EKIRJASTO_TOKEN_SIGNING_SECRET
        )

        # Encrypting requires stronger secret than the sitewide_secret can provide.
        secret = ConfigurationSetting.sitewide(
            _db, Configuration.EKIRJASTO_TOKEN_ENCRYPTING_SECRET
        )
        if not secret.value:
            secret.value = Fernet.generate_key().decode()
            _db.commit()
        self.delegate_token_encrypting_secret = secret.value.encode()

    def create_ekirjasto_delegate_token(
        self, provider_token: str, patron_delegate_id: str, expires: int
    ) -> str:
        """
        Create a JSON Web Token fr patron with encrypted ekirjasto token in the payload.

        The patron will use this as the authentication toekn to authentiacte againsy circulation backend.
        """
        if not self.delegate_token_encrypting_secret:
            raise InternalServerError(
                "Error creating delegate token, encryption secret missing"
            )

        if not self.delegate_token_signing_secret:
            raise InternalServerError(
                "Error creating delegate token, signing secret missing"
            )

        # Encrypt the ekirjasto token with a128cbc-hs256 algorithm.
        fernet = Fernet(self.delegate_token_encrypting_secret)
        encrypted_token = b64encode(fernet.encrypt(provider_token.encode())).decode(
            "ascii"
        )

        payload = dict(
            token=encrypted_token,
            iss=self.label(),
            sub=patron_delegate_id,
            iat=int(utc_now().timestamp()),
            exp=expires,
        )

        return jwt.encode(
            payload, self.delegate_token_signing_secret, algorithm="HS256"
        )

    def decode_ekirjasto_delegate_token(
        self,
        delegate_token: str,
        validate_expire: bool = True,
        decrypt_ekirjasto_token: bool = False,
    ) -> dict:
        """
        Validate and get payload of the JSON Web Token for circulation.

        return decoded payload
        """
        if not self.delegate_token_signing_secret:
            raise InternalServerError(
                "Error decoding delegate token, signing secret missing"
            )

        options = dict(
            verify_signature=True,
            require=["token", "iss", "sub", "iat", "exp"],
            verify_iss=True,
            verify_exp=validate_expire,
            verify_iat=True,
        )

        decoded_payload = jwt.decode(
            delegate_token,
            self.delegate_token_signing_secret,
            algorithms=["HS256"],
            options=options,
            issuer=self.label(),
        )

        if decrypt_ekirjasto_token:
            decoded_payload["token"] = self._decrypt_ekirjasto_token(
                decoded_payload["token"]
            )

        return decoded_payload

    def _decrypt_ekirjasto_token(self, token: str):
        if not self.delegate_token_encrypting_secret:
            raise InternalServerError(
                "Error decrypting ekirjasto token, signing secret missing"
            )
        fernet = Fernet(self.delegate_token_encrypting_secret)
        encrypted_token = b64decode(token.encode("ascii"))
        return fernet.decrypt(encrypted_token).decode()

    def validate_ekirjasto_delegate_token(
        self,
        delegate_token: str,
        validate_expire: bool = True,
        decrypt_ekirjasto_token: bool = False,
    ) -> dict | ProblemDetail:
        """
        Validate and get payload of the JSON Web Token for circulation.

        return decoded payload or ProblemDetail
        """

        try:
            # Validate bearer token and get credential info.
            decoded_payload = self.decode_ekirjasto_delegate_token(
                delegate_token, validate_expire, decrypt_ekirjasto_token
            )
        except jwt.exceptions.InvalidTokenError as e:
            return INVALID_EKIRJASTO_DELEGATE_TOKEN
        except InvalidToken as e:
            return INVALID_EKIRJASTO_DELEGATE_TOKEN
        return decoded_payload

    def remote_refresh_token(
        self, token: str
    ) -> tuple[ProblemDetail, None] | tuple[str, int]:
        """Refresh ekirjasto token with ekirjasto API call.

        We assume that the token is valid, API call fails if not.

        :return: token and expire timestamp if refresh was succesfull or None | ProblemDetail otherwise.
        """

        if self.ekirjasto_environment == EkirjastoEnvironment.FAKE:
            fake_token = self.fake_ekirjasto_token
            expire_date = utc_now() + datetime.timedelta(days=1)
            return fake_token, int(expire_date.timestamp())

        url = self._ekirjasto_api_url + "/v1/auth/refresh"

        try:
            response = self.requests_post(url, token)
        except requests.exceptions.ConnectionError as e:
            raise RemoteInitiatedServerError(str(e), self.__class__.__name__)

        if response.status_code == 401:
            # Do nothing if authentication fails, e.g. token expired.
            return INVALID_EKIRJASTO_TOKEN, None
        elif response.status_code != 200:
            return EKIRJASTO_REMOTE_AUTHENTICATION_FAILED, None
        else:
            try:
                content = response.json()
            except requests.exceptions.JSONDecodeError as e:
                raise RemoteInitiatedServerError(str(e), self.__class__.__name__)

            token = content["token"]
            expires = content["exp"]

            return token, expires

    def remote_patron_lookup(
        self, ekirjasto_token: str | None
    ) -> PatronData | ProblemDetail | None:
        """Ask the remote for detailed information about patron related to the ekirjasto_token.

        If the patron is not found, or an error occurs communicating with the remote,
        return None or a ProblemDetail.

        Otherwise, return a PatronData object with the complete property set to True.
        """

        if self.ekirjasto_environment == EkirjastoEnvironment.FAKE:
            if ekirjasto_token == self.fake_ekirjasto_token:
                # Fake authentication successful, return fake patron data.
                return PatronData(
                    permanent_id="34637274574578",
                    authorization_identifier="test_34637274574578",
                    external_type="user",
                    personal_name="Fake User",
                    complete=True,
                )
            else:
                return None
        url = self._ekirjasto_api_url + "/v1/auth/userinfo"
        try:
            response = self.requests_get(url, ekirjasto_token)
        except requests.exceptions.ConnectionError as e:
            raise RemoteInitiatedServerError(str(e), self.__class__.__name__)

        if response.status_code == 401:
            # Do nothing if authentication fails, e.g. token expired.
            return INVALID_EKIRJASTO_TOKEN
        elif response.status_code != 200:
            msg = "Got unexpected response code %d. Content: %s" % (
                response.status_code,
                response.content,
            )
            return EKIRJASTO_REMOTE_AUTHENTICATION_FAILED
        else:
            try:
                content = response.json()
            except requests.exceptions.JSONDecodeError as e:
                raise RemoteInitiatedServerError(str(e), self.__class__.__name__)

            return self._userinfo_to_patrondata(content)

    def remote_authenticate(
        self, ekirjasto_token: str | None
    ) -> PatronData | ProblemDetail | None:
        """Does the source of truth approve the ekirjasto_token?

        If the ekirjasto_token is valid, return a PatronData object. The PatronData object
        has a `complete` field.

        If the ekirjasto_token is invalid, return None.

        If there is a problem communicating with the remote, return a ProblemDetail.
        """

        return self.remote_patron_lookup(ekirjasto_token)

    def remote_endpoint(
        self, remote_path: str, token: str, method: str, json_body: object = None
    ) -> tuple[ProblemDetail, None] | tuple[object, int]:
        """Call E-kirjasto API's passkey register endpoints on behalf of the user.

        :return: token and expire timestamp if refresh was succesfull or None | ProblemDetail otherwise.
        """

        url = self._ekirjasto_api_url + remote_path

        try:
            if method == "POST":
                response = self.requests_post(url, token, json_body)
            elif method == "GET":
                response = self.requests_get(url, token)
            else:
                return EKIRJASTO_REMOTE_METHOD_NOT_SUPPORTED, None
        except requests.exceptions.ConnectionError as e:
            raise RemoteInitiatedServerError(str(e), self.__class__.__name__)

        if response.status_code == 401:
            # Do nothing if authentication fails, e.g. token expired.
            return INVALID_EKIRJASTO_TOKEN, None
        elif response.status_code != 200:
            return EKIRJASTO_REMOTE_ENDPOINT_FAILED, None

        try:
            response_json = response.json()
        except requests.exceptions.JSONDecodeError as e:
            response_json = None

        return response_json, response.status_code

    def authenticate_and_update_patron(
        self, _db: Session, ekirjasto_token: str | None
    ) -> Patron | PatronData | ProblemDetail | None:
        """Turn an ekirjasto_token into a Patron object.

        :param ekirjasto_token: A token for e-kirjasto authorization.

        :return: A Patron if one can be authenticated; PatronData if
            authenticated, but Patron not available; a ProblemDetail
            if an error occurs; None if the credentials are missing or wrong.
        """

        # Check the ekirjasto token with the remote source of truth.
        patrondata = self.remote_authenticate(ekirjasto_token)

        if not isinstance(patrondata, PatronData):
            # Either an error occurred or the credentials did not correspond to any patron.
            return patrondata

        # At this point we know there is _some_ authenticated patron,
        # but it might not correspond to any Patron in our database.
        # Try to look up the Patron object in our database.
        patron = self.local_patron_lookup(_db, patrondata)

        if patron:
            # Apply the remote information we have to the patron.
            patrondata.apply(patron)

            return patron

        # No Patron found from the database, but we've got remote information (PatronData).
        # Patron should be created through ekirjasto_authenticate.
        return patrondata

    def local_patron_lookup(
        self, _db: Session, patrondata: PatronData | None
    ) -> Patron | None:
        """Try to find a Patron object in the local database.

        :param patrondata: A PatronData object recently obtained from
            the source of truth. This may make it possible to
            identify the patron more precisely. Or it may be None, in
            which case it's no help at all.
        """
        patron = None
        if patrondata and patrondata.permanent_id:
            # Permanent ID is the most reliable way of identifying
            # a patron, since this is supposed to be an internal
            # ID that never changes.
            lookup = dict(external_identifier=patrondata.permanent_id)

            patron = get_one(_db, Patron, **lookup)

        return patron

    def ekirjasto_authenticate(
        self, _db: Session, ekirjasto_token: str
    ) -> tuple[Patron | ProblemDetail | None, bool]:
        """Authenticate patron with remote ekirjasto API and if necessary,
        create authenticated patron if not in database.

        :param ekirjasto_token: A token for e-kirjasto account endpoint.
        """
        is_new = False

        with elapsed_time_logging(
            log_method=self.logger().info,
            message_prefix="authenticated_patron - ekirjasto_authenticate",
        ):
            auth_result = self.authenticate_and_update_patron(_db, ekirjasto_token)

        if auth_result is None or isinstance(auth_result, ProblemDetail):
            return auth_result, False

        patron_library = Library.lookup_by_municipality(
            _db, auth_result.cached_neighborhood
        )

        if not patron_library:
            raise InternalServerError(
                "Could not determine library for municipality. No libraries created in the system yet?"
            )

        if isinstance(auth_result, PatronData):
            # We didn't find the patron, but authentication to external truth was
            # successful, so we create a new patron with the information we have.

            # E-kirjasto users are not tied to a library
            new_patron, is_new = auth_result.get_or_create_patron(
                _db,
                library_id=None,
                analytics=self.analytics,
                create_method_kwargs=dict(library_id=patron_library.id),
            )
            new_patron.last_external_sync = utc_now()
            return new_patron, is_new

        # Update patron library if changed. In practice this means that patron
        # has moved to another kimppa
        if patron_library.id != auth_result.library.id:
            auth_result.library = patron_library

        return auth_result, is_new

    def authenticated_patron(
        self, _db: Session, authorization: dict | str
    ) -> Patron | ProblemDetail | None:
        """Go from a werkzeug.Authorization object to a Patron object.

        If the Patron needs to have their metadata updated, it happens
        transparently at this point.

        :return: A Patron if one can be authenticated; a ProblemDetail
            if an error occurs; None if the credentials are missing or wrong.
        """
        # authorization is the decoded payload of the delegate token, including
        # encrypted ekirjasto token.

        if (
            type(authorization) is not dict
            or "token" not in authorization
            or "exp" not in authorization
            or "sub" not in authorization
        ):
            return UNSUPPORTED_AUTHENTICATION_MECHANISM

        ekirjasto_token = None
        delegate_patron = None
        delegate_expired = from_timestamp(authorization["exp"]) < utc_now()
        if delegate_expired:
            # Causes to return 401 error
            return None

        encrypted_ekirjasto_token = authorization["token"]
        patron_delegate_id = authorization["sub"]

        delegate_patron = self.get_patron_with_delegate_id(_db, patron_delegate_id)
        if delegate_patron == None:
            # Causes to return 401 error
            return None

        if PatronUtility.needs_external_sync(delegate_patron):
            # We should sometimes try to update the patron from remote.
            ekirjasto_token = self._decrypt_ekirjasto_token(encrypted_ekirjasto_token)
        else:
            # No need to update patron.
            return delegate_patron

        # If we come here, we have ekirjasto_token and we should try to update the patron.
        with elapsed_time_logging(
            log_method=self.logger().info,
            message_prefix="authenticated_patron - authenticate",
        ):
            patron = self.authenticate_and_update_patron(_db, ekirjasto_token)

        if isinstance(patron, PatronData):
            # Account not created, should first use ekirjasto_authenticate to
            # create an account. Authenticated to remote, but not to circulation manager.
            return None
        if not isinstance(patron, Patron):
            # Some issue with authentication.
            return patron
        if delegate_patron and patron.id != delegate_patron.id:
            # This situation should never happen.
            raise PatronNotFoundOnRemote(
                404, "Remote patron is conflicting with delegate patron."
            )
        if patron.cached_neighborhood and not patron.neighborhood:
            # Patron.neighborhood (which is not a model field) was not
            # set, probably because we avoided an expensive metadata
            # update. But we have a cached_neighborhood (which _is_ a
            # model field) to use in situations like this.
            patron.neighborhood = patron.cached_neighborhood
        return patron

    def requests_get(self, url, ekirjasto_token=None):
        headers = None
        if ekirjasto_token:
            headers = {"Authorization": f"Bearer {ekirjasto_token}"}
        return requests.get(url, headers=headers)

    def requests_post(self, url, ekirjasto_token=None, json_body=None):
        headers = None
        if ekirjasto_token:
            headers = {"Authorization": f"Bearer {ekirjasto_token}"}
        return requests.post(url, headers=headers, json=json_body)
