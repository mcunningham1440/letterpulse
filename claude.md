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
- **Post**: Newsletter post metadata + engagement stats from Beehiiv. Scoped to `(post_id, user)`
- **ContentSet**: Named collections of extracted content items (legacy). Scoped to `(name, publication, user)`
- **Report**: AI-generated insights per section. Has legacy nullable `content_set` FK. Scoped to `(section_name, user, publication)`
- **UsageAccount**: Per-user credits, API credentials, preferences. OneToOneField to User. Billing resets on signup anniversary each month
- **ProcessedPost**: Marker that a post has been processed. Scoped to `(post, user)`
- **Section**: Structural section extracted from a post's HTML (name, title, line range, HTML content). Scoped to `(post, user, section_name)`
- **LinkData**: Described links with CTR data, grouped by section. Scoped to `(post, user, raw_url, section_name)`
- **PendingContentSearch**: Background content finder task tracker. Has `dev_panel_data` JSONField for local dev panel
- **PendingImprovementTips**: Background improvement tips task tracker. Has `dev_panel_data` JSONField for local dev panel
- **ExecutionLog**: Low-overhead request/function logging with queue-based async writes via `logsink.py`
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
- After login: users without API credentials → Account page; users with credentials → Posts page
- "Please configure your Beehiiv API credentials" message only appears when navigating to Posts/Write without credentials, not immediately after login

**Important:** `SECRET_KEY` derives the encryption key for user beehiiv tokens (via `EncryptedCharField`). Changing it makes existing encrypted tokens unreadable.

## Key Features & Workflows

### 1. Posts Page (`/posts/`)
- **Refresh Posts**: Fetches all posts from Beehiiv API with pagination
- **Process Selected Posts**: Runs immediately (no modal). For each post: fetches HTML, runs `auto_section` → `process_post_links`. Both must succeed atomically — if either fails, nothing is saved. Section + LinkData + ProcessedPost marker saved in a single DB transaction. First 2 posts run sequentially to seed section context; remaining posts run in parallel
- **Processed Column**: Green checkmark for processed posts; trash icon to clear (deletes ProcessedPost, Section, LinkData)
- **Download Click Visualization**: ZIP of HTML files with click counts overlaid on links

### 2. Write Page (`/insights/`)
- **Content Finder**: User selects a processed post as template. Per-section agentic LLM loop uses Perplexity web search to find new content matching historical click patterns. Background task with polling (3s interval). Results in Bootstrap accordion grouped by section
- **Section/Link Data Tables**: Hidden by default, show all Sections and LinkData for current publication with CSV download
- **Improvement Tips**: User selects any post (published or draft). Background task builds numbered text with HTML line mapping, gathers link history, calls LLM with structured output, generates two-column annotated HTML with tip cards and SVG connectors. Downloaded as file

### 3. Account Page (`/account/`)
- Usage stats, API credentials (validated on save), publication selector, account info

**User Scoping**: Posts, ContentSets, Sections, and LinkData are all scoped to both publication AND user.

## API Endpoints

All routes use the `analytics:` namespace. See `analytics/urls.py` for the full list. Key groups:

- **Posts**: `/posts/`, `/posts/process/`, `/posts/refresh-posts/`, `/posts/clear-processed/`
- **Insights**: `/insights/`, `/insights/load-processed-data/`, `/insights/load-link-data/`
- **Content Finder**: `/insights/content-finder/posts/`, `/insights/content-finder/sections/`, `/insights/content-finder/run/`, `/insights/content-finder/status/<uuid>/`, `/insights/content-finder/feedback/`
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
Content Finder, Improvement Tips, and Report generation all use the same pattern: create a Pending* model row → spawn a background thread → frontend polls a status endpoint at 3s intervals → result stored on the model row.

### Management Commands
- `send_click_viz_emails [--dry-run] [--user-email=<email>]`: Emails click visualizations for posts published >24h ago. Runs automatically every 30 minutes via a daemon thread in `AnalyticsConfig.ready()` (gunicorn/runserver only)

## Testing Notes

- LLM calls are logged to `data/llm_call_logs.csv` with timing and token usage
- The `sandboxes/`, `testing_data/`, and `code_dump/` directories are for development/testing
- Time zone is set to `America/Chicago` in settings
