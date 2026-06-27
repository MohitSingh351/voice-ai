# Voice AI - Technical Details

Implementation-level reference for engineers working on the codebase. For the
visual map see [`ARCHITECTURE.md`](./ARCHITECTURE.md); for setup/usage see
[`README.md`](./README.md).

---

## Table of contents

1. [Design principles](#1-design-principles)
2. [Request lifecycle, end to end](#2-request-lifecycle-end-to-end)
3. [Data models - every field](#3-data-models--every-field)
4. [State machines](#4-state-machines)
5. [Dispatch & concurrency](#5-dispatch--concurrency)
6. [Rate limiting (Redis token bucket)](#6-rate-limiting-redis-token-bucket)
7. [Celery tasks & schedule](#7-celery-tasks--schedule)
8. [Retry & failure handling](#8-retry--failure-handling)
9. [The Vapi integration layer](#9-the-vapi-integration-layer)
10. [Provisioning](#10-provisioning)
11. [Webhooks](#11-webhooks)
12. [Voice override](#12-voice-override)
13. [CSV import](#13-csv-import)
14. [Dashboard](#14-dashboard)
15. [REST API surface](#15-rest-api-surface)
16. [Settings & configuration](#16-settings--configuration)
17. [Security model](#17-security-model)
18. [Testing strategy](#18-testing-strategy)
19. [Known limitations & extension points](#19-known-limitations--extension-points)

---

## 1. Design principles

- **The backend never streams audio.** Vapi owns the media path (STT → LLM →
  TTS) and the SIP leg. The backend is a thin, reliable orchestration +
  bookkeeping layer around Vapi's REST API and webhooks. "Throttling" therefore
  means *controlling the rate of `POST /call`*, nothing more.
- **The database is the source of truth for concurrency.** In-flight count is
  derived from rows (`CampaignLead.IN_FLIGHT` / `Call` active statuses), never
  from a mutable counter that can drift.
- **Idempotency everywhere external.** Provisioning is create-if-missing;
  webhook handlers are idempotent on `vapi_call_id`; lead reservation is atomic.
- **One integration boundary.** Only `apps/vapi/` makes HTTP calls to Vapi. No
  `httpx`/`requests` leaks into views or tasks.
- **Single-org now, multi-tenant later.** Every model carries an `Organization`
  FK; the MVP uses one row (`Organization.get_default()`, `pk=1`).
- **Layered dependencies.** `organizations` ← `leads` ← `campaigns` ← `calls`;
  `vapi`, `webhooks`, `dashboard` sit on top. No cycles.

---

## 2. Request lifecycle, end to end

1. **Ingest** - a `Lead` is created from CSV (`apps/leads/services/csv_import.py`)
   or a single manual entry. Phone is normalized to E.164; extra CSV columns go
   to `Lead.variables`.
2. **Batch** - a `Campaign` is created and leads attached as `CampaignLead`
   rows in `PENDING` (`lifecycle.add_leads`).
3. **Start** - `lifecycle.start_campaign` flips status to `RUNNING`, stamps
   `started_at`, and kicks `tick_campaigns.delay(id)` immediately.
4. **Dispatch** - `tick_campaigns` → `dispatch.plan_and_reserve`:
   `available_slots ∩ rate budget` → claims that many due `PENDING` leads via
   `SELECT … FOR UPDATE SKIP LOCKED`, flips to `IN_FLIGHT` (+1 attempt), enqueues
   `place_call` per lead.
5. **Place** - `place_call` builds the payload and `POST /call`s Vapi, then
   creates a `Call(vapi_call_id, status=queued)`. On API error the lead is
   released (`FAILED`/`EXHAUSTED`).
6. **Connect** - Vapi sends a SIP `INVITE` to Twilio's termination over your
   BYO trunk; Twilio dials the lead on the PSTN.
7. **Progress** - `status-update` webhooks move `Call` through
   `ringing → in_progress`.
8. **Finish** - `end-of-call-report` stores transcript, summary, recording,
   structured outcome and cost; resolves the `CampaignLead`
   (`DONE`/`FAILED`/`EXHAUSTED`); kicks dispatch to refill the freed slot.
9. **Complete** - when no `PENDING`/`IN_FLIGHT` leads remain,
   `dispatch.maybe_complete` marks the campaign `COMPLETED`.

---

## 3. Data models - every field

### `organizations.Organization`
Single-tenant container; holds provisioned Vapi IDs and the default voice.

| Field | Type | Notes |
|-------|------|-------|
| `name` | char | default "Default Organization" |
| `vapi_credential_id` | char | BYO-SIP credential id |
| `vapi_phone_number_id` | char | caller-ID number id |
| `vapi_assistant_id` | char | assistant id |
| `default_caller_id` | char | E.164 caller id |
| `default_voice_provider` | char | e.g. `vapi`; blank ⇒ no override |
| `default_voice_id` | char | e.g. `Elliot`; blank ⇒ assistant default |
| `created_at` / `updated_at` | datetime | |

Helpers: `is_provisioned` (`bool` - has phone + assistant), classmethod
`get_default()` (get-or-create `pk=1`).

### `leads.Lead`
| Field | Type | Notes |
|-------|------|-------|
| `organization` | FK | |
| `name` | char | |
| `phone_e164` | char | indexed; **unique per org** |
| `raw_phone` | char | original input |
| `variables` | JSON | extra CSV columns → Vapi `variableValues` |
| `source` | choice | `csv` / `manual` |
| `created_at` | datetime | |

`call_variables()` → `{"name": …, **variables}` (sent to Vapi per call).
Constraint: `unique(organization, phone_e164)`.

### `campaigns.Campaign`
| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `organization` | FK | | |
| `name` | char | | |
| `status` | choice | `draft` | `draft/running/paused/completed` |
| `assistant_id` | char | "" | overrides org assistant if set |
| `from_phone_number_id` | char | "" | overrides org number if set |
| `max_concurrent_calls` | int | 5 | hard concurrency cap |
| `calls_per_minute` | int | 10 | `0` = unthrottled rate |
| `max_attempts` | int | 2 | retry cap per lead |
| `retry_delay_minutes` | int | 30 | backoff between attempts |
| `started_at` / `completed_at` | datetime | null | |

Helpers: `resolved_assistant_id()`, `resolved_phone_number_id()` (campaign value
or org fallback), `counts()` (status breakdown for the dashboard).

### `campaigns.CampaignLead` - the dispatch work queue
| Field | Type | Notes |
|-------|------|-------|
| `campaign` / `lead` | FK | `unique(campaign, lead)` |
| `status` | choice | `pending/in_flight/done/failed/exhausted`; indexed |
| `attempts` | int | incremented on each reservation |
| `next_attempt_at` | datetime | retry gate; null ⇒ due now |
| `updated_at` | datetime | |

`ACTIVE_STATUSES = (IN_FLIGHT,)` - what counts against the concurrency cap.

### `calls.Call`
| Field | Type | Notes |
|-------|------|-------|
| `campaign_lead` | FK | |
| `vapi_call_id` | char | **unique**, indexed |
| `status` | choice | `queued/ringing/in_progress/ended/failed`; indexed |
| `started_at` / `ended_at` | datetime | |
| `ended_reason` | char | Vapi `endedReason` |
| `transcript` | text | |
| `summary` | text | AI summary |
| `recording_url` | url | |
| `structured_outcome` | JSON | `{successEvaluation, structuredData}` |
| `cost` | decimal(8,4) | |
| `raw_end_report` | JSON | full webhook payload (audit + idempotency flag) |
| `created_at` / `updated_at` | datetime | |

`ACTIVE_STATUSES = (QUEUED, RINGING, IN_PROGRESS)`; `is_active` property.

---

## 4. State machines

**CampaignLead:** `PENDING → IN_FLIGHT → {DONE | FAILED | EXHAUSTED}`, with
`FAILED → PENDING` on retry (or `FAILED → EXHAUSTED` when attempts run out). A
lead is `IN_FLIGHT` for the entire active call lifetime; that's what makes the
DB the concurrency source of truth.

**Call:** `QUEUED → RINGING → IN_PROGRESS → ENDED` (driven by webhooks), or
straight to `ENDED` if it fails before connecting.

**Campaign:** `DRAFT → RUNNING ⇄ PAUSED → COMPLETED`.

See diagrams in [`ARCHITECTURE.md` §8](./ARCHITECTURE.md#8-state-machines).

---

## 5. Dispatch & concurrency

`apps/campaigns/services/dispatch.py`:

```python
available_slots(c)  = max(0, c.max_concurrent_calls − count(IN_FLIGHT leads))
plan_and_reserve(c) = reserve_due_leads(c, reserve_call_budget(c.id, c.calls_per_minute, slots))
```

`reserve_due_leads` runs inside `transaction.atomic()`:

```python
ids = (CampaignLead.objects
        .select_for_update(skip_locked=True)
        .filter(campaign=c, status=PENDING)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .order_by("id")
        .values_list("id", flat=True)[:limit])
CampaignLead.objects.filter(id__in=ids).update(status=IN_FLIGHT, attempts=F("attempts")+1)
```

**Why `FOR UPDATE SKIP LOCKED`:** the beat tick and event-driven kicks can run
concurrently. Row locks + skipping already-locked rows let overlapping ticks
claim *disjoint* lead sets without blocking each other or double-dialing.

**The invariant:** a lead occupies exactly one slot from the moment it's
reserved (here) until a webhook or a failed `place_call` resolves it. Because
slots are computed from the live `IN_FLIGHT` count *before* reserving, concurrent
ticks can never push active calls above `max_concurrent_calls`. This is asserted
in `tests/test_dispatch.py`.

`maybe_complete(c)` flips a `RUNNING` campaign to `COMPLETED` once no
`PENDING`/`IN_FLIGHT` work remains.

---

## 6. Rate limiting (Redis token bucket)

`apps/campaigns/services/throttle.py` - a **fixed-window** limiter keyed
`campaign:{id}:rate:{epoch_minute}`. A Lua script reserves up to `want` tokens
without exceeding `calls_per_minute`, atomically:

```lua
local current = tonumber(redis.call('get', KEYS[1]) or '0')
local remaining = tonumber(ARGV[1]) - current          -- limit - used
if remaining <= 0 then return 0 end
local grant = math.min(remaining, tonumber(ARGV[2]))    -- min(remaining, want)
redis.call('incrby', KEYS[1], grant)
redis.call('expire', KEYS[1], tonumber(ARGV[3]))        -- 120s TTL
return grant
```

- Atomicity matters because beat + kicks call it concurrently.
- `calls_per_minute = 0` ⇒ unthrottled (returns `want` immediately).
- The key TTLs out (120s) so windows self-clean. The client + script are
  registered once per process (module-level singletons).

> Trade-off: a fixed window allows a burst at a window boundary (classic
> limitation). For the MVP's coarse pacing this is acceptable; swap for a
> sliding-window/leaky-bucket script if you need smoother egress.

---

## 7. Celery tasks & schedule

Defined in `apps/calls/tasks.py`, wired in `config/celery.py`.

| Task | Trigger | Does |
|------|---------|------|
| `tick_campaigns(campaign_id=None)` | beat (`CAMPAIGN_TICK_SECONDS`) + kicks | reserve & enqueue `place_call` for running campaigns; `maybe_complete` |
| `place_call(campaign_lead_id)` | enqueued per reserved lead | `POST /call`, create `Call`, or release lead on error (`max_retries=0`) |
| `retry_failed()` | beat (120s) | requeue `FAILED` leads whose backoff elapsed; exhaust the rest |

Beat schedule (`config/celery.py`, `on_after_finalize`): `tick-all-campaigns`
every `CAMPAIGN_TICK_SECONDS`, `retry-failed-leads` every 120s. Both delegate to
the `apps.calls.tasks` functions. Celery uses Redis for broker + result backend;
`autodiscover_tasks()` picks up each app's `tasks.py`.

**Status mapping:** `place_call._map_status` and `handlers._STATUS_MAP` translate
Vapi's hyphenated statuses (`in-progress`, `forwarding`) to the `Call.Status`
enum.

---

## 8. Retry & failure handling

Two paths converge on the same policy (`attempts` vs `max_attempts`):

- **Dispatch-time failure** (`tasks._fail_lead`): `POST /call` errored or the
  campaign isn't provisioned → `EXHAUSTED` if out of attempts, else `FAILED`
  with `next_attempt_at = now + retry_delay_minutes`.
- **Call-outcome failure** (`handlers._resolve_lead`): if `endedReason` is in
  `RETRYABLE_ENDED_REASONS` (no-answer, busy, voicemail, did-not-give-mic,
  twilio-failed-to-connect, pipeline-error) and attempts remain → `FAILED`
  (retry scheduled); if retryable but exhausted → `EXHAUSTED`; otherwise
  (connected & finished) → `DONE`.

`retry_failed` (beat) later moves due `FAILED` leads back to `PENDING` (clearing
`next_attempt_at`) so the dispatcher re-reserves them, or to `EXHAUSTED` when
`attempts ≥ max_attempts`.

---

## 9. The Vapi integration layer

`apps/vapi/` is the single HTTP boundary to Vapi.

- **`client.py` - `VapiClient`** (httpx, bearer `VAPI_API_KEY`): `_post`/`_get`
  raise `VapiError(status_code, body)` on non-2xx. Methods:
  `create_byo_sip_credential`, `create_phone_number`, `create_assistant`,
  `place_call`, `get_call`.
- **`schemas.py` - payload builders** (pure dict factories, easy to unit test as
  the Vapi schema evolves):
  - `byo_sip_credential_payload` - `provider: byo-sip-trunk`, one **outbound-only**
    gateway (`inboundEnabled:false`, `outboundEnabled:true`,
    `outboundLeadingPlusEnabled:true`). An inbound gateway would require a
    numeric IPv4, but a `*.pstn.twilio.com` termination is a host - hence
    outbound-only. `outboundAuthenticationPlan` only included if username+password
    are set (otherwise Twilio authorizes Vapi by IP ACL).
  - `byo_phone_number_payload` - `provider: byo-phone-number` referencing the
    credential.
  - `assistant_payload` - **`model` is a nested object**
    (`{provider, model, messages:[{role:system,…}]}`), plus `voice`,
    `transcriber`, an `analysisPlan` (summary + success evaluation), and a
    `server` block (`url`, `secret`) when a webhook URL is configured.
  - `outbound_call_payload` - `phoneNumberId`, `assistantId`, `customer`, and an
    `assistantOverrides` block carrying `variableValues` and an optional
    per-call `voice` override.

---

## 10. Provisioning

`apps/vapi/provisioning.py :: ensure_org_provisioned(org, *, client, force)` is
**idempotent and incremental**:

1. **Credential** - create the BYO-SIP credential if `vapi_credential_id` is
   empty (requires `TWILIO_SIP_TERMINATION_URI`), then
   `save(update_fields=["vapi_credential_id", …])` **immediately**.
2. **Phone number** - create the caller-ID number (requires `TWILIO_CALLER_ID`),
   save `vapi_phone_number_id` + `default_caller_id`.
3. **Assistant** - create with prompt/model/voice/transcriber + the webhook
   `server.url`/`secret`, save `vapi_assistant_id`.

Each ID is persisted **right after** its resource is created (not in one trailing
save) so a mid-run failure can't orphan resources or leave a stale ID. `--force`
recreates everything. Exposed as `manage.py provision_vapi` (warns if
`PUBLIC_WEBHOOK_BASE_URL` is empty; surfaces Vapi errors as `CommandError`).
`webhook_server_url()` builds `<PUBLIC_WEBHOOK_BASE_URL>/webhooks/vapi/`.

---

## 11. Webhooks

- **Endpoint** `apps/webhooks/views.py :: VapiWebhookView` - DRF `APIView`,
  `AllowAny`, **no authentication class**. It verifies the `X-Vapi-Secret` header
  against `VAPI_WEBHOOK_SECRET` with `hmac.compare_digest` (constant-time). A
  missing/blank configured secret → reject (fail closed). Body must contain a
  `message` dict, else `400`. Any handler exception is logged and still returns
  `200` (Vapi expects fast 2xx; we don't want retry storms).
- **Routing** `apps/webhooks/handlers.py :: dispatch_event` keys off
  `message.type`:
  - `status-update` → `handle_status_update`: map status, set `started_at` on
    first `in_progress`.
  - `end-of-call-report` → `handle_end_of_call_report`: persist transcript /
    summary / recording / `structured_outcome` / cost / `raw_end_report`; mark
    `Call.ENDED`; resolve the lead; kick dispatch. **Idempotent** - re-delivery
    is detected via `status == ENDED and raw_end_report` and skips lead
    re-resolution.
  - unknown types → logged + ignored (`200`).
- **Call lookup** tolerates both `message.call.id` and `message.callId`.

---

## 12. Voice override

Org-wide default voice, applied **per call** (no extra assistants):

- Stored on `Organization.default_voice_id` / `default_voice_provider`
  (Settings page → `dashboard.views.settings_view`).
- `tasks.place_call` passes them through **only when `default_voice_id` is set**:
  `voice_provider = org.default_voice_provider if org.default_voice_id else ""`.
- `schemas.outbound_call_payload` adds
  `assistantOverrides.voice = {provider, voiceId}` when both are present;
  otherwise the call uses the assistant's own (dashboard) voice.
- The Settings dropdown is fed by `VAPI_VOICES` (a curated list in
  `apps/dashboard/views.py`).

---

## 13. CSV import

`apps/leads/services/csv_import.py :: import_leads_csv` returns an
`ImportResult(created, skipped_duplicates, errors)`:

- Decodes UTF-8 (BOM-tolerant via `utf-8-sig`), reads with `csv.DictReader`.
- **Column resolution** (`_resolve_columns`) matches headers
  case-insensitively, normalizing spaces/hyphens to `_`, against `NAME_ALIASES`
  and `PHONE_ALIASES`. Missing name *or* phone column → single error, abort.
- Per row (line numbers start at 2): require non-empty name; normalize phone to
  E.164 (`normalize_phone`, default region `US`, `phonenumbers` validation) -
  invalid rows collected with reasons, valid rows continue.
- **Dedup** against existing org phones *and* within-file (`skip_duplicates`).
- Extra columns → `Lead.variables`. Valid rows `bulk_create`d in one query.

---

## 14. Dashboard

`apps/dashboard/` - server-rendered, `@login_required`, light HTMX.

| URL name | Path | View |
|----------|------|------|
| `campaign_list` | `/` | list + **create campaign** (POST) |
| `leads` | `/leads/` | CSV import + manual add + leads table |
| `lead_delete` | `/leads/<id>/delete/` | POST delete (confirm dialog) |
| `settings` | `/settings/` | voice + provisioned resources |
| `campaign_detail` | `/campaigns/<id>/` | controls + live leads table |
| `campaign_leads_partial` | `/campaigns/<id>/leads/` | HTMX fragment (polled every 4s) |
| `campaign_action` | `/campaigns/<id>/<action>/` | start/pause/stop (POST) |
| `call_detail` | `/calls/<id>/` | transcript, recording, outcome |

UI: `templates/base.html` is a sidebar app shell (Campaigns / Leads / Settings,
active-state highlighting, user chip + sign-out) with a light theme and a shared
component vocabulary (`.panel`, `.btn`, `.badge.<status>`, `.stat-card`,
`.empty`, `.messages`). Unauthenticated users get a centered auth layout
(`auth_content` block) used by `registration/login.html`. The
`dashboard_extras.get` template filter indexes the call-by-id map in the leads
partial.

---

## 15. REST API surface

Routers in each app's `api.py`, mounted under `/api/` (`config/urls.py`).
`SessionAuthentication` + `IsAuthenticated`, `PageNumberPagination` (50/page).

- **LeadViewSet** - list / retrieve / destroy + `@action upload` (multipart CSV)
  + `@action manual` (name+phone, E.164-validated via `ManualLeadSerializer`).
- **CampaignViewSet** - full CRUD; `create` accepts `lead_ids` and attaches them;
  `@action`s `start` / `pause` / `stop` / `add_leads` delegate to `lifecycle`.
  `CampaignSerializer` exposes computed `counts`.
- **CallViewSet** - read-only list/retrieve, filterable by `?campaign=<id>`.

The dashboard and API share the same service layer (`lifecycle`, `dispatch`,
`csv_import`) - no logic duplicated between them.

---

## 16. Settings & configuration

`config/settings/base.py` (django-environ): `INSTALLED_APPS` includes the local
apps + `apps.vapi` (model-less, registered so its management command is
discoverable). Key blocks: DRF (session auth, `IsAuthenticated`, pagination);
Celery/Redis (`CELERY_BROKER_URL`/`RESULT_BACKEND` default to `REDIS_URL`,
`CAMPAIGN_TICK_SECONDS`); `VAPI_*`; and a `VAPI_PROVISION` dict that funnels all
provisioning inputs (assistant prompt/model/voice/transcriber + Twilio termination
values) from env.

- `dev.py` - `DEBUG=True`, `ALLOWED_HOSTS=["*"]`, optional
  `CELERY_TASK_ALWAYS_EAGER`.
- `prod.py` - `DEBUG=False`, SSL redirect, secure cookies, HSTS, proxy SSL header.

`pyproject.toml` pins Python ≥3.14, deps via `uv`, pytest (`DJANGO_SETTINGS_MODULE
= config.settings.dev`), and ruff (`E,F,I,UP,B`, line 100, migrations excluded).

---

## 17. Security model

- **Dashboard & API** - login-gated (session auth). MVP is single-org; all
  querysets are scoped to `Organization.get_default()`.
- **Webhook** - public but **secret-verified** (constant-time `X-Vapi-Secret`),
  fail-closed if the secret is unset. Body validated; handler errors swallowed
  into `200` after logging.
- **Provisioning credentials** - Twilio/Vapi secrets live in env, never in code;
  Vapi resource IDs persist on the DB row.
- **Prod hardening** - SSL redirect, `Secure`/HSTS cookies, proxy SSL header in
  `prod.py`. (Before going live: real `SECRET_KEY`, pinned `ALLOWED_HOSTS`,
  managed Postgres/Redis, and a non-trial Twilio account with the right Geo
  Permissions + IP ACL.)

---

## 18. Testing strategy

`tests/` (pytest-django, `respx` for HTTP mocking):

| File | Covers |
|------|--------|
| `test_csv_import.py` | header aliasing, E.164 normalization, dup skipping, row errors |
| `test_dispatch.py` | slot math, Redis rate budget, **never-exceed-concurrency** invariant |
| `test_vapi_client.py` | `place_call` payload (incl. voice override on/off), error raising, **idempotent provisioning** |
| `test_webhooks.py` | secret verification, transcript persistence, idempotency |

Run: `uv run pytest` (needs Postgres + Redis). Lint: `uv run ruff check .`.
A quick render smoke-test (all dashboard pages → `200`) is the fastest way to
catch template regressions.

---

## 19. Known limitations & extension points

- **Single org** - `get_default()` everywhere. To go multi-tenant: resolve the
  org from the request (subdomain/user), drop `pk=1`, keep the existing FKs.
- **Fixed-window rate limit** - allows boundary bursts; swap the Lua for a
  sliding window if you need smoother egress.
- **Voice list is static** (`VAPI_VOICES`) - wire to Vapi's voice-list API for a
  live list, and/or make voice per-campaign by adding a field to `Campaign` and
  reading it in `place_call`.
- **No call recording storage** - only Vapi's `recording_url` is kept; mirror to
  your own object store if you need retention/control.
- **Provisioning recreates on `--force`** - there's no "update assistant"
  call; changing model/voice live is done in the Vapi dashboard (or Settings for
  voice). Add a PATCH path if you want app-driven assistant edits.
- **Beat + worker are separate processes** - fine for one node; for HA run
  multiple workers (dispatch is concurrency-safe) and exactly one beat.
