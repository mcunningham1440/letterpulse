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
│   ├── models.py               # Post, ContentSet, Report models
│   ├── views.py                # All view logic (~1000 lines)
│   ├── urls.py                 # App URL patterns (analytics namespace)
│   ├── utils.py                # Core utility functions (API calls, AI extraction)
│   ├── admin.py                # Django admin configuration
│   ├── templates/analytics/    # HTML templates
│   │   ├── base.html           # Base template with Bootstrap/DataTables
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

### Post
Stores newsletter post metadata and engagement stats from Beehiiv:
- `post_id`: Beehiiv post ID (unique)
- `title`, `subtitle`
- `status`: "Draft" or "Published"
- `creation_date`: DateTime when post was created in Beehiiv (nullable)
- `publish_date_cst`: Date field (nullable for drafts)
- Engagement metrics: `recipients`, `delivered`, `email_opens`, `unique_email_opens`, `email_clicks`, `unique_email_clicks`, `unsubscribes`, `spam_reports`

### ContentSet
Named collections of extracted content items:
- `name`: Unique identifier for the set
- `items_data`: JSON array of extracted items with text, links, clicks, and CTR

### Report
AI-generated content insights:
- `name`: Report name
- `content_set`: ForeignKey to ContentSet
- `report_text`: Markdown-formatted analysis

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

## Environment Variables

Required in `.env`:
```
# Django
SECRET_KEY=your-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# API Keys
OPENAI_API_KEY=your-openai-api-key
BEEHIIV_TOKEN=your-beehiiv-token
BEEHIIV_PUB_ID=your-publication-id

# Database
DB_NAME=letterpulse
DB_USER=letterpulse_user
DB_PASSWORD=local_dev_password
DB_HOST=localhost
DB_PORT=5432
```

## Key Utility Functions (analytics/utils.py)

### API Functions
- `fetch_post_html()` / `fetch_post_clicks()`: Fetch individual post data
- `fetch_posts_html_and_clicks_parallel()`: Batch fetch with semaphore (5 concurrent)
- `fetch_all_posts()`: Paginated fetch of all posts (includes drafts, confirmed, and archived via `status=all`)
- `refresh_posts_data()`: Full refresh from Beehiiv API
- `process_posts_data()`: Converts raw API data to DataFrame. Drafts have `publish_date_cst` set to "Draft"

### AI Functions
- `llm_call()`: Wrapper for OpenAI API with logging to CSV
- `extract_items()`: AI-powered content extraction from HTML
- `generate_content_insights()`: Generate performance analysis report
- `annotate_post_html()`: Insert improvement tips into HTML

### Link Matching
- `match_links_with_clicks()`: Uses exact matching first, then Levenshtein distance (40% threshold) for fuzzy matching

## Testing Notes

- LLM calls are logged to `data/llm_call_logs.csv` with timing and token usage
- The `sandboxes/`, `testing_data/`, and `code_dump/` directories are for development/testing
- Time zone is set to `America/Chicago` in settings