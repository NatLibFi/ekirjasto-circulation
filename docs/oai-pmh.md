# Finna OAI-PMH harvesting

The service provides a library-scoped, MARC-only OAI-PMH 2.0 repository at
`/<library_short_name>/oaipmh`. Requests may use either GET query parameters or
POST form parameters. Records are generated from the database when requested.

The legacy `/marc` endpoint and the batch MARC exporter are still present. They
are separate from OAI-PMH and are not replaced or removed by this endpoint.

## Configuration

Apply the OAI-PMH migration before starting the updated application. It adds a
repository namespace and initialization timestamp to each library. It also
removes the obsolete `include_summary` and `include_genres` settings from
legacy `MARCExporter` library configuration; those fields are always generated
by the OAI-PMH MARC builder.

OAI-PMH settings are library settings, visible to system administrators:

- `oai_pmh_namespace`: optional permanent domain-based repository namespace,
  for example `oai.example.org`. The endpoint remains unavailable until this
  is configured. Identifiers have the form `oai:<namespace>:<type>-<value>`,
  such as `oai:oai.example.org:isbn-9789146237396`.
- `oai_pmh_admin_contact`: public email address or URL returned by `Identify`.
  The default is the E-kirjasto feedback URL.
- `oai_pmh_page_size`: records per page, default 500, allowed range 100–5000.
- `oai_pmh_token_lifetime`: continuation-token lifetime in seconds, default
  86400 (24 hours).
- `oai_pmh_organization_code`: optional MARC organization code written to
  field 003. The default is `FI-Ekirj`.
- `oai_pmh_catalogue_url`: optional public catalogue URL used in MARC 856
  links. If unset, no catalogue URL is added to the generated MARC records.

The site-wide canonical base URL must also be configured as an HTTP or HTTPS
URL, and the library must have a UUID. The endpoint returns HTTP 503 until
these requirements are met. The URL reported by `Identify` is the canonical
URL followed by `/<library_short_name>/oaipmh`.

The repository namespace is stored on first endpoint use. It cannot be changed
afterward; changing it would change every harvested identifier. The endpoint
uses the library's local settings and does not inherit OAI-PMH settings from a
parent or default library.

## Requests

Examples relative to the endpoint:

```text
?verb=Identify
?verb=ListMetadataFormats
?verb=ListMetadataFormats&identifier=<URL-encoded-OAI-identifier>
?verb=ListIdentifiers&metadataPrefix=marc21
?verb=ListRecords&metadataPrefix=marc21
?verb=GetRecord&metadataPrefix=marc21&identifier=<URL-encoded-OAI-identifier>
?verb=ListRecords&resumptionToken=<URL-encoded-token>
```

The supported verbs are `Identify`, `ListMetadataFormats`, `ListIdentifiers`,
`ListRecords`, `GetRecord`, and `ListSets`. Only the `marc21` metadata prefix
is available. `ListSets` and requests containing a `set` parameter return
`noSetHierarchy`; the repository has no set hierarchy. Dublin Core is not
implemented.

`ListIdentifiers` returns OAI headers. `ListRecords` returns the same headers
with one independently parseable MARCXML record per record. `GetRecord` returns
one currently harvestable record. `ListMetadataFormats` with an identifier
also verifies that the identifier currently resolves to a harvestable record.

Protocol errors are returned as HTTP 200 OAI-PMH XML responses. Incomplete
configuration is an HTTP 503 response. Other operational HTTP errors are left
available for clients to retry. Successful responses are marked `no-store`.

## Catalogue selection

The catalogue contains presentation-ready works from the collections exposed
by `library.collections`. For non-default libraries this includes collections
configured for the library and collections configured for the default library.
The collection-level `export_marc_records` flag is not used by OAI-PMH.

For each work, OAI-PMH exposes the same active pool that the OPDS selection
rules would choose within those collections:

1. a non-superseded open-access pool with a usable download;
2. otherwise a non-superseded unlimited-access pool; or
3. otherwise a non-superseded pool with a titled presentation edition and a
   positive `licenses_owned` value.

The selected pool must not be suppressed, must have a delivery mechanism, and
must have both a titled presentation edition and an identifier. A work that
fails any of these checks is omitted. Patron-specific loans, holds,
availability-copy counts, and `allow_holds` do not affect harvesting.

Each work produces one record, using the selected pool's identifier and
presentation edition. The identifier therefore represents the selected work,
not every publication or license pool. If the selected pool changes, the
identifier or record may change. Run a complete harvest with identifier
reconciliation after deploying selection changes.

The generated MARC record includes summaries and genres when present. The
configured organization code is written to field 003, and the configured
catalogue URL is used for MARC 856 links. MARC control fields use the
catalogue's modification and entry timestamps.

## Dates and continuation tokens

`from` and `until` are inclusive and accept either UTC second precision
(`YYYY-MM-DDThh:mm:ssZ`) or date-only precision (`YYYY-MM-DD`). Both bounds
must use the same precision. A date-only `until` includes the whole day.

Datestamps are derived from the latest available work update, pool check, or
pool availability timestamp, truncated to whole seconds. The library's
persisted OAI-PMH initialization timestamp is used as a fallback. Because
these are not publication-level change records, date filtering should not yet
be treated as a complete incremental-harvest mechanism.

`Identify` advertises `deletedRecord=no`; deletion tombstones are not
maintained. Initially configure Finna for complete harvesting and identifier
reconciliation.

Listing is ordered by work ID. A new harvest records an upper work-ID bound;
continuation requests use a signed, library-specific, verb-specific token that
also preserves the date filters, page size, expiry, and cursor. Tokens expire
after `oai_pmh_token_lifetime`, and their expiry cannot be extended by paging.
Continuation pages read live data rather than a database snapshot, so concurrent
changes can alter page contents. If a continuation page no longer exists, the
token is rejected and the harvest must be restarted. No token is returned on
the final page.

### Resumption-token signing

OAI-PMH requires resumption tokens to be opaque to clients, but it does not
require them to be cryptographically signed. An implementation may use a
database key, encoded state, a signed token, or an encrypted token. This
service uses a signed `itsdangerous.URLSafeTimedSerializer` token so clients
cannot alter the library, repository namespace, verb, cursor, date filters,
page size, upper work-ID bound, or expiry without invalidating the token.

Signing provides integrity and authenticity; it does not encrypt the token's
contents. Clients must treat tokens as opaque and must not attempt to decode or
modify them.

The signing key is the Flask application `secret_key`, loaded from the
site-wide `secret_key` configuration setting. This is an application-level
implementation choice, not an OAI-PMH-specific requirement. The same key also
signs Flask application cookies, so it must not be exposed. If the application
has no signing secret, the endpoint cannot issue or validate continuation tokens.
