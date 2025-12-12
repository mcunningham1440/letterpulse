# LetterPulse

A Django web application for analyzing newsletter engagement data from the Beehiiv platform. The app extracts content from newsletter posts, tracks click-through rates (CTR), generates AI-powered insights, and provides annotated HTML exports with improvement tips.

## LLM/AI agent instructions

Any time you make a change to the code, determine whether it makes any information in this file obsolete, and, if so, update it.

## Project Overview

This application helps newsletter creators understand which content resonates with their audience by:
- Fetching post data and statistics from the Beehiiv API
- Extracting specific content items (e.g., "quick links", "product releases") using AI
- Matching links with click data using fuzzy matching (Levenshtein distance)
- Generating AI-powered content performance reports
- Annotating newsletter HTML with actionable improvement tips

## Tech Stack

- **Backend**: Django 5.0+, Python
- **Database**: PostgreSQL (via Docker)
- **AI**: OpenAI API (GPT-5.1 with reasoning)
- **Authentication**: django-allauth (email-based auth)
- **Frontend**: Bootstrap 5, DataTables, jQuery, Marked.js (markdown rendering)
- **Async**: aiohttp, asyncio for parallel API calls

## Project Structure

```
beehiiv_analytics_django/
├── beehiiv_analytics/          # Django project settings
│   ├── settings.py             # Main configuration (uses python-dotenv)
│   ├── urls.py                 # Root URL routing
│   ├── wsgi.py / asgi.py       # WSGI/ASGI entry points
│   └── __init__.py
├── analytics/                  # Main Django app
│   ├── models.py               # Post, ContentSet, Report, UsageAccount models
│   ├── views.py                # All view logic (login-protected)
│   ├── urls.py                 # App URL patterns (analytics namespace)
│   ├── utils.py                # Core utility functions (API calls, AI extraction, credit charging)
│   ├── admin.py                # Django admin configuration
│   ├── signals.py              # User signals (auto-create UsageAccount)
│   ├── context_processors.py   # Usage stats for templates
│   ├── templates/analytics/    # HTML templates
│   │   ├── base.html           # Base template with Bootstrap/DataTables and user sidebar
│   │   ├── account.html        # Account settings (usage, API credentials)
│   │   ├── extract.html        # Content extraction page
│   │   └── analyze.html        # Analysis and reporting page
│   └── migrations/             # Database migrations
├── data/                       # Runtime data (LLM call logs)
├── manage.py                   # Django management script
├── requirements.txt            # Python dependencies
├── docker-compose.yml          # PostgreSQL container config
└── .env / .env.example         # Environment variables
```

## Database Models

### Publication
Represents a Beehiiv publication (users may have access to multiple):
- `pub_id`: Beehiiv publication ID (unique)
- `name`: Publication name
- `organization_name`: Organization the publication belongs to

### Post
Stores newsletter post metadata and engagement stats from Beehiiv:
- `post_id`: Beehiiv post ID (unique)
- `publication`: ForeignKey to Publication (nullable)
- `title`, `subtitle`
- `status`: "Draft" or "Published"
- `creation_date`: DateTime when post was created in Beehiiv (nullable)
- `publish_date_cst`: Date field (nullable for drafts)
- Engagement metrics: `recipients`, `delivered`, `email_opens`, `unique_email_opens`, `email_clicks`, `unique_email_clicks`, `unsubscribes`, `spam_reports`

### ContentSet
Named collections of extracted content items:
- `name`: Identifier for the set (unique per publication)
- `publication`: ForeignKey to Publication (nullable)
- `items_data`: JSON array of extracted items with text, links, clicks, and CTR

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

Billing cycle: Credits reset on the same day of the month as the user's signup date (e.g., signup on the 15th means credits renew on the 15th of each month). For months with fewer days, renewal occurs on the last day of the month.

API key validation: When a user enters their API token, it is immediately validated against the Beehiiv `/publications` endpoint. If valid, the list of available publications is cached and the user can select which publication to work with.

## Authentication

Uses django-allauth for email-based authentication:
- Email as primary identifier (no username required)
- Password-based login at `/accounts/login/`
- Registration at `/accounts/signup/`
- Password reset via email
- All views are protected with `@login_required`
- UsageAccount is auto-created for new users via signals

## Key Features & Workflows

### 1. Extract Page (`/extract/`)
- **Refresh Posts**: Fetches all posts from Beehiiv API with pagination
- **Select Posts**: DataTable with sorting by date, opens, clicks
- **Content Extraction**: Describe content to extract (e.g., "items in the quick links section")
  - Uses GPT-5.1 to identify HTML line ranges matching the description
  - Extracts text and links from each section
  - Matches links with click data using Levenshtein fuzzy matching
- **Download Click Visualization**: ZIP of HTML files with click counts overlaid on links
- **Download Improvement Tips**: ZIP of HTML files with AI-generated improvement tips
- **Save Content Sets**: Create new or add to existing sets

### 2. Analyze Page (`/analyze/`)
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

## API Endpoints

All routes use the `analytics:` namespace.

### Extract Routes
- `GET /extract/` - Main extraction page
- `POST /extract/run/` - Run AI content extraction
- `POST /extract/save/` - Save extracted items as ContentSet
- `POST /extract/delete-items/` - Remove items from session
- `POST /extract/refresh-posts/` - Fetch latest posts from Beehiiv
- `POST /extract/download-click-viz/` - Generate click visualization ZIP
- `POST /extract/download-annotated/` - Generate annotated HTML ZIP

### Analyze Routes
- `GET /analyze/` - Analysis dashboard
- `GET /analyze/load-content-set/<name>/` - Load ContentSet as JSON
- `POST /analyze/generate-insights/` - Generate AI report
- `GET /analyze/download-csv/<name>/` - Export as CSV
- `POST /analyze/rename-set/`, `/copy-set/`, `/merge-sets/`, `/delete-set/`, `/delete-items/`
- `POST /analyze/save-report/`, `GET /analyze/load-report/<id>/`, `DELETE /analyze/delete-report/<id>/`
- `GET /analyze/get-all-reports/` - List all reports

### Account Routes
- `GET /account/` - Account settings page
- `POST /account/` - Update API credentials

## Environment Variables

Required in `.env`:
```
# Django
SECRET_KEY=your-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# API Keys
OPENAI_API_KEY=your-openai-api-key

# Database
DB_NAME=letterpulse
DB_USER=letterpulse_user
DB_PASSWORD=local_dev_password
DB_HOST=localhost
DB_PORT=5432
```

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
- `process_posts_data()`: Converts raw API data to DataFrame. Drafts have `publish_date_cst` set to "Draft"

Views use `get_user_api_credentials(user)` helper to retrieve credentials and redirect to Account page if not configured.

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

## Testing Notes

- LLM calls are logged to `data/llm_call_logs.csv` with timing and token usage
- The `sandboxes/`, `testing_data/`, and `code_dump/` directories are for development/testing
- Time zone is set to `America/Chicago` in settings