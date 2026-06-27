# Voice AI — AI Sales Calling Platform (MVP)

Outbound AI sales calling. Upload a CSV of leads (or add one by hand), create a
campaign, and an AI voice agent calls each lead — **throttled**, with automatic
**retries**, and full **transcripts, recordings and structured outcomes** stored
for every call.

**One-line architecture:** [Vapi](https://vapi.ai) runs the voice conversation
over **your own Twilio Elastic SIP Trunk** (BYO-SIP); this Django backend
orchestrates *which* leads to call, *how fast*, and stores the results from
Vapi's webhooks. **The backend never streams audio — Vapi does.**

> 📐 See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for diagrams and
> [`TECHNICAL_DETAILS.md`](./TECHNICAL_DETAILS.md) for the implementation deep-dive.

```
CSV / manual ─▶ Lead ─▶ Campaign(throttle) ─▶ Celery dispatch ─▶ Vapi POST /call ─▶ Twilio SIP ─▶ lead's phone
                                                    ▲                                     │
                                                    └──────── webhook (status / report) ──┘
```

---

## Table of contents

1. [Features](#features)
2. [Tech stack](#tech-stack)
3. [Project layout](#project-layout)
4. [Prerequisites](#prerequisites)
5. [Local setup](#1-local-setup)
6. [Twilio — Elastic SIP Trunk](#2-twilio--create-the-elastic-sip-trunk-byo-sip)
7. [Vapi — account & provisioning](#3-vapi--account--provisioning)
8. [Webhooks — let Vapi reach you](#4-webhooks--let-vapi-reach-you)
9. [Using the app](#5-using-the-app)
10. [REST API](#rest-api)
11. [CSV format](#csv-format)
12. [Configuration reference (`.env`)](#configuration-reference-env)
13. [How throttling works](#how-throttling-works)
14. [Choosing the agent voice](#choosing-the-agent-voice)
15. [Testing](#testing)
16. [Troubleshooting](#troubleshooting)

---

## Features

- **Leads** — import a CSV (with column auto-detection + E.164 normalization) or
  add a single lead by hand. View and **delete** leads from a dedicated page.
- **Campaigns** — batch leads into a campaign with per-campaign throttle and
  retry policy; **Start / Pause / Resume / Stop** from the UI or API.
- **AI calling** — each lead is dialed by a Vapi assistant (Anthropic Claude
  brain by default) over your Twilio trunk. Lead CSV columns are passed as
  personalization variables.
- **Throttling** — hard concurrency cap + per-minute rate limit per campaign,
  enforced atomically across overlapping dispatch ticks.
- **Automatic retries** — no-answer / busy / voicemail outcomes retry up to
  `max_attempts` after a backoff.
- **Results** — transcript, recording URL, AI summary, success evaluation,
  structured data and cost, captured from Vapi webhooks and shown per call.
- **Voice selection** — pick an org-wide default voice in **Settings**.
- **Dashboard** — clean, login-gated HTMX UI with live status polling.
- **REST API** — `/api/` endpoints in parallel for automation / a future SPA.

---

## Tech stack

| Layer | Choice |
|-------|--------|
| Web framework | **Django 5 + Django REST Framework** |
| Async / jobs | **Celery + Redis** (broker, result backend, rate buckets) |
| Database | **Postgres** (system of record) |
| Voice agent | **Vapi** — STT (Deepgram) · LLM (Anthropic Claude) · TTS |
| Telephony | **Twilio Elastic SIP Trunk** (BYO-SIP egress) |
| UI | Server-rendered Django templates + **HTMX** |
| Tooling | **uv** (deps/venv), **pytest**, **ruff**, Python **3.14** |

---

## Project layout

```
config/            settings (split), celery app + beat schedule, urls
apps/organizations Organization (single-tenant MVP; Vapi resource ids, voice)
apps/leads         Lead model + CSV import service (+ DRF api)
apps/campaigns     Campaign/CampaignLead, dispatch + throttle + lifecycle
apps/calls         Call model, Celery dispatch tasks
apps/vapi          VapiClient, payload schemas, idempotent provisioning, command
apps/webhooks      /webhooks/vapi/ — secret-verified event handlers
apps/dashboard     login-gated HTMX UI (campaigns, leads, settings, call detail)
templates/         base.html (sidebar shell) + registration/login
tests/             pytest suite (csv, dispatch/throttle, vapi, webhooks)
```

---

## Prerequisites

- **Python 3.14** and [**uv**](https://docs.astral.sh/uv/)
- **Postgres** (running locally or reachable via `DATABASE_URL`)
- **Redis** (running locally or reachable via `REDIS_URL`)
- A **Twilio** account with Elastic SIP Trunking
- A **Vapi** account + private API key
- A tunnel for local webhooks: **cloudflared** or **ngrok**

---

## 1. Local setup

```bash
cd voice-ai
uv sync                                  # create venv + install deps
cp .env.example .env                     # then edit values (see reference below)
createdb voice_ai                        # local Postgres database
uv run python manage.py migrate
uv run python manage.py createsuperuser  # dashboard login
```

Run the three processes (each in its own terminal):

```bash
uv run python manage.py runserver                 # web + dashboard + webhooks
uv run celery -A config worker -l info            # dispatch worker
uv run celery -A config beat   -l info            # periodic dispatcher tick
```

- Dashboard → http://localhost:8000/
- Django admin → http://localhost:8000/admin/

> **Dev shortcut:** set `CELERY_TASK_ALWAYS_EAGER=true` in `.env` to run tasks
> inline (no worker/beat needed) while developing — calls fire synchronously in
> the web process.

---

## 2. Twilio — create the Elastic SIP Trunk (BYO-SIP)

1. Twilio Console → **Elastic SIP Trunking** → **Create a trunk**.
2. Note the **Termination URI** (e.g. `your-trunk.pstn.twilio.com`) → this is
   `TWILIO_SIP_TERMINATION_URI`.
3. Under **Termination**, configure authentication:
   - **IP ACL** (recommended) — allowlist **Vapi's SBC IPs** so Vapi's outbound
     `INVITE` is accepted. *(If these aren't allowlisted you'll get a SIP
     `403 Forbidden` — see [Troubleshooting](#troubleshooting).)*
   - and/or a **Credential List** → `TWILIO_SIP_USERNAME` / `TWILIO_SIP_PASSWORD`.
4. Buy / attach a phone number to use as caller ID → `TWILIO_CALLER_ID`
   (E.164, e.g. `+15551234567`).
5. **Voice → Geo Permissions** — enable the destination countries you'll call
   (e.g. India `+91`). They're blocked by default.
6. On a **trial** account you can only call **verified** numbers.

---

## 3. Vapi — account & provisioning

1. Create a [Vapi](https://vapi.ai) account → copy your **private API key** →
   `VAPI_API_KEY`.
2. Pick a long random `VAPI_WEBHOOK_SECRET`.
3. Fill the Twilio + assistant values in `.env`. The assistant brain defaults to
   Anthropic Claude (`VAPI_MODEL_PROVIDER=anthropic`,
   `VAPI_MODEL_NAME=claude-sonnet-4-6`) — confirm the exact model id against
   Vapi's supported list before going live.
4. Provision the BYO-SIP credential, the caller-ID number, and the assistant in
   one **idempotent** command:

   ```bash
   uv run python manage.py provision_vapi
   ```

   Re-running is safe — it skips resources whose IDs are already stored on the
   Organization. Use `--force` to recreate them (e.g. after changing the webhook
   URL, prompt, model or voice in `.env`).

   > The created Vapi IDs are persisted on the `Organization` row (visible under
   > **Settings → Provisioned Vapi resources**), not left loose in settings.

---

## 4. Webhooks — let Vapi reach you

Vapi POSTs call events to a public URL. In dev, tunnel to `runserver`:

```bash
cloudflared tunnel --url http://localhost:8000      # or: ngrok http 8000
```

Put the public URL in `.env` as `PUBLIC_WEBHOOK_BASE_URL`, then re-run
`provision_vapi --force` so the assistant's `server.url` points at
`<PUBLIC_WEBHOOK_BASE_URL>/webhooks/vapi/`.

Every event carries an `X-Vapi-Secret` header that the endpoint verifies (in
constant time) against `VAPI_WEBHOOK_SECRET`; mismatches get `401`.

---

## 5. Using the app

**Dashboard flow:**

1. **Leads** → import a CSV or add a single lead. Review them in the table;
   delete any you don't want to call.
2. **Campaigns** → *New campaign* (name + throttle settings) → it attaches all
   your current leads → **Create**.
3. On the campaign page, press **Start**. Watch lead/call status update live
   (HTMX polls every 4s). Click a call for transcript, recording and outcome.
4. **Settings** → choose the agent voice and review provisioned Vapi resources.

---

## REST API

Session-authenticated (log in via the dashboard first). All endpoints are scoped
to the single MVP organization.

```bash
# Upload a CSV (multipart; needs name + phone columns)
curl -F file=@leads.csv http://localhost:8000/api/leads/upload/

# Add a single lead
curl -X POST http://localhost:8000/api/leads/manual/ \
     -H 'Content-Type: application/json' \
     -d '{"name":"Ada Lovelace","phone":"+14155552671"}'

# List / delete leads
curl http://localhost:8000/api/leads/
curl -X DELETE http://localhost:8000/api/leads/1/

# Create a campaign with specific leads, then start it
curl -X POST http://localhost:8000/api/campaigns/ \
     -H 'Content-Type: application/json' \
     -d '{"name":"Q3 Outbound","max_concurrent_calls":3,"calls_per_minute":10,"lead_ids":[1,2,3]}'
curl -X POST http://localhost:8000/api/campaigns/1/start/
# also: /pause/  /stop/  /add_leads/

# Results
curl "http://localhost:8000/api/calls/?campaign=1"
```

| Resource | Endpoints |
|----------|-----------|
| Leads | `GET/DELETE /api/leads/`, `POST /api/leads/upload/`, `POST /api/leads/manual/` |
| Campaigns | `GET/POST /api/campaigns/`, `POST /api/campaigns/{id}/{start,pause,stop,add_leads}/` |
| Calls | `GET /api/calls/`, `GET /api/calls/?campaign={id}`, `GET /api/calls/{id}/` |
| Webhook | `POST /webhooks/vapi/` (Vapi only; secret-verified) |

---

## CSV format

```csv
name,phone,company,city
Ada Lovelace,+14155552671,Analytical Engines,London
Grace Hopper,(415) 555-2672,US Navy,Arlington
```

- **`name`** and **`phone`** are required. Header aliases are accepted
  case-insensitively and with spaces/hyphens: `full_name`, `contact`, `mobile`,
  `phone_number`, `number`, `tel`, `cell`, …
- Phones are normalized to **E.164** (default region `US`; include `+<country>`
  to override). Invalid rows are reported with line numbers; valid rows still
  import.
- Duplicates (same E.164 within the org or within the file) are **skipped**.
- **Every extra column** (here `company`, `city`) is stored on the lead and
  forwarded to Vapi as `variableValues` for prompt personalization — reference
  them in the assistant prompt / first message as `{{company}}`, `{{city}}`, etc.

---

## Configuration reference (`.env`)

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | `true` in dev |
| `ALLOWED_HOSTS` | comma-separated hosts |
| `PUBLIC_WEBHOOK_BASE_URL` | public base URL Vapi POSTs webhooks to (your tunnel) |
| `DATABASE_URL` | Postgres DSN, e.g. `postgres://localhost:5432/voice_ai` |
| `REDIS_URL` | Redis URL (broker + rate buckets) |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | optional; default to `REDIS_URL` |
| `CELERY_TASK_ALWAYS_EAGER` | dev-only; run tasks inline (no worker) |
| `CAMPAIGN_TICK_SECONDS` | dispatcher beat interval (default `5`) |
| `VAPI_BASE_URL` | `https://api.vapi.ai` |
| `VAPI_API_KEY` | Vapi **private** API key |
| `VAPI_WEBHOOK_SECRET` | shared secret verified on every webhook |
| `VAPI_ASSISTANT_NAME` | assistant display name |
| `VAPI_ASSISTANT_FIRST_MESSAGE` | opening line (supports `{{name}}`) |
| `VAPI_ASSISTANT_SYSTEM_PROMPT` | sales agent system prompt |
| `VAPI_MODEL_PROVIDER` / `VAPI_MODEL_NAME` | LLM brain (default `anthropic` / `claude-sonnet-4-6`) |
| `VAPI_VOICE_PROVIDER` / `VAPI_VOICE_ID` | TTS voice used at provisioning (default `vapi` / `Elliot`) |
| `VAPI_TRANSCRIBER_PROVIDER` / `VAPI_TRANSCRIBER_MODEL` | STT (default `deepgram` / `nova-2`) |
| `TWILIO_SIP_TERMINATION_URI` | trunk termination host, e.g. `your-trunk.pstn.twilio.com` |
| `TWILIO_SIP_USERNAME` / `TWILIO_SIP_PASSWORD` | only if using credential-list auth |
| `TWILIO_CALLER_ID` | E.164 number used as caller ID |

> These values are read by `provision_vapi` to **create** the assistant. Once
> created, changing model/voice in the Vapi dashboard (or via Settings for the
> voice) is what affects live calls — `.env` only re-applies on `--force`.

---

## How throttling works

Each `Campaign` has `max_concurrent_calls` and `calls_per_minute`. A Celery beat
task (`tick_campaigns`, every `CAMPAIGN_TICK_SECONDS`) **and** an event-driven
kick on every `end-of-call-report` both call the dispatcher, which:

1. counts **in-flight** leads (DB = source of truth) → `available_slots`,
2. reserves a per-minute budget from a **Redis token bucket** (atomic Lua),
3. atomically claims that many `pending`, due leads
   (`SELECT … FOR UPDATE SKIP LOCKED`), flips them to `in_flight`, and enqueues
   `place_call` for each.

A lead occupies a slot from reservation until a webhook (or a failed dispatch)
resolves it — so concurrency can never exceed the cap, even across overlapping
ticks. No-answer / voicemail / busy outcomes are retried up to `max_attempts`
after `retry_delay_minutes`. Full detail in
[`TECHNICAL_DETAILS.md`](./TECHNICAL_DETAILS.md).

---

## Choosing the agent voice

**Settings → Agent voice** sets an **org-wide default** applied to *every*
outbound call via Vapi `assistantOverrides.voice` — one assistant, any voice, no
re-provisioning. Choosing **Assistant default** clears the override so calls fall
back to the voice configured on the assistant in the Vapi dashboard.

The dropdown lists Vapi's built-in voices (provider `vapi`); edit the
`VAPI_VOICES` list in `apps/dashboard/views.py` to match your Vapi account, or
wire it to Vapi's voice-list API.

---

## Testing

```bash
uv run pytest          # full suite
uv run ruff check .    # lint
```

The suite covers CSV import (normalization, duplicates, row errors), dispatch
slot math + Redis rate limiting + the "never exceed concurrency" invariant, the
Vapi client & idempotent provisioning (HTTP mocked with `respx`), webhook secret
verification + transcript persistence + idempotency, and the voice override.
Tests need Postgres + Redis running.

---

## Troubleshooting

| Symptom | Cause & fix |
|---------|-------------|
| Call ends instantly, `endedReason: …outbound-sip-403-forbidden` | Twilio rejected Vapi's SIP `INVITE`. Add **Vapi's SBC IPs** to the trunk **Termination IP ACL** (and/or set credential-list auth matching `TWILIO_SIP_*`). |
| Calls to a country never connect | Enable that country under Twilio **Voice → Geo Permissions**. |
| Trial account can't reach a number | Twilio trials only call **verified** numbers — verify it first. |
| `Unknown command: provision_vapi` | Ensure `apps.vapi` is in `INSTALLED_APPS` and run via `uv run python manage.py …`. |
| Vapi `400` on provisioning (SIP gateway / model / messages) | Termination URI must be a host (not IP) with `inboundEnabled:false`; model must be a valid Vapi id. See `apps/vapi/schemas.py`. |
| Empty transcript/summary after a call | The call never connected (e.g. SIP 403). Fix the connection first; a connected call posts the report to `/webhooks/vapi/`. |
| Webhook returns `401` | `X-Vapi-Secret` doesn't match `VAPI_WEBHOOK_SECRET`, or the secret is unset. |
| Status/transcript not updating in dashboard | Worker/beat not running, or `PUBLIC_WEBHOOK_BASE_URL` not reachable / not re-provisioned with `--force`. |
