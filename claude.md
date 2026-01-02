## LLM/AI agent instructions

Any time you make a change to the code, determine whether it makes any information in this file obsolete. If so, update it; otherwise, state that no update to claude.md was necessary. This should ALWAYS be the last thing you do when editing the code.

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
- **Frontend**: Bootstrap 5, DataTables, jQuery, Marked.js (markdown rendering)
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
│   ├── models.py               # Post, ContentSet, Report, UsageAccount, ExecutionLog models
│   ├── views.py                # All view logic (login-protected)
│   ├── urls.py                 # App URL patterns (analytics namespace)
│   ├── utils.py                # Core utility functions (API calls, AI extraction, credit charging)
│   ├── logsink.py              # Queue-based async logging system
│   ├── logutils.py             # Logging middleware and decorators
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
AI-generated content insights:
- `name`: Report name
- `content_set`: ForeignKey to ContentSet
- `report_text`: Markdown-formatted analysis

### UsageAccount
Tracks AI usage credits and API credentials per user:
- `user`: OneToOneField to User
- `monthly_quota`: Credits available per month (default from `settings.DEFAULT_MONTHLY_CREDITS`)
- `used_this_period`: Credits consumed this period
- `period_start`: Start of current billing period (resets on user's signup anniversary each month)
- `beehiiv_token`: User's Beehiiv API token
- `beehiiv_pub_id`: User's currently selected Beehiiv publication ID
- `api_key_valid`: Boolean indicating if the API key has been validated
- `available_publications`: JSON list of publications available to the user
- `timezone`: User's preferred timezone for date display (IANA timezone string, default 'America/Chicago')

Billing cycle: Credits reset on the same day of the month as the user's signup date (e.g., signup on the 15th means credits renew on the 15th of each month). For months with fewer days, renewal occurs on the last day of the month.

API key validation: When a user enters their API token, it is immediately validated against the Beehiiv `/publications` endpoint. If valid, the list of available publications is cached and the user can select which publication to work with.

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

## Authentication

Uses django-allauth for email-based authentication:
- Email as primary identifier (no username required)
- Password-based login at `/accounts/login/`
- Registration at `/accounts/signup/`
- Password reset via email
- All views are protected with `@login_required`
- UsageAccount is auto-created for new users via signals

## Key Features & Workflows

### 1. Posts Page (`/posts/`)
- **Refresh Posts**: Fetches all posts from Beehiiv API with pagination
- **Select Posts**: DataTable with sorting by date, opens, clicks
- **Content Extraction**: Describe content to extract (e.g., "items in the quick links section")
  - Uses GPT-5.1 to identify HTML line ranges matching the description
  - Extracts text and links from each section
  - Matches links with click data using Levenshtein fuzzy matching
- **Download Click Visualization**: ZIP of HTML files with click counts overlaid on links
- **Download Improvement Tips**: ZIP of HTML files with AI-generated improvement tips
- **Save Content Sets**: Create new or add to existing sets

### 2. Insights Page (`/insights/`)
- **View Content Sets**: Browse extracted items with CTR data
- **Generate Insights**: AI analysis identifying top/bottom performing content patterns
- **Manage Sets**: Rename, copy, merge, delete items or entire sets
- **Reports**: Save, load, and delete generated reports
- **Export**: Download as CSV

### 3. Account Page (`/account/`)
- **Usage Stats**: View AI credits used and remaining
- **API Credentials**: Configure Beehiiv API token (validated on save)
- **Publication Selector**: Dropdown to switch between available publications (populated from API)
- **Account Info**: View email and change password

**Publication Switching**: Each publication has its own posts, content sets, and reports. Switching publications changes the active data context throughout the app.

**User Scoping**: Posts and ContentSets are scoped to both publication AND user. This means two different users can use the same publication without seeing each other's data.

## API Endpoints

All routes use the `analytics:` namespace.

### Posts Routes
- `GET /posts/` - Main posts page
- `POST /posts/run/` - Run AI content extraction
- `POST /posts/save/` - Save extracted items as ContentSet
- `POST /posts/delete-items/` - Remove items from session
- `POST /posts/refresh-posts/` - Fetch latest posts from Beehiiv
- `POST /posts/download-click-viz/` - Generate click visualization ZIP
- `POST /posts/download-annotated/` - Generate annotated HTML ZIP

### Insights Routes
- `GET /insights/` - Insights dashboard
- `GET /insights/load-content-set/<name>/` - Load ContentSet as JSON
- `POST /insights/generate-insights/` - Generate AI report
- `GET /insights/download-csv/<name>/` - Export as CSV
- `POST /insights/rename-set/`, `/copy-set/`, `/merge-sets/`, `/delete-set/`, `/delete-items/`
- `POST /insights/save-report/`, `GET /insights/load-report/<id>/`, `DELETE /insights/delete-report/<id>/`
- `GET /insights/get-all-reports/` - List all reports

### Account Routes
- `GET /account/` - Account settings page
- `POST /account/` - Update API credentials

## Deployment Modes

The app can run in two modes: **local** and **cloud**. Both modes connect to the same AWS RDS database.

### Local Mode

For local development, the app reads environment variables from the `.env` file (via `python-dotenv`).

Required in `.env`:
```
# Database credentials as JSON (matches AWS Secrets Manager format)
DATABASE_SECRET={"username":"your_db_user","password":"your_db_password"}

# API Keys
OPENAI_API_KEY=your-openai-api-key
```

Run locally with:
```bash
python manage.py runserver
```

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
- `extract_items()`: AI-powered content extraction from HTML
- `generate_content_insights()`: Generate performance analysis report
- `annotate_post_html(post_id, content_perf_evals, beehiiv_token, beehiiv_pub_id, user=None)`: Insert improvement tips into HTML
- `annotate_posts_parallel(post_ids, content_perf_evals, beehiiv_token, beehiiv_pub_id, user=None)`: Parallel annotation of multiple posts

## Credit System Configuration

Credit costs are configured in `settings.py`:

```python
# Default monthly credits for new users
DEFAULT_MONTHLY_CREDITS = 100

# Credit costs per operation
CREDITS_PER_EXTRACTION = 1      # Per post extracted from
CREDITS_PER_REPORT = 1          # Flat cost for generating insights
CREDITS_PER_ANNOTATION = 1      # Per post annotated with improvement tips
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