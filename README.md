# job-pipeline

> **Status (2026-08-25):** public snapshot (May 2026). The current version — a
> five-stage graph (scrape → dedup → match → research/enrich → cover letters →
> Telegram digest) with a shared core — lives in a private platform monorepo. It
> ran daily on cron; **last run 2026-08-20**, parked on 2026-08-23 to focus on the
> AI phone receptionist. Live products + dated evidence: [sherrybuilds.com](https://sherrybuilds.com).

Autonomous daily pipeline that finds AI jobs in Berlin, scores them with Claude, writes a tailored cover letter for each match, saves everything to Supabase, and sends a Telegram digest — all without manual input.

```
Adzuna API → score with Claude → write cover letter → Supabase → Telegram
```

Runs at 08:00 every day via PM2 cron.

---

## What it does

1. **Scrapes** the Adzuna Jobs API for Berlin AI roles across three search queries: `AI Developer`, `AI Automation Engineer`, `AI Consultant`
2. **Deduplicates** against Supabase — only new listings are processed
3. **Scores** each job 1–100 using Claude via OpenRouter, based on skill match, seniority, location, and hours (targets werkstudent / junior / part-time)
4. **Filters** out jobs below the score threshold (default 60) and dealbreakers (senior-only, full-time-only, non-Berlin non-remote)
5. **Writes** a tailored cover letter (≤300 words) per match using Claude
6. **Saves** each job + cover letter to Supabase with status `pending`
7. **Sends** a Telegram digest of the top 3 matches

---

## Stack

| Layer | Tool |
|---|---|
| Job data | [Adzuna API](https://developer.adzuna.com) (free tier) |
| Scoring & writing | Claude via [OpenRouter](https://openrouter.ai) (`anthropic/claude-3.5-haiku` in this snapshot; Haiku 4.5 now — 3.5 was retired 2026-07) |
| Storage | [Supabase](https://supabase.com) |
| Notifications | Telegram Bot API via `python-telegram-bot` |
| HTTP | `httpx` (async) |
| Scheduling | PM2 cron (`0 8 * * *`) |

---

## Project structure

```
agent.py        — orchestrator: runs the full pipeline
scraper.py      — async Adzuna fetcher + normaliser
matcher.py      — Claude scoring with dealbreaker/bonus logic
writer.py       — Claude cover letter generation
tracker.py      — Supabase insert / dedup
notifier.py     — Telegram digest
profile.py      — single source of truth for candidate profile
cv_builder.py   — generates sherry_cv.pdf from profile (reportlab)
setup.sql       — Supabase schema (run once)
```

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/sherrybuilds-studio/job-pipeline.git
cd job-pipeline
pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env
```

Fill in all seven keys:

| Variable | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `SUPABASE_URL` | Supabase project settings → API |
| `SUPABASE_KEY` | Supabase project settings → API (anon key) |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram |
| `TELEGRAM_CHAT_ID` | Send a message to your bot, then call `getUpdates` |
| `ADZUNA_APP_ID` | [developer.adzuna.com](https://developer.adzuna.com) (free) |
| `ADZUNA_APP_KEY` | Same as above |

### 3. Supabase schema

Run `setup.sql` once in the Supabase SQL editor:

```sql
-- creates the applications table with score, cover_letter, and status columns
```

### 4. Test without side effects

```bash
python3 agent.py --dry-run
```

Dry run scrapes and scores jobs, previews cover letters in the log, and skips all Supabase writes and Telegram.

### 5. Live run

```bash
python3 agent.py
```

### 6. Schedule with PM2

```bash
pm2 start "python3 agent.py" --name cv-job-hunter --cron "0 8 * * *" --no-autorestart
pm2 save
```

Fires daily at 08:00. Logs available via `pm2 logs cv-job-hunter`.

---

## CLI options

```
python3 agent.py [--dry-run] [--min-score N] [--pages N]

--dry-run       Skip Supabase writes and Telegram (safe for testing)
--min-score N   Minimum Claude score to keep a job (default: 60)
--pages N       Adzuna pages to fetch per query (default: 2, each page = 20 results)
```

---

## Candidate profile

Edit `profile.py` to change the target candidate. Key fields:

```python
target_roles        # job titles to target in cover letters and scoring
location_filter     # "Berlin, Germany" — jobs outside this are penalised
max_hours_per_week  # 20 — prefers werkstudent / part-time listings
salary_range        # shown in cover letters when the listing specifies a range
skills              # used by Claude to evaluate match quality
portfolio           # projects referenced in cover letters
```

---

## Supabase schema

```sql
applications (
  id           uuid  primary key
  job_title    text
  company      text
  url          text  unique        -- used for deduplication
  score        int                 -- Claude score 1–100
  cover_letter text
  status       text  default 'pending'   -- pending | applied | rejected
  applied_at   timestamptz
)
```

Update `status` to `applied` or `rejected` manually in the Supabase dashboard as you work through the list.

---

## Regenerate CV

```bash
python3 cv_builder.py
```

Outputs `sherry_cv.pdf` from the profile dataclass using reportlab.
