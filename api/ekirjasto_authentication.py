from __future__ import annotations

import datetime
import json
import jwt
import logging
import requests
import uuid

from abc import ABC
from base64 import b64decode, b64encode
from cryptography.fernet import Fernet
from enum import Enum
from flask import url_for, Response
from flask_babel import lazy_gettext as _
from typing import Any

from sqlalchemy.orm import Session
from werkzeug.datastructures import Authorization

from api.authentication.base import (
    AuthenticationProvider,
    AuthProviderLibrarySettings,
    AuthProviderSettings,
    PatronData,
)

from api.util.patron import PatronUtility
from .circulation_exceptions import (
    InternalServerError,
    PatronNotFoundOnRemote,
    RemoteInitiatedServerError,
    RemotePatronCreationFailedException
)
from .config import Configuration
from core.analytics import Analytics
from core.integration.settings import (
    ConfigurationFormItem,
    ConfigurationFormItemType,
    FormField,
)
from core.model import ConfigurationSetting, Credential, DataSource, Patron, get_one
from core.util.datetime_helpers import from_timestamp, utc_now
from core.util.log import elapsed_time_logging
from core.util.problem_detail import ProblemDetail
from .problem_details import (
    UNSUPPORTED_AUTHENTICATION_MECHANISM,
    EKIRJASTO_PROVIDER_NOT_CONFIGURED,
    EKIRJASTO_REMOTE_AUTHENTICATION_FAILED,
    INVALID_EKIRJASTO_TOKEN,
    INVALID_EKIRJASTO_DELEGATE_TOKEN
)

class EkirjastoController():
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

    def _get_delegate_expire_timestamp(self, ekirjasto_token_expires: int) -> int:
        """ Get the expire time to use for delegate token, it is calculated based on 
        expire time of the ekirjasto token.
        
        :param ekirjasto_token_expires: Ekirjasto token expiration timestamp in seconds.
        
        :return: Timestamp for the delegate token expiration.
        """
        
        delegate_token_expires = (
            utc_now() + datetime.timedelta(
                seconds=self._authenticator.ekirjasto_provider.delegate_expire_timemestamp
            )
        ).timestamp()
        
        # Use ekirjasto expire time at 70 % of the remaining duration, so we have some time to refresh it.
        now_seconds = utc_now().timestamp()
        ekirjasto_token_expires = (ekirjasto_token_expires - now_seconds) * 0.7 + now_seconds
        
        if ekirjasto_token_expires < delegate_token_expires:
            return int(ekirjasto_token_expires)
        
        return int(delegate_token_expires)
    
    def authenticate(self, request, _db):
        """ Authenticate patron with ekirjasto API and return delegate token for 
        circulation manager API access.
        
        New Patron is created to database if ekirjasto authentication was succesfull 
        and no patron for it was found. Token for ekirjasto API is stored for later usage.
        """
        
        if self._authenticator.ekirjasto_provider:
            ekirjasto_provider = self._authenticator.ekirjasto_provider
            
            token = request.authorization.token
            if token is None or len(token) == 0:
                return EKIRJASTO_REMOTE_AUTHENTICATION_FAILED
            
            ekirjasto_token = None
            delegate_token = None
            delegate_expired = True
            try:
                # We won't to attempt to refresh ekirjasto token in any case, so we don't validate 
                # delegate token expiration and we need the decrypted ekrijasto token.
                delegate_payload = ekirjasto_provider.validate_ekirjasto_delegate_token(
                    token,
                    validate_expire=False,
                    decrypt_ekirjasto_token=True
                )
                if isinstance(delegate_payload, ProblemDetail):
                    # The ekirjasto_token might be ProblemDetail, indicating that the token 
                    # is not valid. Still it might be ekirjasto_token (which is not JWT or 
                    # at least not signed by us), so we can continue.
                    ekirjasto_token = token
                else:
                    # Successful validation of a delegate token for circulation manager.
                    ekirjasto_token = delegate_payload["token"]
                    delegate_expired = from_timestamp(delegate_payload["exp"]) < utc_now()
                    delegate_token = token
            except jwt.exceptions.DecodeError as e:
                # It might be just an ekirjasto_token, it will be used to authenticate 
                # with the remote ekirjasto API. We don't do anything further validation
                # for it as it is valdiated with successful authentication.
                ekirjasto_token = token
                pass
            
            patron, is_new = ekirjasto_provider.ekirjasto_authenticate(_db, ekirjasto_token)
            if not isinstance(patron, Patron):
                # Authentication was failed.
                if patron == None:
                    return EKIRJASTO_REMOTE_AUTHENTICATION_FAILED
                # Return ProblemDetail
                return patron
            
            ekirjasto_token_expires = None
            if delegate_expired:
                ekirjasto_token, ekirjasto_token_expires = ekirjasto_provider.remote_refresh_token(ekirjasto_token)
                
                if isinstance(ekirjasto_token, ProblemDetail):
                    return ekirjasto_token
                if ekirjasto_token == None or ekirjasto_token_expires == None:
                    return ekirjasto_token
            
            if ekirjasto_token_expires != None:
                # Create a delegate token which we can give to the patron.
                delegate_expires = self._get_delegate_expire_timestamp(ekirjasto_token_expires)
                delegate_token = ekirjasto_provider.create_ekirjasto_delegate_token(
                    ekirjasto_token,
                    ekirjasto_provider.get_patron_delegate_id(_db, patron),
                    delegate_expires
                )

            patrondata = ekirjasto_provider.remote_patron_lookup(ekirjasto_token)
            patron_info = None
            if patrondata:
                patron_info = json.dumps(patrondata.to_dict)
            response_json = json.dumps({"access_token": delegate_token, "patron_info": patron_info})
            response_code = 201 if is_new else 200
            return Response(response_json, response_code, mimetype='application/json')
            
        return EKIRJASTO_PROVIDER_NOT_CONFIGURED

class EkirjastoEnvironment(Enum):
    FAKE = "http://localhost"
    DEVELOPMENT = "https://e-kirjasto.loikka.dev"
    PRODUCTION = "https://tunnus.e-kirjasto.fi"


class EkirjastoAuthAPISettings(AuthProviderSettings):
    """Settings for the EkirjastoAuthenticationAPI."""

    # API environment form field, choose between dev and prod.
    ekirjasto_environment: EkirjastoEnvironment = FormField(
        EkirjastoEnvironment.FAKE,
        form=ConfigurationFormItem(
            label=_("E-kirjasto API environment"),
            description=_("Select what environment of E-kirjasto accounts should be used."),
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
    
    delegate_expire_time: int = FormField(
        60*60*12, # 12 hours
        form=ConfigurationFormItem(
            label=_("Delegate token expire time in seconds"),
            description=_("Expire time for a delegate token to authorize in behalf of a ekirjasto token. This should be less than the expire time for ekirjasto token, so it can be refreshed."),
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

        self.log = logging.getLogger(f"{self.__module__}.{self.__class__.__name__}")

        self.ekirjasto_environment = settings.ekirjasto_environment
        self.delegate_expire_timemestamp = settings.delegate_expire_time

        self.delegate_token_signing_secret = None
        self.delegate_token_encrypting_secret = None
        
        self.analytics = analytics
        
        self.fake_ekirjasto_token = "4d2i2w3o1f6t3e1y0d46655q114q4d37200o3s6q5f1z2r4i1z0q1o5d3f695g1g"
        
        self._ekirjasto_api_url = self.ekirjasto_environment.value
        if self.ekirjasto_environment == EkirjastoEnvironment.FAKE:
            self._ekirjasto_api_url = EkirjastoEnvironment.DEVELOPMENT.value
        
        self._metadata_cache = None
        self._metadata_cache_expires = utc_now()

    @property
    def flow_type(self) -> str:
        return "http://opds-spec.org/auth/ekirjasto"
        
    @classmethod
    def label(cls) -> str:
        return "E-kirjasto"
        
    @classmethod
    def patron_delegate_id_credential_key(cls) -> str:
        return "E-kirjasto patron uuid"

    @classmethod
    def description(cls) -> str:
        return (
            "Authenticate patrons with E-kirjasto accounts service."
        )

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
            "type": "http://opds-spec.org/auth/ekirjasto",
            "description": "E-kirjasto",
            "links": [
                {
                    "rel": "authenticate",
                    "href": "http://localhost:6500/ellibs-test/ekirjasto_authenticate?provider=E-kirjasto"
                },
                {
                    "rel": "ekirjasto_api",
                    "href": "https://e-kirjasto.loikka.dev"
                },
                {
                    "rel": "ekirjasto_google_auth",
                    "href": "https://e-kirjasto.loikka.dev/v1/auth/google"
                },
                
                ...
            ],
            "metadata": {
                "apple": {
                    "client_id": "dev.loikka.e-kirjasto",
                    "redirect_uris": [
                        "https://e-kirjasto.loikka.dev/v1/auth/apple"
                    ]
                },
                "google": {
                    "client_id": "462705856349-4c6e3qq8pbf8d9431fiank56akm4li4s.apps.googleusercontent.com",
                    "redirect_uris": [
                        "https://e-kirjasto.loikka.dev/v1/auth/google"
                    ],
                    "android_client_ids": [
                        "462705856349-1u8eeh5v656bq1dekrkub1gh56gth316.apps.googleusercontent.com"
                    ],
                    "ios_client_ids": [
                        "462705856349-elejphck0ojv1m9jvful1edjp3uen8aj.apps.googleusercontent.com"
                    ]
                }
            }
        }
        """
        
        flow_doc = {
            "type": self.flow_type, 
            "description": self.label(), 
            "links": [
                {
                    "rel" : "authenticate",
                    "href": self._create_authenticate_url(_db)
                },
                {
                    "rel" : "ekirjasto_api",
                    "href": self._ekirjasto_api_url
                },
                {
                    "rel" : "ekirjasto_google_auth",
                    "href": f'{self._ekirjasto_api_url}/v1/auth/google'
                },
                {
                    "rel" : "ekirjasto_apple_auth",
                    "href": f'{self._ekirjasto_api_url}/v1/auth/google'
                },
                {
                    "rel" : "ekirjasto_tunnistus_start",
                    "href": f'{self._ekirjasto_api_url}/v1/tunnistus?locale=fi'
                },
                {
                    "rel" : "ekirjasto_tunnistus_finish",
                    "href": f'{self._ekirjasto_api_url}/v1/tunnistus/saml2acs'
                },
                {
                    "rel" : "ekirjasto_passkey_login_start",
                    "href": f'{self._ekirjasto_api_url}/v1/auth/passkey/login/start'
                },
                {
                    "rel" : "ekirjasto_passkey_login_finish",
                    "href": f'{self._ekirjasto_api_url}/v1/auth/passkey/login/finish'
                },
                {
                    "rel" : "ekirjasto_passkey_register_start",
                    "href": f'{self._ekirjasto_api_url}/v1/auth/passkey/register/start'
                },
                {
                    "rel" : "ekirjasto_passkey_register_finish",
                    "href": f'{self._ekirjasto_api_url}/v1/auth/passkey/register/finish'
                },
                {
                    "rel" : "ekirjasto_otp_start",
                    "href": f'{self._ekirjasto_api_url}/v1/auth/otp/start'
                },
                {
                    "rel" : "ekirjasto_otp_finish",
                    "href": f'{self._ekirjasto_api_url}/v1/auth/otp/finish'
                },
            ],
            "metadata": self._get_ekirjasto_metadata()
        }
        
        return flow_doc

    def _create_authenticate_url(self, db):
        """Returns an authentication link used by clients to authenticate patrons

        :param db: Database session
        :type db: sqlalchemy.orm.session.Session

        :return: URL for authentication using the chosen IdP
        :rtype: string
        """

        library = self.library(db)

        return url_for(
            "ekirjasto_authenticate",
            _external=True,
            library_short_name=library.short_name,
            provider=self.label(),
        )
        
    def _get_ekirjasto_metadata(self):
        """ Get metadata for the ekirjasto authentication methods. Local cache 
        is used if available and not too old, otherwise metadata fetched from remote.
        """
        
        metadata = None
        if not self._metadata_cache or self._metadata_cache_expires < utc_now():
            # Update local metadata cache.
            metadata = self.remote_fetch_metadata()
            
            # Update global cache every 5 minutes.
            self._metadata_cache_expires = utc_now() + datetime.timedelta(minutes=5)
        else:
            metadata = self._metadata_cache
            
        return metadata
        
    def _run_self_tests(self, _db):
        pass
        
    def _userinfo_to_patrondata(self, userinfo_json: dict) -> PatronData:
        """ Convert user info JSON received from the ekirjasto API to PatronData.
        
        Example of userinfo_json
        {
            "email": "example@example.com",
            "exp": 1703943722208,
            "family_name": "Sukunimi",
            "given_name": "Etunimi",
            "name": "Etunimi Sukunimi",
            "role": "user",
            "sub": "12345678901234567890"
        }
        
        After OTP authentication seems to return:
        {'username': 'johannes.ylonen+test1@indium.fi', 'exp': 1707333838913, 'verified': False}
        """
        
        def _get_key_or_none(userinfo_json, key):
            if key in userinfo_json:
                return userinfo_json[key]
            return None
        
        # TODO: Check if more info is needed/available. E.g. blocked, fines, etc
        #       At least cached_neighborhood is required.
        patrondata = PatronData(
            permanent_id=_get_key_or_none(userinfo_json, "username"), # TODO: This must be some permanent like "sub"
            authorization_identifier=_get_key_or_none(userinfo_json, "email"),
            external_type=_get_key_or_none(userinfo_json, "role"),
            personal_name=_get_key_or_none(userinfo_json, "name"),
            email_address=_get_key_or_none(userinfo_json, "email"),
            username=_get_key_or_none(userinfo_json, "username"),
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

    def get_patron_delegate_id(self, _db: Session, patron: Patron) -> str:
        """Find or randomly create an identifier to use when identifying
        this patron from delegate token.
        """
        def refresher_method(credential):
            credential.credential = str(uuid.uuid4())
        credential = Credential.lookup(
            _db,
            self.label(),
            self.patron_delegate_id_credential_key(),
            patron,
            refresher_method,
            allow_persistent_token=True,
        )
        return credential.credential

    def get_patron_with_delegate_id(self, _db: Session, patron_delegate_id: str) -> Patron | None:
        """Find patron based on its delegate id.
        """
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
        
        # Encrypt requires more secure secret than the sitewide_secret can provide.
        secret = ConfigurationSetting.sitewide(_db, Configuration.EKIRJASTO_TOKEN_ENCRYPTING_SECRET)
        if not secret.value:
            secret.value = Fernet.generate_key().decode()
            _db.commit()
        self.delegate_token_encrypting_secret = secret.value.encode()

    def _check_secrets_or_throw(self):
        if (
            self.delegate_token_signing_secret == None or len(self.delegate_token_signing_secret) == 0
            or self.delegate_token_encrypting_secret == None or len(self.delegate_token_encrypting_secret) == 0
        ):
            raise InternalServerError("Ekirjasto authenticator not fully setup, secrets are missing.")

    def create_ekirjasto_delegate_token(
        self, provider_token: str, patron_delegate_id: str, expires: int
    ) -> str:
        """
        TODO: CHECK ME
        Create a JSON web token with the given provider name and access
        token.

        The patron will use this as a bearer token in lieu of the
        token we got from their OAuth provider. The big advantage of
        this token is that it tells us _which_ OAuth provider the
        patron authenticated against.

        When the patron uses the bearer token in the Authenticate header,
        it will be decoded with `decode_bearer_token_from_header`.
        """
        self._check_secrets_or_throw()
        
        # Encrypt the ekirjasto token with a128cbc-hs256 algorithm.
        fernet = Fernet(self.delegate_token_encrypting_secret)
        encrypted_token = b64encode(fernet.encrypt(provider_token.encode())).decode("ascii") 
        
        payload = dict(
            token=encrypted_token,
            iss=self.label(),
            sub=patron_delegate_id,
            iat=int(utc_now().timestamp()),
            exp=expires,
        )
        return jwt.encode(payload, self.delegate_token_signing_secret, algorithm="HS256")

    def decode_ekirjasto_delegate_token(self, delegate_token: str, validate_expire: bool = True, decrypt_ekirjasto_token: bool = False) -> dict:
        """
        TODO: CHECK ME
        Extract auth provider name and access token from JSON web token.
        
        return decoded payload
        """
        self._check_secrets_or_throw()
        
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
            issuer=self.label()
        )
        
        if decrypt_ekirjasto_token:
            decoded_payload["token"] = self._decrypt_ekirjasto_token(decoded_payload["token"])
        
        return decoded_payload

    def _decrypt_ekirjasto_token(self, token: str):
        fernet = Fernet(self.delegate_token_encrypting_secret)
        encrypted_token = b64decode(token.encode("ascii"))
        return fernet.decrypt(encrypted_token).decode()

    def validate_ekirjasto_delegate_token(self, delegate_token: str, validate_expire: bool = True, decrypt_ekirjasto_token: bool = False) -> dict | ProblemDetail:
        """
        TODO: DOCUMENT ME
        
        return decoded payload
        """
        
        try:
            # Validate bearer token and get credential info.
            decoded_payload = self.decode_ekirjasto_delegate_token(delegate_token, validate_expire, decrypt_ekirjasto_token)
        except jwt.exceptions.InvalidTokenError as e:
            return INVALID_EKIRJASTO_DELEGATE_TOKEN
        return decoded_payload

    def remote_fetch_metadata(self):
        """ Fetch metadata for the ekirjasto authentication methods from the ekirjasto API."""
        
        url = self._ekirjasto_api_url + "/v1/auth/metadata"
        
        try:
            response = requests.get(url)
            
            # TODO: REMOVE ME
            print("remote_fetch_metadata content", response)
        except requests.exceptions.ConnectionError as e:
            raise RemoteInitiatedServerError(str(e), self.__class__.__name__)
        
        if response.status_code != 200:
            return None
        
        try:
            content = response.json()
            
            # TODO: REMOVE ME
            print("remote_fetch_metadata content", content)
        except requests.exceptions.JSONDecodeError as e:
            raise RemoteInitiatedServerError(str(e), self.__class__.__name__)
            
        return content

    def remote_refresh_token(self, token: str):
        """ Refresh ekirjasto token with ekirjasto API call.
        
        We assume that the token is valid, API call fails if not.
        
        :return: token and expire timestamp if refresh was succesfull or None | ProblemDetail otherwise.
        """
        
        if self.ekirjasto_environment == EkirjastoEnvironment.FAKE:
            token = self.fake_ekirjasto_token
            expires = utc_now() + datetime.timedelta(days=1)
            return token, expires.timestamp()
        
        url = self._ekirjasto_api_url + "/v1/auth/refresh"
        headers = {'Authorization': f'Bearer {token}'}
        
        try:
            response = requests.post(url, headers=headers)
            
            # TODO: REMOVE ME
            print("remote_refresh_token content", response, response.content)
        except requests.exceptions.ConnectionError as e:
            raise RemoteInitiatedServerError(str(e), self.__class__.__name__)
        
        if response.status_code == 401:
            # Do nothing if authentication fails, e.g. token expired.
            return None, None
        elif response.status_code != 200:
            # TODO Log as warnign or similar.
            msg = "Got unexpected response code %d. Content: %s" % (
                response.status_code,
                response.content,
            )
            return None, None
        else:
            try:
                content = response.json()
                
                # TODO: REMOVE ME
                print("remote_refresh_token content", content)
            except requests.exceptions.JSONDecodeError as e:
                raise RemoteInitiatedServerError(str(e), self.__class__.__name__)
            
            token = content["token"]
            expires = content["exp"]/1000
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
                    permanent_id='34637274574578',
                    authorization_identifier='test_34637274574578',
                    external_type='user',
                    personal_name='Fake User',
                    complete=True,
                )
            else:
                return None
        
        url = self._ekirjasto_api_url + "/v1/auth/userinfo"
        headers = {'Authorization': f'Bearer {ekirjasto_token}'}
        print("remote_patron_lookup headers", headers)
        print("remote_patron_lookup url", url)
        
        try:
            response = requests.get(url, headers=headers)
            
            # TODO: REMOVE ME
            print("remote_patron_lookup content", response, response.content)
            print("remote_patron_lookup response.request.headers", response.request.headers)
            print("remote_patron_lookup response.headers", response.headers)
        except requests.exceptions.ConnectionError as e:
            raise RemoteInitiatedServerError(str(e), self.__class__.__name__)
        
        if response.status_code == 401:
            # Do nothing if authentication fails, e.g. token expired.
            return INVALID_EKIRJASTO_TOKEN
        elif response.status_code != 200:
            # TODO: log warning or something.
            msg = "Got unexpected response code %d. Content: %s" % (
                response.status_code,
                response.content,
            )
            return None
        else:
            try:
                content = response.json()
                
                # TODO: REMOVE ME
                print("remote_patron_lookup content", content)
            except requests.exceptions.JSONDecodeError as e:
                raise RemoteInitiatedServerError(str(e), self.__class__.__name__)
            
            return self._userinfo_to_patrondata(content)
        
        return None

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
            lookup = dict(
                external_identifier=patrondata.permanent_id,
                library_id=self.library_id
            )
            
            patron = get_one(_db, Patron, **lookup)

        return patron

    def ekirjasto_authenticate(
        self, _db: Session, ekirjasto_token: str
    ):
        """ Authenticate patron with remote ekirjasto API and if necessary, 
        create authenticated patron if not in database.

        :param ekirjasto_token: A token for e-kirjasto account endpoint.
        """
        is_new = False
        
        with elapsed_time_logging(
            log_method=self.logger().info,
            message_prefix="authenticated_patron - ekirjasto_authenticate",
        ):
            patron = self.authenticate_and_update_patron(_db, ekirjasto_token)
        
        if isinstance(patron, PatronData):
            # We didn't find the patron, but authentication to external truth was 
            # succesfull, so we create a new patron with the information we have.
            patron, is_new = patron.get_or_create_patron(
                _db, self.library_id, analytics=self.analytics
            )
        
        return patron, is_new

    def authenticated_patron(
        self, _db: Session, authorization: dict | str
    ) -> Patron | ProblemDetail | None:
        """Go from a werkzeug.Authorization object to a Patron object.

        If the Patron needs to have their metadata updated, it happens
        transparently at this point.

        :return: A Patron if one can be authenticated; a ProblemDetail
            if an error occurs; None if the credentials are missing or wrong.
        """
        if type(authorization) != dict:
            return UNSUPPORTED_AUTHENTICATION_MECHANISM
        
        ekirjasto_token = None
        delegate_patron = None
        if "token" in authorization and "exp" in authorization and "sub" in authorization:
            encrypted_ekirjasto_token = authorization["token"]
            delegate_expired = from_timestamp(authorization["exp"]) < utc_now()
            patron_delegate_id = authorization["sub"]
            
            if delegate_expired:
                # Causes to return 401 error
                return None
            
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
        else:
            return UNSUPPORTED_AUTHENTICATION_MECHANISM

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
            raise PatronNotFoundOnRemote(404, "Remote patron is confilcting with delegate patron.")
        if patron.cached_neighborhood and not patron.neighborhood:
            # Patron.neighborhood (which is not a model field) was not
            # set, probably because we avoided an expensive metadata
            # update. But we have a cached_neighborhood (which _is_ a
            # model field) to use in situations like this.
            patron.neighborhood = patron.cached_neighborhood
        return patron
