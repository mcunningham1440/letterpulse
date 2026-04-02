## LLM/AI agent instructions

Any time you make a change to the code, determine whether it makes any information in this file obsolete. If so, update it; otherwise, state that no update to claude.md was necessary. This should ALWAYS be the last thing you do when editing the code.

### Frontend error handling rules

- **Never use `alert()`** in JavaScript. Use `showToast()` instead (defined in base.html, available globally).
- **Error messages shown to users must be generic**: "An error occurred. Please note this in the feedback form." with a link to the Google Form. This message is stored in the `GENERIC_ERROR_TOAST` constant in base.html (available globally).
- **Validation messages** (e.g., "Please select at least one post") can be specific but must still use `showToast(..., 'warning')`, not `alert()`.
- **Never use `confirm()`** for confirmation dialogs. Use `showConfirm(message, title)` instead (defined in base.html). It returns a Promise that resolves to `true` (Proceed) or `false` (Cancel). The caller must be `async` and use `await`. Example: `if (!await showConfirm('Delete this item?')) return;`

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

## Project Overview

This application helps newsletter creators understand which content resonates with their audience by:
- Fetching post data and statistics from the Beehiiv API
- Extracting specific content items (e.g., "quick links", "product releases") using AI
- Matching links with click data using fuzzy matching (Levenshtein distance)
- Generating AI-powered content performance reports
- Annotating newsletter HTML with actionable improvement tips

## Tech Stack

- **Backend**: Django 5.0+, Python
- **Database**: PostgreSQL on AWS RDS (Aurora)
- **AI**: OpenAI API (GPT-5.1 with reasoning)
- **Authentication**: django-allauth (email-based auth)
- **Frontend**: Bootstrap 5, DataTables, jQuery, Marked.js (markdown rendering), Chart.js (trend charts on Insights page)
- **Async**: aiohttp, asyncio for parallel API calls
- **Deployment**: AWS App Runner (cloud), local development with gunicorn

## Project Structure

```
app/
├── beehiiv_analytics/          # Django project settings
│   ├── settings.py             # Main configuration (uses python-dotenv)
│   ├── urls.py                 # Root URL routing
│   ├── wsgi.py / asgi.py       # WSGI/ASGI entry points
│   └── __init__.py
├── analytics/                  # Main Django app
│   ├── models.py               # Post, ContentSet, Report, UsageAccount, ExecutionLog, SurveyResponse, ProcessedPost, LinkData, Section, ClickVizEmailLog, CronRunLog models
│   ├── views.py                # All view logic (login-protected)
│   ├── urls.py                 # App URL patterns (analytics namespace)
│   ├── utils.py                # Core utility functions (API calls, AI extraction, credit charging)
│   ├── logsink.py              # Queue-based async logging system
│   ├── logutils.py             # Logging middleware and decorators
│   ├── forms.py                # Custom allauth signup form (first/last name, newsletter name)
│   ├── admin.py                # Django admin configuration
│   ├── signals.py              # User signals (auto-create UsageAccount)
│   ├── context_processors.py   # Usage stats for templates
│   ├── templates/analytics/    # HTML templates
│   │   ├── base.html           # Base template with Bootstrap/DataTables and user sidebar
│   │   ├── account.html        # Account settings (usage, API credentials)
│   │   ├── posts.html          # Posts selection and content extraction page
│   │   └── insights.html       # Analysis and reporting page
│   └── migrations/             # Database migrations
├── data/                       # Runtime data (LLM call logs)
├── manage.py                   # Django management script
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Docker image definition for AWS App Runner
├── .dockerignore               # Files excluded from Docker builds
├── push_to_ecr.sh              # Deployment script (builds and pushes to ECR)
└── .env / .env.example         # Environment variables (local mode)
```

## Database Models

### Publication
Represents a Beehiiv publication (users may have access to multiple):
- `pub_id`: Beehiiv publication ID (unique)
- `name`: Publication name
- `organization_name`: Organization the publication belongs to

### Post
Stores newsletter post metadata and engagement stats from Beehiiv:
- `post_id`: Beehiiv post ID (unique per user)
- `user`: ForeignKey to User (owner of the post data)
- `publication`: ForeignKey to Publication (nullable)
- `title`, `subtitle`
- `status`: "Draft", "Scheduled", or "Published"
- `creation_date`: DateTime when post was created in Beehiiv (nullable, stored in UTC)
- `publish_date`: DateTime when post was published (nullable for drafts, stored in UTC)
- Engagement metrics: `recipients`, `delivered`, `email_opens`, `unique_email_opens`, `email_clicks`, `unique_email_clicks`, `unsubscribes`, `spam_reports`
- Unique constraint: `(post_id, user)` - same post can exist for multiple users

### ContentSet
Named collections of extracted content items:
- `name`: Identifier for the set (unique per publication and user)
- `user`: ForeignKey to User (owner of the content set)
- `publication`: ForeignKey to Publication (nullable)
- `items_data`: JSON array of extracted items with text, links, clicks, and CTR
- Unique constraint: `(name, publication, user)` - ensures users can have same-named sets for different publications

### Report
AI-generated content insights, scoped per section:
- `name`: Report name (auto-generated as `"{section_name} insights"`)
- `section_name`: The section this report covers (e.g., "Quick Bites")
- `user`: ForeignKey to User (owner)
- `publication`: ForeignKey to Publication (nullable)
- `content_set`: ForeignKey to ContentSet (nullable, legacy — no longer required)
- `report_text`: Markdown-formatted analysis
- Unique constraint: `(section_name, user, publication)` — one report per section per user per publication

### UsageAccount
Tracks AI usage credits and API credentials per user:
- `user`: OneToOneField to User
- `monthly_quota`: Credits available per month (default from `settings.DEFAULT_MONTHLY_CREDITS`)
- `used_this_period`: Credits consumed this period
- `period_start`: Start of current billing period (resets on user's signup anniversary each month)
- `beehiiv_token`: User's Beehiiv API key
- `beehiiv_pub_id`: User's currently selected Beehiiv publication ID
- `api_key_valid`: Boolean indicating if the API key has been validated
- `available_publications`: JSON list of publications available to the user
- `timezone`: User's preferred timezone for date display (IANA timezone string, default 'America/Chicago')
- `survey_completed`: Boolean indicating if the user has completed the signup survey
- `newsletter_name`: Name of the user's newsletter (collected at signup)
- `auto_click_viz_email`: Boolean — whether to auto-email click visualizations after post publication (default False)
- `auto_click_viz_enabled_at`: DateTimeField (nullable) — when the user enabled the feature; prevents old posts from triggering emails

Billing cycle: Credits reset on the same day of the month as the user's signup date (e.g., signup on the 15th means credits renew on the 15th of each month). For months with fewer days, renewal occurs on the last day of the month.

API key validation: When a user enters their API key, it is immediately validated against the Beehiiv `/publications` endpoint. If valid, the list of available publications is cached and the user can select which publication to work with.

### ExecutionLog
Low-overhead execution logging for HTTP requests and function calls:
- `ts_start`, `ts_end`: Start and end timestamps
- `duration_ms`: Execution duration in milliseconds
- `kind`: "request" (HTTP) or "function" (decorated functions)
- `name`: View name or module.function
- `success`: Boolean indicating success/failure
- `error_type`, `error_message`, `traceback`: Error details if failed
- `user`: ForeignKey to User (nullable)
- `request_id`: UUID for request correlation
- `parent_id`: Optional BigIntegerField for nesting
- `inputs`, `outputs`, `meta`: JSONField placeholders (currently empty)
- Indexes on: `created_at`, `(kind, name)`, `request_id`, `success`

Uses queue-based async logging via `logsink.py` with a background worker thread for batch inserts.

### SurveyResponse
Stores user responses to the signup survey (displayed on first login):
- `user`: OneToOneField to User
- `beehiiv_analytics_inadequate`: Boolean (nullable) - whether user feels Beehiiv analytics are inadequate
- `missing_features`: Text field for features the user feels are missing from Beehiiv
- `other_tools`: Text field for other third-party tools the user uses for newsletter analytics

The survey modal appears automatically on first login and is dismissed once submitted. Survey completion is tracked via `UsageAccount.survey_completed`.

### ProcessedPost
Lightweight marker indicating a post has been processed for section data:
- `post`: ForeignKey to Post (the source post)
- `user`: ForeignKey to User (owner)
- `publication`: ForeignKey to Publication (nullable)
- Unique constraint: `(post, user)` - one marker per post per user

Created automatically when "Process Selected Posts" runs. The actual section data is stored in the `Section` model.

### Section
Stores section data extracted from a processed post via an agentic GPT loop:
- `post`: ForeignKey to Post (the source post)
- `user`: ForeignKey to User (owner)
- `publication`: ForeignKey to Publication (nullable)
- `section_name`: Snake-case identifier for the section (e.g., "tech_news", "quick_links")
- `section_title`: Display title as it appears in the newsletter (nullable, None for untitled sections)
- `section_description`: Brief description of the section's format
- `start_line`: 1-based starting line number in the post HTML
- `end_line`: 1-based ending line number in the post HTML
- `post_html_length`: Total line count of the post HTML
- `section_html`: The raw HTML content of the section
- Unique constraint: `(post, user, section_name)`

Created via the "Process Selected Posts" workflow. Each row represents one structural section identified in the post HTML. Posts are processed sequentially so each post's sections enrich the context for subsequent posts.

### LinkData
Stores described link data extracted from a processed post, grouped by section:
- `post`: ForeignKey to Post
- `user`: ForeignKey to User
- `publication`: ForeignKey to Publication (nullable)
- `raw_url`: The link URL (tracking params stripped)
- `description`: AI-generated description of the link destination
- `section_name`: Which section this link belongs to (matches `Section.section_name`)
- `rank_in_post`: Global rank among all selected links in the post (by CTR descending)
- `rank_in_section`: Rank among ALL links in the section (by CTR descending, computed before filtering)
- `mean_ctr`: Mean CTR as percentage
- `mean_clicks`: Mean unique clicks
- Unique constraint: `(post, user, raw_url, section_name)`

Created atomically alongside Section rows during "Process Selected Posts". Links are allocated across sections using `LINK_PROCESS_TOP_N`; if a section has more links than its allocation, the top and bottom by CTR are selected.

### PendingReport
Tracks background report generation tasks:
- `task_id`: UUID (unique, auto-generated)
- `user`: ForeignKey to User
- `publication`: ForeignKey to Publication (nullable)
- `section_name`: The section being generated for
- `status`: "pending", "complete", or "error"
- `result_text`: The generated report markdown (populated on completion)
- `error_message`: Error details (populated on failure)

Created when a user initiates section-level report generation. One PendingReport is created per section. The LLM call runs in a background thread per section. The frontend polls `/insights/report-status/<task_id>/` for each task until all complete, then shows a review overlay.

## Authentication

Uses django-allauth for email-based authentication:
- Email as primary identifier; usernames are auto-generated from the email local part (e.g. `user@example.com` → `user`), with progressive integers appended if taken (`user1`, `user2`, etc.)
- Signup collects first name, last name, newsletter name, email, and password (custom form in `analytics/forms.py`, adapter in `analytics/adapters.py`)
- Password-based login at `/accounts/login/`
- Registration at `/accounts/signup/`
- Password reset via email
- All views are protected with `@login_required` except the public about page
- UsageAccount is auto-created for new users via signals; a signup notification email is sent in a background thread to `SIGNUP_NOTIFICATION_EMAIL` (if configured)
- "Successfully signed in as" message is suppressed via custom adapter
- After login, users without API credentials are redirected to Account page (not Posts); users with credentials go to Posts
- The "Please configure your Beehiiv API credentials" message only appears when navigating to Posts/Insights without credentials, not immediately after login

### Public Pages
- `/` - About page (unauthenticated users see landing page; authenticated users redirect to posts)

## Key Features & Workflows

### 1. Posts Page (`/posts/`)
- **Refresh Posts**: Fetches all posts from Beehiiv API with pagination
- **Select Posts**: DataTable with sorting by date, opens, clicks
- **Process Selected Posts**: Runs immediately when clicked (no modal). For each post, fetches HTML from Beehiiv, runs `auto_section` to identify structural sections, then runs `process_post_links` to extract/describe/score links within each section. Both operations must succeed atomically — if either fails, nothing is saved for that post. Section and LinkData rows plus the `ProcessedPost` marker are saved in a single DB transaction. Shows progress bar during extraction. If any selected posts already have processed data, shows an overwrite warning. First 2 posts are processed sequentially to seed section context; remaining posts run in parallel.
- **Processed Column**: Shows a green checkmark for posts that have been processed. Trash icon to clear processed data (deletes `ProcessedPost`, `Section`, and `LinkData` records).
- **Download Click Visualization**: ZIP of HTML files with click counts overlaid on links
- **Download Improvement Tips**: ZIP of HTML files with AI-generated improvement tips

### 2. Insights Page (`/insights/`)
- **Section Data Table**: DataTable showing all Sections for the current publication — Post Title, Post Date, Section Name, Description, Start Line, End Line. Sortable and scrollable. Download CSV button (client-side).
- **Link Data Table**: DataTable showing all LinkData for the current publication — Post Title, Post Date, Section, URL, Description, Rank in Section, Mean CTR (%), Mean Clicks. Sortable and scrollable. Download CSV button (client-side).

### 3. Account Page (`/account/`)
- **Usage Stats**: View AI credits used and remaining
- **API Credentials**: Configure Beehiiv API key (validated on save)
- **Publication Selector**: Dropdown to switch between available publications (populated from API)
- **Account Info**: View email and change password

**Publication Switching**: Each publication has its own posts, content sets, and reports. Switching publications changes the active data context throughout the app.

**User Scoping**: Posts and ContentSets are scoped to both publication AND user. This means two different users can use the same publication without seeing each other's data.

### 4. Signup Survey
A modal survey appears on first login for new users, collecting feedback about:
1. Whether they feel Beehiiv's existing analytics tools are inadequate (yes/no)
2. What analytics features they feel are missing from Beehiiv (freeform text)
3. What other third-party tools they use for newsletter analytics (freeform text)

The survey is required (modal blocks interaction until submitted) and responses are stored in the `SurveyResponse` model. Once submitted, `UsageAccount.survey_completed` is set to `True` and the survey won't appear again.

### ClickVizEmailLog
Log of click visualization emails sent to users:
- `user`: ForeignKey to User
- `publication`: ForeignKey to Publication (nullable)
- `post_id`: CharField — Beehiiv post ID (not FK to Post, since the Post record may not exist in DB)
- `post_title`: CharField (blank)
- `sent_at`: DateTimeField (auto_now_add)
- `success`: BooleanField (default True)
- `error_message`: TextField (blank)
- Unique constraint: `(user, post_id)` — prevents duplicate emails

### CronRunLog
Log of each management command invocation for monitoring:
- `command`: CharField — management command name (e.g. `send_click_viz_emails`)
- `started_at`: DateTimeField
- `finished_at`: DateTimeField (nullable)
- `duration_ms`: PositiveIntegerField (nullable)
- `users_processed`: PositiveIntegerField
- `emails_sent`: PositiveIntegerField
- `errors`: PositiveIntegerField
- `output`: TextField — captured stdout from the command
- `success`: BooleanField
- `triggered_by`: CharField — `cron`, `manual`, etc.

## API Endpoints

All routes use the `analytics:` namespace.

### Posts Routes
- `GET /posts/` - Main posts page
- `POST /posts/run/` - Run AI content extraction (legacy single-description flow)
- `POST /posts/process/` - Run section-level extraction on selected posts, stores results in Section (AJAX)
- `POST /posts/save/` - Save extracted items as ContentSet
- `POST /posts/delete-items/` - Remove items from session
- `POST /posts/refresh-posts/` - Fetch latest posts from Beehiiv
- `POST /posts/download-click-viz/` - Generate click visualization ZIP
- `POST /posts/download-annotated/` - Generate annotated HTML ZIP
- `POST /posts/clear-processed/` - Delete ProcessedPost, Section, and LinkData records for selected posts (AJAX)

### Insights Routes
- `GET /insights/` - Insights dashboard (section and link data tables)
- `GET /insights/load-processed-data/` - Load all Section items as JSON for the current user/publication
- `GET /insights/load-link-data/` - Load all LinkData items as JSON for the current user/publication

### Account Routes
- `GET /account/` - Account settings page
- `POST /account/` - Update API credentials, toggle click viz email

### Survey Routes
- `POST /survey/submit/` - Submit signup survey response

### Cron Routes
- `GET /cron/click-viz-status/` - JSON status page showing recent cron runs, email logs, and eligible users (login required; non-superusers see only their own email logs)

## Deployment Modes

The app can run in two modes: **local** and **cloud**. Both modes connect to the same AWS RDS database.

### Local Mode

For local development, the app reads environment variables from the `.env` file (via `python-dotenv`).

Required in `.env`:
```
# Django
SECRET_KEY=your-secret-key-here

# Database credentials as JSON (matches AWS Secrets Manager format)
DATABASE_SECRET={"username":"your_db_user","password":"your_db_password"}

# API Keys
OPENAI_API_KEY=your-openai-api-key

# Email (Gmail SMTP for signup notifications)
EMAIL_HOST_USER=yourgmail@gmail.com
EMAIL_HOST_PASSWORD=xxxx-xxxx-xxxx-xxxx
SIGNUP_NOTIFICATION_EMAIL=yourgmail@gmail.com
```

**Important:** `SECRET_KEY` is used to derive the encryption key for user beehiiv tokens (via `EncryptedCharField`). Changing the SECRET_KEY will make existing encrypted tokens unreadable. Always backup the SECRET_KEY alongside database backups.

A Python 3.11 virtual environment exists at `.venv/`. The system default `python` is Python 3.8 (Anaconda) and does **not** have project dependencies installed. When running Python commands locally, use the venv explicitly:

```bash
source .venv/bin/activate && python manage.py runserver
```

Or directly:

```bash
.venv/bin/python manage.py check
```

Or run locally with Docker (mirrors production environment):
```bash
./run_local.sh
```

**`run_local.sh`** builds and runs the app in a Docker container:
1. Stops and removes any existing `letterpulse_local` container
2. Builds the Docker image for ARM64 (Apple Silicon)
3. Runs the container on port 8000 with environment variables from `.env`

The app will be available at `http://localhost:8000`. The script uses relative paths so it can be run from any directory.

### Cloud Mode (AWS App Runner via ECR)

For production, the app runs on AWS App Runner using a Docker image stored in ECR. Secrets are configured in the App Runner service settings to pull from AWS Secrets Manager.

**Dockerfile** builds a `python:3.11-slim` image that:
1. Installs system dependencies (`gcc`, `python3-dev`)
2. Installs Python dependencies from `requirements.txt`
3. On startup: runs `migrate`, `collectstatic`, then starts gunicorn (1 worker, 4 threads, 120s timeout)

**Deployment** via `push_to_ecr.sh`:
```bash
./push_to_ecr.sh dev    # Pushes to letterpulse:dev-latest
./push_to_ecr.sh prod   # Pushes to letterpulse:prod-latest
./push_to_ecr.sh both   # Pushes to both dev and prod
```

The script:
1. Logs into ECR
2. Builds the image for `linux/amd64` (required for App Runner)
3. Tags and pushes to ECR
4. App Runner auto-deploys when a new image is pushed

### Environment Variable Format

**DATABASE_SECRET**: JSON string with database credentials. AWS RDS Secrets Manager automatically generates this format:
```json
{"username": "db_user", "password": "db_password"}
```

The `settings.py` parses this JSON to configure the Django database connection:
```python
db = json.loads(os.environ["DATABASE_SECRET"])
DATABASES = {
    'default': {
        'USER': db['username'],
        'PASSWORD': db['password'],
        'HOST': 'letterpulse-dev.cluster-....us-east-1.rds.amazonaws.com',
        ...
    }
}
```

**OPENAI_API_KEY**: Standard OpenAI API key string.

**Note:** Beehiiv API credentials (token and publication ID) are configured per-user in the Account settings page, not via environment variables.

## Key Utility Functions (analytics/utils.py)

### API Functions
All Beehiiv API functions require `beehiiv_token` and `beehiiv_pub_id` parameters (obtained from user's UsageAccount):
- `validate_beehiiv_api_key(beehiiv_token)`: Validate API key and return list of available publications
- `fetch_post_html(session, post_id, semaphore, beehiiv_token, beehiiv_pub_id)`: Fetch individual post HTML
- `fetch_post_clicks(session, post_id, semaphore, beehiiv_token, beehiiv_pub_id)`: Fetch individual post clicks
- `fetch_posts_html_and_clicks_parallel(post_ids, beehiiv_token, beehiiv_pub_id)`: Batch fetch with semaphore (5 concurrent)
- `fetch_all_posts(beehiiv_token, beehiiv_pub_id)`: Paginated fetch of all posts (includes drafts, confirmed, and archived via `status=all`)
- `refresh_posts_data(beehiiv_token, beehiiv_pub_id)`: Full refresh from Beehiiv API
- `process_posts_data()`: Converts raw API data to DataFrame. All dates are stored in UTC. Drafts have null `publish_date`

Views use `get_user_api_credentials(user)` helper to retrieve credentials and redirect to Account page if not configured.

### Database Functions
- `load_posts_from_db(publication_id=None, user=None)`: Load posts from database filtered by publication and user

### AI Functions
- `llm_call(user=None)`: Wrapper for OpenAI API with logging to CSV
- `charge_credits(user, credits)`: Atomically charge credits against user quota
- `NotEnoughCredits`: Exception raised when quota exceeded
- `extract_items()`: AI-powered content extraction from HTML (single-description, legacy)
- `allocate_links_to_sections(section_link_counts, top_n)`: Divide `top_n` link slots fairly across sections; sections with fewer links than their share get capped and surplus is redistributed
- `select_top_bottom(link_stats, n)`: Select top `ceil(n/2)` and bottom `floor(n/2)` links by CTR from a sorted list
- `process_post_links(session, post_id, user, beehiiv_token, beehiiv_pub_id, sections, pretty_html)`: Section-aware link extraction. Matches clicks at post level, assigns links to sections by line position, allocates via `LINK_PROCESS_TOP_N`, selects top/bottom per section, uses GPT-5.4-mini for descriptions. Returns list of link row dicts with `section_name` and `rank_in_section`.
- `build_sections_desc(user, publication, post, n_examples=5)`: Build context prompt from existing sections of nearby posts for the agentic loop
- `auto_section(html, user, publication, post, n_examples=5, pretty_html=None)`: Single structured-output LLM call (returns `AllSections` Pydantic model) to identify structural sections in newsletter HTML. Accepts optional pre-prettified HTML to avoid double-prettifying.
- `process_post_full(session, post_id, user, beehiiv_token, beehiiv_pub_id, publication)`: Fetch HTML, run `auto_section` then `process_post_links` using the same prettified HTML. If either fails, the exception propagates and nothing is saved.
- `_save_post_full(post_id, sections, link_rows, user, publication)`: Save Section rows, LinkData rows, and ProcessedPost marker in a single atomic transaction.
- `process_posts_sections_sequential(post_ids, user, beehiiv_token, beehiiv_pub_id, publication)`: Process multiple posts with hybrid sequential/parallel strategy. Uses `process_post_full` + `_save_post_full` per post.
- `generate_content_insights()`: Generate performance analysis report for a single section. When item count exceeds `MAX_REPORT_ITEMS`, top and bottom performers by CTR are kept and middle items are omitted (with a note to the LLM).
- `annotate_post_html(post_id, content_perf_evals, beehiiv_token, beehiiv_pub_id, user=None)`: Insert improvement tips into HTML
- `annotate_posts_parallel(post_ids, content_perf_evals, beehiiv_token, beehiiv_pub_id, user=None)`: Parallel annotation of multiple posts
- `fetch_recent_published_posts(beehiiv_token, beehiiv_pub_id, max_pages=3)`: Async — fetch recently published posts ordered by publish_date desc; stops early if oldest post on page is >24h old
- `build_click_viz_email_html(viz_html, post_title, site_url)`: Wrap click viz HTML with branded header banner and footer for email delivery

## Management Commands

### `send_click_viz_emails`
Sends click visualization emails for posts published more than 24 hours ago.

```bash
python manage.py send_click_viz_emails [--dry-run] [--user-email=<email>]
```

**Flow per user** (only users with `auto_click_viz_email=True` and `api_key_valid=True`):
1. Calls `fetch_recent_published_posts()` to get recent published posts from Beehiiv API
2. Filters to posts where: `publish_date` > `auto_click_viz_enabled_at`, `publish_date` < `now - 24h`, and no successful `ClickVizEmailLog` exists for the user+post_id
3. Generates click visualization HTML and emails it via Django's `EmailMessage`
4. Creates `ClickVizEmailLog` entry (success or failure)

Runs automatically every 30 minutes via a background daemon thread started in `AnalyticsConfig.ready()` (gunicorn and runserver only — does not start during migrations or other management commands). Can also be run manually via `python manage.py send_click_viz_emails`.

## Credit System Configuration

Credit costs are configured in `settings.py`:

```python
# Default monthly credits for new users
DEFAULT_MONTHLY_CREDITS = 150

# Credit costs per operation
CREDITS_PER_EXTRACTION = 1      # Per post extracted from
CREDITS_PER_REPORT = 1          # Flat cost for generating insights
CREDITS_PER_ANNOTATION = 1      # Per post annotated with improvement tips

# Section processing configuration
SECTION_N_EXAMPLES = 5              # Number of nearby-post examples per section for context

# Link processing configuration
LINK_PROCESS_TOP_N = 60             # Total links to select across all sections
LINK_PROCESS_MAX_RETRIES = 2        # Max LLM retries for link description count mismatch

# Maximum items sent to the LLM for report generation
MAX_REPORT_ITEMS = 150

# Whether to show the signup survey modal to new users
SIGNUP_SURVEY_ENABLED = False

# Maximum new user signups allowed per rolling 24-hour window (None = unlimited)
DAILY_SIGNUP_CAP = 5

# Auto click viz email settings
SITE_URL = 'https://letterpulse.com'  # Base URL for links in emails (env: SITE_URL)

```

Credits are charged at the view level before each AI operation runs.

### Link Matching
- `match_links_with_clicks()`: Uses exact matching first, then Levenshtein distance (40% threshold) for fuzzy matching

## Execution Logging System

Queue-based async logging for HTTP requests and function calls with minimal overhead:

### Architecture
- `ExecutionLoggingMiddleware` in `logutils.py` logs all HTTP requests automatically
- `@log_function()` decorator for logging specific function executions
- `LogSink` in `logsink.py` manages a Queue + background worker thread
- Batch inserts via `bulk_create()` every 50 entries or 1 second
- Each Gunicorn worker process has its own queue and worker thread

### Configuration (settings.py)
```python
EXECUTION_LOG_QUEUE_MAXSIZE = 2000   # Max entries in queue before overflow
EXECUTION_LOG_BATCH_SIZE = 50        # Entries per bulk_create
EXECUTION_LOG_FLUSH_INTERVAL = 1.0   # Seconds between flushes
EXECUTION_LOG_ON_FULL = 'drop'       # 'drop' or 'sync' when queue is full
```

### Usage
```python
from analytics.logutils import log_function

@log_function()
async def my_function():
    ...

@log_function(name="custom.name")
def another_function():
    ...
```

### Resilience
- Queue full → logs dropped silently (or sync-write if `EXECUTION_LOG_ON_FULL='sync'`)
- Database errors caught and never propagated to the application
- Worker thread is a daemon thread (won't block app shutdown)

## Testing Notes

- LLM calls are logged to `data/llm_call_logs.csv` with timing and token usage
- The `sandboxes/`, `testing_data/`, and `code_dump/` directories are for development/testing
- Time zone is set to `America/Chicago` in settings