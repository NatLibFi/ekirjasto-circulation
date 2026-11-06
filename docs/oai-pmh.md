# Finna OAI-PMH harvesting

The library-scoped endpoint is `/<library_short_name>/oaipmh`. It accepts GET
query parameters and POST form parameters. It generates MARCXML from the
database and does not require S3 or the MARC file-generation job.

## Configuration

Apply database migrations before starting the updated application. The new
migration adds the repository initialization timestamp and a stored namespace
to each library.

In library settings, configure the OAI-PMH section:

- `oai_pmh_namespace`: a permanent domain-based namespace, such as
  `oai.example.org`. Record identifiers use this namespace followed by a
  typed identifier such as `isbn-9789146237396`.
- `oai_pmh_admin_contact`: a public contact address returned by `Identify`.
- `oai_pmh_page_size`: defaults to 500; accepts 100 through 5000.
- `oai_pmh_token_lifetime`: defaults to 86400 seconds (24 hours).

Configure the service's canonical base URL and the OAI-PMH MARC presentation
settings: organization code and catalogue URL. Summaries and genres are always
included in MARC records. The endpoint includes all collections supplied by
`library.collections`, including collections shared through the default library;
there is no collection-level inclusion setting. Publication eligibility filters
still apply.

The old `/marc` endpoint, S3 batch files, MARC exporter integration, and cache
cron job are removed. The retirement migration copies existing exporter
settings into each library's OAI settings, removes active exporter
integrations, and drops the obsolete `marcfiles` table. It does not delete S3
objects.

A subsequent migration removes the collection inclusion flag. Downgrading that
migration recreates the flag as false for all collections; previous values
cannot be restored.

Roll out the cutover in this order:

1. Deploy the migration and application.
2. Verify migrated library settings and collection associations.
3. Configure Finna to use `/<library_short_name>/oaipmh`.
4. Run a complete Finna harvest and reconcile identifiers.
5. Remove old S3 MARC files operationally and remove any old batch cron configuration.

The repository namespace is stored on first endpoint use. Changing it afterward
is rejected because that would change every harvested identifier. The public
contact address is separate from the library's private configuration contact.

## Requests

Supported examples, relative to the endpoint:

```text
?verb=Identify
?verb=ListMetadataFormats
?verb=ListIdentifiers&metadataPrefix=marc21
?verb=ListRecords&metadataPrefix=marc21
?verb=GetRecord&metadataPrefix=marc21&identifier=<URL-encoded OAI identifier>
?verb=ListRecords&resumptionToken=<URL-encoded token>
```

Only `marc21` is advertised. Each metadata element contains an independently
parseable MARCXML record. `ListSets` and set-filtered requests return
`noSetHierarchy`. Dublin Core is not implemented, using Finna's MARC-only
allowance rather than claiming full general OAI-PMH interoperability.

Both listing verbs and single-record requests use the same catalogue selection.
A record represents one work, using the identifier and presentation edition of
the pool selected by `Work.active_license_pool()`, as ordinary OPDS entries do.
Selection is scoped to the library's collections, ignores superseded pools,
prefers open access with a usable download, and also accepts unlimited access
or a titled edition with positive `licenses_owned`. Available-copy counts and
`allow_holds` do not affect harvesting: fully borrowed-out titles remain included
while licenses are owned. Patron-specific loan and hold overrides do not apply.
Works must be presentation-ready. A work is omitted if its selected pool is
suppressed, lacks delivery mechanisms, or cannot supply an identifier and titled
presentation edition for MARC generation.

This replaces the earlier publication-per-identifier selection. Run a fresh full
harvest with identifier reconciliation after deploying this change: multiple
publications can collapse into one record, and the exposed identifier can change
when the selected pool changes. Existing continuation tokens are rejected; no
additional database migration is needed for this selection change.

For continuation requests, send only the original verb and resumption token.
Tokens preserve the original filters, page size, expiry, and work-ID upper
bound. They are signed, library-specific, and verb-specific. No token is
returned on the final page. Invalid or expired tokens require restarting the
harvest. Protocol errors use HTTP 200 OAI-PMH XML responses; operational errors
use HTTP error statuses so the harvester can retry.

## Phase-one limitations

Configure Finna for full harvesting initially, with identifier reconciliation
to discover removals. `Identify` advertises `deletedRecord=no`: no deletion
tombstones are maintained in this release.

`from` and `until` accept inclusive UTC-second or date-only bounds. Datestamps
are provisional and derive from Work updates and pool refresh/discovery times,
with the persisted repository initialization time as a fallback. Availability
refreshes can advance them without changing MARC. Publication-level tracking of
every metadata, settings, and membership change is deferred, so these filters
are not yet a reliable basis for incremental harvesting.

Pagination uses live data with a work-ID cursor and an initial upper bound.
It does not hold a database snapshot between requests. Concurrent edits can
change page contents; if a continuation page disappears, restart the harvest.
Consistent revision snapshots and persistent deletion reporting belong to phase
two. Finna controls its own harvesting schedule; there is no new refresh job.
