# Phase 1: token vault, egress client, notifications

What exists in `server/src/blogwriter/core/crypto.py`, `server/src/blogwriter/core/egress.py` and
`server/src/blogwriter/notify/`, how to use and test them, and what web, infra and the API wiring
need to know. Binding rules: `server/ARCHITECTURE.md`. Plan sections: Security model (prompt
injection and egress; secrets), Planner/engagement (notifications), CI/CD and operations
(out-of-band alerts).

No new dependencies. Everything uses pinned packages: `cryptography`, `httpx`/`httpcore`,
`pywebpush`, `py-vapid` and `requests` (the last two come with `pywebpush`).

## Public API

| Module | Use it for |
|---|---|
| `core.crypto` | `TokenVault` (`encrypt`, `decrypt`, `needs_rotation`, `reencrypt`, `primary_kid`, `kids`, `from_settings`), `VaultSettings` (`BW_VAULT_`), `VaultError` (`.reason`), `key_id(token)`, `parse_key_ring(spec)`, `generate_key()` |
| `core.egress` | `SafeHttpClient.get(url, *, allow_http=False, headers=None, max_bytes=None) -> EgressResponse`; errors `EgressError` > `EgressBlockedError`, `EgressRedirectError`, `EgressTooLargeError`, `EgressTimeoutError`, `EgressTransportError`, `EgressResponseError`; `blocked_reason(ip)`; `Resolver`, `SystemResolver`, `PinnedTarget`, `pinned_transport`, `TransportFactory` |
| `notify` (package root, light) | `emit_alert`, `AlertKind`, `ALERT_KINDS`, `ALERT_EVENT`, `NotificationPayload`, `PushSubscription`, `SubscriptionRepo`, `MemorySubscriptionRepo`, `PUSH_SERVICE_HOSTS`, `NotifySettings`, `NotifyConfigError`, `load_vapid_private_key` |
| `notify.webpush` (imports `pywebpush`) | `WebPushNotifier` (`send`, `notify_all`, `application_server_key`, `from_settings`), `DeliveryReceipt`, `new_push_session`, `WebPushFunction` |

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `BW_VAULT_KEYS` | unset | `kid:base64key[,kid:base64key]`. Each key is 32 random bytes, base64 or base64url, padding optional. Kid: 1-32 of `[A-Za-z0-9_-]`. Generate a key: `uv run python -c "from blogwriter.core.crypto import generate_key; print(generate_key())"`. Key Vault secret (the plan's `APP_ENCRYPTION_KEY`). |
| `BW_VAULT_PRIMARY` | the only kid | Kid for new tokens. Required when the ring has more than one key. |
| `BW_NOTIFY_VAPID_PRIVATE_KEY` | unset | P-256 key: base64url raw 32 bytes (what `npx web-push generate-vapid-keys` prints as the private key), base64url DER, or PEM. Key Vault secret. |
| `BW_NOTIFY_VAPID_SUBJECT` | unset | `mailto:<address>` or `https://<host>` (no path or port). Set together with the key. |
| `BW_NOTIFY_PUSH_TTL_SECONDS` | `43200` (12 h) | 0 to 28 days. Per-call override on `send`/`notify_all`. |
| `BW_NOTIFY_PUSH_TIMEOUT_SECONDS` | `10` | Connect and read timeout per push, more than 0 and at most 60. |

Unset means the feature is off and fails closed: `TokenVault.from_settings()` raises
`VaultError("not_configured")` and `WebPushNotifier.from_settings()` raises `NotifyConfigError`.
Key material is parsed by those two calls, not by pydantic, because pydantic keeps raw input in
`ValidationError.errors()`. Call them at startup so a bad key stops the boot. Their messages
name an entry or kid, never key material.

`VaultSettings` is a second settings class inside `core`, with prefix `BW_VAULT_`, because I could
not edit `core/settings.py`. The integrator may fold `vault_keys`/`vault_primary` into
`CoreSettings` later; the env names stay the same.

## Token vault (`core.crypto`)

- Format: `v1.<kid>.<nonce_b64url>.<ct_b64url>`. The nonce is 12 random bytes. `ct` is the AES-256-GCM
  ciphertext plus its 16-byte tag. Encodings are unpadded and must be canonical.
- Associated data is `b"v1." + kid + b"." + context`, so the header and the caller's context are
  both bound. Use the Cosmos document id as `context`. A token copied into another document,
  relabelled to another kid (even one holding the same key) or edited in any way fails.
- `decrypt` raises `VaultError` with one of these reasons: `bad_format`, `unknown_kid`, or
  `decrypt_failed` (tampering, wrong context and wrong key look the same). Per the plan, the
  LinkedIn flow treats any `VaultError` as "reconnect".
- Rotation: add a new kid, make it primary and redeploy. Old tokens still decrypt.
  `needs_rotation(token)` flags them and `reencrypt(token, context=...)` moves them to the new
  key. Drop the old kid once nothing uses it.
- Calls are synchronous: microseconds for token-sized payloads.

```python
vault = TokenVault.from_settings()                       # at startup
doc["accessToken"] = vault.encrypt(token, context=doc["id"])
token = vault.decrypt(doc["accessToken"], context=doc["id"])
```

## Egress client (`core.egress`)

Every fetch of a URL we did not configure ourselves goes through `SafeHttpClient`: feeds,
owner-typed URLs, images and `fetch_page`. Build one per process and share it. It is stateless
and loads the CA bundle once.

For each hop (the first request and every redirect):

1. **URL policy.** `https` only unless the call passes `allow_http=True` (which applies to redirects
   too). No userinfo. Ports `{80, 443}` by default (`allowed_ports=` widens). The host must be
   a valid DNS name or a canonical IP literal. Numeric shorthands such as `127.1`, `2130706433`,
   `0x7f000001` or `017700000001`, a numeric last label, `localhost` and `*.localhost` names, and
   IPv6 zone ids are refused before DNS.
2. **DNS.** `SystemResolver` calls `loop.getaddrinfo` off the loop. It looks up the absolute name
   (trailing dot), so resolv.conf search domains such as `*.svc.cluster.local` never apply, and
   passes `AI_ADDRCONFIG`. If **any** address is blocked, the host is refused (`blocked_reason`):
   - `unspecified` (0.0.0.0/8, ::), `loopback`, `private` (RFC 1918)
   - `cgnat` (100.64.0.0/10)
   - `link_local` (169.254.0.0/16, which includes 169.254.169.254; fe80::/10)
   - `azure_platform` (168.63.129.16)
   - `unique_local` (fc00::/7), `site_local` (fec0::/10), `multicast`
   - `reserved`: TEST-NETs, benchmarking, 192.0.0.0/24, 240/4, broadcast, documentation and
     2001::/23. Any IPv6 address outside 2000::/3 also counts as reserved.
   - `ipv4_embedded`: 6to4, Teredo, NAT64 64:ff9b::/96 and /48, IPv4-compatible and
     IPv4-translated forms, whatever they embed.
   - `scoped`: any IPv6 address with a zone id.

   An IPv4-mapped address (`::ffff:a.b.c.d`) gets the verdict of its IPv4 address and is dialled
   as IPv4.
3. **Pinned connect.** Each hop gets its own `httpcore` pool. Its network backend dials only the
   vetted addresses, in order, falling back on connect errors, and refuses any other host or
   port (`pin_mismatch`). TLS SNI, certificate verification and `Host` still use the hostname.
   `httpx.AsyncHTTPTransport` 0.28 cannot take a network backend, so `_CoreTransport` mirrors its
   mapping using public `httpcore` APIs. No proxies, cookies or `.netrc`.
4. **Redirects.** The client follows 301, 302, 303, 307 and 308 itself, at most 5, and re-runs steps
   1-3 on every hop. Other 3xx statuses (300, 304, 305) and a 3xx without `Location` are returned
   as they are.
5. **Body.** The cap is 5 MiB, applied both on the wire and after decoding. `Content-Length` over the
   cap fails before reading. gzip and deflate are decoded here with a bounded zlib decoder, so a
   compression bomb stops at the cap. Other encodings are refused. One 15 s deadline covers DNS,
   every hop and the body.

Response: `EgressResponse(url, status, content_type, content, redirects, etag, last_modified)`
plus the `media_type` and `charset` properties. Any HTTP status is returned. Callers may set only
`Accept`, `Accept-Language`, `If-None-Match` and `If-Modified-Since`. `max_bytes=` per call can
only lower the cap.

Errors, all with `.reason` (the message is the reason, never the URL):

| Class | Reasons | Retry? |
|---|---|---|
| `EgressBlockedError` | `invalid_url`, `scheme_not_allowed`, `credentials_in_url`, `port_not_allowed`, `invalid_host`, `numeric_host`, `local_host_name`, `address_<category>`, `pin_mismatch` | never |
| `EgressRedirectError` | `too_many_redirects` | no |
| `EgressTooLargeError` | `too_large` | no |
| `EgressResponseError` | `unsupported_encoding`, `bad_encoding` | no |
| `EgressTimeoutError` | `timeout` | later |
| `EgressTransportError` | `dns_failed`, `connect_failed`, `protocol_error`, `transport_error` | later |

Logs: `egress_fetch` (status, redirects, size_bytes, duration_ms) and `egress_failed` (reason,
redirects, duration_ms). Neither carries a URL, host or content. Registered fields:
`redirects`, `size_bytes`.

**Testing code that fetches.** Inject a fake resolver and `httpx.MockTransport`; no network:

```python
class FakeResolver:
    async def resolve(self, host: str, port: int) -> list[IPAddress]:
        return [ipaddress.ip_address("93.184.216.34")]

client = SafeHttpClient(
    resolver=FakeResolver(),
    transport_factory=lambda target: httpx.MockTransport(handler),  # target: PinnedTarget
)
```

`content=` responses, which httpx reads eagerly, work. For gzip or other encoded bodies, build
the response with `stream=httpx.ByteStream(raw)`. Otherwise httpx decodes the body when it builds
the response.

## Notifications (`notify`)

**Payload.** `NotificationPayload(title, url, tag)`, with extra fields forbidden. There is no
body or description field, so draft text cannot be pushed.
- Title: 1-80 characters; no control or bidi characters.
- URL: a same-origin path: one leading `/` (never `//` or `/\`), RFC 3986 characters only, at most
  512 characters.
- Tag: `[A-Za-z0-9][A-Za-z0-9._:-]{0,63}`.

**Wire format** (for the web service worker): the decrypted push data is UTF-8 JSON with exactly
`{"title": ..., "url": ..., "tag": ...}`. Show `title` with `{tag}` and open `url` on click. The
URL leads to a confirmation page; GET never changes state.

**Subscriptions.** `PushSubscription.from_browser(subscription.toJSON(), now=...)` or `.create(...)`.
- The id is the first 32 hex digits of SHA-256(endpoint), so re-subscribing upserts one record.
- `p256dh` must be a valid uncompressed P-256 point and `auth` must be 16 bytes.
- Endpoints must be `https` on an allowlisted push service (`PUSH_SERVICE_HOSTS`: FCM, Mozilla
  autopush, WNS `*.notify.windows.com` and Apple `*.push.apple.com`), with no credentials and the
  default port.
- The endpoint is a capability. It never appears in logs, `repr` or validation messages.
- `SubscriptionRepo` has `upsert`, `get`, `list_active`, `mark_expired(id, at=)` and `delete`.
  `MemorySubscriptionRepo` is for tests; the Cosmos implementation comes later. Timestamps must be
  UTC.

**Web push.** `WebPushNotifier.from_settings(None, repo)` builds the notifier.
`await notifier.notify_all(payload, urgency="high")` returns one `DeliveryReceipt` per active
subscription.
- `pywebpush.webpush` runs in `asyncio.to_thread` with VAPID (RFC 8292 `vapid t=...,k=...`),
  aes128gcm, `TTL` and `Urgency` headers. Each push uses a fresh `requests` session with
  `trust_env=False` and `max_redirects=0`, closed afterwards.
- Receipt outcomes:
  - `delivered`: 201 or 202 only.
  - `expired`: 404 or 410. The repo marks the subscription expired, and it gets no further pushes.
  - `failed`: everything else, including 200, timeouts, connection errors and encryption errors.
- A failed receipt also calls `emit_alert("notification_failed", subscription_id)`.
- `send` never raises for delivery problems. `notify_all` re-raises a repository error only after
  every push has finished.
- `notifier.application_server_key` is the base64url VAPID public key for
  `PushManager.subscribe({applicationServerKey})`.
- Logs: `push_receipt` (subscription_id, push_outcome, status, duration_ms and, on exceptions,
  error_type and error_frames) and `push_no_subscriptions`.
- `pywebpush` sends with `requests`, outside the egress helper. The push-service host allowlist,
  no redirects and no proxies are what stop a forged subscription from reaching internal
  addresses. Subscriptions only come from the authenticated owner.

**Out-of-band alerts.** `emit_alert(kind, ref_id=None)` writes exactly one line at level ERROR:
`{"event": "alert_event", "logger": "blogwriter.notify.alerts", "alert_kind": ..., "ref_id": ...}`.
- Kinds:
  - `job_failed`, `publish_failed`, `needs_attention`: the plan's names.
  - `quarantined`: a quarantine.
  - `brake_tripped`: a runaway-brake trip.
  - `notification_failed`: a failed receipt.
  - `test`: for the Phase 1 exit criterion "a test alert fires out-of-band".
- An unknown kind is logged as `unknown` and still alerts.
- A `ref_id` that is not id-shaped (`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`) is dropped with
  `reason: "ref_id_rejected"`.
- `emit_alert` never raises, so it is safe in `except` blocks. Registered fields: `alert_kind`,
  `ref_id`.

## For other parts of v2

- **infra (`infra/v2`)**
  - Azure Monitor log alert rule on the Container Apps console log, emailing the owner's action
    group. The `parse_json` and `where` steps can be swapped for `has` checks if the table
    flattens differently:

    ```kusto
    ContainerAppConsoleLogs_CL
    | where ContainerAppName_s == "ca-blog-writer-dev" and Log_s has "alert_event"
    | extend e = parse_json(Log_s)
    | where tostring(e.event) == "alert_event"
    | project TimeGenerated, kind = tostring(e.alert_kind), ref = tostring(e.ref_id)
    ```

  - Key Vault secrets to reference: `BW_VAULT_KEYS` (and `BW_VAULT_PRIMARY` when there are
    several keys) and `BW_NOTIFY_VAPID_PRIVATE_KEY`. Set `BW_NOTIFY_VAPID_SUBJECT` as a plain
    setting.
- **web**
  - The service worker parses `event.data.json()` as above and shows only the title.
  - Subscribe with the `applicationServerKey` from an API route, then POST `subscription.toJSON()`.
- **api / wiring**
  - At startup: build one `SafeHttpClient`, then `TokenVault.from_settings()` (when LinkedIn is
    enabled) and `WebPushNotifier.from_settings(None, cosmos_repo)` (when push is configured).
  - Routes to add:
    - `GET` VAPID public key.
    - `POST` subscription: `PushSubscription.from_browser`, then `repo.upsert`. The core 422
      handler already drops pydantic `input`.
    - `DELETE` subscription.
    - `POST` "send test alert": `emit_alert("test", request_id)`, plus an optional test push.
  - `core/__init__.py` (not mine) could mention crypto and egress in its docstring.

## Decisions worth knowing

- 200 from a push service counts as `failed` (RFC 8030 says 201; the task said 201/202).
- All of 6to4, Teredo and NAT64 are refused, even forms that embed a public IPv4 address. On an
  IPv6-only network with DNS64 this would block IPv4-only sites. The app runs on IPv4, and the
  rule fails closed.
- The egress body cap is 5 MiB (5 x 1024 x 1024 bytes), applied to both the wire size and the
  decoded size.
- `pyright` strict: `pywebpush` and `py_vapid` ship no types, so their imports carry targeted
  `# pyright: ignore[reportMissingTypeStubs]`. `pywebpush.webpush` is checked against the typed
  `WebPushFunction` protocol.

## Checks

From `server/`, final run on 2026-11-26:
- `uv sync --locked`: ok.
- `pyright` (strict, whole project): 0 errors.
- `pytest -q`: 2440 passed, 1 live test skipped. This includes 70 crypto, 235 egress and 193
  notify tests.
- `ruff check .` and `ruff format --check .`: clean on every file in this area. The whole-repo
  run reported 5 lint findings and 2 unformatted files, all in `tests/jobs/test_claims.py` and
  `tests/jobs/test_models.py`, which the jobs work was editing at the time (not this area).

A manual, non-unit check against a local TLS server with a certificate for `example.test`
confirmed the real pinned path over sockets. It dials `127.0.0.1`, while SNI and `Host` are
`example.test`, and a certificate/hostname mismatch is refused.
