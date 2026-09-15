# dontvibecode API

Django REST backend for dontvibecode.

## Architecture

The app is still Django + Gemini + Google Sign-In. The paid Google Cloud pieces are gone:

| What | Provider | Why |
|---|---|---|
| Django container | [Render](https://render.com) free web service | Same Docker image; sleeps after 15 idle minutes |
| PostgreSQL | [Neon](https://neon.tech) free plan | Permanent free Postgres; Django already spoke Postgres |
| Profile pictures | [Cloudflare R2](https://developers.cloudflare.com/r2/) free tier | Drop-in replacement for GCS signed uploads |
| Lessons / chat | Gemini Developer API (Google AI Studio key) | Already in the app; free-tier models, no Cloud Run/Vertex |
| Login | Google Sign-In | Free identity API, not Cloud billing |

A single free host that also gives durable Postgres, object storage, and this Django app does not exist. The three new providers above are the smallest split that stays free and leaves the product code you already understand in place.

## Local development

Use the same Neon database as production. Set `DATABASE_URL` in `.env` (see `.env.example`). There is no local Postgres.

```bash
python -m venv .venv
.venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp .env.example .env     # then fill in values
python manage.py migrate
python manage.py runserver
```

## Production (one-time)

1. Create a Neon project and copy the connection string into `DATABASE_URL`.
2. Create a Cloudflare R2 bucket, make a public development URL or custom domain, and apply `cors.json` on the bucket (R2 → the bucket → Settings → CORS Policy). Profile pictures are uploaded by the browser straight to R2, so **every origin the frontend is served from must be listed in `cors.json`**. Adding a new frontend URL without re-applying this file makes uploads fail with a CORS error that never reaches Django.
3. Keep using a Gemini API key from [Google AI Studio](https://aistudio.google.com/). Do not enable Vertex AI / GCP billing for this.
4. Deploy this repo as a Docker web service on Render. Set `PORT` automatically, paste the env vars from `.env.example`, and run `python manage.py migrate` after the first deploy (`build.sh` already does this if you use it as the build command).
5. Point the frontend `NEXT_PUBLIC_API_URL` at the Render URL.

Existing Google users keep working: the backend still verifies Google ID tokens. Existing Cloud SQL data can be dumped and restored into Neon when you are ready; this repo change does not move data for you.

## Voice (ElevenLabs)

AI replies and lessons can be read aloud. Premium voices come from ElevenLabs. Whenever they can't be used (no key, allowance spent, ElevenLabs down), the API returns the prepared text and the frontend reads it with the browser's built-in voice, so the feature degrades instead of breaking.

| Endpoint | What it does |
|---|---|
| `POST /api/chat/speech/<message_id>/` with `{"scope": "summary" or "full"}` | A 6-hour link to the narration plus timing marks for highlighting. Generates the audio first if it doesn't exist yet. |
| `GET /api/chat/speech/voices/` | The voices the picker offers, and whether premium voices are available. |

- **Generated once.** Each clip is a `SpeechClip` row plus an MP3 in R2 under `speech/`. Replays are free and never count against allowances.
- **Allowances.** Per-user characters per rolling day (`TIER_SPEECH_DAILY_CHARACTERS` in `chatbot/services/user.py`) and an app-wide rolling 30-day budget (`ELEVENLABS_MONTHLY_CHARACTER_BUDGET`).
- **Measured.** `python manage.py speech_stats` reports characters spent and how often stored audio saved a generation.

Setup: create an API key at elevenlabs.io and set `ELEVENLABS_API_KEY` on Render only; it must never reach the frontend. Optionally shortlist voices with `ELEVENLABS_VOICE_IDS`. Free-plan audio is non-commercial and must credit ElevenLabs, which the UI does next to the voice controls.
