## LLM/AI agent instructions

Any time you make a change to the code, determine whether it makes any information in this file obsolete. If so, update it; otherwise, state that no update to claude.md was necessary. This should ALWAYS be the last thing you do when editing the code. Keep your additions to claude.md as succinct as possible, avoiding unnecessary details. Make sure the file stays below 200 lines. If it exceeds 150, warn the user that it is getting too long and should probably be shortened.

Whenever the actual repo structure changes (files/directories added, removed, renamed, or moved), also update the `### Project layout` tree in `readme.md` so it stays accurate.

### Django admin

- **Always register new models in `analytics/admin.py`** with an `@admin.register` decorator and a `ModelAdmin` class that includes sensible `list_display`, `list_filter`, and `ordering` fields.

### Frontend error handling rules

- **Never use `alert()`** in JavaScript. Use `showToast()` instead (defined in base.html, available globally).
- **Error messages shown to users must be generic**: "An error occurred. Please note this in the feedback form." with a link to the Google Form. Stored in `GENERIC_ERROR_TOAST` in base.html.
- **Validation messages** can be specific but must use `showToast(..., 'warning')`, not `alert()`.
- **Never use `confirm()`**. Use `showConfirm(message, title)` (returns a Promise resolving to `true`/`false`). Caller must be `async` and `await` it.
- **For success/info messages the user should acknowledge**, use `showAlert(message, title)` — modal with OK button. Reserve `showToast()` for transient background notifications.

### Async / ORM gotcha when porting from notebooks

When porting code from Jupyter notebooks into `async` Django utility functions, **all synchronous Django ORM calls must be wrapped with `sync_to_async`**. Notebooks don't enforce Django's async safety checks, so ORM code that works in a notebook will raise `SynchronousOnlyOperation` in an `async def` function. Extract ORM-heavy blocks into a sync helper and call via `await sync_to_async(helper)()`.

### Coach mark / spotlight pattern

For onboarding and guided UI hints, use the **coach mark** pattern (not static banners). Reuses the existing `confirm-overlay` CSS from base.html (dimmed backdrop at `z-index: 10002`) with a `confirm-modal` dialog inside it.

**To spotlight a UI element**: set `position: relative` and `z-index: 10003` on it while the overlay is active; reset both on dismiss. Use default-sized buttons (`btn btn-primary`), not `btn-sm`. Trigger via a URL query param or template variable; on dismiss, hide the overlay, reset z-index, and clean up the URL with `window.history.replaceState`.


# LetterPulse

Django web app for analyzing newsletter engagement data from the Beehiiv platform. Extracts post content, tracks CTR, generates AI-powered insights, and produces annotated HTML exports with improvement tips.

## Tech Stack

- **Backend**: Django 5.0+, Python 3.11; **DB**: PostgreSQL on AWS RDS (Aurora)
- **AI**: OpenAI API (GPT-5.4 and GPT-5.4-mini); **Auth**: django-allauth (email-based)
- **Frontend**: Bootstrap 5, DataTables, jQuery, Marked.js, Chart.js
- **Async**: aiohttp, asyncio; **Deployment**: AWS App Runner via ECR, local with gunicorn

## Project Structure

- `beehiiv_analytics/` — Django project (settings, urls, wsgi/asgi).
- `analytics/` — main app. `models.py`; `views/` split by feature (`__init__.py` re-exports public names; submodules: `_helpers`, `public`, `account`, `insights`, `learning`, `content_finder`, `improvement_tips`, `monetize`, `feedback`); `utils/` submodules (`credits`, `llm`, `text`, `beehiiv_api`, `post_selection`, `links`, `sections`, `posts`, `content_finder`, `improvement_tips`, `learning`, `niche`, `_constants`); `llm_tracker.py` (contextvar LLM call accumulator); `logsink.py`/`logutils.py` (queue-based async logging + middleware); `forms.py`/`adapters.py`/`signals.py`/`context_processors.py`; `templates/analytics/`; `static/analytics/js/` (insights split into fancy-select/datatables-init/learning-flow/content-finder/improvement-tips; `monetize.js`; shared `csv.js`/`progress-bar.js`/`dev-panel.js` — server context flows in via `<div id="page-config" data-...>`, read with `.dataset`); `migrations/`.
- Deploy/run: `push_to_ecr.sh` (ECR), `run_local.sh` (Docker, cloud DB), `run_local_dev.sh` (venv + Postgres-in-Docker on host port 5433). `Dockerfile`, `requirements.txt`, `.env`/`.env.example`.

## Database Models

All in `analytics/models.py`:

- **Publication** / **UserPublication**: Publication is global (unique by `pub_id`); UserPublication is per-(user, pub) access + onboarding state. Holds `initial_fetch_done_at` (null until a clean Learning fetch completes); reset to null on abandoned initial flow.
- **Post**: Beehiiv post metadata + engagement. Scoped to `(post_id, user)`. `platform` ∈ {'email','web','both'} filters processing eligibility.
- **UsageAccount**: Per-user credits, encrypted API credentials, preferences. Billing resets on signup anniversary.
- **ProcessedPost**, **Section**, **LinkData**: Per-post artifacts. All scoped to `(post, user, …)`.
- **BackgroundTask** (abstract): Shared base for the Pending* tables. Provides `task_id`, `status`, `last_heartbeat`, `error_message`, `credits_charged`, plus `claim()` / `mark_complete()` / `mark_error()` / `touch_heartbeat()` / `sweep_stale()`. Subclasses tune `RUNNING_STATUSES`, `SWEEPABLE_STATUSES`, `STALE_SECONDS`, and override `get_credits_cost()` to charge inside `claim()`. The stale-sweep (called from `AppConfig.ready()` and view entrypoints) marks stale rows errored and refunds via `analytics.utils.credits.refund_credits`. `PendingLearningTask` overrides `on_error()` to wipe partial `(user, publication)` data on `kind='initial'`.
- **PendingContentSearch**: Content finder task. `dev_panel_data` JSON for dev panel. `awaiting_feedback` excluded from `SWEEPABLE_STATUSES` so it can park while user reads the plan modal.
- **PendingImprovementTips**: Improvement tips task.
- **PendingLearningTask**: Onboarding (`kind='initial'`) and per-page-load update (`kind='update'`). Fields include `phase`, `status`, `target_process_count`, `posts_processed_count`. Cancellation via polling-as-heartbeat — when client stops polling, heartbeat ages out and sweep marks errored. Initial runs wipe `(user, publication)` data at start.
- **PendingNicheAnalysis**: Monetize-tab niche analysis. Stores `niche` (str) and `content_types` (≤5 strings). Most-recent row for `(user, publication)` doubles as result cache. No credit charge.
- **ExecutionLog**: Low-overhead request/function logging via `logsink.py`.
- **LLMCall**: Per-call record for every OpenAI Responses API invocation through `utils.llm_call`. Async writes via `logsink.py` (routed by `_target='LLMCall'`). Stores user, publication, function_name, model, token breakdowns, success/error, task_id + task_kind, and free-form `additional_info` JSON. Does NOT store prompts/outputs (use the dev panel). Context flows via `contextvars` set by `analytics.llm_tracker.set_llm_context` / `set_additional_info`.
- **Feedback**, **ContentSearchFeedback**: Per-user feature feedback. Thumbs-down content finder URLs are excluded from future searches.

## Authentication

- Email-based; usernames auto-generated from email local part.
- Signup collects first/last name, newsletter name, email, password.
- All views `@login_required` except public about page at `/`.
- UsageAccount auto-created via signals. After login: no-creds → Account; with-creds → Write.

**Beehiiv tokens** are encrypted via `EncryptedCharField` using a key derived from `BEEHIIV_TOKEN_ENCRYPTION_KEY` (falls back to `SECRET_KEY`). To rotate `SECRET_KEY` safely, first set `BEEHIIV_TOKEN_ENCRYPTION_KEY` to the *current* `SECRET_KEY` and deploy, then rotate. On decrypt failure, `from_db_value` returns `''` and logs `EncryptedCharField: InvalidToken` — the user is silently re-onboarded; monitor that log line.

## Key Features & Workflows

### 1. Write Page (`/insights/`) — onboarding entrypoint
- **Learning Your Audience (initial)**: After first valid creds save, the page shows a blocking coach. Scan starts a `PendingLearningTask` (kind='initial') that fetches all posts, then processes the top-k most recent eligible (published ≥48h, `platform ∈ {'email','both'}`). `k` chosen so summed `recipients` ≥ `subscribers × 15` (subscribers from `GET /v2/publications/{id}?expand=stats`). Free, capped silently at `MAX_POSTS_PROCESSED_PER_PERIOD` (45) per billing period. Wipes partial data at start. Polling-as-heartbeat for cancellation. `initial_fetch_done_at` stamped only on clean non-empty complete.
- **Updating Your Posts**: On every Write page load, if initial is done and nothing running, JS checks a 15-min TTL (`localStorage` key `incrementalRefreshLastRun:{pub_id}`) and kicks off a `kind='update'` task (incremental fetch + process newly-eligible). Modal only revealed on transition into `process` phase.
- **Content Finder**: User picks a template post. 3 stages on the same `PendingContentSearch` (3s poll): (1) **plan** — gpt-5.4 drafts a search plan from all sections; modal shows it with a feedback textbox. (2) **dispatch** — gpt-5.4 turns plan+feedback into `List[str]` of section labels (cap 6, may include `"Other Interesting Links"`). (3) **search** — each label runs a parallel gpt-5.4-mini agent (3 rounds Perplexity + final structured output). Conversation context is carried as a growing message list: each stage's input = prior stage's input + prior output items (serialized via `response.model_dump(mode='json')`). `plan_messages` / `dispatch_messages` JSONFields persist between threads. Results render in a Bootstrap accordion grouped by label.
- **Section/Link Data Tables**: Hidden by default, show Sections and LinkData with CSV download.
- **Improvement Tips**: User picks any post. Background task builds numbered prettified HTML, gathers link history, calls LLM with structured output (tips reference HTML line numbers), then inserts inline anchor `<span>`s via BeautifulSoup so logically-continuous paragraphs stay grouped even when split across many prettified lines. Generates two-column annotated HTML with tip cards + SVG connectors. Downloaded as file.

### 2. Monetize Page (`/monetize/`)
- Frontend-only skeleton for sponsor outreach. Backend (matching, email, billing) is **not built**. `monetize_view` resolves the publication's name and pulls live stats (`active_subscriptions`, `average_open_rate`, `average_click_rate`) from `fetch_publication_stats` on every load. Beehiiv returns rate fields as percentage points (e.g. `51.16` == 51.16%). A failed stats fetch silently falls back to "—". Decorated with `@require_valid_api_credentials`.
- Three card sections: **Newsletter profile** (niche + audience + topic pills), **Campaign settings** (3-tab email preview + 3 weekly-volume tiers), **Campaign status** (state-dependent).
- **Niche analysis (one-shot LLM)**: On first visit with no `complete` `PendingNicheAnalysis` AND ≥1 processed post, `monetize_view` creates a row and spawns `utils.run_niche_analysis_background`. A single gpt-5.4 call sees text of last 3 processed posts (sections concatenated via `html_to_text_with_links` in `start_line` order) + top `NICHE_ANALYSIS_TOP_LINKS_PER_SECTION` links per section across last 10 issues (sorted by `mean_ctr`, with CTR shown relative to section avg). Returns `NicheAnalysisResult { niche: str, content_types: List[str] }`; `content_types` silently capped at 5. Result cached on the row. Free. Frontend renders placeholder pills, polls `/monetize/niche-analysis/status/<uuid>/` at 3s (≤60 attempts). **Empty-state pills** (dashed muted) shown when niche or topics are blank. Once user saves (even to clear), `savedProfile !== null` and the poll handler stops overwriting their choices.
- "Edit profile" toggles the card into edit mode; saves to `localStorage['lp_monetize_profile_v1']`. Audience stays static.
- Campaign status states derived from `localStorage['lp_monetize_campaign_state_v1'] = { launch_at: ISO|null, cancelled: bool }`: cancelled / idle / preparing (launch_at>now) / active (launch_at<=now). Launch sets `launch_at = now + 48h`. Most buttons (Edit email, Pause, etc.) are visually present but inert.

### 3. Account Page (`/account/`)
- Cards: Beehiiv API Credentials, AI Credits Usage, Account Information, and (dev only) Post Fetching diagnostics. Post Fetching is hidden in cloud/prod.
- No manual refresh/process controls — fetching/processing are automatic via Learning/Update flows.

## API Endpoints

All under `analytics:` namespace. See `analytics/urls.py`. Groups:
- **Insights**: `/insights/`, `/insights/load-processed-data/`, `/insights/load-link-data/`
- **Learning/Update**: `/insights/learning/{start,update,status/<uuid>,abandon/<uuid>}/`
- **Content Finder**: `/insights/content-finder/{posts,run,confirm-plan/<uuid>,status/<uuid>,feedback}/`
- **Improvement Tips**: `/insights/improvement-tips/{posts,run,status/<uuid>,download/<uuid>}/`
- **Monetize**: `/monetize/`, `/monetize/niche-analysis/status/<uuid>/`
- **Account**: `/account/`; **Feedback**: `/feedback/submit/`

## Deployment

Local mode reads `.env` via python-dotenv. Python 3.11 venv at `.venv/`. System `python` is 3.8 Anaconda and lacks deps — use `source .venv/bin/activate && python manage.py runserver` or `./run_local.sh` (Docker, ARM64, port 8000).

**Fully-local dev DB** (`./run_local_dev.sh`): Idempotently starts `postgres:16` container `letterpulse_pg` on host port **5433** (not 5432 — loopback wins over wildcard on macOS, which would silently misroute), volume `letterpulse_pg_data`. Exports `DB_HOST`, `DB_PORT`, `DATABASE_SECRET` in the shell — `load_dotenv()` defaults to `override=False`, so these win over `.env`. Activates `.venv`, migrates, runs server. `settings.py` reads `DB_PORT` via `os.environ.get('DB_PORT', '5432')`.

**Cloud** (App Runner via ECR): Export `AWS_ACCOUNT_ID` first. `./push_to_ecr.sh {dev|prod|both}` pushes to `letterpulse:{dev,prod}-latest`. Dockerfile: `python:3.11-slim`, runs migrate → collectstatic → gunicorn (1 worker, 4 threads, 120s timeout). Beehiiv creds are per-user, not env vars.

## Key Architecture Notes

### Credit System
Constants in `settings.py` (`CREDITS_PER_*`, `DEFAULT_MONTHLY_CREDITS`). Charged via `utils.charge_credits`, which wraps `select_for_update()` around `UsageAccount.ensure_current_period()` and the quota check. Billing resets on signup anniversary; `ensure_current_period()` lazily recalculates `period_start` and zeroes `used_this_period`, clamping the billing day to month-end for short months. Because rollover mutates `used_this_period` in memory, `charge_credits` writes the new total as a plain int (NOT `F("used_this_period") + n`) so the reset persists.

### LLM Dev Panel (local only)
When `ENVIRONMENT == 'local'`, a floating panel shows per-call details. `llm_tracker.py` is a contextvar-based accumulator; sync workflows return data in JSON, background workflows store it in `dev_panel_data` on Pending* models. `static/analytics/js/dev-panel.js` renders, loaded via `{% if is_local %}`.

### Background Task Pattern
Content Finder and Improvement Tips: create a Pending* row → spawn background thread → frontend polls a status endpoint at 3s → result stored on the row.

## Testing Notes

- `sandboxes/`, `testing_data/`, `code_dump/` are dev/test. TZ: `America/Chicago`.

### Automated tests
Tests in `analytics/tests/`. Runner: **pytest-django** (`pytest.ini`: `DJANGO_SETTINGS_MODULE = beehiiv_analytics.test_settings`, `testpaths = analytics/tests`). Run: `.venv/bin/pytest` or `.venv/bin/coverage run -m pytest && .venv/bin/coverage report -m`.

`test_settings.py` overrides `DATABASES` to SQLite `:memory:`, provides env defaults, uses `locmem` email backend, disables migrations via `MIGRATION_MODULES` sentinel (test-only speed; real migrations still run in deployed envs and `run_local_dev.sh`), and switches `PASSWORD_HASHERS` to MD5. Session-autouse fixture in `conftest.py` suppresses `_send_welcome_email` (its daemon thread races SQLite teardown). Session-scoped because pytest-django doesn't run function-scoped autouse fixtures around `unittest.TestCase` tests.

### External-boundary tests
`test_beehiiv_api.py` and `test_llm_call.py` use **hand-written inline mocks**. Beehiiv via `aioresponses`; OpenAI patches `analytics.utils.llm.AsyncOpenAI` and tracker hooks. Mocks were validated against real recordings (May 2026) — see `analytics/tests/fixtures/_record_phase2.py` and `_scrub.py`. Recorded JSONs are gitignored. To re-validate: add `BEEHIIV_API_TOKEN` to `.env` (need pub with ≥11 posts), then `.venv/bin/python -m analytics.tests.fixtures._record_phase2 && .venv/bin/python -m analytics.tests.fixtures._scrub`. Recorder caches via per-file existence; `--force` to overwrite.
