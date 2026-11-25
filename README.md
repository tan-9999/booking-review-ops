# Guest Review Operations Dashboard

Collects public Booking.com reviews for four Sydney properties, groups them into
operational topics (cleanliness, check-in, noise …), and presents them in a
dashboard built for non-technical operations staff.

| Property | Booking.com page |
|---|---|
| Olympic Paddington Budget Hotel | `hotel/au/olympic-paddington` |
| Azzurro Pod Hotel Potts Point | `hotel/au/venus-potts-point-sydney` |
| Azzurro Pod Hotel Central Sydney | `hotel/au/venus-surry-hills` |
| Azzurro Pod Hotel Darling Harbour | `hotel/au/chateau-de-venus` |

**Stack:** Python 3.12+ · Django 5 + Django REST Framework · SQLite (Postgres via `DATABASE_URL`) ·
Playwright · React 18 + TypeScript + Vite + Recharts.

---

## Contents
1. [Quick start (sample data, ~5 minutes)](#1-quick-start-sample-data-5-minutes)
2. [Collecting live reviews](#2-collecting-live-reviews)
3. [Architecture](#3-architecture)
4. [How review collection works](#4-how-review-collection-works)
5. [How the insights work](#5-how-the-insights-work)
6. [The dashboard](#6-the-dashboard)
7. [API](#7-api)
8. [Data quality controls](#8-data-quality-controls)
9. [Tests](#9-tests)
10. [Sample data](#10-sample-data)
11. [Known limitations and assumptions](#11-known-limitations-and-assumptions)
12. [What I would do next](#12-what-i-would-do-next)

---

## 1. Quick start (sample data, ~5 minutes)

You can try the whole dashboard **without scraping**, using the reviews collected
on 25 Nov 2026 in `sample_data/`.

**Prerequisites:** Python 3.12+ and Node 18+ (developed on Python 3.14 and Node 24, Windows).

**Terminal 1 (backend)**
```bash
python -m venv .venv
# Windows (Git Bash): source .venv/Scripts/activate   ·   cmd: .venv\Scripts\activate   ·   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional: defaults work without it

cd backend
python manage.py migrate
python manage.py load_sample_data
python manage.py runserver       # http://127.0.0.1:8000
```

**Terminal 2 (frontend)**
```bash
cd frontend
npm install
npm run dev                      # open http://localhost:5173
```

Vite forwards `/api` to Django, so no CORS setup is needed in development.
If Django runs on another port, point the proxy at it:
`API_TARGET=http://127.0.0.1:8001 npm run dev` (Git Bash / macOS / Linux) or
`set API_TARGET=http://127.0.0.1:8001 && npm run dev` (cmd).
The admin is at http://127.0.0.1:8000/admin/ (`python manage.py createsuperuser` first).

> "This week" is calculated from today's date. The sample data ends on 25 Nov 2026,
> so on later dates the current week will be empty; use **Last 12 months** or
> **Custom dates** in the dashboard to explore it.

---

## 2. Collecting live reviews

```bash
python -m playwright install chromium     # one-off browser download (~200 MB)
cd backend
python manage.py seed_properties          # creates/updates the 4 properties from properties.json
python manage.py scrape_reviews           # collects all active properties
```

A Chromium window opens and works through each property; leave it alone while it runs
(why a visible browser: see [limitations](#11-known-limitations-and-assumptions)).
A full first run takes about 5–10 minutes; daily incremental runs take about a minute.

| Option | Meaning |
|---|---|
| `--mode auto` (default) | Full backfill until one has completed successfully for a property, then incremental |
| `--mode full` / `--mode incremental` | Force a mode |
| `--property property-2` | One property (repeatable) |
| `--history-days 365` | How far back to collect (default 365; `0` = everything Booking lists) |
| `--max-pages N` | Safety cap per property (default 200) |
| `--headless` | No browser window (see limitations: Booking returns no review data to headless Chromium) |
| `--source http` | Legacy server-rendered endpoint (currently retired by Booking; kept as an option) |

Other commands:

| Command | Purpose |
|---|---|
| `classify_reviews [--all]` | (Re)tag topics/sentiment; only needed after changing the lexicon |
| `review_insights [--property X] [--weeks-ago N]` | Print the topic table and insights in the terminal |
| `import_review_html FILE… --property X` | Manual fallback: import review pages saved from a browser |
| `export_sample_data [--include-names]` | Write `sample_data/` (JSON + CSV) |
| `load_sample_data` | Load `sample_data/reviews.json` into a fresh database |

**Scheduling.** Run `python manage.py scrape_reviews` once a day (Windows Task Scheduler,
cron, or a CI job on a machine with a display / `xvfb-run`). The command exits with a
non-zero code when any property fails or is blocked, so the scheduler can alert someone,
and every run is recorded (`Scrape runs` in the admin, `freshness` in the dashboard).

---

## 3. Architecture

```
 Booking.com property page ("Read all reviews" panel)
        │  Playwright Chromium, on its own worker thread
        ▼
 scraper/sources.py ── pages, one at a time, on demand, newest first
        ▼
 scraper/parsing.py ── layout "strategies" → ParsedReview (normalised)
        ▼
 services/ingest.py ── validate → dedup (id / fingerprint) → detect edits (content hash)
        │                                        └─► services/classify.py → insights/topics.py
        ▼                                             (sentiment + topic tags)
 SQLite / Postgres: Property · Review · ReviewTopic · ScrapeRun
        ▼
 api/ (Django REST Framework, read-only) ── summary · trends · topics · insights · reviews · properties
        ▼
 frontend/ (React + Recharts) ── operations dashboard
```

```
backend/
  config/                Django settings & URLs
  properties.json        the 4 tracked properties (source of truth for seed_properties)
  reviews/
    models.py            Property, Review, ReviewTopic, ScrapeRun
    scraper/             collection: sources, parsing, block detection, runner, snapshots
    services/            ingest (validation + dedup), classify, insights
    insights/topics.py   topic lexicon + sentiment rules (pure Python, no Django)
    api/                 REST API: filters, serializers, views
    management/commands/ scrape_reviews, classify_reviews, review_insights, import/export/load …
    tests/               113 tests incl. fixtures captured from the live site
frontend/src/            App, API client, components (KPIs, insights, charts, review feed)
sample_data/             exported reviews (JSON + CSV) and scrape-run log
```

**Key decisions**
- **Collection is a management command, not part of the web app.** The dashboard stays fast and
  never triggers scraping; collection can be scheduled, retried and monitored separately.
- **Parsing is separate from fetching.** When Booking changes its markup, only a parser
  "strategy" changes; fetching, dedup and storage are untouched.
- **SQLite by default** so reviewers can run it with no setup; `DATABASE_URL` switches to Postgres.
- **Rule-based topic classification** (transparent, testable, free) instead of an AI model; see §5.

---

## 4. How review collection works

### Why a browser
Booking.com offers no review API to us. I first used the server-rendered `reviewlist.html`
endpoint (fast, plain HTTP). **It returned HTTP 404 for all four properties** when tested on
25 Nov 2026: Booking has retired it. Reviews now load inside the property page's
"Read all reviews" panel through an internal API tied to the browser session.

So the collector does what a guest does, in a real Chromium browser (Playwright):
open the public property page → click **Read all reviews** → choose **Newest first** →
read the page → click **Next** … until the end of the list or the history window.

### Reliability
| Concern | How it's handled |
|---|---|
| **Duplicates** | Booking's live markup has no review id, so each review gets a fingerprint (property, guest name, date, normalised title + text). Two database unique constraints (id and fingerprint) make duplicates impossible even if the code had a bug. |
| **New reviews** | Pages are read newest first. Incremental runs stop after 2 consecutive pages with nothing new or changed. |
| **Edited reviews / new hotel replies** | A separate content hash detects changes; the row is updated (and re-tagged), never duplicated. |
| **History** | Collection stops at the first page entirely older than the window (default 12 months). The largest property has ~2,500 reviews; a full history would take ~20 minutes for that property alone. |
| **Temporary failures** | Navigation retries with exponential backoff + jitter (honours `Retry-After`). 3 consecutive failures end the run as `failed`. |
| **Blocking / CAPTCHA** | Detected and recorded as `blocked`; the run stops and remaining properties are skipped. **There is no attempt to bypass bot protection**: no stealth plugins, no CAPTCHA solving. The next scheduled run tries again. |
| **Page/layout changes** | If no review cards are recognised, or >50% of a page fails validation, the run is flagged with a clear reason; the raw HTML and a screenshot are saved to `backend/scrape_snapshots/` so the parser can be fixed and the page added as a test fixture. |
| **Truncated hotel replies** | Replies are expanded before capture; any still collapsed are flagged and never overwrite a complete stored reply. |
| **Interrupted runs** | Ctrl+C is recorded as a failed run, so "auto" mode never mistakes a half-finished backfill for a complete one. |
| **Politeness** | One browser, one page at a time, 3–4.5 s between pages, pages loaded only when the runner asks for them (an early stop never loads an extra page). |
| **Audit trail** | Every run is stored (`ScrapeRun`): mode, status, pages, new/updated/invalid counts, errors. Exported to `sample_data/scrape_runs.csv`. |
| **Manual fallback** | `import_review_html` imports pages saved from a browser, through the same parser, validation and dedup. |

**Threading note.** Playwright's sync API runs an asyncio event loop on the thread that starts it,
and Django refuses database queries on such a thread. All browser work therefore runs on a
dedicated worker thread and all database work on the main thread, connected by queues. (The
common shortcut, `DJANGO_ALLOW_ASYNC_UNSAFE`, only disables Django's safety check.)

### What was collected (25 Nov 2026)
| Property | Reviews | From | Avg score |
|---|---|---|---|
| Olympic Paddington Budget Hotel | 127 | Jul 2026* | 6.39 |
| Azzurro Pod Hotel Potts Point | 272 | Nov 2025 | 5.33 |
| Azzurro Pod Hotel Central Sydney | 257 | Nov 2025 | 5.68 |
| Azzurro Pod Hotel Darling Harbour | 1,317 | Feb 2025 | 6.94 |

\* Booking's public list for this property ends there (it shows "129 reviews" but lists 74–127 depending on the day).

---

## 5. How the insights work

**Review sentiment** uses Booking's own 1–10 score (the guest's explicit verdict):
**8+ positive**, **6–7.9 mixed**, **below 6 negative**.

**Topics.** Booking asks every guest what they *liked* and *disliked* in two separate boxes.
A topic found in the **disliked** text is a **complaint**; in the **liked** text, **praise**.
This sidesteps the hard parts of sentiment analysis: negation ("not clean"), sarcasm and
mixed reviews need no guessing.

Each text is split into sentences and matched against a phrase list per topic (English plus
the most common Spanish, French, German and Italian terms found in the data). Every tag stores
**the sentence that triggered it**, so staff can always see *why* a review was tagged.

| Topic | Examples of what it catches |
|---|---|
| Cleanliness | dirty, clean, mould, smell, cockroach, sucio, sale |
| Check-in & access | check-in/out, key card, door code, luggage, deposit, tablet / remote reception |
| Staff & service | staff, reception(ist), rude, friendly, helpful, manager |
| Noise | noisy, loud, thin walls, slamming, couldn't sleep, ruido, bruit |
| Facilities | kitchen, laundry, lift, wifi, lockers, common areas |
| Rooms & bathrooms | bed, bunk, pod, mattress, shower, toilet, air-con, small, broken |
| Location | location, close to, walking distance, station, CBD, harbour |
| Value for money | price, value, expensive, cheap, worth, fees |
| Food & breakfast | breakfast, dinner, meals, food, cena, desayuno |

*Food & breakfast* was added to the brief's list because "dinner" was among the most frequent
words in both praise and complaints (these hostels serve meals).

**Insight sentences** are generated from counts, e.g.
*"40% of negative reviews this week so far mentioned rooms & bathrooms issues (2 of 5)."*
- Every sentence shows its counts.
- Below 5 reviews it says "too few for reliable percentages" instead of giving numbers.
- Week-on-week changes compare **shares**, not counts, so busy and quiet weeks compare fairly,
  and are only reported for a change of ≥10 percentage points backed by ≥3 reviews.
- **An unfinished week is compared like-for-like** with the same weekdays of last week
  (Mon–Wed vs Mon–Wed). The first version compared 3 days with a full week and reported a
  false "drop" in complaints.

**How well it works (measured on the 1,973 collected reviews)**
- 85% of reviews with text get at least one topic.
- Reading ~90 random tags: about 9 in 10 correct. The audit found false matches that were then
  fixed, each with a regression test (German "Bett" matching "better"; "dining area" counted as
  location; French "parties communes" counted as noise).

**Why not an AI model?** For this task the rules are transparent (every tag is explainable),
deterministic, testable, free and editable by non-engineers. An LLM classifier is the natural
next step for the gaps below; the code is structured so a classifier can replace
`classify_review()` behind the same interface (`CLASSIFIER_VERSION` triggers re-tagging).

**Limitations of the method**
- Reviews in Chinese, Japanese and Korean (~5%) aren't tagged.
- Keyword context errors remain (~1 in 10 tags), e.g. "the upper bunk was not convenient" counts as location.
- If a guest writes a complaint in the *liked* box ("I liked nothing…"), it's counted as praise.

---

## 6. The dashboard

Built for operations staff who are not technical:

- **This week at a glance:** average rating, review count and % negative, each with
  "▲ +0.5 vs the same days last week". Numbers based on fewer than 5 reviews are marked
  *treat with caution*.
- **What guests are saying:** the insight sentences, each with *Read these reviews →*.
- **Properties:** this week's rating, change, 12-month average, and a **data status** badge
  (*Updated 2 h ago* / *Last updated 3 days ago* / *Data collection failing*).
  Click a property to focus the whole page on it.
- **Rating trends:** weekly average rating, and positive / mixed / negative review counts.
- **What reviews talk about:** complaints vs praise per topic; click a bar to read those reviews.
- **Reviews:** readable cards with *Liked* / *Disliked* separated, topic tags, the hotel's reply,
  search and filters (property, period, overall score, topic, complaint/praise, sort).

Design choices: plain language ("Complaint", not "negative polarity"); one colour meaning
everywhere (blue = good, red = bad, grey = mixed), validated for colour-blind safety and never
the only signal (arrows, icons and words carry it too); every chart has a **Show as table** view;
every section loads and fails independently with a readable message.

---

## 7. API

Read-only JSON under `/api/`. Common filters: `property=slug[,slug]`, `start=YYYY-MM-DD`,
`end=YYYY-MM-DD`. Invalid input returns **400** with a field message
(e.g. `{"property": ["Unknown property: nope"]}`), never a silently empty result.

| Endpoint | Returns |
|---|---|
| `GET /api/meta/` | Topic labels and definitions used by the UI |
| `GET /api/properties/` | Per property: review count, average, newest review, scrape freshness |
| `GET /api/summary/?weeks_ago=0` | This week vs the same days last week, overall and per property |
| `GET /api/trends/?granularity=week\|month` | Average rating + positive/mixed/negative per period (gaps filled) |
| `GET /api/topics/` | Complaints/praise per topic + weekly complaint share |
| `GET /api/insights/` | Insight sentences (custom range → compared with the previous equal-length period) |
| `GET /api/reviews/` | Paginated feed; also `sentiment`, `topic`, `polarity`, `min_rating`, `max_rating`, `search`, `ordering`, `page`, `page_size` |

Every aggregate includes `review_count` and a `low_sample` flag.

---

## 8. Data quality controls

- Every scraped review is normalised (Unicode, whitespace) and validated before storage:
  score 1–10, date present, not in the future, not implausibly old; invalid reviews are
  counted and reported per run, never stored.
- Database constraints: unique review per property (id and fingerprint), score range check.
- Score-only reviews (no text) are kept: they count toward averages but not topics.
- Placeholder text ("nothing", "-", "n/a", "no complaints") is not treated as a complaint.
- Snapshots + screenshots for anything unrecognised; layout drift alarm at >50% invalid.
- Data-quality check run on the collected data: 0 duplicate fingerprints, 0 invalid scores
  or dates, 0 truncated hotel replies inside the window, 12 possible same-guest pairs (0.6%)
  kept separate because reviews have no id (see limitations).

---

## 9. Tests

```bash
cd backend
python manage.py test reviews
```

113 tests, no network access needed. Parser tests run against **markup captured from the live
Booking.com page**; every bug found during development has a regression test (redirect handling,
false "success" with 0 reviews, auto-mode backfill gap, Ctrl+C recorded as success, truncated
replies, unfair week comparison, classifier false matches, name-less sample import …).
API tests pin "today" so they never depend on the date they run.

---

## 10. Sample data

`sample_data/` (generated by `python manage.py export_sample_data`):

| File | Contents |
|---|---|
| `reviews.json` | All 1,973 reviews with topic tags and evidence sentences; re-importable with `load_sample_data` |
| `reviews.csv` | One row per review incl. complaint/praise topics; opens in Excel / Google Sheets |
| `scrape_runs.csv` | Every collection run, including the failures described above |

Reviewer first names are **omitted by default**. They are public on Booking.com, but a code
repository doesn't need personal names (`--include-names` keeps them).

---

## 11. Known limitations and assumptions

- **Terms of use.** Booking.com's terms restrict automated access. This project collects only
  publicly visible reviews, slowly, for the properties' own operations, and never circumvents
  access controls. For production use I would seek Booking's permission or use an official
  channel (e.g. a Connectivity Partner / channel-manager integration that exposes the
  property's reviews).
- **A visible browser is required.** In headless mode Booking returned an empty review panel
  ("no reviews match your filters" for a property with 129 reviews). Rather than disguise the
  browser, the collector runs a normal visible Chromium. On a server, use a virtual display
  (`xvfb-run python manage.py scrape_reviews`) or a scheduled desktop task.
- **Booking doesn't list every review publicly** (e.g. "129 reviews" but only part of them in the list).
  The dashboard reflects what is publicly listed.
- **No review ids** in the current markup: dedup relies on the content fingerprint. If a guest
  edits their text, the edited version may appear as a second review. 12 possible same-guest
  pairs (0.6%) were found and deliberately not merged on a guess.
- **Markup can change at any time.** That is detected and reported (snapshots, screenshots,
  failing status in the dashboard), not prevented; fixing it means updating a parser strategy.
- **Topic tagging limits:** see §5 (non-Latin languages, ~1 in 10 context errors).
- **Sentiment bands** (8+ / 6–7.9 / below 6) are my assumption, based on Booking's own score labels.
- **Week boundaries** use Monday–Sunday in `Australia/Sydney` (`APP_TIME_ZONE`), since all
  properties are in Sydney. The frontend's period presets use the browser's local date.
- **No authentication:** it's an internal, read-only tool; in production it would sit behind
  the company's SSO/VPN, with Postgres instead of SQLite.

---

## 12. What I would do next

1. **Response-rate KPI:** share of negative reviews that got a hotel reply, and time to reply.
2. **Weekly email digest** of the insight sentences to each property manager.
3. **AI classifier** behind the same interface for multilingual reviews and the context errors,
   evaluated against a small hand-labelled sample before replacing the rules.
4. **Alerting** when a property's collection is `failing` or `stale` for more than a day.
5. **Docker Compose** (Django + Postgres + Playwright with `xvfb`) for one-command deployment.
