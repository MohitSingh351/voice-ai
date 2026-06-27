# Voice AI — Architecture

This document is the visual + structural map of the system. For setup and usage
see [`README.md`](./README.md); for implementation-level detail see
[`TECHNICAL_DETAILS.md`](./TECHNICAL_DETAILS.md).

> All diagrams are [Mermaid](https://mermaid.js.org/). They render natively on
> GitHub and in most Markdown viewers.

---

## 1. The core idea

**Vapi runs the voice conversation over your own Twilio Elastic SIP Trunk
(BYO-SIP). This Django backend never touches audio.** Its job is orchestration
and bookkeeping:

- decide **which** leads to call and **how fast** (dispatch + throttling),
- hand each call to Vapi over REST (`POST /call`),
- receive **webhooks** with status, transcript, recording and outcome,
- present everything in a login-gated dashboard and a REST API.

```
CSV / manual ─▶ Lead ─▶ Campaign(throttle) ─▶ Celery dispatch ─▶ Vapi POST /call ─▶ Twilio SIP ─▶ lead's phone
                                                    ▲                                     │
                                                    └──────── webhook (status / report) ──┘
```

---

## 2. System context

```mermaid
flowchart LR
    user([User / Sales Ops])
    subgraph browser[Browser]
        dash[HTMX Dashboard]
    end
    subgraph backend[Voice AI Backend]
        web[Django + DRF<br/>web process]
        worker[Celery worker<br/>+ beat]
        db[(Postgres)]
        redis[(Redis)]
    end
    vapi[[Vapi<br/>STT · LLM · TTS · SIP]]
    twilio[[Twilio Elastic<br/>SIP Trunk]]
    phone([Lead's phone])

    user --> dash --> web
    web <--> db
    web --> redis
    worker <--> db
    worker <--> redis
    worker -- "POST /call, /credential,<br/>/phone-number, /assistant" --> vapi
    vapi -- "webhook: status-update,<br/>end-of-call-report" --> web
    vapi -- "SIP INVITE" --> twilio
    twilio -- PSTN --> phone
    phone -. audio .- vapi
```

Two backend processes share Postgres and Redis:

| Process | Responsibility |
|---------|----------------|
| **Web** (`runserver` / gunicorn) | Dashboard, REST API, inbound Vapi webhooks |
| **Worker + beat** (Celery) | Dispatch loop, placing calls, retries |

---

## 3. Component / module map

```mermaid
flowchart TB
    subgraph config[config/]
        settings[settings: base/dev/prod]
        celeryapp[celery.py<br/>app + beat schedule]
        urls[urls.py]
    end

    subgraph apps[apps/]
        org[organizations<br/>Organization]
        leads[leads<br/>Lead + csv_import]
        camp[campaigns<br/>Campaign · CampaignLead<br/>dispatch · throttle · lifecycle]
        calls[calls<br/>Call + Celery tasks]
        vapi[vapi<br/>VapiClient · schemas · provisioning]
        webhooks[webhooks<br/>secret-verified handlers]
        dash[dashboard<br/>HTMX views + templates]
    end

    leads --> org
    camp --> leads
    camp --> org
    calls --> camp
    calls --> vapi
    webhooks --> calls
    webhooks --> camp
    dash --> camp
    dash --> leads
    dash --> org
    vapi --> org
    celeryapp --> calls
```

Dependency direction is one-way and layered: `organizations` is the base,
everything that places or records calls depends on `vapi` and `campaigns`, and
`dashboard` / `webhooks` sit on top. The `vapi` app is the **only** place that
talks HTTP to Vapi.

---

## 4. Placing one call — sequence

```mermaid
sequenceDiagram
    autonumber
    participant Beat as Celery Beat
    participant Tick as tick_campaigns
    participant Disp as dispatch service
    participant Redis
    participant DB as Postgres
    participant Place as place_call task
    participant Vapi
    participant Twilio

    Beat->>Tick: every CAMPAIGN_TICK_SECONDS
    Tick->>Disp: plan_and_reserve(campaign)
    Disp->>DB: count IN_FLIGHT → available_slots
    Disp->>Redis: reserve_call_budget (Lua token bucket)
    Redis-->>Disp: granted N
    Disp->>DB: SELECT … FOR UPDATE SKIP LOCKED<br/>claim N PENDING → IN_FLIGHT, attempts+1
    Disp-->>Tick: reserved CampaignLead ids
    loop each reserved lead
        Tick->>Place: place_call.delay(cl_id)
        Place->>Vapi: POST /call {phoneNumberId, assistantId,<br/>customer, variableValues, voice?}
        Vapi-->>Place: 201 {id, status:"queued"}
        Place->>DB: create Call(vapi_call_id, status=queued)
        Vapi->>Twilio: SIP INVITE (BYO trunk)
        Twilio-->>Phone: dials the lead
    end
```

If `POST /call` fails, the lead is released back to `FAILED` (retry scheduled)
or `EXHAUSTED` — the slot is never leaked.

---

## 5. Webhook / outcome — sequence

```mermaid
sequenceDiagram
    autonumber
    participant Vapi
    participant Hook as /webhooks/vapi/
    participant H as handlers
    participant DB as Postgres
    participant Tick as tick_campaigns

    Note over Vapi: call progresses & ends
    Vapi->>Hook: POST {message:{type, call, …}}<br/>X-Vapi-Secret header
    Hook->>Hook: hmac.compare_digest(secret)
    alt secret invalid
        Hook-->>Vapi: 401
    else valid
        Hook->>H: dispatch_event(message)
        alt type = status-update
            H->>DB: update Call.status / started_at
        else type = end-of-call-report
            H->>DB: store transcript, summary,<br/>recording, outcome, cost (idempotent)
            H->>DB: resolve CampaignLead<br/>(done / failed-retry / exhausted)
            H->>Tick: tick_campaigns.delay(campaign_id)<br/>(refill freed slot now)
        else unknown
            H-->>H: log + ignore
        end
        Hook-->>Vapi: 200 fast
    end
```

The webhook handler is **idempotent on `vapi_call_id`** (Vapi may redeliver) and
always returns `2xx` quickly so Vapi doesn't retry storm.

---

## 6. Dispatch & throttling logic

```mermaid
flowchart TD
    A[tick_campaigns] --> B{campaign RUNNING?}
    B -- no --> Z[skip]
    B -- yes --> C[available_slots =<br/>max_concurrent_calls minus count IN_FLIGHT]
    C --> D{slots > 0?}
    D -- no --> M[maybe_complete]
    D -- yes --> E[reserve_call_budget<br/>Redis Lua, per-minute window]
    E --> F[granted = min of slots, budget]
    F --> G{granted > 0?}
    G -- no --> M
    G -- yes --> H[SELECT FOR UPDATE SKIP LOCKED<br/>claim PENDING and due leads]
    H --> I[flip to IN_FLIGHT, attempts+1]
    I --> J[place_call.delay per lead]
    J --> M
    M --> K{any PENDING or IN_FLIGHT left?}
    K -- no --> N[status = COMPLETED]
    K -- yes --> Z
```

Two independent guards bound the call rate:

- **Concurrency** — derived from the DB (`IN_FLIGHT` count), never a counter.
- **Rate** — a Redis fixed-window token bucket per `campaign:{id}:rate:{minute}`.

A lead holds its slot from reservation until a webhook (or failed dispatch)
resolves it, so concurrency can never exceed the cap even with overlapping ticks.

---

## 7. Data model

```mermaid
erDiagram
    Organization ||--o{ Lead : has
    Organization ||--o{ Campaign : has
    Campaign ||--o{ CampaignLead : queues
    Lead ||--o{ CampaignLead : "appears in"
    CampaignLead ||--o{ Call : "attempts"

    Organization {
        int id PK
        string name
        string vapi_credential_id
        string vapi_phone_number_id
        string vapi_assistant_id
        string default_caller_id
        string default_voice_provider
        string default_voice_id
    }
    Lead {
        int id PK
        int organization_id FK
        string name
        string phone_e164
        string raw_phone
        json variables
        string source
        datetime created_at
    }
    Campaign {
        int id PK
        int organization_id FK
        string name
        string status
        string assistant_id
        string from_phone_number_id
        int max_concurrent_calls
        int calls_per_minute
        int max_attempts
        int retry_delay_minutes
        datetime started_at
        datetime completed_at
    }
    CampaignLead {
        int id PK
        int campaign_id FK
        int lead_id FK
        string status
        int attempts
        datetime next_attempt_at
    }
    Call {
        int id PK
        int campaign_lead_id FK
        string vapi_call_id UK
        string status
        string ended_reason
        text transcript
        text summary
        url recording_url
        json structured_outcome
        decimal cost
        json raw_end_report
    }
```

Constraints: `unique(organization, phone_e164)` on Lead, `unique(campaign,
lead)` on CampaignLead, `unique(vapi_call_id)` on Call. Every model carries an
`Organization` FK so single-tenant MVP can become multi-tenant without a rewrite.

---

## 8. State machines

### CampaignLead — the dispatch work queue

```mermaid
stateDiagram-v2
    [*] --> PENDING : added to campaign
    PENDING --> IN_FLIGHT : reserved by dispatcher (attempts+1)
    IN_FLIGHT --> DONE : call connected & ended
    IN_FLIGHT --> FAILED : no-answer / busy / error & attempts left
    IN_FLIGHT --> EXHAUSTED : no-answer/error & no attempts left
    IN_FLIGHT --> FAILED : place_call API error (retryable)
    FAILED --> PENDING : retry_failed after retry_delay_minutes
    FAILED --> EXHAUSTED : retry_failed & attempts ≥ max_attempts
    DONE --> [*]
    EXHAUSTED --> [*]
```

### Call — mirrors the Vapi call, driven by webhooks

```mermaid
stateDiagram-v2
    [*] --> QUEUED : created on POST /call
    QUEUED --> RINGING : status-update
    RINGING --> IN_PROGRESS : status-update
    QUEUED --> IN_PROGRESS : status-update
    IN_PROGRESS --> ENDED : end-of-call-report
    QUEUED --> ENDED : failed before connect
    ENDED --> [*]
```

`QUEUED · RINGING · IN_PROGRESS` are the **active** statuses (occupy a slot).

### Campaign

```mermaid
stateDiagram-v2
    [*] --> DRAFT : created
    DRAFT --> RUNNING : Start
    RUNNING --> PAUSED : Pause
    PAUSED --> RUNNING : Resume
    RUNNING --> COMPLETED : Stop / all work done
    PAUSED --> COMPLETED : Stop
```

---

## 9. Process & deployment topology

```mermaid
flowchart LR
    subgraph hostA[Web tier]
        gunicorn[gunicorn / runserver<br/>Django + DRF]
    end
    subgraph hostB[Worker tier]
        cworker[celery worker]
        cbeat[celery beat]
    end
    subgraph data[Stateful]
        pg[(Postgres)]
        rd[(Redis<br/>broker + rate buckets)]
    end
    tunnel{{Public HTTPS<br/>tunnel / ingress}}
    vapi[[Vapi]]

    gunicorn --- pg
    gunicorn --- rd
    cworker --- pg
    cworker --- rd
    cbeat --- rd
    vapi -- webhooks --> tunnel --> gunicorn
    cworker -- REST --> vapi
```

Redis is dual-purpose: **Celery broker/result backend** *and* the **rate-limit
token buckets**. Postgres is the single source of truth for call concurrency.

---

## 10. Directory map

```
voice-ai/
├── ARCHITECTURE.md          ← this file
├── README.md                ← setup + usage
├── TECHNICAL_DETAILS.md     ← implementation reference
├── manage.py
├── pyproject.toml           ← uv deps, pytest + ruff config
├── .env.example
├── config/
│   ├── settings/{base,dev,prod}.py
│   ├── celery.py            ← Celery app + beat schedule
│   ├── urls.py              ← admin · auth · /api · /webhooks · dashboard
│   ├── asgi.py / wsgi.py
├── apps/
│   ├── organizations/       ← Organization (Vapi ids, default voice)
│   ├── leads/               ← Lead, csv_import service, DRF api
│   ├── campaigns/           ← Campaign, CampaignLead
│   │   └── services/        ← dispatch · throttle · lifecycle
│   ├── calls/               ← Call model, Celery tasks (dispatch loop)
│   ├── vapi/                ← VapiClient, schemas, provisioning, command
│   ├── webhooks/            ← /webhooks/vapi/ + handlers
│   └── dashboard/           ← HTMX views, urls, templates, templatetags
├── templates/               ← base.html (sidebar shell) + registration/login
└── tests/                   ← pytest: csv, dispatch, vapi client, webhooks
```
