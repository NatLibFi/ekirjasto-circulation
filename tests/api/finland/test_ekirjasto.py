import datetime
import uuid
from base64 import b64encode
from collections.abc import Callable
from functools import partial

import jwt
import pytest
import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import url_for

from api.authentication.base import PatronData
from api.circulation_exceptions import InternalServerError, RemoteInitiatedServerError
from api.ekirjasto_authentication import (
    EkirjastoAuthAPILibrarySettings,
    EkirjastoAuthAPISettings,
    EkirjastoAuthenticationAPI,
    EkirjastoEnvironment,
)
from api.util.patron import PatronUtility
from core.model import Credential, DataSource, Patron
from core.util.datetime_helpers import utc_now
from core.util.problem_detail import ProblemDetail
from tests.core.mock import MockRequestsResponse
from tests.fixtures.api_controller import ControllerFixture


class MockEkirjastoRemoteAPI:
    def __init__(self):
        self.test_users = {
            # TODO: currently there is no difference with (in our perspective) verified and unverified, but this might change.
            "unverified": {
                "sub": "0CA5A0D5-ABA2-4104-AEFB-E37A30B66E23",
                "name": "Unverified User",
                "family_name": "User",
                "given_name": "Unverified",
                "email": "unverified.user@example.com",
                "username": "unverified.user",
                "role": "customer",
                "municipality": "Helsinki",
                # When True, this will be replaced with real expire timestamp in init.
                "exp": True,
                "verified": False,
                "passkeys": [],
            },
            "verified": {
                "sub": "6B0DDBEE-572A-4B94-8619-500CAD9747D6",
                "name": "Verified User",
                "family_name": "User",
                "given_name": "Verified",
                "email": "verified.user@example.com",
                "username": "verified.user",
                "role": "customer",
                "municipality": "Helsinki",
                "exp": True,
                "verified": True,
                "passkeys": [],
            },
        }
        self.access_tokens = {}
        for user_id, user in self.test_users.items():
            if user["exp"] == True:
                self._refresh_token_for_user_id(user_id)

    def _generate_auth_token_info(self, exp_duration_seconds: int = 600):
        return {
            "token": b64encode(uuid.uuid4().bytes).decode("ascii"),
            # Ekirjasto API returns expire timestamp in milliseconds
            "exp": int(
                (
                    (
                        utc_now() + datetime.timedelta(seconds=exp_duration_seconds)
                    ).timestamp()
                )
                * 1000
            ),
        }

    def _refresh_token_for_user_id(self, user_id):
        # Remove (invalidate) old tokens for the user.
        self.access_tokens = {
            key: val
            for key, val in self.access_tokens.items()
            if val["user_id"] != user_id
        }

        # Create new valid token, with meta info.
        token_info = self._generate_auth_token_info()
        self.access_tokens[token_info["token"]] = {"user_id": user_id, **token_info}

        # Update token expiration in user info.
        self.test_users[user_id]["exp"] = token_info["exp"]

        return token_info

    def get_test_access_token_for_user(self, user_id):
        # Normally this is got after using some login method with Ekirjasto.
        for token, token_info in self.access_tokens.items():
            if token_info["user_id"] == user_id:
                return token_info["token"], token_info["exp"]
        assert None, f"Token info for user '{user_id}' was not found."

    def _check_authentication(self, access_token):
        token_info = None
        if access_token in self.access_tokens.keys():
            token_info = self.access_tokens[access_token]

        user_id = None
        if (
            token_info != None
            and float(token_info["exp"] / 1000) > utc_now().timestamp()
        ):
            # Token is not expired.
            user_id = token_info["user_id"]

        if user_id != None and not user_id in self.test_users:
            user_id = None

        return user_id

    def api_userinfo(self, access_token):
        user_id = self._check_authentication(access_token)

        if user_id != None:
            return MockRequestsResponse(200, content=self.test_users[user_id])

        return MockRequestsResponse(401)

    def api_refresh(self, access_token):
        user_id = self._check_authentication(access_token)

        if user_id != None:
            token_info = self._refresh_token_for_user_id(user_id)
            return MockRequestsResponse(200, content=token_info)

        return MockRequestsResponse(401)


class MockEkirjastoAuthenticationAPI(EkirjastoAuthenticationAPI):
    def __init__(
        self,
        library_id,
        integration_id,
        settings,
        library_settings,
        analytics=None,
        bad_connection=False,
        failure_status_code=None,
    ):
        super().__init__(library_id, integration_id, settings, library_settings, None)

        self.bad_connection = bad_connection
        self.failure_status_code = failure_status_code

        self.mock_api = MockEkirjastoRemoteAPI()

    def _create_circulation_url(self, endpoint, db):
        library = self.library(db)

        return url_for(
            endpoint,
            _external=True,
            library_short_name="test-library",
            provider=self.label(),
        )

    def requests_get(self, url, ekirjasto_token=None):
        if self.bad_connection:
            raise requests.exceptions.ConnectionError(
                "Connection error", self.__class__.__name__
            )
        elif self.failure_status_code:
            # Simulate a server returning an unexpected error code.
            return MockRequestsResponse(
                self.failure_status_code, "Error %s" % self.failure_status_code
            )
        if "userinfo" in url:
            return self.mock_api.api_userinfo(ekirjasto_token)

        assert None, f"Mockup for GET {url} not created"

    def requests_post(self, url, ekirjasto_token=None, json_body=None):
        if self.bad_connection:
            raise requests.exceptions.ConnectionError(
                "Connection error", self.__class__.__name__
            )
        elif self.failure_status_code:
            # Simulate a server returning an unexpected error code.
            return MockRequestsResponse(
                self.failure_status_code, "Error %s" % self.failure_status_code
            )
        if "refresh" in url:
            return self.mock_api.api_refresh(ekirjasto_token)

        assert None, f"Mockup for POST {url} not created"


@pytest.fixture
def mock_library_id() -> int:
    return 20


@pytest.fixture
def mock_integration_id() -> int:
    return 20


@pytest.fixture
def create_settings() -> Callable[..., EkirjastoAuthAPISettings]:
    return partial(
        EkirjastoAuthAPISettings, ekirjasto_environment=EkirjastoEnvironment.DEVELOPMENT
    )


@pytest.fixture
def create_provider(
    mock_library_id: int,
    mock_integration_id: int,
    create_settings: Callable[..., EkirjastoAuthAPISettings],
) -> Callable[..., MockEkirjastoAuthenticationAPI]:
    return partial(
        MockEkirjastoAuthenticationAPI,
        library_id=mock_library_id,
        integration_id=mock_integration_id,
        settings=create_settings(),
        library_settings=EkirjastoAuthAPILibrarySettings(),
    )


class TestEkirjastoAuthentication:
    def test_authentication_flow_document(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
        controller_fixture: ControllerFixture,
    ):
        # We're about to call url_for, so we must create an
        # application context.
        provider = create_provider()
        controller_fixture.app.config["SERVER_NAME"] = "localhost"

        with controller_fixture.app.test_request_context("/"):
            doc = provider._authentication_flow_document(controller_fixture.db.session)
            assert provider.label() == doc["description"]
            assert provider.flow_type == doc["type"]

            assert (
                doc["links"][0]["href"]
                == "http://localhost/test-library/ekirjasto_authenticate?provider=E-kirjasto+provider+for+circulation+manager"
            )

            assert (
                doc["links"][6]["rel"] == "passkey_register_start"
                and doc["links"][6]["href"]
                == "http://localhost/test-library/ekirjasto/passkey/register/start?provider=E-kirjasto+provider+for+circulation+manager"
            )

            assert (
                doc["links"][7]["rel"] == "passkey_register_finish"
                and doc["links"][7]["href"]
                == "http://localhost/test-library/ekirjasto/passkey/register/finish?provider=E-kirjasto+provider+for+circulation+manager"
            )

    def test_from_config(
        self,
        create_settings: Callable[..., EkirjastoAuthAPISettings],
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        settings = create_settings(
            ekirjasto_environment=EkirjastoEnvironment.FAKE,
            delegate_expire_time=537457,
        )
        provider = create_provider(settings=settings)

        # Verify that the configuration details were stored properly.
        assert EkirjastoEnvironment.FAKE == provider.ekirjasto_environment
        assert 537457 == provider.delegate_expire_timemestamp

    def test_persistent_patron_delegate_id(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
        controller_fixture: ControllerFixture,
    ):
        provider = create_provider()

        db_patron = controller_fixture.db.patron()
        assert isinstance(db_patron, Patron)

        delegate_id = provider.get_patron_delegate_id(
            controller_fixture.db.session, db_patron
        )
        delegate_id2 = provider.get_patron_delegate_id(
            controller_fixture.db.session, db_patron
        )

        assert delegate_id == delegate_id2

        data_source = DataSource.lookup(controller_fixture.db.session, provider.label())
        assert isinstance(data_source, DataSource)

        credential = Credential.lookup_by_token(
            controller_fixture.db.session,
            data_source,
            provider.patron_delegate_id_credential_key(),
            delegate_id,
            allow_persistent_token=True,
        )

        assert credential.credential == delegate_id

        # Must be persistent credential
        assert credential.expires == None

        delegate_patron = provider.get_patron_with_delegate_id(
            controller_fixture.db.session, delegate_id
        )

        assert isinstance(delegate_patron, Patron)
        assert db_patron.id == delegate_patron.id

    def test_secrets(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
        controller_fixture: ControllerFixture,
    ):
        provider = create_provider()

        provider.set_secrets(controller_fixture.db.session)

        assert provider.delegate_token_signing_secret is not None
        assert provider.delegate_token_encrypting_secret is not None

        # Secrets should be strong enough.
        assert len(provider.delegate_token_signing_secret) > 30
        assert len(provider.delegate_token_encrypting_secret) > 30

    def test_create_delegate_token(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
        controller_fixture: ControllerFixture,
    ):
        provider = create_provider()

        decrypted_provider_token = "test_token"
        patron_delegate_id = "test_delegate_id"
        expires_at = int(utc_now().timestamp()) + 500

        # Secrets are not set, so this will fail.
        pytest.raises(
            InternalServerError,
            provider.create_ekirjasto_delegate_token,
            decrypted_provider_token,
            patron_delegate_id,
            expires_at,
        )

        provider.set_secrets(controller_fixture.db.session)
        delegate_token = provider.create_ekirjasto_delegate_token(
            decrypted_provider_token, patron_delegate_id, expires_at
        )

        # Is valid, otherwise throws exception.
        decoded_payload = provider.decode_ekirjasto_delegate_token(
            delegate_token, decrypt_ekirjasto_token=False
        )
        decoded_payload_decrypted = provider.decode_ekirjasto_delegate_token(
            delegate_token, decrypt_ekirjasto_token=True
        )

        # Double validate payload
        required_options = ["token", "iss", "sub", "iat", "exp"]
        payload_options = decoded_payload.keys()
        for option in required_options:
            assert option in payload_options

        assert decoded_payload["token"] != decrypted_provider_token
        assert decoded_payload_decrypted["token"] == decrypted_provider_token
        assert decoded_payload_decrypted["iss"] == provider.label()
        assert decoded_payload["iss"] == provider.label()
        assert decoded_payload_decrypted["sub"] == patron_delegate_id
        assert decoded_payload["sub"] == patron_delegate_id

        timestamp_now = utc_now().timestamp()
        assert decoded_payload_decrypted["iat"] < timestamp_now
        assert decoded_payload["iat"] < timestamp_now

    @pytest.mark.parametrize(
        "wrong_signing_secret,wrong_encrypting_secret",
        [
            (True, True),
            (True, False),
            (False, True),
        ],
    )
    def test_wrong_secret_delegate_token(
        self,
        wrong_signing_secret,
        wrong_encrypting_secret,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
        controller_fixture: ControllerFixture,
    ):
        provider = create_provider()

        decrypted_provider_token = "test_token"
        patron_delegate_id = "test_delegate_id"
        expires_at = int(utc_now().timestamp()) + 500

        provider.set_secrets(controller_fixture.db.session)
        delegate_token = provider.create_ekirjasto_delegate_token(
            decrypted_provider_token, patron_delegate_id, expires_at
        )

        # Change secrets
        if wrong_signing_secret:
            provider.delegate_token_signing_secret = Fernet.generate_key().decode()
        if wrong_encrypting_secret:
            provider.delegate_token_encrypting_secret = Fernet.generate_key()

        # Try to decode with wrong secrets, should throw exception.
        if wrong_signing_secret:
            # Signing validate error due to wrong key.
            pytest.raises(
                jwt.exceptions.InvalidTokenError,
                provider.decode_ekirjasto_delegate_token,
                delegate_token,
                decrypt_ekirjasto_token=True,
            )
        else:
            # Signing is ok, decrypt error due to wrong key.
            pytest.raises(
                InvalidToken,
                provider.decode_ekirjasto_delegate_token,
                delegate_token,
                decrypt_ekirjasto_token=True,
            )

        # Also this method should fail.
        decoded_payload = provider.validate_ekirjasto_delegate_token(
            delegate_token, decrypt_ekirjasto_token=True
        )
        assert isinstance(
            decoded_payload, ProblemDetail
        ), "Validation must not work with wrong secrets."

        # Set secrets back to the original secrets from database.
        provider.set_secrets(controller_fixture.db.session)

        # Verify that it now works
        decoded_payload = provider.decode_ekirjasto_delegate_token(
            delegate_token, decrypt_ekirjasto_token=True
        )
        validated_payload = provider.validate_ekirjasto_delegate_token(
            delegate_token, decrypt_ekirjasto_token=True
        )

        assert decoded_payload == validated_payload

    def test_expired_delegate_token(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
        controller_fixture: ControllerFixture,
    ):
        provider = create_provider()

        decrypted_provider_token = "test_token"
        patron_delegate_id = "test_delegate_id"
        # Expire time 1 second in history.
        expires_at = int((utc_now() - datetime.timedelta(seconds=1)).timestamp())

        provider.set_secrets(controller_fixture.db.session)
        delegate_token = provider.create_ekirjasto_delegate_token(
            decrypted_provider_token, patron_delegate_id, expires_at
        )

        # This fails with InvalidTokenError as expected
        pytest.raises(
            jwt.exceptions.InvalidTokenError,
            provider.decode_ekirjasto_delegate_token,
            delegate_token,
        )

        # This will not fail.
        decoded_payload = provider.decode_ekirjasto_delegate_token(
            delegate_token, validate_expire=False
        )
        assert decoded_payload != None

    def test_refresh_token_remote_success(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider()
        user_id = "verified"

        first_token, first_token_exp = provider.mock_api.get_test_access_token_for_user(
            user_id
        )

        token = first_token
        expires = first_token_exp
        previous_token = first_token
        previous_expires = first_token_exp
        # Test that refresh works multiple times in row. Running this multiple
        # times mostly ensures that the mock API works properly.
        for i in range(3):
            previous_token = token
            previous_expires = expires
            token, expires = provider.remote_refresh_token(token)

            assert isinstance(token, str)
            assert isinstance(expires, int)

            assert token != first_token
            assert expires >= first_token_exp
            assert token != previous_token
            assert expires >= previous_expires

            # Verify the refresh happened correctly in the mock API.
            assert provider.mock_api.get_test_access_token_for_user(user_id)[0] == token
            assert provider.mock_api._check_authentication(token) == user_id

    def test_refresh_token_remote_invalidated(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider()
        user_id = "verified"
        first_token, first_token_exp = provider.mock_api.get_test_access_token_for_user(
            user_id
        )

        # This works properly
        token, expires = provider.remote_refresh_token(first_token)
        assert isinstance(token, str)
        assert isinstance(expires, int)

        # This fails, because we use old token.
        token, expires = provider.remote_refresh_token(first_token)

        assert isinstance(token, ProblemDetail)
        assert token.status_code == 401
        assert expires == None

    def test_refresh_token_remote_bad_status_code(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider(failure_status_code=502)
        user_id = "verified"
        first_token, first_token_exp = provider.mock_api.get_test_access_token_for_user(
            user_id
        )

        token, expires = provider.remote_refresh_token(first_token)

        assert isinstance(token, ProblemDetail)
        assert token.status_code == 400
        assert expires == None

    def test_refresh_token_remote_bad_connection(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider(bad_connection=True)
        user_id = "verified"
        first_token, first_token_exp = provider.mock_api.get_test_access_token_for_user(
            user_id
        )

        pytest.raises(
            RemoteInitiatedServerError, provider.remote_refresh_token, first_token
        )

    def test_patron_lookup_remote_success(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider()
        user_id = "verified"
        user = provider.mock_api.test_users[user_id]
        token, expires = provider.mock_api.get_test_access_token_for_user(user_id)

        patrondata = provider.remote_patron_lookup(token)

        assert isinstance(patrondata, PatronData)

        assert patrondata.permanent_id == user["sub"]

    def test_patron_lookup_remote_invalidated(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider()
        user_id = "verified"
        first_token, first_token_exp = provider.mock_api.get_test_access_token_for_user(
            user_id
        )

        # Invalidate first_token
        token, expires = provider.remote_refresh_token(first_token)
        assert isinstance(token, str)
        assert isinstance(expires, int)

        # This fails, because we use old (invalid) token.
        patrondata = provider.remote_patron_lookup(first_token)

        assert isinstance(patrondata, ProblemDetail)
        assert patrondata.status_code == 401

    def test_patron_lookup_remote_status_code(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider(failure_status_code=502)
        user_id = "verified"
        token, _ = provider.mock_api.get_test_access_token_for_user(user_id)

        patrondata = provider.remote_patron_lookup(token)

        assert isinstance(patrondata, ProblemDetail)
        assert patrondata.status_code == 400

    def test_patron_lookup_remote_connection(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider(bad_connection=True)
        user_id = "verified"
        token, _ = provider.mock_api.get_test_access_token_for_user(user_id)

        pytest.raises(RemoteInitiatedServerError, provider.remote_patron_lookup, token)

    def test_update_patron_from_remote(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
        controller_fixture: ControllerFixture,
    ):
        default_library = controller_fixture.db.default_library()
        provider = create_provider(library_id=default_library.id)
        user_id = "verified"

        # Update patrons role on remote API.
        first_role = "first_user_role"
        provider.mock_api.test_users[user_id]["role"] = first_role

        token, expires = provider.mock_api.get_test_access_token_for_user(user_id)

        # Create new patron, because we don't have one in database yet.
        patron, is_new = provider.ekirjasto_authenticate(
            controller_fixture.db.session, token
        )

        assert is_new == True
        assert isinstance(patron, Patron)
        assert patron.external_type == first_role

        # Update patrons role on remote API.
        new_role = "new_user_role"
        provider.mock_api.test_users[user_id]["role"] = new_role

        # Now we get updated patron with new role.
        updated_patron = provider.authenticate_and_update_patron(
            controller_fixture.db.session, token
        )

        assert isinstance(updated_patron, Patron)
        assert updated_patron.external_type == new_role
        assert updated_patron.id == patron.id

    def test_authenticated_patron_delegate_token_expired(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
        controller_fixture: ControllerFixture,
    ):
        default_library = controller_fixture.db.default_library()
        provider = create_provider(library_id=default_library.id)
        user_id = "verified"

        decrypted_provider_token = "test_token"
        patron_delegate_id = "test_delegate_id"
        # Expire time 1 second in history.
        expires_at = int((utc_now() - datetime.timedelta(seconds=1)).timestamp())

        provider.set_secrets(controller_fixture.db.session)
        ekirjasto_token, _ = provider.mock_api.get_test_access_token_for_user(user_id)
        delegate_token = provider.create_ekirjasto_delegate_token(
            ekirjasto_token, patron_delegate_id, expires_at
        )
        assert isinstance(delegate_token, str)

        # Our delegate_token is now expired.
        decoded_payload = provider.validate_ekirjasto_delegate_token(
            delegate_token, validate_expire=False
        )

        assert type(decoded_payload) is dict

        patron = provider.authenticated_patron(
            controller_fixture.db.session, decoded_payload
        )

        assert patron is None

    def test_authenticated_patron_ekirjasto_token_invalid(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
        controller_fixture: ControllerFixture,
    ):
        default_library = controller_fixture.db.default_library()
        provider = create_provider(library_id=default_library.id)

        user_id = "verified"
        # Expire time far in future.
        expires_at = int((utc_now() + datetime.timedelta(seconds=600)).timestamp())

        provider.set_secrets(controller_fixture.db.session)

        # Get valid ekirjasto_token.
        ekirjasto_token, _ = provider.mock_api.get_test_access_token_for_user(user_id)

        # Create new patron, because we don't have one in database yet.
        patron, is_new = provider.ekirjasto_authenticate(
            controller_fixture.db.session, ekirjasto_token
        )
        assert is_new == True
        assert isinstance(patron, Patron)
        patron_delegate_id = provider.get_patron_delegate_id(
            controller_fixture.db.session, patron
        )

        assert patron_delegate_id is not None

        # Delegate token with the ekirjasto token.
        delegate_token = provider.create_ekirjasto_delegate_token(
            ekirjasto_token, patron_delegate_id, expires_at
        )
        assert isinstance(delegate_token, str)

        # Invalidate ekirjasto_token.
        valid_ekirjasto_token = provider.mock_api._refresh_token_for_user_id(user_id)[
            "token"
        ]

        # Our delegate_token is not expired.
        decoded_payload = provider.validate_ekirjasto_delegate_token(
            delegate_token, validate_expire=True
        )
        assert isinstance(decoded_payload, dict)

        # Patron is synced, because it was just created.
        assert PatronUtility.needs_external_sync(patron) == False

        # This works because delegate token is valid, even though ekirjsto token is not.
        patron = provider.authenticated_patron(
            controller_fixture.db.session, decoded_payload
        )
        assert isinstance(patron, Patron)

        # Change that patron is synced far in history and needs new remote sync.
        patron.last_external_sync = (
            utc_now() - Patron.MAX_SYNC_TIME - datetime.timedelta(minutes=1)
        )
        assert PatronUtility.needs_external_sync(patron) == True

        # This fails because remote sync is needed, but ekirjasto token is invalid.
        result = provider.authenticated_patron(
            controller_fixture.db.session, decoded_payload
        )
        assert isinstance(result, ProblemDetail)
        assert result.status_code == 401

        # Delegate token with the valid ekirjasto token.
        delegate_token = provider.create_ekirjasto_delegate_token(
            valid_ekirjasto_token, patron_delegate_id, expires_at
        )
        assert isinstance(delegate_token, str)
        decoded_payload = provider.validate_ekirjasto_delegate_token(
            delegate_token, validate_expire=True
        )
        assert isinstance(decoded_payload, dict)

        # Now we can sync the patron.
        patron = provider.authenticated_patron(
            controller_fixture.db.session, decoded_payload
        )
        assert isinstance(patron, Patron)
        assert PatronUtility.needs_external_sync(patron) == False

    def test_remote_endpoint_get_success(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider()
        user_id = "verified"
        token, expires = provider.mock_api.get_test_access_token_for_user(user_id)

        response_json, response_code = provider.remote_endpoint("/v1/auth/userinfo", token, "GET")

        assert isinstance(response_json, dict)
        assert response_code == 200

    def test_remote_endpoint_post_success(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider()
        user_id = "verified"
        token, expires = provider.mock_api.get_test_access_token_for_user(user_id)

        response_json, response_code = provider.remote_endpoint("/v1/auth/refresh", token, "POST", {"empty": "json"})

        assert isinstance(response_json, dict)
        assert response_code == 200

    def test_remote_endpoint_invalid_token(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider()
        user_id = "verified"
        token, expires = provider.mock_api.get_test_access_token_for_user(user_id)
        
        # Invalidate the token.
        provider.mock_api._refresh_token_for_user_id(user_id)

        response_json, response_code = provider.remote_endpoint("/v1/auth/userinfo", token, "GET")

        assert response_json == None
        assert response_code == 401

    def test_remote_endpoint_unsupported_method(
        self,
        create_provider: Callable[..., MockEkirjastoAuthenticationAPI],
    ):
        provider = create_provider()

        response_json, response_code = provider.remote_endpoint("/v1/auth/userinfo", "token", "PUT")

        assert isinstance(response_json, ProblemDetail)
        assert response_json.status_code == 415
        assert response_code == None
