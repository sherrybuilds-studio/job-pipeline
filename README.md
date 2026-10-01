# job-pipeline

Finds working-student roles, drops the ones the candidate cannot honestly apply to, and prepares a one-page CV and a cover letter that an independent reviewer pass must clear with zero unsupported claims.

This is a cleaned, profile-driven port of the job pipeline its author runs privately. Everything personal lives in one YAML file that is not committed; the repository ships a made-up candidate, Alex Example, so it runs and tests out of the box.

Nothing is ever sent to an employer. A person sends every application, and the tracker records a send only after the send gate passes.

## What it does

The pipeline has two passes.

**1. The daily run** (`python -m pipeline daily`, meant for cron). It reads Adzuna, Arbeitnow, the Bundesagentur Jobbörse, Himalayas, RemoteOK and any pages you list for Firecrawl, drops postings that fail a hard bar, scores the rest with transparent keyword rules, writes a reviewed letter and a tailored CV for each strong match, and sends a Telegram digest. When nothing clears the bar, it widens the search a bounded number of times before it reports an empty day.

**2. The sourcing pass** (`python -m pipeline source`, on demand). It pulls company career boards directly from Greenhouse, Ashby, Lever, Personio, SmartRecruiters and Join, adds public search results from LinkedIn's guest pages, XING and Indeed, and merges duplicates so the employer's own ATS row wins. Then it drops roles the candidate cannot honestly apply to, before anything is scored, and checks that the employer's own posting page is still open. The result is a report with three tables: READY, WEAK and DROPPED, each drop with its reason. From that report, `python -m pipeline kit build` writes the applications: a one-page CV tailored to the posting and a cover letter that an independent reviewer must clear at zero unsupported claims.

The board registry starts empty and grows from a list you seed, from ATS links in XING apply buttons, and from your own application history. In the author's deployment it grew to 83 career boards.

## Architecture

```text
DAILY RUN  python -m pipeline daily

  Adzuna · Arbeitnow · Bundesagentur · Himalayas · RemoteOK · Firecrawl pages
      │
      ▼
  scrape ─▶ match ─┬─ strong ──▶ write ──▶ notify ─────────▶ Telegram digest
                   │               └─ per match: tailored CV, letter (draft → review → revise → regex gate),
                   │                  output lint, ATS parser check, tracker row
                   ├─ weak ────▶ widen ─▶ scrape      lower the bar, read deeper, bounded
                   └─ no_match ▶ notify_empty

SOURCING PASS  python -m pipeline source

  ATS boards from the registry         public search              aggregators
  Greenhouse · Ashby · Lever           LinkedIn guest · XING      Arbeitnow · Adzuna
  Personio · SmartRecruiters · Join    Indeed via Firecrawl       Bundesagentur
      └────────────────────────────────────┬────────────────────────────┘
                                           ▼
                        merge duplicates (the ATS row wins)
                                           ▼
   hard bars → role type → non-tech → full-time only → location → seen before → employer page open?
                                           ▼
            data/sourcing/<date>.md   READY · WEAK · DROPPED (every drop with its reason)

KITS  python -m pipeline kit build data/sourcing/<date>.json

   tailored CV ─▶ letter draft ─▶ independent review ─▶ revise (bounded) ─▶ clearance
   CLEARED: zero unsupported claims, regex gate clean, no slash, under 250 words
      ─▶ letter PDF ─▶ output lint ─▶ kit note ─▶ a person sends ─▶ tracker sent (send gate)
```

## Key design decisions

- **Hard bars before scoring.** A posting that requires German at C1 or above, a master's degree, or a finished degree is dropped before it is scored, digested or kitted, and the reason is logged. A stated preference ("C1 von Vorteil") is not a bar, a posting that also accepts a bachelor is not a master's bar, and a preference one sentence earlier cannot rescue a requirement in the next one. The profile switches each bar on or off.
- **The posting's own words decide the role type.** A Werkstudent marker beats an internship, which beats a full-time role; a posting that says nothing about students is dropped. A navigation item like "Werkstudenten" on a careers page is never evidence, and a student word in a made-up kit title cannot rescue an internship posting.
- **Liveness on the employer's own page.** ATS board rows are live by construction. Anything else is followed through its redirects to the page the role actually applies on, and that page is read for closed markers (visible text only, since some ATS ship "position closed" strings in their scripts). A URL that only ever lands on an aggregator stays unverified and never reaches READY.
- **Honesty gate at zero unsupported claims.** Every letter goes draft, then an independent review in a fresh context, then a bounded revision, then a regex gate as the final word. The reviewer returns a requirement-coverage table and every claim the facts do not support, and a deterministic audit adds every number that appears in neither the profile nor the posting. A kit clears only with zero unsupported claims; otherwise no letter PDF is rendered. The writer may cite only the profile's verified claims.
- **Postings are untrusted input.** Posting text is fenced in tags and declared data in both prompts, and a closing tag inside a posting cannot end the fence, so instructions planted in a job ad are not followed.
- **Output lint after generation.** The letter and the CV PDF on disk are read back and checked for slashes, machine tells ("I am thrilled", "delve into"), skill claims with no project behind them, skills the profile disclaims, placeholders and the profile's banned claims. A hit marks the kit needs-human-fix; nothing is regenerated behind your back.
- **Send gate.** `python -m pipeline tracker sent <company>` stamps a send only when the employer page was verified live within 24 hours, no hard bar appears in the archived posting, the archived posting offers an accepted role type, and the lint passes. There is no override flag. Follow-ups are due on day 4 after the send, rolled to Monday when that falls on a weekend, and are drafted, never e-mailed.
- **One profile, no personal data in code.** Name, contact details, education, languages, availability, role types, hard bars, skills, projects, CV wording, letter closing, banned claims, scoring weights and search queries all come from `profile.yaml`, which is validated on load with every problem named.

## Features

- Pullers for Greenhouse, Ashby, Lever, Personio, SmartRecruiters and Join with polite rate limiting: Personio boards run one at a time with spacing, a 429 is retried once, and two in a row stop Personio for the run.
- Public search without a login: LinkedIn guest search and job pages, XING's public search state, Indeed through Firecrawl. A rate limit stops that source for the run and is written into the report.
- Duplicate folding across sources by URL and by normalised company and title (legal suffixes, gender markers, requisition ids).
- Dedup against everything ever kitted, parked, dropped or sent, including kit notes moved into a `_parked` folder.
- Transparent rule-based scoring with weights from the profile; no model call to rank postings.
- A one-page, ATS-readable CV (two frames, real text, no tables) tailored per posting, with a built-in PDF text reader so the CV that is actually on disk gets checked.
- Cover letter PDFs in the same design family, rendered only for cleared kits.
- A verbatim posting archive per application, an outcome log per stage, and a funnel by source.
- Supabase tracker with SQL migrations, a summary report and a follow-up schedule.
- Telegram digest over the plain Bot API, split on blank lines to stay under the message limit.
- Model calls through the Anthropic API or any OpenAI-compatible endpoint, chosen by environment variable.
- A daily run modelled as a small graph with a bounded retry branch, testable node by node.

## Quickstart

Requires Python 3.11 or newer.

```bash
git clone <this repository's URL> job-pipeline
cd job-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"            # add the firecrawl extra for Firecrawl sources: pip install -e ".[dev,firecrawl]"

cp profile.example.yaml profile.yaml   # then describe yourself: every field is commented
cp .env.example .env                   # then fill in the keys for the services you use

python -m pipeline cv                      # render your master CV to output/
python -m pipeline daily --dry-run         # scrape and score; writes nothing, sends nothing, needs no keys
python -m pipeline source --budget 300     # sourcing pass → data/sourcing/<date>.md and .json
python -m pipeline source --show           # print today's READY table again
python -m pipeline kit plan  data/sourcing/<date>.json --limit 5
python -m pipeline kit build data/sourcing/<date>.json --limit 5 --batch batch-1
python -m pipeline lint                    # re-lint every unsent kit after you edit one
python -m pipeline review data/applications/<folder>   # re-run the independent review on one kit
python -m pipeline send-gate "<company>"   # dry check before you send
python -m pipeline tracker sent "<company>"    # after you sent it yourself
python -m pipeline archive outcome "<company>" replied "asked for a call"
python -m pipeline followups               # drafts for follow-ups that are due
python -m pipeline summary                 # counts by stage from the tracker
python -m pipeline --help                  # every command
```

Seed the board registry with boards you care about (see `scripts/seed_boards.example.csv` for the format):

```bash
python scripts/seed_ats_boards.py my_boards.csv --dry-run   # probe and print
python scripts/seed_ats_boards.py my_boards.csv             # write data/sources/ats_boards.json
```

For the tracker, create a Supabase project and run `migrations/001_applications.sql`, `002_ats_tracking.sql` and `003_contact.sql` in order in its SQL editor. Without Supabase the pipeline still runs: dedup treats every posting as new and inserts are skipped with a warning.

To run the daily pass every morning, add a cron line such as:

```text
0 8 * * * cd /path/to/job-pipeline && .venv/bin/python -m pipeline daily >> data/daily.log 2>&1
```

Everything the pipeline writes stays in `data/` and `output/`, both git-ignored: one folder per application under `data/applications/`, sourcing reports under `data/sourcing/`, the board registry, the run history in `data/top_matches.json` and the follow-up schedule in `data/state/followups.json`.

## Configuration

### Profile (`profile.yaml`)

| Field | What it controls |
| --- | --- |
| `name`, `first_name`, `email`, `phone`, `location`, `website`, `code_url` | CV header, letter header, file names such as `<Name>_CV.pdf`, follow-up signature |
| `education`, `languages`, `availability`, `work_authorization`, `facts` | the facts every model call may use; language levels drive the CV's proficiency dots |
| `search` | city, country terms, accepted role types (`werkstudent`, `internship`), target titles, remote regions, the queries for every source, Firecrawl pages |
| `hard_bars` | `german_c1`, `masters_degree`, `completed_degree`, each on or off |
| `core_skills`, `not_my_skills`, `skill_aliases` | the only skills a CV may show, the skills the lint flags when claimed, and how posting tool names map to your skills |
| `projects` | descriptions, stack, the one evidenced number, CV wording, and the patterns that map a posting to the project (one strong hit or two supporting hits) |
| `experience` | the experience section of the CV |
| `verified_claims` | the only numbers the writer may cite, each with where it can be checked |
| `cover_letter_hooks`, `evidence_anchors` | openings the writer may use, and the words that tie a skill claim to something real |
| `honesty` | `banned` and `letter_only` patterns: claims that may never appear |
| `letters` | persona line, the availability sentence, the kit closing paragraph, sign-off, extra writer rules |
| `cv` | headline, availability card, education line, how many projects get the full write-up, skill groups and chip labels |
| `scoring` | keyword groups a posting must touch, skill weights, and the language, location and role bonuses |

### Environment (`.env`)

| Variable | Used by |
| --- | --- |
| `LLM_PROVIDER` | `anthropic` (default) or `openai` for any OpenAI-compatible endpoint |
| `LLM_MODEL`, `LLM_MAX_TOKENS` | model name (the anthropic default is claude-sonnet-5) and reply cap |
| `ANTHROPIC_API_KEY` | letter writer and reviewer with the anthropic provider |
| `LLM_BASE_URL`, `LLM_API_KEY` | letter writer and reviewer with the openai provider |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Adzuna source |
| `FIRECRAWL_API_KEY` | Firecrawl pages and Indeed |
| `SUPABASE_URL`, `SUPABASE_KEY` | tracker |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | digest and follow-up drafts |
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD` | `scripts/mailcheck.py`, a read-only inbox pull |

Set these in the shell, not in `.env`: `JOB_PIPELINE_PROFILE` (another profile file), `JOB_PIPELINE_DATA` (another data directory), `JOB_PIPELINE_HOME` (another repository root) and `CV_FONT_DIR` (where the DejaVu fonts live; without them the CV falls back to the built-in PDF fonts).

## Tests

```bash
pytest -q
ruff check .
```

The suite runs offline against the made-up profile: model calls, job boards, Telegram and Supabase are fakes or mock transports, and the CV and letter PDFs are rendered and read back for real. CI runs the same two commands on every push.

## Project layout

```text
pipeline/            the package; python -m pipeline <command>
  agent.py           daily run wiring          job_graph.py, graph.py   its control flow
  scraper.py         daily sources             ats_boards.py            career-board pullers and registry
  public_boards.py   LinkedIn, XING, Indeed    sourcing.py, source.py   the sourcing pass and its CLI
  matcher.py         hard bars, gates, score   roletype.py              employment type of a posting
  liveness.py        employer-page check       kit.py                   application kits
  writer.py          letter writer             reviewer.py              independent review, number audit
  honesty.py         regex gate                lint.py, pdftext.py      output lint, PDF text reader
  cv_builder.py      one-page CV               cv_copy.py               CV wording from the profile
  letter_pdf.py      letter PDF                parser_check.py          ATS self-check
  send_gate.py       send gate                 tracker.py               Supabase tracker and CLI
  archive.py         posting archive, outcomes followup.py              follow-up schedule and drafts
  notifier.py        Telegram digest           telegram.py, llm.py      HTTP clients
  profile.py         profile loader            config.py                paths and environment
migrations/          SQL for the tracker table
scripts/             board seeding, read-only inbox pull
tests/               offline tests and anonymised board fixtures
```

## Limitations

- It is tuned for working-student searches in Germany: the vocabulary is German and English, the hard bars are German-market rules, and several sources (Bundesagentur, XING, de.indeed.com) are German. Another market needs vocabulary changes in code, not only in the profile.
- Matching is keyword rules. A posting phrased in an unusual way can be scored low or mapped to no project.
- The public-search parsers read page structures that their owners change without notice; when that happens a source returns nothing until the parser is updated. Only public pages are read, without a login and at a slow pace; check each site's terms before you run them.
- Listings that only exist on an aggregator can never be verified, so they never reach READY, even when the role is real.
- The reviewer is a model and can miss an unsupported claim. The number audit, the regex gate and the lint only catch what they are written to catch, which is why a person reads and sends every letter.
- The board registry starts empty; the sourcing pass is only as wide as the boards you seed.
- Supabase is the only tracker backend, and follow-ups are drafts you send yourself.

## License

MIT, see [LICENSE](LICENSE).

## Contact

Shehryar Irfan · sherry.aiops@gmail.com · [sherrybuilds.com](https://sherrybuilds.com)
