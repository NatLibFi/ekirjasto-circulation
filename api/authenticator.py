from __future__ import annotations

import json
import logging
import sys
from abc import ABC
from collections.abc import Iterable
from typing import cast

import flask
import jwt
from flask import url_for
from flask_babel import lazy_gettext as _
from sqlalchemy.orm import Session
from werkzeug.datastructures import Authorization, Headers

from api.adobe_vendor_id import AuthdataUtility
from api.annotations import AnnotationWriter
from api.authentication.access_token import AccessTokenProvider
from api.authentication.base import (
    AuthenticationProvider,
    LibrarySettingsType,
    SettingsType,
)
from api.authentication.basic import BasicAuthenticationProvider
from api.authentication.basic_token import BasicTokenAuthenticationProvider
from api.config import CannotLoadConfiguration, Configuration
from api.custom_patron_catalog import CustomPatronCatalog
from api.ekirjasto_authentication import EkirjastoAuthenticationAPI  # Finland
from api.integration.registry.patron_auth import PatronAuthRegistry
from api.problem_details import *
from core.analytics import Analytics
from core.integration.goals import Goals
from core.integration.registry import IntegrationRegistry
from core.model import ConfigurationSetting, Library, Patron, PatronProfileStorage
from core.model.announcements import Announcement
from core.model.integration import IntegrationLibraryConfiguration
from core.user_profile import ProfileController
from core.util.authentication_for_opds import AuthenticationForOPDSDocument
from core.util.http import RemoteIntegrationException
from core.util.log import LoggerMixin, elapsed_time_logging
from core.util.opds_writer import OPDSFeed
from core.util.problem_detail import ProblemDetail, ProblemError

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self


class CirculationPatronProfileStorage(PatronProfileStorage):
    """A patron profile storage that can also provide short client tokens"""

    @property
    def profile_document(self):
        doc = super().profile_document
        drm = []
        links = []
        device_link = {}

        authdata = AuthdataUtility.from_config(self.patron.library)
        if authdata:
            vendor_id, token = authdata.short_client_token_for_patron(self.patron)
            adobe_drm = {}
            adobe_drm["drm:vendor"] = vendor_id
            adobe_drm["drm:clientToken"] = token
            adobe_drm[
                "drm:scheme"
            ] = "http://librarysimplified.org/terms/drm/scheme/ACS"
            drm.append(adobe_drm)

            annotations_link = dict(
                rel="http://www.w3.org/ns/oa#annotationService",
                type=AnnotationWriter.CONTENT_TYPE,
                href=self.url_for(
                    "annotations",
                    library_short_name=self.patron.library.short_name,
                    _external=True,
                ),
            )
            links.append(annotations_link)

            doc["links"].extend(links)

        if drm:
            doc["drm"] = drm

        return doc


class Authenticator(LoggerMixin):
    """Route requests to the appropriate LibraryAuthenticator."""

    def __init__(
        self, _db, libraries: Iterable[Library], analytics: Analytics | None = None
    ):
        # Create authenticators
        self.library_authenticators: dict[str, LibraryAuthenticator] = {}
        self.populate_authenticators(_db, libraries, analytics)

    @property
    def current_library_short_name(self):
        return flask.request.library.short_name

    def populate_authenticators(
        self, _db, libraries: Iterable[Library], analytics: Analytics | None
    ):
        with elapsed_time_logging(
            log_method=self.log.debug, message_prefix="populate_authenticators"
        ):
            for library in libraries:
                if library.short_name is None:
                    self.log.error(
                        f"Library {library.name} ({library.id}) has no short name."
                    )
                    continue
                self.library_authenticators[
                    library.short_name
                ] = LibraryAuthenticator.from_config(_db, library, analytics)

    def invoke_authenticator_method(self, method_name, *args, **kwargs):
        short_name = self.current_library_short_name
        if short_name not in self.library_authenticators:
            return LIBRARY_NOT_FOUND
        return getattr(self.library_authenticators[short_name], method_name)(
            *args, **kwargs
        )

    def authenticated_patron(self, _db, header):
        return self.invoke_authenticator_method("authenticated_patron", _db, header)

    def create_authentication_document(self):
        return self.invoke_authenticator_method("create_authentication_document")

    def create_authentication_headers(self):
        return self.invoke_authenticator_method("create_authentication_headers")

    def get_credential_from_header(self, auth):
        return self.invoke_authenticator_method("get_credential_from_header", auth)

    def create_bearer_token(self, *args, **kwargs):
        return self.invoke_authenticator_method("create_bearer_token", *args, **kwargs)

    def saml_provider_lookup(self, *args, **kwargs):
        return self.invoke_authenticator_method("saml_provider_lookup", *args, **kwargs)

    def decode_bearer_token(self, *args, **kwargs):
        return self.invoke_authenticator_method("decode_bearer_token", *args, **kwargs)

    # Finland
    @property
    def ekirjasto_provider(self) -> EkirjastoAuthenticationAPI:
        return self.invoke_authenticator_method("get_ekirjasto_provider")


class LibraryAuthenticator(LoggerMixin):
    """Use the registered AuthenticationProviders to turn incoming
    credentials into Patron objects.
    """

    @classmethod
    def from_config(
        cls: type[Self],
        _db: Session,
        library: Library,
        analytics: Analytics | None = None,
        custom_catalog_source: type[CustomPatronCatalog] = CustomPatronCatalog,
    ) -> Self:
        """Initialize an Authenticator for the given Library based on its
        configured ExternalIntegrations.

        :param custom_catalog_source: The lookup class for CustomPatronCatalogs.
            Intended for mocking during tests.
        """

        custom_catalog = custom_catalog_source.for_library(library)

        # Start with an empty list of authenticators.
        authenticator = cls(
            _db=_db, library=library, authentication_document_annotator=custom_catalog
        )

        # Find all of this library's ExternalIntegrations set up with
        # the goal of authenticating patrons.
        integrations: list[
            IntegrationLibraryConfiguration
        ] = IntegrationLibraryConfiguration.for_library_and_goal(
            _db, library, Goals.PATRON_AUTH_GOAL
        ).all()

        # Turn each such ExternalIntegration into an
        # AuthenticationProvider.
        for integration in integrations:
            try:
                authenticator.register_provider(integration, analytics)
            except CannotLoadConfiguration as e:
                # CannotLoadConfiguration is caused by misconfiguration, as opposed to bad code.
                logging.error(
                    f"Error registering authentication provider {integration.parent.name} "
                    f"({integration.parent.protocol}) for library {library.short_name}: {e}.",
                    exc_info=e,
                )
                authenticator.initialization_exceptions[
                    (integration.parent.id, library.id)
                ] = e

        if authenticator.saml_providers_by_name:
            # NOTE: this will immediately commit the database session,
            # which may not be what you want during a test. To avoid
            # this, you can create the bearer token signing secret as
            # a regular site-wide ConfigurationSetting.
            authenticator.bearer_token_signing_secret = (
                BearerTokenSigner.bearer_token_signing_secret(_db)
            )

        # Finland
        if authenticator.ekirjasto_provider:
            authenticator.ekirjasto_provider.set_secrets(_db)
        authenticator.assert_ready_for_token_signing()

        return authenticator

    def __init__(
        self,
        _db: Session,
        library: Library,
        basic_auth_provider: BasicAuthenticationProvider | None = None,
        saml_providers: list[BaseSAMLAuthenticationProvider] | None = None,
        ekirjasto_provider: EkirjastoAuthenticationAPI | None = None,  # Finland
        bearer_token_signing_secret: str | None = None,
        authentication_document_annotator: CustomPatronCatalog | None = None,
        integration_registry: None
        | (IntegrationRegistry[AuthenticationProvider]) = None,
    ):
        """Initialize a LibraryAuthenticator from a list of AuthenticationProviders.

        :param _db: A database session (probably a scoped session, which is
            why we can't derive it from `library`)

        :param library: The Library to which this LibraryAuthenticator guards
        access.

        :param basic_auth_provider: The AuthenticatonProvider that handles
        HTTP Basic Auth requests.

        :param saml_providers: A list of AuthenticationProviders that handle
        SAML requests.

        :param bearer_token_signing_secret: The secret to use when
        signing JWTs for use as bearer tokens.

        """
        self._db = _db
        self.library_id = library.id
        self.library_uuid = library.uuid
        self.library_name = library.name
        self.library_short_name = library.short_name
        self.authentication_document_annotator = authentication_document_annotator
        self.integration_registry = (
            PatronAuthRegistry()
            if integration_registry is None
            else integration_registry
        )

        self.saml_providers_by_name = {}
        self.ekirjasto_provider = ekirjasto_provider  # Finland
        self.bearer_token_signing_secret = bearer_token_signing_secret
        self.initialization_exceptions: dict[
            tuple[int | None, int | None], Exception
        ] = {}

        self.basic_auth_provider: BasicAuthenticationProvider | None = None
        self.access_token_authentication_provider: BasicTokenAuthenticationProvider | None = (
            None
        )
        if basic_auth_provider:
            self.register_basic_auth_provider(basic_auth_provider)

        if saml_providers:
            for provider in saml_providers:
                self.saml_providers_by_name[provider.label()] = provider

        self.assert_ready_for_token_signing()

    @property
    def supports_patron_authentication(self) -> bool:
        """Does this library have any way of authenticating patrons at all?"""
        if self.basic_auth_provider or self.saml_providers_by_name:
            return True
        # Finland
        if self.ekirjasto_provider:
            return True
        return False

    @property
    def identifies_individuals(self) -> bool:
        """Does this library require that individual patrons be identified?

        Most libraries require authentication as an individual. Some
        libraries don't identify patrons at all; others may have a way
        of identifying the patron population without identifying
        individuals, such as an IP gate.

        If some of a library's authentication mechanisms identify individuals,
        and others do not, the library does not identify individuals.
        """
        if not self.supports_patron_authentication:
            return False
        matches = list(self.providers)
        return len(matches) > 0 and all([x.identifies_individuals for x in matches])

    @property
    def library(self) -> Library | None:
        if self.library_id is None:
            return None
        return Library.by_id(self._db, self.library_id)

    def assert_ready_for_token_signing(self):
        """If this LibraryAuthenticator has SAML providers, ensure that it
        also has a secret it can use to sign bearer tokens.
        """
        if self.saml_providers_by_name and not self.bearer_token_signing_secret:
            raise CannotLoadConfiguration(
                _(
                    "SAML providers are configured, but secret for signing bearer tokens is not."
                )
            )

    def register_provider(
        self,
        integration: IntegrationLibraryConfiguration,
        analytics: Analytics | None = None,
    ):
        """Turn an ExternalIntegration object into an AuthenticationProvider
        object, and register it.

        :param integration: An ExternalIntegration that configures
            a way of authenticating patrons.
        """
        if integration.parent.goal != Goals.PATRON_AUTH_GOAL:
            raise CannotLoadConfiguration(
                f"Was asked to register an integration with goal={integration.parent.goal} as though it were a "
                "way of authenticating patrons."
            )

        if self.library_id != integration.library_id:
            raise CannotLoadConfiguration(
                f"Was asked to register an integration with library {self.library_short_name}, which doesn't use it."
            )

        impl_cls = (
            self.integration_registry.get(integration.parent.protocol)
            if integration.parent.protocol
            else None
        )
        if not impl_cls:
            raise CannotLoadConfiguration(
                f"Unable to load implementation for external integration: {integration.parent.protocol}."
            )
        if not issubclass(impl_cls, AuthenticationProvider):
            raise CannotLoadConfiguration(
                f"Implementation class {impl_cls} is not an AuthenticationProvider."
            )
        try:
            settings = impl_cls.settings_load(integration.parent)
            library_settings = impl_cls.library_settings_load(integration)
            provider = impl_cls(
                self.library_id,  # type: ignore[arg-type]
                integration.parent_id,  # type: ignore[arg-type]
                settings,
                library_settings,
                analytics,
            )
        except (RemoteIntegrationException, ProblemError):
            raise CannotLoadConfiguration(
                f"Could not instantiate {impl_cls.__name__} authentication provider for "
                f"library {self.library_short_name}."
            )

        if isinstance(provider, BasicAuthenticationProvider):
            self.register_basic_auth_provider(provider)
            # TODO: Run a self-test, or at least check that we have
            # the ability to run one.
        elif isinstance(provider, BaseSAMLAuthenticationProvider):
            self.register_saml_provider(provider)
        elif isinstance(provider, EkirjastoAuthenticationAPI):  # Finland
            self.register_ekirjasto_provider(provider)
        else:
            raise CannotLoadConfiguration(
                f"Authentication provider {impl_cls.__name__} is neither a BasicAuthenticationProvider nor a "
                "BaseSAMLAuthenticationProvider. I can create it, but not sure where to put it."
            )

    def register_basic_auth_provider(
        self,
        provider: BasicAuthenticationProvider,
    ):
        if (
            self.basic_auth_provider is not None
            and self.basic_auth_provider != provider
        ):
            raise CannotLoadConfiguration("Two basic auth providers configured")
        self.basic_auth_provider = provider
        # TODO: We can remove the configuration test once
        #  basic token authentication is fully deployed.
        if self.library is not None and Configuration.basic_token_auth_is_enabled():
            self.access_token_authentication_provider = (
                BasicTokenAuthenticationProvider(
                    self._db, self.library, self.basic_auth_provider
                )
            )

    def register_saml_provider(
        self,
        provider: BaseSAMLAuthenticationProvider,
    ):
        already_registered = self.saml_providers_by_name.get(provider.label())
        if already_registered and already_registered != provider:
            raise CannotLoadConfiguration(
                'Two different SAML providers claim the name "%s"' % (provider.label())
            )
        self.saml_providers_by_name[provider.label()] = provider

    # Finland
    def register_ekirjasto_provider(
        self,
        provider: EkirjastoAuthenticationAPI,
    ):
        if self.ekirjasto_provider is not None and self.ekirjasto_provider != provider:
            raise CannotLoadConfiguration("Two ekirjasto auth providers configured")
        self.ekirjasto_provider = provider

    # Finland
    def get_ekirjasto_provider(self) -> EkirjastoAuthenticationAPI | None:
        return self.ekirjasto_provider

    @property
    def providers(self) -> Iterable[AuthenticationProvider]:
        """An iterator over all registered AuthenticationProviders."""
        if self.access_token_authentication_provider:
            yield self.access_token_authentication_provider
        if self.basic_auth_provider:
            yield self.basic_auth_provider
        if self.ekirjasto_provider:  # Finland
            yield self.ekirjasto_provider
        yield from self.saml_providers_by_name.values()

    def _unique_basic_lookup_providers(
        self, auth_providers: Iterable[AuthenticationProvider | None]
    ) -> Iterable[AuthenticationProvider]:
        providers: filter[AuthenticationProvider] = filter(
            None,
            (p.patron_lookup_provider for p in auth_providers if p is not None),
        )
        # De-dupe, but preserve provider order.
        return dict.fromkeys(list(providers)).keys()

    @property
    def unique_patron_lookup_providers(self) -> Iterable[AuthenticationProvider]:
        """Iterator over unique patron data providers for registered AuthenticationProviders.

        We want a unique list of providers in order to avoid hitting the same
        provider multiple times, most likely in the case of failing lookups.
        """
        yield from self._unique_basic_lookup_providers(
            [
                self.access_token_authentication_provider,
                self.basic_auth_provider,
            ]
        )
        yield from self.saml_providers_by_name.values()

    def authenticated_patron(
        self, _db: Session, auth: Authorization
    ) -> Patron | ProblemDetail | None:
        """Go from an Authorization header value to a Patron object.

        :param auth: A werkzeug.Authorization object

        :return: A Patron, if one can be authenticated. None, if the
            credentials do not authenticate any particular patron. A
            ProblemDetail if an error occurs.
        """
        provider: AuthenticationProvider | None = None
        provider_token: dict[str, str | None] | str | None = None
        if self.basic_auth_provider and auth.type.lower() == "basic":
            # The patron wants to authenticate with the
            # BasicAuthenticationProvider.
            provider = self.basic_auth_provider
            provider_token = auth.parameters
        elif self.ekirjasto_provider and auth.type.lower() == "bearer":  # Finland
            # The patron wants to authenticate with the
            # EkirjastoAuthenticationAPI.
            if auth.token is None:
                return INVALID_EKIRJASTO_DELEGATE_TOKEN
            provider = self.ekirjasto_provider
            # Get decoded payload from the delegate token.
            validate_result = provider.validate_ekirjasto_delegate_token(auth.token)
            if isinstance(validate_result, ProblemDetail):
                return validate_result
            provider_token = validate_result
        elif auth.type.lower() == "bearer":
            # The patron wants to use an
            # SAMLAuthenticationProvider. Figure out which one.
            if auth.token is None:
                return INVALID_SAML_BEARER_TOKEN

            if (
                self.access_token_authentication_provider
                and AccessTokenProvider.is_access_token(auth.token)
            ):
                provider = self.access_token_authentication_provider
                provider_token = auth.token
            elif self.saml_providers_by_name:
                # The patron wants to use an
                # SAMLAuthenticationProvider. Figure out which one.
                try:
                    provider_name, provider_token = self.decode_bearer_token(auth.token)
                except jwt.exceptions.InvalidTokenError as e:
                    return INVALID_SAML_BEARER_TOKEN
                saml_provider = self.saml_provider_lookup(provider_name)
                if isinstance(saml_provider, ProblemDetail):
                    # There was a problem turning the provider name into
                    # a registered SAMLAuthenticationProvider.
                    return saml_provider
                provider = saml_provider

        if provider and provider_token:
            # Turn the token/header into a patron
            return provider.authenticated_patron(_db, provider_token)

        # We were unable to determine what was going on with the
        # Authenticate header.
        return UNSUPPORTED_AUTHENTICATION_MECHANISM

    def get_credential_from_header(self, auth: Authorization) -> str | None:
        """Extract a password credential from a WWW-Authenticate header
        (or equivalent).

        This is used to pass on a patron's credential to a content provider,
        such as Overdrive, which performs independent validation of
        a patron's credentials.

        :return: The patron's password, or None if not available.
        """
        credential = None
        for provider in self.providers:
            credential = provider.get_credential_from_header(auth)
            if credential is not None:
                break

        return credential

    def saml_provider_lookup(
        self, provider_name: str | None
    ) -> BaseSAMLAuthenticationProvider | ProblemDetail:
        """Look up the SAMLAuthenticationProvider with the given name. If that
        doesn't work, return an appropriate ProblemDetail.
        """
        if not self.saml_providers_by_name:
            # We don't support OAuth at all.
            return UNKNOWN_SAML_PROVIDER.detailed(
                _("No SAML providers are configured.")
            )

        if not provider_name or not provider_name in self.saml_providers_by_name:
            # The patron neglected to specify a provider, or specified
            # one we don't support.
            possibilities = ", ".join(list(self.saml_providers_by_name.keys()))
            return UNKNOWN_SAML_PROVIDER.detailed(
                UNKNOWN_SAML_PROVIDER.detail
                + _(" The known providers are: %s") % possibilities
            )
        return self.saml_providers_by_name[provider_name]

    def create_bearer_token(
        self, provider_name: str | None, provider_token: str | None
    ) -> str:
        """Create a JSON web token with the given provider name and access
        token.

        The patron will use this as a bearer token in lieu of the
        token we got from their OAuth provider. The big advantage of
        this token is that it tells us _which_ OAuth provider the
        patron authenticated against.

        When the patron uses the bearer token in the Authenticate header,
        it will be decoded with `decode_bearer_token_from_header`.
        """
        payload = dict(
            token=provider_token,
            # I'm not sure this is the correct way to use an
            # Issuer claim (https://tools.ietf.org/html/rfc7519#section-4.1.1).
            # Maybe we should use something custom instead.
            iss=provider_name,
        )
        return jwt.encode(
            payload, cast(str, self.bearer_token_signing_secret), algorithm="HS256"
        )

    def decode_bearer_token(self, token: str) -> tuple[str, str]:
        """Extract auth provider name and access token from JSON web token."""
        decoded = jwt.decode(
            token, cast(str, self.bearer_token_signing_secret), algorithms=["HS256"]
        )
        provider_name = decoded["iss"]
        token = decoded["token"]
        return (provider_name, token)

    def authentication_document_url(self) -> str:
        """Return the URL of the authentication document for the
        given library.
        """
        return url_for(
            "authentication_document",
            library_short_name=self.library_short_name,
            _external=True,
        )

    def create_authentication_document(self) -> str:
        """Create the Authentication For OPDS document to be used when
        a request comes in with no authentication.
        """
        links: list[dict[str, str | None]] = []
        if self.library is None:
            raise ValueError("No library specified!")

        # Add the same links that we would show in an OPDS feed.
        if self.library.settings.terms_of_service:
            links.append(
                dict(
                    rel="terms-of-service",
                    href=self.library.settings.terms_of_service,
                    type="text/html",
                )
            )

        if self.library.settings.privacy_policy:
            links.append(
                dict(
                    rel="privacy-policy",
                    href=self.library.settings.privacy_policy,
                    type="text/html",
                )
            )

        if self.library.settings.copyright:
            links.append(
                dict(
                    rel="copyright",
                    href=self.library.settings.copyright,
                    type="text/html",
                )
            )

        if self.library.settings.about:
            links.append(
                dict(
                    rel="about",
                    href=self.library.settings.about,
                    type="text/html",
                )
            )

        if self.library.settings.license:
            links.append(
                dict(
                    rel="license",
                    href=self.library.settings.license,
                    type="text/html",
                )
            )

        # Plus some extra like 'registration' that are specific to Authentication For OPDS.
        if self.library.settings.registration_url:
            links.append(
                dict(
                    rel="register",
                    href=self.library.settings.registration_url,
                    type="text/html",
                )
            )

        if self.library.settings.patron_password_reset:
            links.append(
                dict(
                    rel="http://librarysimplified.org/terms/rel/patron-password-reset",
                    href=self.library.settings.patron_password_reset,
                    type="text/html",
                )
            )

        # Add a rel="start" link pointing to the root OPDS feed.
        index_url = url_for(
            "index", _external=True, library_short_name=self.library_short_name
        )
        loans_url = url_for(
            "active_loans", _external=True, library_short_name=self.library_short_name
        )
        selected_books_url = url_for(
            "selected_books", _external=True, library_short_name=self.library_short_name
        )
        profile_url = url_for(
            "patron_profile", _external=True, library_short_name=self.library_short_name
        )

        links.append(
            dict(rel="start", href=index_url, type=OPDSFeed.ACQUISITION_FEED_TYPE)
        )
        links.append(
            dict(
                rel="http://opds-spec.org/shelf",
                href=loans_url,
                type=OPDSFeed.ACQUISITION_FEED_TYPE,
            )
        )
        links.append(
            dict(
                rel="http://opds-spec.org/shelf/selected_books",
                href=selected_books_url,
                type=OPDSFeed.ACQUISITION_FEED_TYPE,
            )
        )
        links.append(
            dict(
                rel=ProfileController.LINK_RELATION,
                href=profile_url,
                type=ProfileController.MEDIA_TYPE,
            )
        )

        # If there is a Designated Agent email address, add it as a
        # link.
        designated_agent_uri = Configuration.copyright_designated_agent_uri(
            self.library
        )
        if designated_agent_uri:
            links.append(
                dict(
                    rel="http://librarysimplified.org/rel/designated-agent/copyright",
                    href=designated_agent_uri,
                )
            )

        # Add a rel="help" link for every type of URL scheme that
        # leads to library-specific help.
        for type, uri in Configuration.help_uris(self.library):
            links.append(dict(rel="help", href=uri, type=type))

        # Add a link to the web page of the library itself.
        library_uri = self.library.settings.website
        if library_uri:
            links.append(dict(rel="alternate", type="text/html", href=library_uri))

        # Add the library's logo, if it has one.
        if self.library and self.library.logo:
            links.append(
                dict(rel="logo", type="image/png", href=self.library.logo.data_url)
            )

        # Add the library's custom CSS file, if it has one.
        css_file = self.library.settings.web_css_file
        if css_file:
            links.append(dict(rel="stylesheet", type="text/css", href=css_file))

        library_name = self.library_name or str(_("Library"))
        auth_doc_url = self.authentication_document_url()
        doc = AuthenticationForOPDSDocument(
            id=auth_doc_url,
            title=library_name,
            authentication_flows=list(self.providers),
            links=links,
        ).to_dict(self._db)

        # Add the library's mobile color scheme, if it has one.
        color_scheme = self.library.settings.color_scheme
        if color_scheme:
            doc["color_scheme"] = color_scheme

        # Add the library's web colors, if it has any.
        primary = self.library.settings.web_primary_color
        secondary = self.library.settings.web_secondary_color
        if primary or secondary:
            doc["web_color_scheme"] = dict(
                primary=primary,
                secondary=secondary,
                background=primary,
                foreground=secondary,
            )

        # Add the description of the library as the OPDS feed's
        # service_description.
        description = self.library.settings.library_description
        if description:
            doc["service_description"] = description

        # Add the library's public key.
        if self.library and self.library.public_key:
            doc["public_key"] = dict(type="RSA", value=self.library.public_key)
        else:
            error_library = (
                self.library.short_name
                if self.library
                else f'Library ID "{self.library_id}"'
            )
            self.log.error(
                f"{error_library} has no public key to include in auth document."
            )

        # Add feature flags to signal to clients what features they should
        # offer.
        enabled: list[str] = []
        disabled: list[str] = []
        if self.library and self.library.settings.allow_holds:
            bucket = enabled
        else:
            bucket = disabled
        bucket.append(Configuration.RESERVATIONS_FEATURE)
        doc["features"] = dict(enabled=enabled, disabled=disabled)

        # Add any active announcements for the library.
        if self.library:
            doc["announcements"] = Announcement.authentication_document_announcements(
                self.library
            )

        # Finally, give the active annotator a chance to modify the document.

        if self.authentication_document_annotator:
            doc = (
                self.authentication_document_annotator.annotate_authentication_document(
                    self.library, doc, url_for
                )
            )

        return json.dumps(doc)

    def create_authentication_headers(self) -> Headers:
        """Create the HTTP headers to return with the OPDS
        authentication document."""
        headers = Headers()
        headers.add("Content-Type", AuthenticationForOPDSDocument.MEDIA_TYPE)
        headers.add(
            "Link",
            "<%s>; rel=%s"
            % (
                self.authentication_document_url(),
                AuthenticationForOPDSDocument.LINK_RELATION,
            ),
        )
        # if requested from a web client, don't include WWW-Authenticate header,
        # which forces the default browser authentication prompt
        if (
            self.basic_auth_provider
            and not flask.request.headers.get("X-Requested-With") == "XMLHttpRequest"
        ):
            headers.add(
                "WWW-Authenticate",
                self.basic_auth_provider.authentication_header,
            )

        # TODO: We're leaving out headers for other providers to avoid breaking iOS
        # clients that don't support multiple auth headers. It's not clear what
        # the header for an oauth provider should look like. This means that there's
        # no auth header for app without a basic auth provider, but we don't have
        # any apps like that yet.

        return headers


class BearerTokenSigner:
    """Mixin class used for storing a secret used for signing Bearer tokens"""

    # Name of the site-wide ConfigurationSetting containing the secret
    # used to sign bearer tokens.
    BEARER_TOKEN_SIGNING_SECRET = Configuration.BEARER_TOKEN_SIGNING_SECRET

    @classmethod
    def bearer_token_signing_secret(cls, db):
        """Find or generate the site-wide bearer token signing secret.

        :param db: Database session
        :type db: sqlalchemy.orm.session.Session

        :return: ConfigurationSetting object containing the signing secret
        :rtype: ConfigurationSetting
        """
        return ConfigurationSetting.sitewide_secret(db, cls.BEARER_TOKEN_SIGNING_SECRET)


class BaseSAMLAuthenticationProvider(
    AuthenticationProvider[SettingsType, LibrarySettingsType], BearerTokenSigner, ABC
):
    """
    Base class for SAML authentication providers
    """

    @property
    def flow_type(self) -> str:
        return "http://librarysimplified.org/authtype/SAML-2.0"
