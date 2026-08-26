# dontvibecode API

Django REST backend for dontvibecode.

## Architecture

The app is still Django + Gemini + Google Sign-In. The paid Google Cloud pieces are gone:

| What | Provider | Why |
|---|---|---|
| Django container | [Koyeb](https://www.koyeb.com) free web instance | Same Docker image; sleeps after 1 idle hour |
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
2. Create a Cloudflare R2 bucket, make a public development URL or custom domain, and apply `cors.json` on the bucket.
3. Keep using a Gemini API key from [Google AI Studio](https://aistudio.google.com/). Do not enable Vertex AI / GCP billing for this.
4. Deploy this repo as a Docker web service on Koyeb. Set `PORT` automatically, paste the env vars from `.env.example`, and run `python manage.py migrate` after the first deploy (`build.sh` already does this if you use it as the build command).
5. Point the frontend `NEXT_PUBLIC_API_URL` at the Koyeb URL.

Existing Google users keep working: the backend still verifies Google ID tokens. Existing Cloud SQL data can be dumped and restored into Neon when you are ready; this repo change does not move data for you.
