# LetterPulse

LetterPulse is an LLM-powered application designed to assist Beehiiv newsletter writers in writing more engaging posts by learning what their audience responds to and acting on that knowledge.

It works by pulling each user's click data from Beehiiv, identifying the topics and link types their subscribers actually engage with, and using that signal to power the two capabilities below.

This is a personal project that I test-marketed but decided not to launch. I may return to it someday if the circumstances are right.

## Capabilities

### Finds new content ideas
Proposes new post topics tailored to the audience's demonstrated interests.

![Found content](analytics/static/analytics/images/found_content.png)

### Suggests post improvements
Annotates a draft with tips informed by what's worked before.

![Improvement tips](analytics/static/analytics/images/improvement_tips.png)

## How it works

### Post processing

LetterPulse pulls each user's posts from the Beehiiv API along with per-link click statistics, parses the HTML of the most recent eligible posts into sections, and stores the resulting sections and link-level CTRs as the dataset that the LLM-powered features draw on.

```mermaid
flowchart TD
    A[Beehiiv API] --> B[Fetch posts<br/>+ per-link click stats]
    B --> C[Filter to recent eligible posts]
    C --> D[Parse each post into sections]
    D --> E[Extract links and CTR<br/>per section]
    E --> F[Persist as<br/>ProcessedPost / Section / LinkData]
```

### Content Finder

The most involved feature is the **Content Finder**, which runs a three-stage agentic pipeline on a chosen template post. A first call drafts a search plan from the post's sections; once the user accepts or amends it, a second call distills the plan into a list of search topics; each topic is then dispatched to a parallel mini-agent that runs three rounds of web search before returning a structured result.

```mermaid
flowchart TD
    A[Template post] --> B[Plan stage<br/>gpt-5.4 reads sections,<br/>drafts search plan]
    B --> C[User reviews / amends plan]
    C --> D[Dispatch stage<br/>gpt-5.4 turns plan into<br/>up to 6 search topics]
    D --> E1[Search agent<br/>gpt-5.4-mini]
    D --> E2[Search agent<br/>gpt-5.4-mini]
    D --> E3[Search agent<br/>gpt-5.4-mini]
    E1 --> F[Results grouped by topic]
    E2 --> F
    E3 --> F
```

Conversation context is carried across stages by appending each stage's output items to the next stage's input, so the dispatch and search calls see the full reasoning history.

### Post annotation

The Improvement Tips feature renders a draft as a two-column annotated HTML document with inline tip cards. The post HTML is prettified and line-numbered, then handed to an LLM along with the user's historical link-click data; the LLM returns structured tips that reference specific line numbers, which are wired back into the rendered HTML as anchor spans so each tip card can draw an SVG connector to the exact passage it refers to.

```mermaid
flowchart TD
    A[Selected post] --> B[Prettify HTML<br/>with line numbers]
    A --> C[Gather link history<br/>across user's posts]
    B --> D[LLM call<br/>structured tips referencing<br/>line numbers]
    C --> D
    D --> E[BeautifulSoup inserts<br/>anchor spans into HTML]
    E --> F[Two-column HTML<br/>+ SVG tip connectors<br/>for download]
```

## Project layout

```
app/
├── analytics/                       # main Django app
│   ├── models.py                    # Publication, Post, ProcessedPost, Section, LinkData, Pending* tasks, LLMCall, Feedback
│   ├── admin.py                     # Django admin registrations
│   ├── urls.py                      # all routes under the `analytics:` namespace
│   ├── views/                       # request handlers, split by feature
│   │   ├── insights.py              #   Write page
│   │   ├── learning.py              #   initial audience scan + incremental updates
│   │   ├── content_finder.py        #   content-idea search (plan → dispatch → search)
│   │   ├── improvement_tips.py      #   annotated-HTML tips export
│   │   ├── monetize.py              #   Monetize page + niche analysis
│   │   ├── account.py               #   account / credentials / credits
│   │   ├── public.py                #   public landing
│   │   └── feedback.py              #   feedback submission
│   ├── utils/                       # business logic, importable from views
│   │   ├── beehiiv_api.py           #   Beehiiv HTTP client
│   │   ├── llm.py                   #   OpenAI Responses API wrapper
│   │   ├── credits.py               #   billing / quota
│   │   ├── posts.py, sections.py,   #   post fetching + processing
│   │   │   links.py, text.py
│   │   ├── post_selection.py        #   picks which posts to process
│   │   ├── learning.py,             #   per-feature workflows (background tasks)
│   │   │   content_finder.py,
│   │   │   improvement_tips.py,
│   │   │   niche.py
│   │   └── background.py            #   background-thread runner
│   ├── llm_tracker.py               # contextvar accumulator for per-LLM-call metadata
│   ├── logsink.py, logutils.py      # async queue-based logging (ExecutionLog, LLMCall)
│   ├── templates/analytics/         # Django templates
│   ├── static/analytics/            # JS, CSS, images (including readme screenshots)
│   ├── migrations/
│   └── tests/                       # pytest-django tests
├── beehiiv_analytics/               # Django project package
│   ├── settings.py
│   ├── test_settings.py             #   SQLite in-memory, migrations disabled, MD5 hasher
│   └── urls.py
├── manage.py
├── requirements.txt
├── Dockerfile                       # python:3.11-slim, gunicorn
├── run_local_dev.sh                 # venv + Postgres-in-Docker on host port 5433
├── run_local.sh                     # Docker against the cloud DB
├── push_to_ecr.sh                   # build & push image to AWS ECR (dev/prod)
├── pytest.ini
├── claude.md                        # architecture + workflow notes (LLM/agent guide)
└── readme.md
```