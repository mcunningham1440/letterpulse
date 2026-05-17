## LLM/AI agent instructions

Any time you make a change to the code, determine whether it makes any information in this file obsolete. If so, update it; otherwise, state that no update to claude.md was necessary. This should ALWAYS be the last thing you do when editing the code.

### Django admin

- **Always register new models in `analytics/admin.py`** with an `@admin.register` decorator and a `ModelAdmin` class that includes sensible `list_display`, `list_filter`, and `ordering` fields.

### Frontend error handling rules

- **Never use `alert()`** in JavaScript. Use `showToast()` instead (defined in base.html, available globally).
- **Error messages shown to users must be generic**: "An error occurred. Please note this in the feedback form." with a link to the Google Form. This message is stored in the `GENERIC_ERROR_TOAST` constant in base.html (available globally).
- **Validation messages** (e.g., "Please select at least one post") can be specific but must still use `showToast(..., 'warning')`, not `alert()`.
- **Never use `confirm()`** for confirmation dialogs. Use `showConfirm(message, title)` instead (defined in base.html). It returns a Promise that resolves to `true` (Proceed) or `false` (Cancel). The caller must be `async` and use `await`. Example: `if (!await showConfirm('Delete this item?')) return;`
- **For success/info messages that the user should acknowledge**, use `showAlert(message, title)` instead of `showToast()`. It displays a modal overlay with an OK button that the user must dismiss. Returns a Promise. Defined in base.html, available globally. Reserve `showToast()` for transient background notifications (e.g., "3 posts processed") — use `showAlert()` when you want the user to actually read the message.

### Async / ORM gotcha when porting from notebooks

When porting code from Jupyter notebooks or standalone scripts into `async` Django utility functions, **all synchronous Django ORM calls must be wrapped with `sync_to_async`**. Jupyter notebooks run in their own event loop and don't enforce Django's async safety checks, so ORM code that works fine in a notebook will raise `SynchronousOnlyOperation` at runtime in an `async def` function. Extract ORM-heavy blocks into a sync helper and call it via `await sync_to_async(helper)()`.

### Coach mark / spotlight pattern

For onboarding and guided UI hints, use the **coach mark** pattern instead of static alert banners. This reuses the existing `confirm-overlay` CSS from base.html (dimmed backdrop at `z-index: 10002`) with a `confirm-modal` dialog inside it.

**To spotlight a specific UI element** (make it interactive while the rest of the page is dimmed), set `position: relative` and `z-index: 10003` on that element when the overlay is active. Reset both when dismissed.

Implementation checklist:
1. Add a `confirm-overlay` div (hidden by default) containing a `confirm-modal` with your message and a dismiss button
2. Trigger it via JS (e.g., based on a URL query param like `?setup=configure`, or a Django template variable)
3. To spotlight an element: give it `z-index: 10003` so it sits above the overlay
4. On dismiss: hide the overlay, reset the spotlighted element's z-index, and clean up the URL if a query param was used (`window.history.replaceState`)
5. Use default-sized buttons (`btn btn-primary`), not `btn-sm` — coach marks are more prominent than confirm dialogs


# LetterPulse

A Django web application for analyzing newsletter engagement data from the Beehiiv platform. The app extracts content from newsletter posts, tracks click-through rates (CTR), generates AI-powered insights, and provides annotated HTML exports with improvement tips.

## Tech Stack

- **Backend**: Django 5.0+, Python 3.11
- **Database**: PostgreSQL on AWS RDS (Aurora)
- **AI**: OpenAI API (GPT-5.4 and GPT-5.4-mini)
- **Authentication**: django-allauth (email-based auth)
- **Frontend**: Bootstrap 5, DataTables, jQuery, Marked.js (markdown rendering), Chart.js (trend charts on Write page)
- **Async**: aiohttp, asyncio for parallel API calls
- **Deployment**: AWS App Runner (cloud), local development with gunicorn

## Project Structure

```
app/
├── beehiiv_analytics/          # Django project settings
│   ├── settings.py             # Main configuration (uses python-dotenv)
│   ├── urls.py                 # Root URL routing
│   └── wsgi.py / asgi.py       # WSGI/ASGI entry points
├── analytics/                  # Main Django app
│   ├── models.py               # All database models
│   ├── views/                  # View logic, split by feature group. `__init__.py` re-exports
│   │                           #   every public name so `urls.py`'s `from . import views`
│   │                           #   keeps working. Submodules: _helpers (sanitize_filename,
│   │                           #   get_user_api_credentials, require_valid_api_credentials,
│   │                           #   _resolve_publication), public, account, insights, learning,
│   │                           #   content_finder, improvement_tips, monetize, feedback.
│   ├── urls.py                 # App URL patterns (analytics namespace)
│   ├── utils/                  # Core utilities, split into topic submodules.
│   │                           #   Submodules: credits, llm, text, beehiiv_api, post_selection,
│   │                           #   links, sections, posts, content_finder, improvement_tips,
│   │                           #   learning, niche, _constants (TIMEZONE_CHOICES).
│   ├── llm_tracker.py          # Thread-local LLM call accumulator for dev panel
│   ├── logsink.py              # Queue-based async logging system
│   ├── logutils.py             # Logging middleware and decorators
│   ├── forms.py                # Custom allauth signup form
│   ├── adapters.py             # Custom allauth adapter
│   ├── signals.py              # User signals (auto-create UsageAccount)
│   ├── context_processors.py   # Usage stats and environment context for templates
│   ├── templates/analytics/    # HTML templates (base, posts, insights, account, about, mobile)
│   ├── static/analytics/js/    # Page-specific JS (insights split into fancy-select / datatables-init /
│   │                           #   learning-flow / content-finder / improvement-tips; monetize.js;
│   │                           #   shared utilities csv.js / progress-bar.js / dev-panel.js).
│   │                           #   Server-side context flows in via a <div id="page-config" data-...>
│   │                           #   element in insights.html (read with element.dataset).
│   └── migrations/
├── manage.py
├── requirements.txt
├── Dockerfile
├── push_to_ecr.sh              # Deployment script (builds and pushes to ECR)
├── run_local.sh                # Local Docker development (full app in container, uses cloud DB)
├── run_local_dev.sh            # Local dev runner (venv + Postgres-in-Docker on host port 5433)
└── .env / .env.example
```

## Database Models

All models are in `analytics/models.py`. Key models and their relationships:

- **Publication**: Beehiiv publication (globally unique by `pub_id`; users may have multiple, shared across users on the same pub)
- **UserPublication**: Per-user access to a Publication, plus per-(user, pub) onboarding state. Holds `initial_fetch_done_at` (timestamp of completed initial Learning fetch; null if not yet completed). Created/refreshed from the Beehiiv API response in `account_view._sync_user_publications` (rows for pubs no longer in the response are deleted). Cleared on creds invalid/cleared. `initial_fetch_done_at` is reset to null by `wipe_user_publication_data` on abandoned initial flow. Unique on `(user, publication)`.
- **Post**: Newsletter post metadata + engagement stats from Beehiiv, including `platform` ('email', 'web', or 'both') used to filter for processing eligibility. Scoped to `(post_id, user)`
- **UsageAccount**: Per-user credits, API credentials, preferences. OneToOneField to User. Billing resets on signup anniversary each month
- **ProcessedPost**: Marker that a post has been processed. Scoped to `(post, user)`
- **Section**: Structural section extracted from a post's HTML (name, title, line range, HTML content). Scoped to `(post, user, section_name)`
- **LinkData**: Described links with CTR data, grouped by section. Scoped to `(post, user, raw_url, section_name)`
- **BackgroundTask** (abstract): Shared base for the four `Pending*` tables below. Provides `task_id`, `status`, `last_heartbeat`, `error_message`, `credits_charged`, `created_at`, `updated_at`, plus the `claim()` / `mark_complete()` / `mark_error()` / `touch_heartbeat()` / `sweep_stale()` API. Subclasses tune `RUNNING_STATUSES`, `SWEEPABLE_STATUSES`, `STALE_SECONDS`, and override `get_credits_cost()` to charge inside `claim()`. The stale-sweep (called at boot from `AppConfig.ready()` and from view entry points) marks heartbeat-stale rows errored and refunds their charged credits via `analytics.utils.credits.refund_credits`. `PendingLearningTask` overrides `on_error()` to wipe partial `(user, publication)` data on `kind='initial'`.
- **PendingContentSearch**: Background content finder task tracker. Has `dev_panel_data` JSONField for local dev panel. `awaiting_feedback` is intentionally excluded from `SWEEPABLE_STATUSES` so the task can park indefinitely while the user reads the plan modal
- **PendingImprovementTips**: Background improvement tips task tracker. Has `dev_panel_data` JSONField for local dev panel
- **PendingLearningTask**: Tracks the onboarding "Learning Your Audience" flow (`kind='initial'`) and the per-page-load "Updating Your Posts" flow (`kind='update'`). Fields include `phase` (fetch/process), `status` (pending/running/complete/error), `target_process_count`, `posts_processed_count`. Each `kind='initial'` run calls `wipe_user_publication_data` at start so a previous abandoned attempt can't poison the new one. Cancellation is driven by polling-as-heartbeat: when the client stops polling `poll_learning_task`, the heartbeat ages out and the sweep marks the task errored
- **PendingNicheAnalysis**: Tracks the Monetize-tab one-shot niche analysis. Stores the LLM-generated `niche` (string) and `content_types` (list of up to 5 strings) plus `status` (pending/running/complete/error) and `dev_panel_data`. The most recent row for `(user, publication)` doubles as the result cache — subsequent visits reuse it instead of re-running. Scoped to `(user, publication)`. No credit charge.
- **ExecutionLog**: Low-overhead request/function logging with queue-based async writes via `logsink.py`
- **LLMCall**: Per-call record for every OpenAI Responses API invocation made through `utils.llm_call`. Written asynchronously via the same `logsink.py` queue (routed by `_target='LLMCall'`). Stores timestamps, user, publication, function_name, model, token breakdowns (cached/new input, reasoning/response output), success/error, task_id + task_kind (for correlating with `PendingContentSearch` / `PendingImprovementTips` / `PendingLearningTask` / `PendingNicheAnalysis`), and a free-form `additional_info` JSONField (e.g. `{'section_name': ...}` for content finder calls). Does NOT store prompts or outputs — use the dev panel for those. Context (user/pub/task/section) flows in via `contextvars` set by `analytics.llm_tracker.set_llm_context` / `set_additional_info`, called from the background worker entry points
- **Feedback**: Per-user feature feedback. Scoped to `(user, feature)`
- **ContentSearchFeedback**: Thumbs-up/down feedback on content finder results. Scoped to `(user, publication, url)`. Reviewed URLs are excluded from future content searches

## Authentication

- Email as primary identifier; usernames auto-generated from email local part with progressive integers if taken
- Signup collects first name, last name, newsletter name, email, password
- All views `@login_required` except the public about page at `/`
- UsageAccount auto-created for new users via signals
- After login: users without API credentials → Account page; users with credentials → Write page
- "Please configure your Beehiiv API credentials" message only appears when navigating to Write without credentials, not immediately after login

**Important:** User Beehiiv tokens are encrypted via `EncryptedCharField` using a key derived from `BEEHIIV_TOKEN_ENCRYPTION_KEY` (settings.py), which falls back to `SECRET_KEY` when the env var is unset. To rotate `SECRET_KEY` without corrupting stored tokens, first set `BEEHIIV_TOKEN_ENCRYPTION_KEY` to the *current* `SECRET_KEY` value and deploy, then rotate `SECRET_KEY`. Rotating the active encryption key still makes existing tokens unreadable, so back it up alongside DB backups. On decrypt failure, `from_db_value` returns `''` and logs `EncryptedCharField: InvalidToken` — the affected user appears to have no credentials configured and is silently re-onboarded, so monitor that log line.

## Key Features & Workflows

### 1. Write Page (`/insights/`) — also the onboarding entrypoint
- **Learning Your Audience (initial onboarding)**: After a user first saves valid Beehiiv credentials, the Write page shows a blocking "Learning Your Audience" coach. Clicking **Scan** starts a background `PendingLearningTask` (kind='initial'): full Beehiiv post fetch, then process the top-k most recent eligible posts (published ≥48h ago with `platform ∈ {'email','both'}`). `k` is chosen so the sum of the posts' `recipients` is ≥ `subscribers × 15` (subscribers fetched from `GET /v2/publications/{id}?expand=stats`). Post processing is free (no credit charge) but silently capped at `MAX_POSTS_PROCESSED_PER_PERIOD` (45) per billing period — over-cap posts are truncated from the selection and become eligible again next period via the update flow. The runner wipes any leftover `(user, publication)` data at the start of every initial run so a previous abandoned attempt can't poison the new one — that wipe also resets `UserPublication.initial_fetch_done_at` to null for the pair. Cancellation runs on **polling-as-heartbeat**: the frontend polls `poll_learning_task` every 3s and each poll bumps `last_heartbeat`; if the user closes the tab the heartbeat ages past `PendingLearningTask.STALE_SECONDS` and the sweep (called from view entrypoints and at process boot via `AppConfig.ready()`) marks the task `error`, which triggers `on_error()` → `wipe_user_publication_data` for `kind='initial'`. `UserPublication.initial_fetch_done_at` is only stamped on a clean non-empty complete, so an abandoned run rehydrates the Learning coach on the user's next visit.
- **Updating Your Posts (auto-refresh)**: On every Write page load, if initial learning has finished and no task is running, JS checks a 15-minute TTL (`localStorage` keyed as `incrementalRefreshLastRun:{pub_id}`) and, if expired, kicks off a `PendingLearningTask` (kind='update'): incremental fetch (speculative-prefetch window size 1) + process any newly-eligible posts. Runs silently in the background — the modal is only revealed on the transition into the `process` phase (i.e., newly-eligible posts were found and need processing). If the fetch turns up nothing, the task completes without ever surfacing UI.
- **Content Finder**: User selects a processed post as template. Runs in three stages tracked on the same `PendingContentSearch` row (polled at 3s): (1) **plan** — gpt-5.4 sees all sections of the post and drafts a search plan, which is shown to the user in a modal with a free-form feedback textbox; (2) **dispatch** — after the user clicks Confirm, a gpt-5.4 call turns the plan + feedback into a structured `List[str]` of section labels (hard cap 6, may include `"Other Interesting Links"`); (3) **search** — each dispatched label spawns a parallel gpt-5.4-mini agent that runs 3 rounds of Perplexity search + a final structured-output call. Conversation context is carried explicitly as a growing message list: each stage's input starts with the prior stage's full input plus the prior response's output items (serialized via `response.model_dump(mode='json')`), so every search agent sees the original `CONTENT_FINDER_PLAN_PROMPT`, the concatenated per-section user prompt, the plan output, the user feedback, the dispatch output, and then its own `CONTENT_FINDER_SEARCH_PROMPT`. `plan_messages` and `dispatch_messages` JSONFields on `PendingContentSearch` persist these lists between threads. Results render in a Bootstrap accordion grouped by section label
- **Section/Link Data Tables**: Hidden by default, show all Sections and LinkData for current publication with CSV download
- **Improvement Tips**: User selects any post (published or draft). Background task builds numbered prettified HTML, gathers link history, calls LLM with structured output (model returns tips referencing HTML line numbers), then inserts inline anchor `<span>`s into the live DOM (via BeautifulSoup) so logically-continuous paragraphs stay grouped even when split across many prettified lines by inline tags like `<a>`. Generates two-column annotated HTML with tip cards and SVG connectors. Downloaded as file

### 2. Monetize Page (`/monetize/`)
- Frontend-only skeleton for sponsor-outreach campaigns. Backend (sponsor matching, email sending, billing) is **not** built. `monetize_view` resolves the current publication's name for the hero copy and pulls live publication stats (`active_subscriptions`, `average_open_rate`, `average_click_rate`) from Beehiiv via `fetch_publication_stats` (`GET /v2/publications/{id}?expand=stats`) on every page load — these populate the Audience line in the Newsletter profile card. Beehiiv returns the rate fields in percentage points (e.g. `51.16` == 51.16%), not as 0-1 fractions; the view formats them directly. A failed stats fetch silently falls back to "—" placeholders rather than erroring. Decorated with `@require_valid_api_credentials`, so users without configured Beehiiv creds are redirected to `/account/?setup=invalid`.
- One page, three card sections (matching the Write page's section pattern but unnumbered): **Newsletter profile** (niche + audience + click-topic pills), **Campaign settings** (3-tab email sequence preview + 3 weekly-volume tier cards), **Campaign status** (state-dependent block).
- **Niche analysis (one-shot LLM)**: On the user's first visit (no `complete` `PendingNicheAnalysis` row for the (user, publication) pair) AND if the user has at least one processed post, `monetize_view` synchronously creates a `PendingNicheAnalysis` row and spawns a background thread (`utils.run_niche_analysis_background`) that runs a single gpt-5.4 call. The model receives the text of the last 3 processed posts (sections concatenated via `html_to_text_with_links` in `start_line` order) plus the top `NICHE_ANALYSIS_TOP_LINKS_PER_SECTION` links per section across the last 10 issues (sorted by `mean_ctr`, with each link's CTR shown relative to its section's average) and returns a structured `NicheAnalysisResult { niche: str, content_types: List[str] }`. `content_types` is silently capped at 5 entries. The result is cached on the row and reused on subsequent visits. Free (no credit charge) — same model as the Learning task. Frontend renders "Analyzing your newsletter…" placeholder pills while the task runs and polls `/monetize/niche-analysis/status/<uuid>/` at 3s intervals (60-attempt cap = ~3 min) to swap them out on completion. **Empty-state pills** (dashed muted "Click 'Edit profile' to add…" chips) replace the niche pill or the topics row whenever there's no value — first-time users with no processed posts, an LLM result that came back blank, or a user who has saved a cleared profile. The empty-state pills carry `data-empty="true"` so `getCurrentNiche()` / `getCurrentTopics()` skip them when entering edit mode. Once the user clicks Save *at all* (even to clear everything), `savedProfile !== null` and the poll handler stops trying to overwrite their choices — `userHasManualEdits()` keys off the existence of a saved profile, not on whether values are non-empty.
- "Edit profile" toggles the Newsletter profile card into edit mode: niche becomes a text input, click-topic pills become editable chips with × buttons + an "Add topic" button (capped at 5). Save persists to `localStorage['lp_monetize_profile_v1'] = { niche: string, topics: string[] }` and re-renders the read-only pills; Cancel reverts. Audience and the descriptive paragraph stay static.
- Campaign status has 4 derived states. Persisted as `localStorage['lp_monetize_campaign_state_v1'] = { launch_at: ISO|null, cancelled: bool }`; the displayed state is computed from those two fields:
  - `cancelled === true` → **cancelled** (cancellation message + "Start a new campaign" reset button)
  - `launch_at === null` → **idle** ("Launch campaign" button)
  - `launch_at > now` → **preparing** (Submitted→Preparing→Live timeline + ETA + Cancel link)
  - `launch_at <= now` → **active** (status chip, replies callout, static stats strip with hardcoded Sent/Opened/Replied numbers, Pause + Cancel buttons)
- "Launch campaign" sets `launch_at = now + 48h` and re-renders. "Cancel campaign" sets `cancelled: true`. All other buttons (Edit profile, Edit email 1, Pause, etc.) are visually present but inert. Email sequence tab switching and volume tier selection are interactive but not persisted.

### 3. Account Page (`/account/`)
- Single grid of cards: **Beehiiv API Credentials**, **AI Credits Usage**, **Account Information**, and (dev only) **Post Fetching** (last-fetch datetime, most-recent-published post, most-recent-processed post, and total/processed post counts). The Post Fetching card is hidden entirely in cloud/prod — it's a dev-only diagnostic.
- No manual "Refresh Posts" or "Process Posts" controls exist anywhere; fetching and processing are fully automatic via the Learning/Update flows.

**User Scoping**: Posts, Sections, and LinkData are all scoped to both publication AND user.

## API Endpoints

All routes use the `analytics:` namespace. See `analytics/urls.py` for the full list. Key groups:

- **Insights**: `/insights/`, `/insights/load-processed-data/`, `/insights/load-link-data/`
- **Learning / Update flow**: `/insights/learning/start/`, `/insights/learning/update/`, `/insights/learning/status/<uuid>/`, `/insights/learning/abandon/<uuid>/`
- **Content Finder**: `/insights/content-finder/posts/`, `/insights/content-finder/run/`, `/insights/content-finder/confirm-plan/<uuid>/`, `/insights/content-finder/status/<uuid>/`, `/insights/content-finder/feedback/`
- **Improvement Tips**: `/insights/improvement-tips/posts/`, `/insights/improvement-tips/run/`, `/insights/improvement-tips/status/<uuid>/`, `/insights/improvement-tips/download/<uuid>/`
- **Monetize**: `/monetize/`, `/monetize/niche-analysis/status/<uuid>/`
- **Account**: `/account/`
- **Feedback**: `/feedback/submit/`

## Deployment

The app runs in **local** or **cloud** mode. By default, local mode connects to the same AWS RDS database as cloud — `run_local_dev.sh` is the exception, which points local Django at a Postgres-in-Docker instance for fully offline testing.

### Local Mode
Reads environment variables from `.env` (via python-dotenv). A Python 3.11 venv exists at `.venv/`. The system default `python` is Python 3.8 (Anaconda) and does **not** have project dependencies. Use:

```bash
source .venv/bin/activate && python manage.py runserver
```

Or run via Docker: `./run_local.sh` (builds ARM64 image, runs on port 8000).

#### Fully-local dev DB (`run_local_dev.sh`)
For offline development against a local Postgres instead of cloud RDS:

```bash
./run_local_dev.sh
```

The script:
- Idempotently starts a `postgres:16` container named `letterpulse_pg` on **host port 5433** (not 5432, to avoid conflicts with any native Postgres on the dev machine — loopback bindings win over wildcard on macOS, which would otherwise silently misroute Django's connections), backed by Docker volume `letterpulse_pg_data`.
- Exports `DB_HOST=localhost`, `DB_PORT=5433`, and a `DATABASE_SECRET` JSON literal in the shell only — `python-dotenv`'s `load_dotenv()` defaults to `override=False`, so these shell vars take precedence over the cloud values in `.env` without modifying the file.
- Activates `.venv`, runs `migrate`, then `runserver`.

`settings.py` reads `DB_PORT` via `os.environ.get('DB_PORT', '5432')` so the cloud path (where `DB_PORT` is unset) is unchanged.

### Cloud Mode (AWS App Runner via ECR)
Requires `AWS_ACCOUNT_ID` to be exported in the shell (the script errors out if unset).
```bash
export AWS_ACCOUNT_ID=<12-digit-account-id>
./push_to_ecr.sh dev    # Pushes to letterpulse:dev-latest
./push_to_ecr.sh prod   # Pushes to letterpulse:prod-latest
./push_to_ecr.sh both   # Pushes to both
```

Dockerfile: `python:3.11-slim`, runs migrate → collectstatic → gunicorn (1 worker, 4 threads, 120s timeout).

Beehiiv API credentials are per-user (configured in Account page), not environment variables.

## Key Architecture Notes

### Credit System
Credit costs and configuration constants are in `settings.py` (search for `CREDITS_PER_*`, `DEFAULT_MONTHLY_CREDITS`). Credits charged at the view level before each AI operation via `utils.charge_credits`, which wraps a `select_for_update()` lock around `UsageAccount.ensure_current_period()` and the quota check. Billing resets on user's signup anniversary each month — `ensure_current_period()` lazily recalculates `period_start` and zeroes `used_this_period` on rollover, clamping the billing day to the last day of short months (e.g. Jan 31 signup → Feb 28 in non-leap years). Because the rollover mutates `used_this_period` in memory, `charge_credits` writes the new total as a plain Python int (NOT `F("used_this_period") + n`) so the reset actually persists.

### LLM Dev Panel (local mode only)
When `ENVIRONMENT == 'local'`, a floating panel appears after LLM-powered workflows showing per-call details (model, prompts, runtime, tokens, costs). Architecture:
- `llm_tracker.py`: Context-variable-based accumulator. `start_tracking()` / `finish_tracking()` bracket workflows; `llm_call()` in utils.py records each call. No-ops when not local
- **Data transport**: Sync workflows (Process Posts) include data in JSON response. Background workflows (Content Finder, Improvement Tips) store data in `dev_panel_data` JSONField on their Pending* models, returned via polling endpoint
- `static/analytics/js/dev-panel.js`: Frontend renderer, loaded conditionally via `{% if is_local %}`

### Background Task Pattern
Content Finder and Improvement Tips both use the same pattern: create a Pending* model row → spawn a background thread → frontend polls a status endpoint at 3s intervals → result stored on the model row.

## Testing Notes

- The `sandboxes/`, `testing_data/`, and `code_dump/` directories are for development/testing
- Time zone is set to `America/Chicago` in settings

### Automated tests

Tests live in `analytics/tests/` (e.g. `test_credits.py`) and run against a dedicated test settings module that uses an in-memory SQLite DB so they never touch RDS.

Run from the project root:
```bash
.venv/bin/python manage.py test analytics --settings=beehiiv_analytics.test_settings
```

`beehiiv_analytics/test_settings.py`:
- Imports from `settings.py` and overrides `DATABASES` to SQLite `:memory:`.
- Provides `os.environ.setdefault(...)` defaults for `SECRET_KEY`, `OPENAI_API_KEY`, `DATABASE_SECRET`, `DB_HOST`, `ENVIRONMENT` so tests run without a real `.env`.
- Uses `EMAIL_BACKEND = locmem` and blanks `SIGNUP_NOTIFICATION_EMAIL` so the post_save signal on `User` doesn't try to send mail. Tests should additionally patch `analytics.signals._send_welcome_email` (see `_CreditTestBase` in `test_credits.py`) to suppress the welcome-email daemon thread.
- Sets `MIGRATION_MODULES` to a sentinel that disables migrations — Django creates schema directly from models. This is purely for test-suite speed; real migrations run in both deployed envs (the Dockerfile invokes `migrate`) and the local-dev DB (`run_local_dev.sh` invokes `migrate`).
- Switches `PASSWORD_HASHERS` to MD5 for speed.

When adding new test files, prefer subclassing a base class that suppresses the welcome-email signal (or use `mock.patch("analytics.signals._send_welcome_email")` per test). The signal's daemon thread calls `user.refresh_from_db()` and can race with SQLite teardown.
