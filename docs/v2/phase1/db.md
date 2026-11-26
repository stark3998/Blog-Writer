# Phase 1: storage layer (`server/src/blogwriter/db`)

Plan sections: "Data", "Reliability model". Status: done. Verified offline and against the real v2
account (`cosmos-blogwriter-v2-7c3167`, database `blog-writer`).

## Files

| File | Contents |
|---|---|
| `db/base.py` | `Doc`, the errors, `CONTAINERS` (name -> partition key path, mirrors `infra/v2/data/main.tf`), `UNINDEXED_FIELDS`, the filter DSL (`Filter`, `OrderBy`), the `Store` protocol, and the shared write validation both stores use |
| `db/query.py` | `find_spec` (argument validation), `compile_query` (parameterized Cosmos SQL), `apply` (the same semantics in Python, for `MemoryStore`) |
| `db/memory.py` | `MemoryStore` |
| `db/cosmos.py` | `CosmosStore` over `azure.cosmos.aio`, Entra ID only; error mapping; `ensure_containers()`; `close()` |
| `db/settings.py` | `CosmosSettings`: `BW_COSMOS_ENDPOINT` (unset by default), `BW_COSMOS_DATABASE` (default `blog-writer`) |
| `db/lifecycle.py` | `startup(app)`, `shutdown(app)`, `get_store` (FastAPI dependency), per ARCHITECTURE rule 7 |
| `tests/db/` | the contract suite (both stores), DSL goldens, CosmosStore unit tests with fakes, settings, lifecycle, the live-suite guard |

## API (everything is exported from `blogwriter.db`)

```python
class Store(Protocol):
    async def read(self, container, id, *, partition_key=None) -> Doc
    async def create(self, container, body) -> Doc                   # Conflict if the id exists
    async def replace(self, container, body, *, if_match=None) -> Doc  # NotFound / PreconditionFailed
    async def upsert(self, container, body) -> Doc
    async def delete(self, container, id, *, partition_key=None, if_match=None) -> None
    async def find(self, container, filters=(), *, order_by=None, limit=None,
                   partition_key=None) -> list[Doc]
    async def ensure_containers(self) -> None
    async def close(self) -> None

@dataclass(frozen=True)
class Doc: id: str; partition_key: str; body: dict[str, Any]; etag: str
```

- `body` is a fresh copy without Cosmos system properties (`_rid`, `_self`, `_etag`,
  `_attachments`, `_ts`, `_lsn`). The etag is `Doc.etag`. Every write returns a new etag.
- The partition key value always comes from the body (at the container's path). `partition_key=`
  is only needed for point reads and deletes in containers not keyed by `/id`
  (`article-versions` -> `articleId`, `signals` -> `sourceId`, `usage` -> `month`).
- CAS for claims (plan: "Reliability model"):
  `doc = await store.read("jobs", id)`, then
  `await store.replace("jobs", {**doc.body, "leaseUntil": t, "fence": f + 1}, if_match=doc.etag)`.
  `PreconditionFailed` (412) means someone else wrote first, so the claim is lost.
- `CosmosStore.open(endpoint, *, database="blog-writer", credential=None)` or
  `CosmosStore.from_settings(settings=None, *, credential=None)`. Without a credential it creates
  a `DefaultAzureCredential` (on ACA: the managed identity in `AZURE_CLIENT_ID`) and closes it
  in `close()`. An injected credential stays the caller's to close.
- Wiring: `await db.startup(app)` keeps an injected `app.state.store` (tests put a `MemoryStore`
  there) or opens a `CosmosStore` from `BW_COSMOS_*`, then runs `ensure_containers()`. It fails
  closed: no endpoint, auth failure or a missing container stops startup. There is no in-memory
  fallback. `await db.shutdown(app)` closes `app.state.store`. Routes take
  `store: Annotated[Store, Depends(db.get_store)]`.

### Errors

| Error | When |
|---|---|
| `NotFound` (404) | read, replace or delete of a missing document. Also replace/delete with `if_match` on a missing document: Cosmos answers 404 there, not 412 (verified live) |
| `Conflict` (409) | create of an existing id (ids are unique per partition) |
| `PreconditionFailed` (412) | `if_match` etag differs |
| `StoreUnavailable` | 408/429/449/5xx and network or timeout failures, after the SDK's own retries. The write may or may not have happened: re-read before retrying |
| `InvalidRequest` (also a `ValueError`) | a caller bug, raised before any I/O and identically by both stores |
| `ContainerCheckFailed` | `ensure_containers()`: lists missing containers and partition-key mismatches |
| `StoreError` (base) | anything else (400, 401, 403, 413, auth failure, a 404 on create/upsert/find meaning the container or database is missing). `.status` has the HTTP status |

Messages carry the operation, container, id and status only: never document content, and never
the SDK's own message text.

### Document rules (both stores, before any I/O)

- `id`: a non-empty string of at most 255 characters. No `/ \ ? #`, control characters, or
  leading/trailing whitespace.
- Partition key value: a non-empty string of at most 1024 characters.
- The body is JSON only (dict with string keys, list/tuple, str, int, float, bool, None):
  - nested at most 64 levels;
  - integers within +/-(2^53 - 1), because Cosmos stores doubles and would silently round
    anything larger;
  - finite floats only;
  - at most 2,000,000 bytes as compact UTF-8 JSON.
- Enum and other str/int subclasses are stored as plain values, and tuples as lists.
- System properties may not be written.
- `ttl`, if present, is an int: -1 or at least 1. TTL is GC only; check `expiresAt` in code.

### Filter DSL

`Filter(field, op, value)`. `field` is a dotted path of ASCII identifiers (`lease.until`); system
properties are rejected. `op` is one of `== != < <= > >= in exists`. Filters are ANDed. Use
`OrderBy(field, descending=False)` for ordering. Semantics, the same in both stores and verified
live:

- **Missing fields** never match, except `exists False`. That includes `!=`.
- **`==` and `in`** only match a stored value of the same JSON type (`1` != `true` != `"1"`).
  `== None` matches an explicit null. `in []` matches nothing.
- **`!=`** matches every stored primitive that is not equal, including primitives of another type
  (`!= 1` matches `"1"` and `null`; `!= None` means "present and not null").
  - **Live finding:** Cosmos's docs say cross-type `!=` is undefined, but the service returns
    those documents.
  - The compiled SQL adds an `IS_PRIMITIVE` guard, so arrays and objects never match.
- **`< <= > >=`** take a number or a string and only match stored values of that type. The SQL
  carries an `IS_NUMBER` / `IS_STRING` guard. Strings compare ordinally, so keep range fields
  ASCII (ISO-8601 UTC).
- **`order_by`**:
  - It adds `IS_PRIMITIVE(field)`, so documents where the field is missing, an array or an
    object are not returned.
  - Mixed types sort null < false < true < numbers < strings (verified live).
  - Only one field: no composite indexes are declared.
  - Ties have no defined order. MemoryStore breaks them by (partition key, id).
- **Fields excluded from indexing** cannot be filtered or sorted on; Cosmos would need a scan.
  That is `UNINDEXED_FIELDS`, mirroring Terraform: `embedding`, `markdown`, `htmlContent`,
  `mdxContent` everywhere, plus `payload` in `posts` and `events` in `jobs`.
- **The SQL itself** uses bracket notation (`c["value"]` is safe) and binds every value,
  including `TOP @limit`, as a parameter.
- **Results**: `find` returns everything matching (no paging); use `limit`. Without `order_by`
  the order is unspecified (MemoryStore: by partition key, then id).

### MemoryStore

- Shares validation and the DSL with CosmosStore, so it accepts and rejects the same requests.
- Ignores TTL.
- `ensure_containers`/`close` are no-ops, and the store stays usable after `close`.
- Each operation yields to the loop once, then runs atomically. Concurrent read-then-replace
  tasks therefore race like remote calls, and exactly one CAS write wins.
- One event loop only.

## Tests and checks

- Offline: `uv run pytest tests/db -q` gives 406 passed and 1 skipped (the live-only check). No
  network: CosmosStore unit tests use fakes that implement the `CosmosContainer`/`CosmosDatabase`
  protocols, which pyright confirmed the real `ContainerProxy`/`DatabaseProxy` satisfy. `open()`
  is tested with `CosmosClient.__aenter__`/`close` patched.
- Drift checks parse `infra/v2/data/main.tf`: the partition keys and the excluded paths must
  match the registries.
- Live: `BW_LIVE_COSMOS=1 BW_COSMOS_ENDPOINT=https://cosmos-blogwriter-v2-7c3167.documents.azure.com:443/ uv run pytest tests/db -q`
  (needs `az login`). It runs the whole contract suite against Cosmos and adds a read-only
  `ensure_containers()` check.
  - **Safety:** a guard lets live writes touch only `test-` ids in `jobs` and `ephemeral` and
    fails the test before any other request. Every write is recorded before it is sent.
    Teardown, which also runs on failure, deletes the recorded ids and sweeps both containers
    for the test's `suite` marker. Test documents also carry `ttl: 3600`.
  - **Run 1** stopped at its first failure (`-x`): the cross-type `!=` finding above. The cleanup
    check afterwards found 0 leftovers.
  - **Fixes:** `!=` semantics plus the type guards; the unindexed-field rule was also added.
  - **Run 2:** 512 passed (106 of them against Cosmos) in 165 s. A read-only
    `STARTSWITH(c.id, "test-")` count afterwards: `jobs` 0, `ephemeral` 0.

## Notes for integration

- No new dependencies are needed.
- Core logging: the Azure SDK's `azure.core.pipeline.policies.http_logging_policy` logger logs
  request URLs at INFO. Set the `azure` loggers to WARNING in the logging setup.
- The app's managed identity needs Cosmos Built-in Data Contributor scoped to `/dbs/blog-writer`.
  This is in the plan's platform root, not done yet. Today only the owner has the role.
- The store passes SDK defaults for retries (throttling: 9 attempts / 30 s) and timeouts. If the
  job runner needs a hard bound per call (lease renewals), wrap calls in `asyncio.timeout`;
  `StoreUnavailable` covers SDK timeouts.
- Live-test documents never use job-like fields (`state`, `owner`, `leaseUntil`). That keeps a
  running app's runner from mistaking them for real jobs.
