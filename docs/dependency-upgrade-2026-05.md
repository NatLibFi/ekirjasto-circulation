# Dependency upgrade — May 2026

## Updates that required code changes

### opensearch-py `1.1` → `3.2` + opensearch-dsl removed entirely

**opensearch-dsl removed.** In opensearch-py 3.x the DSL query classes were absorbed directly
into `opensearchpy.helpers.query`, `opensearchpy.helpers.function`, and
`opensearchpy.helpers.response`. The standalone `opensearch-dsl` package is no longer needed
and was confirmed removed by upstream (ThePalaceProject/circulation). It has been dropped from
`pyproject.toml` and all import paths migrated.

**`core/external_search.py`** — all DSL imports replaced:

```python
# before
from opensearch_dsl import SF
from opensearch_dsl.query import Bool, DisMax, Exists, FunctionScore, Match, MatchAll, ...
from opensearch_dsl.query import Query as BaseQuery
from opensearch_dsl.query import Range, Regexp, Term, Terms
# after
from opensearchpy import SF
from opensearchpy.helpers.query import Bool, DisMax, Exists, FunctionScore, Match, MatchAll, ...
from opensearchpy.helpers.query import Query as BaseQuery
from opensearchpy.helpers.query import Range, Regexp, Term, Terms
```

**`core/search/service.py`** — `Search`/`MultiSearch` moved to main `opensearchpy` package;
`opensearchpy.helpers` re-imported explicitly for the `helpers.bulk()` call:

```python
# before
import opensearchpy.helpers
from opensearch_dsl import MultiSearch, Search
from opensearchpy import NotFoundError, OpenSearch, RequestError
# after
import opensearchpy.helpers
from opensearchpy import MultiSearch, NotFoundError, OpenSearch, RequestError, Search
```

**`api/opensearch_analytics_provider.py`**, **`api/opensearch_analytics_search.py`** — merged
into single `from opensearchpy import OpenSearch, Search`.

**`tests/mocks/search.py`**, **`tests/api/controller/test_crawlfeed.py`** — `Hit` path changed:

```python
# before
from opensearch_dsl import Hit   # (transitively via opensearch-dsl)
# after
from opensearchpy.helpers.response import Hit
```

**`tests/core/test_external_search.py`** — all query/function class imports replaced:

```python
# before
from opensearch_dsl.query import Bool, DisMax, Match, MatchAll, ...
from opensearch_dsl.query import Query as opensearch_dsl_query
from opensearch_dsl.function import RandomScore, ScriptScore
# after
from opensearchpy.helpers.query import Bool, DisMax, Match, MatchAll, ...
from opensearchpy.helpers.query import Query as opensearch_dsl_query
from opensearchpy.helpers.function import RandomScore, ScriptScore
```

**Compatibility:** opensearch-py 3.x is compatible with OpenSearch 1.3.x servers. Confirmed
by running the full CI test suite against the `opensearchproject/opensearch:1` (1.3.x) Docker
image used in tox-docker — all 1639 core tests and 1703 API tests pass.

---

**opensearch-py 3.x keyword-only API changes.** The 3.x client also made all index-management
API parameters keyword-only. Previously positional arguments were accepted; now they are not.

**`api/opensearch_analytics_provider.py`**

```python
# before
self.indices.exists(index_name)
self.indices.get(index_name)
# after
self.indices.exists(index=index_name)
self.indices.get(index=index_name)
```

**`core/search/service.py`**

```python
# before
self._client.put_script(name, script)        # positional
self._client.delete(index=p, id=id, doc_type="_doc")  # doc_type removed in 3.x
# after
self._client.put_script(id=name, body=script)
self._client.delete(index=p, id=id)
```

**Tests** (`tests/core/search/test_migration_states.py`, `test_service.py`, `tests/fixtures/search.py`):
same positional→keyword fix throughout (`indices.get`, `indices.delete`, `indices.exists`,
`indices.exists_alias`).

---

### lxml `4.x` → `6.x`

lxml 6 is stricter about XML 1.0 character references. Feed data from vendors sometimes
contains numeric character references for codepoints that are illegal in XML 1.0 (e.g.
`&#x8;` for backspace). Previously lxml silently ignored them; now it rejects them.

**`core/util/xmlparser.py`** — added `_strip_invalid_xml_character_references` static method:
pre-processes the raw bytes before parsing, removing any `&#x...;` / `&#...;` references
whose codepoints fall outside the XML 1.0 legal range
`(0x09, 0x0A, 0x0D, 0x20–0xD7FF, 0xE000–0xFFFD, 0x10000–0x10FFFF)`.
Valid references are preserved unchanged.

---

### html-sanitizer `2.1` → `2.6`

The public `Sanitizer` class moved to a sub-module.

**`api/discovery/opds_registration.py`**

```python
# before
from html_sanitizer import Sanitizer
# after
from html_sanitizer.sanitizer import Sanitizer
```

---

### pytest `7.4` / test fixes

Two pre-existing test bugs surfaced under stricter assert handling:

**`tests/api/controller/test_base.py`**

- `work.age_appropriate_for_patron.called_with(...)` → `.assert_called_with(...)`:
  the old form is always `True` on a Mock, making the assertion a no-op.
- `LicensePoolDeliveryMechanism` creation now supplies an explicit `Resource` to satisfy
  an integrity constraint that became enforced; added missing `assert isinstance` checks
  to make test intent explicit.

---

### pydantic `2.11.7` → pinned to `~2.11.7` (2.11.x only)

Upgrading to 2.12+ broke all admin settings forms. In pydantic 2.12, custom `FieldInfo`
subclasses are no longer preserved in `model_fields` — they get coerced to plain
`pydantic.fields.FieldInfo`. The codebase uses a custom `FormFieldInfo(FieldInfo)` subclass
(see [`core/integration/settings.py`](../core/integration/settings.py)) throughout the admin
integration settings system to carry form metadata. Every admin controller that processes
form submissions (`CatalogServices`, `Collections`, `LibrarySettings`, `PatronAuth`, etc.)
asserts `isinstance(field_info, FormFieldInfo)` and breaks immediately.

**Fix needed:** either override `__get_pydantic_core_schema__` on `FormFieldInfo` to
re-register it properly in 2.12+, or switch to `Annotated[T, FormMetadata(...)]` metadata
instead of a `FieldInfo` subclass.

---

### pymarc `5.1.1` → reverted (5.3.x broke tests)

pymarc 5.3 changed `field.indicators` from a plain `list` (`[' ', ' ']`) to a typed
`Indicators` namedtuple. All 12 tests in
[`tests/core/test_marc.py`](../tests/core/test_marc.py) that compare indicators against
list literals fail.

**Fix needed:** update `_check_field` helper and any other indicator comparisons to use
`list(field.indicators)` or compare against the namedtuple directly.

---

---

## Additional safe upgrades (2026-05-27)

Constraint-only changes in `pyproject.toml` to allow newer minor/patch releases,
plus transitive bumps resolved by `poetry update`:

| Package | Old constraint | New constraint | Resolved to | Notes |
| --- | --- | --- | --- | --- |
| `levenshtein` | `^0.24` | `^0.27` | 0.27.3 | Edit-distance lib; backwards-compatible API |
| `pycountry` | `^24.6.1` | `^26.0` | 26.2.16 | Country/language data; stable API |
| `pyspellchecker` | `0.8.1` (exact) | `^0.9` | 0.9.0 | Minor API bump; internal use only |
| `pytz` | `^2023.3` | `>=2023.3` | 2026.2 | Timezone data; `^` was overly conservative |

Transitive updates pulled in by the above (no constraint change needed):

| Package | Before | After | Notes |
| --- | --- | --- | --- |
| `cryptography` | 46.0.7 | 48.0.0 | Security update; compatible with `pyOpenSSL ^26` |
| `idna` | 3.15 | 3.16 | Minor |
| `google-auth` | 2.52.0 | 2.53.0 | Minor |
| `boto3` / `botocore` | 1.43.14 | 1.43.15 | Patch |
| `isodate` | 0.6.1 | 0.7.2 | Minor |
| `rapidfuzz` | 3.4.0 | 3.14.5 | Transitive of `levenshtein` |

---

## Not upgraded: would require significant work

| Package | Pinned | Available | Reason |
| --- | --- | --- | --- |
| `sqlalchemy` | `^1.4` | 2.0.50 | Complete ORM rewrite: `Query` API removed. All 34 model files affected. |
| `mypy` | `^1.4` | 2.1.0 | Major version; likely surfaces new type errors. Best alongside a mypy-strict cleanup. |
| `pytest` | `>=7.2` | 9.0.3 | `pytest-cov`, `pytest-alembic`, `tox-docker` compatibility needs verification. |
| `pytest-cov` | `^4.0` | 7.1.0 | Follows pytest — upgrade together. |
| `firebase-admin` | `^6.0` | 7.4.0 | FCM API changes; may affect `core/jobs/notifications/` push jobs. |
| `bcrypt` | `^4.0` | 5.0.0 | Password hashing API may have changed. |
| `protobuf` | `5.29.6` (pinned) | 7.35.0 | Two major versions; affects protobuf code and firebase-admin/grpc deps. |
| `pyld` | `2.0.3` (pinned) | 3.0.0 | JSON-LD library used in OPDS/LDP processing. |
| `pyfakefs` | `^5.3` | 6.2.0 | Major version; filesystem mocking changes may break test fixtures. |
| `google-cloud-storage` | `^2.9` (transitive) | 3.10.1 | Upgrade blocked by firebase-admin 6.x. |
| `pydantic` | `~2.11.7` | 2.13.4 | See FormFieldInfo section above. |
| `pymarc` | `5.1.1` (pinned) | 5.3.1 | See indicators section above. |
