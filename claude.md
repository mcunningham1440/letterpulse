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
│   ├── views.py                # All view logic (login-protected)
│   ├── urls.py                 # App URL patterns (analytics namespace)
│   ├── utils.py                # Core utility functions (API calls, AI extraction, credit charging)
│   ├── llm_tracker.py          # Thread-local LLM call accumulator for dev panel
│   ├── logsink.py              # Queue-based async logging system
│   ├── logutils.py             # Logging middleware and decorators
│   ├── forms.py                # Custom allauth signup form
│   ├── adapters.py             # Custom allauth adapter
│   ├── signals.py              # User signals (auto-create UsageAccount)
│   ├── context_processors.py   # Usage stats and environment context for templates
│   ├── templates/analytics/    # HTML templates (base, posts, insights, account, about, mobile)
│   ├── static/analytics/js/    # JS including dev-panel.js (local mode only)
│   └── migrations/
├── data/                       # Runtime data (LLM call logs)
├── manage.py
├── requirements.txt
├── Dockerfile
├── push_to_ecr.sh              # Deployment script (builds and pushes to ECR)
├── run_local.sh                # Local Docker development
└── .env / .env.example
```

## Database Models

All models are in `analytics/models.py`. Key models and their relationships:

- **Publication**: Beehiiv publication (users may have multiple)
- **Post**: Newsletter post metadata + engagement stats from Beehiiv, including `platform` ('email', 'web', or 'both') used to filter for processing eligibility. Scoped to `(post_id, user)`
- **UsageAccount**: Per-user credits, API credentials, preferences. OneToOneField to User. Billing resets on signup anniversary each month
- **ProcessedPost**: Marker that a post has been processed. Scoped to `(post, user)`
- **Section**: Structural section extracted from a post's HTML (name, title, line range, HTML content). Scoped to `(post, user, section_name)`
- **LinkData**: Described links with CTR data, grouped by section. Scoped to `(post, user, raw_url, section_name)`
- **PendingContentSearch**: Background content finder task tracker. Has `dev_panel_data` JSONField for local dev panel
- **PendingImprovementTips**: Background improvement tips task tracker. Has `dev_panel_data` JSONField for local dev panel
- **PendingLearningTask**: Tracks the onboarding "Learning Your Audience" flow (`kind='initial'`) and the per-page-load "Updating Your Posts" flow (`kind='update'`). Fields include `phase` (fetch/process), `status` (pending/running/complete/error/abandoned), `target_process_count`, `posts_processed_count`, `last_heartbeat` (used by stale-task sweep). A runner thread checks the `abandoned` flag between steps; for `kind='initial'` abandonment triggers `wipe_user_publication_data`
- **ExecutionLog**: Low-overhead request/function logging with queue-based async writes via `logsink.py`
- **LLMCall**: Per-call record for every OpenAI Responses API invocation made through `utils.llm_call`. Written asynchronously via the same `logsink.py` queue (routed by `_target='LLMCall'`). Stores timestamps, user, publication, function_name, model, token breakdowns (cached/new input, reasoning/response output), success/error, task_id + task_kind (for correlating with `PendingContentSearch` / `PendingImprovementTips` / `PendingLearningTask`), and a free-form `additional_info` JSONField (e.g. `{'section_name': ...}` for content finder calls). Does NOT store prompts or outputs — use the dev panel for those. Context (user/pub/task/section) flows in via `contextvars` set by `analytics.llm_tracker.set_llm_context` / `set_additional_info`, called from the background worker entry points
- **Feedback**: Per-user feature feedback. Scoped to `(user, feature)`
- **SurveyResponse**: Signup survey answers (survey currently disabled via `SIGNUP_SURVEY_ENABLED = False`)
- **ClickVizEmailLog**: Log of auto-emailed click visualizations. Scoped to `(user, post_id)`
- **ContentSearchFeedback**: Thumbs-up/down feedback on content finder results. Scoped to `(user, publication, url)`. Reviewed URLs are excluded from future content searches
- **CronRunLog**: Management command execution log

## Authentication

- Email as primary identifier; usernames auto-generated from email local part with progressive integers if taken
- Signup collects first name, last name, newsletter name, email, password
- All views `@login_required` except the public about page at `/`
- UsageAccount auto-created for new users via signals
- After login: users without API credentials → Account page; users with credentials → Write page
- "Please configure your Beehiiv API credentials" message only appears when navigating to Write without credentials, not immediately after login

**Important:** `SECRET_KEY` derives the encryption key for user beehiiv tokens (via `EncryptedCharField`). Changing it makes existing encrypted tokens unreadable.

## Key Features & Workflows

### 1. Write Page (`/insights/`) — also the onboarding entrypoint
- **Learning Your Audience (initial onboarding)**: After a user first saves valid Beehiiv credentials, the Write page shows a blocking "Learning Your Audience" coach. Clicking **Scan** starts a background `PendingLearningTask` (kind='initial'): full Beehiiv post fetch, then process the top-k most recent eligible posts (published ≥48h ago with `platform ∈ {'email','both'}`). `k` is chosen so the sum of the posts' `recipients` is ≥ `subscribers × 15` (subscribers fetched from `GET /v2/publications/{id}?expand=stats`). Post processing is free (no credit charge) but silently capped at `MAX_POSTS_PROCESSED_PER_PERIOD` (45) per billing period — over-cap posts are truncated from the selection and become eligible again next period via the update flow. The modal is **non-dismissible** — if the user reloads, navigates away, or closes the tab before completion, `navigator.sendBeacon` fires `abandon_learning_task/`, which sets `status='abandoned'` and wipes all Posts/ProcessedPost/Section/LinkData for the (user, pub) and removes `pub_id` from `UsageAccount.initial_fetched_pub_ids`. A stale-task sweep (15s `last_heartbeat` threshold) backs up the beacon.
- **Updating Your Posts (auto-refresh)**: On every Write page load, if initial learning has finished and no task is running, JS checks a 15-minute TTL (`localStorage` keyed as `incrementalRefreshLastRun:{pub_id}`) and, if expired, kicks off a `PendingLearningTask` (kind='update'): incremental fetch (speculative-prefetch window size 1) + process any newly-eligible posts. Runs silently in the background — the modal is only revealed on the transition into the `process` phase (i.e., newly-eligible posts were found and need processing). If the fetch turns up nothing, the task completes without ever surfacing UI.
- **Content Finder**: User selects a processed post as template. Runs in three stages tracked on the same `PendingContentSearch` row (polled at 3s): (1) **plan** — gpt-5.4 sees all sections of the post and drafts a search plan, which is shown to the user in a modal with a free-form feedback textbox; (2) **dispatch** — after the user clicks Confirm, a gpt-5.4 call turns the plan + feedback into a structured `List[str]` of section labels (hard cap 6, may include `"Other Interesting Links"`); (3) **search** — each dispatched label spawns a parallel gpt-5.4-mini agent that runs 3 rounds of Perplexity search + a final structured-output call. Conversation context is carried explicitly as a growing message list: each stage's input starts with the prior stage's full input plus the prior response's output items (serialized via `response.model_dump(mode='json')`), so every search agent sees the original `CONTENT_FINDER_PLAN_PROMPT`, the concatenated per-section user prompt, the plan output, the user feedback, the dispatch output, and then its own `CONTENT_FINDER_SEARCH_PROMPT`. `plan_messages` and `dispatch_messages` JSONFields on `PendingContentSearch` persist these lists between threads. Results render in a Bootstrap accordion grouped by section label
- **Section/Link Data Tables**: Hidden by default, show all Sections and LinkData for current publication with CSV download
- **Improvement Tips**: User selects any post (published or draft). Background task builds numbered prettified HTML, gathers link history, calls LLM with structured output (model returns tips referencing HTML line numbers), then inserts inline anchor `<span>`s into the live DOM (via BeautifulSoup) so logically-continuous paragraphs stay grouped even when split across many prettified lines by inline tags like `<a>`. Generates two-column annotated HTML with tip cards and SVG connectors. Downloaded as file

### 2. Account Page (`/account/`)
- Single grid of cards: **Beehiiv API Credentials**, **AI Credits Usage**, **Account Information**, and **Post Fetching** (last-fetch datetime, most-recent-published post, and — dev only — most-recent-processed post plus total/processed post counts).
- No manual "Refresh Posts" or "Process Posts" controls exist anywhere; fetching and processing are fully automatic via the Learning/Update flows.

**User Scoping**: Posts, Sections, and LinkData are all scoped to both publication AND user.

## API Endpoints

All routes use the `analytics:` namespace. See `analytics/urls.py` for the full list. Key groups:

- **Insights**: `/insights/`, `/insights/load-processed-data/`, `/insights/load-link-data/`
- **Learning / Update flow**: `/insights/learning/start/`, `/insights/learning/update/`, `/insights/learning/status/<uuid>/`, `/insights/learning/abandon/<uuid>/`
- **Content Finder**: `/insights/content-finder/posts/`, `/insights/content-finder/run/`, `/insights/content-finder/confirm-plan/<uuid>/`, `/insights/content-finder/status/<uuid>/`, `/insights/content-finder/feedback/`
- **Improvement Tips**: `/insights/improvement-tips/posts/`, `/insights/improvement-tips/run/`, `/insights/improvement-tips/status/<uuid>/`, `/insights/improvement-tips/download/<uuid>/`
- **Account**: `/account/`
- **Feedback/Survey**: `/feedback/submit/`, `/survey/submit/`
- **Cron**: `/cron/click-viz-status/`

## Deployment

The app runs in **local** or **cloud** mode. Both connect to the same AWS RDS database.

### Local Mode
Reads environment variables from `.env` (via python-dotenv). A Python 3.11 venv exists at `.venv/`. The system default `python` is Python 3.8 (Anaconda) and does **not** have project dependencies. Use:

```bash
source .venv/bin/activate && python manage.py runserver
```

Or run via Docker: `./run_local.sh` (builds ARM64 image, runs on port 8000).

### Cloud Mode (AWS App Runner via ECR)
```bash
./push_to_ecr.sh dev    # Pushes to letterpulse:dev-latest
./push_to_ecr.sh prod   # Pushes to letterpulse:prod-latest
./push_to_ecr.sh both   # Pushes to both
```

Dockerfile: `python:3.11-slim`, runs migrate → collectstatic → gunicorn (1 worker, 4 threads, 120s timeout).

Beehiiv API credentials are per-user (configured in Account page), not environment variables.

## Key Architecture Notes

### Credit System
Credit costs and configuration constants are in `settings.py` (search for `CREDITS_PER_*`, `DEFAULT_MONTHLY_CREDITS`). Credits charged at the view level before each AI operation. Billing resets on user's signup anniversary each month.

### LLM Dev Panel (local mode only)
When `ENVIRONMENT == 'local'`, a floating panel appears after LLM-powered workflows showing per-call details (model, prompts, runtime, tokens, costs). Architecture:
- `llm_tracker.py`: Context-variable-based accumulator. `start_tracking()` / `finish_tracking()` bracket workflows; `llm_call()` in utils.py records each call. No-ops when not local
- **Data transport**: Sync workflows (Process Posts) include data in JSON response. Background workflows (Content Finder, Improvement Tips) store data in `dev_panel_data` JSONField on their Pending* models, returned via polling endpoint
- `static/analytics/js/dev-panel.js`: Frontend renderer, loaded conditionally via `{% if is_local %}`

### Background Task Pattern
Content Finder and Improvement Tips both use the same pattern: create a Pending* model row → spawn a background thread → frontend polls a status endpoint at 3s intervals → result stored on the model row.

### Management Commands
- `send_click_viz_emails [--dry-run] [--user-email=<email>]`: Emails click visualizations for posts published >24h ago. Runs automatically every 30 minutes via a daemon thread in `AnalyticsConfig.ready()` (gunicorn/runserver only)

## Testing Notes

- The `sandboxes/`, `testing_data/`, and `code_dump/` directories are for development/testing
- Time zone is set to `America/Chicago` in settings
