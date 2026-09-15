# dontvibecode — backend

Django REST API for [dontvibecode](https://dontvibecode.com): an AI coding tutor that decides, per message, whether to answer directly or hand off to a slower model that builds a full lesson with exercises. Two-tier LLM routing, atomic token metering, idempotent Stripe billing, and text-to-speech narration that shares one caching/allowance layer across every voice request.

## Stack

Django 5 + DRF, PostgreSQL (Neon), Gemini Developer API, Stripe, Cloudflare R2, ElevenLabs. Deployed as a Docker image on Render; `gunicorn` with `gevent` workers so a container can hold many slow, mostly-idle LLM/TTS requests concurrently instead of blocking a worker per request.

## Architecture

```
Client ──▶ GoogleIDTokenAuthentication ──▶ APIView ──▶ Service ──▶ Model
                                                  │
                                                  ├─ LLMService     (Gemini: router → instructor)
                                                  ├─ UserService    (membership + token ledger)
                                                  ├─ SpeechService  (ElevenLabs narration)
                                                  ├─ ObjectStorageService (R2)
                                                  └─ payment services (Stripe)
```

Views hold no business logic — they validate input with a serializer, call one service method, and translate the result to a response. Every state transition that matters (a token charge, a membership change, a narration clip's cache/lock) lives in a service method with a docstring explaining *why*, not just what.

### Two-tier LLM routing

Every message first hits a **router** (`gemini-3.6-flash`, cheap, low-latency) that either answers directly — greetings, follow-ups, hints, quick syntax questions — or sets `redirect: true` and hands a compact `prepared_context` summary to an **instructor** call that builds a full lesson with exercises. Most turns never reach the instructor, which is where the token cost lives; the docstrings in `chatbot/prompts.py` and `LLMService.respond_streaming` spell out the decision boundary the router is asked to hold.

The router's long, static system instruction is cached server-side via Gemini's context caching (`LLMService._get_router_cache`), refreshed proactively before expiry, and falls back to sending it inline if cache creation or lookup ever fails — a cost optimization is not allowed to become a reliability dependency. Both stages stream thought-summaries and the final payload to the client over one SSE connection (`ChatStreamAPIView`), so the UI can show "thinking" progress instead of a blank spinner during a multi-second multi-call turn.

### Token economy

`UserService.consume_tokens` is one conditional `UPDATE … WHERE token_used <= token_limit - amount`: the affordability check and the charge are the same atomic statement, so two concurrent requests can't both spend the last of a user's balance. Membership transitions (Pro renewal, expiry, free-tier refill) run the same way — a `WHERE` clause that only matches when the transition is actually due — so `reconcile_membership` can run on every authenticated request for free instead of needing a cron job, and concurrent requests can't double-refill.

### Idempotent billing

Stripe webhooks are the only path that can grant or revoke Pro access — nothing in the request path trusts client-reported payment state. `ProcessedStripeEvent` (a unique event-id column) makes replayed webhooks a no-op, and `create_subscription` refuses to create a second active subscription for a customer who already has one, so a duplicate webhook delivery can't double-bill or strip a paying user back to free. See `chatbot/tests/test_payment_webhooks.py`.

### Narration (ElevenLabs), and how it fails safe

`SpeechService.get_clip` is the shape worth reading if you only read one service in this repo: ownership check → cache lookup → allowance check → claim-and-generate. A `SpeechClip` row does three jobs at once — it's the **cache** (a `READY` row is replayed for free), the **lock** (a unique constraint means only the request that wins the insert calls ElevenLabs; a second concurrent request for the same clip polls and reuses the winner's result instead of paying twice), and the **ledger** the daily/monthly character allowances are summed from.

Every failure mode — no API key configured, allowance exhausted, text too long, ElevenLabs erroring — returns the same shape: the prepared, markdown-stripped text, so the frontend can read it aloud with the browser's built-in speech synthesis instead of the feature simply breaking. `speech_text.py` owns turning a lesson's markdown into that text; it numbers lines the same way the frontend's markdown renderer does (documented in both files), because ElevenLabs' character-level timing data is matched back to those line numbers to drive read-along highlighting.

### Object storage

`ObjectStorageService` wraps R2's S3-compatible API for two unrelated uses: user-uploaded profile pictures (signed PUT URLs, so images never transit this server) and server-generated narration audio (signed GET URLs, since that audio is private to a conversation). Deleting a conversation cascades to its `SpeechClip` rows, and a Django signal deletes the matching R2 object — deferred until the enclosing transaction actually commits, so a delete that rolls back doesn't orphan-delete audio that's still referenced.

## Local development

Uses the same Neon database as production — there is no local Postgres.

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows; source .venv/bin/activate elsewhere
pip install -r requirements.txt
cp .env.example .env        # fill in DATABASE_URL, GEMINI_API_KEY, GOOGLE_CLIENT_ID at minimum
python manage.py migrate
python manage.py runserver
```

`poetry.lock` also tracks dependencies (`poetry lock` after changing `pyproject.toml`); Docker and CI install from `requirements.txt`, so that file is the one that must stay authoritative.

## Testing

```bash
python manage.py test chatbot --settings=api.settings
```

76 tests, no network calls — Gemini, Stripe, ElevenLabs and R2 are all mocked at the client boundary, so the suite runs in well under a second and exercises the actual concurrency-sensitive paths (double-webhook delivery, two requests racing to generate the same narration clip, a token charge racing a balance check) rather than asserting against mocks that assume the race away. CI (`.github/workflows/ci.yml`) runs the same suite against a real Postgres 16 container, plus `makemigrations --check` so a model change without its migration fails the build instead of production.

## Deployment

| Piece | Provider | Why |
|---|---|---|
| App container | [Render](https://render.com) free web service | Same Docker image; sleeps after 15 idle minutes |
| PostgreSQL | [Neon](https://neon.tech) free plan | Durable managed Postgres |
| Object storage | [Cloudflare R2](https://developers.cloudflare.com/r2/) free tier | S3-compatible; no egress fees |
| LLM | Gemini Developer API (AI Studio key) | Free-tier models — not billed Vertex AI |
| Auth | Google Sign-In | ID-token verification only, no GCP billing |
| TTS | ElevenLabs (optional) | `ELEVENLABS_API_KEY` unset ⇒ narration falls back to the browser voice |

One-time setup:

1. Create a Neon project; put its connection string in `DATABASE_URL`.
2. Create an R2 bucket and apply `cors.json` to it (R2 → bucket → Settings → CORS Policy). Profile-picture uploads go browser → R2 directly, so **every origin the frontend is served from must be listed in `cors.json`** — a frontend URL added without re-applying this file fails uploads with a CORS error that never reaches Django.
3. Deploy this repo as a Docker web service on Render; paste the env vars from `.env.example`. `build.sh` runs `migrate` on deploy if used as the build command — otherwise run migrations manually after each deploy that adds one.
4. Point the frontend's `NEXT_PUBLIC_API_URL` at the Render URL.
5. Optional: set `ELEVENLABS_API_KEY` to turn on premium narration (see below).

### Voice (ElevenLabs)

| Endpoint | What it does |
|---|---|
| `POST /api/chat/speech/<message_id>/` `{"scope": "summary" \| "full"}` | A 6-hour link to the message's narration plus per-line timing marks. Generates the audio first if it doesn't already exist. |
| `GET /api/chat/speech/voices/` | The voices the picker may offer, and whether premium narration is configured at all. |

`ELEVENLABS_API_KEY` is Render-only and must never reach the frontend. `ELEVENLABS_VOICE_IDS` optionally shortlists voices; `TIER_SPEECH_DAILY_CHARACTERS` (in `chatbot/services/user.py`) and `ELEVENLABS_MONTHLY_CHARACTER_BUDGET` cap spend per user and app-wide. `python manage.py speech_stats` reports characters spent and the cache hit rate. Free-plan ElevenLabs audio is non-commercial and requires attribution, which the frontend renders next to the voice controls.
