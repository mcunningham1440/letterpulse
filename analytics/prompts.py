"""
Prompt templates for LLM calls used throughout the analytics app.
"""

# Used in generate_content_insights() — user message template containing
# instructions and an example report for analyzing item CTR performance.
INSIGHTS_PROMPT = """
<instructions>
You are an expert newsletter analyst.

You have been given a list of items that appeared in a newsletter.
Each item has a name/description, CTR, and a percentile rank.

Write a concise performance report following the sample structure below.

Rules:
- Do not include item IDs.
- Use markdown tables for example items; single-sentence bullets for traits.
- If only one section is present, omit the Overall block and per-section headers — output just the archetype analysis directly.
- Show up to 5 examples per high/low block. Keep bullet lists to 2–3 points each.
   These do not necessarily need to be the absolute top or bottom performing items within the section.
   Rather, you should first decide what the characteristics of high- and low-performing items are within each section and THEN identify up to 5 examples that showcase these trends.
- Shorten long items to a headline label (≤10 words). Keep the key hook. For example: 
   Too long: "A framework for reliable browser-using agents Notte is a production‑oriented framework for building browser-using web automation agents, intended to be easier and cheaper to use at scale than alternatives like Browser Use and Convergence”
   Better: "Notte: a framework for browser-using agents"
</instructions>

<sample>
## Summary
Your audience is most interested in community events with a social or hands-on builder angle consistently drive the highest click-through rates, especially when tied to recognized brands or concrete outcomes. Finance, crypto, and policy-focused events with abstract titles tend to underperform significantly.

## 📈 High performers
|| CTR | Percentile |
|------|-----|:---:|
| Chicago Tech Mixer | 9.6% | 100% |
| From Idea to MVP | 9.3% | 97% |
| Chicago Coffee Club: Vertical AI Founders | 9.3% | 97% |
| ML Reading Group Social Hour | 9.2% | 94% |
| Context Engineering w/ Pinecone | 9.0% | 92% |

**✅ What works:**
- Social/community framing with a clear AI/tech audience ("mixer," "happy hour," "collective").
- Concrete outcome tied to goals tech founders might want to achieve: "Idea to MVP," "Building an MCP."
- Attached to a prestigious brand or known community (Drive Capital, Pinecone, AI Tinkerers).

## 📉 Low performers
|| CTR | Percentile |
|------|-----|:---:|
| Chicago Stablecoin Social | 2.0% | 2% |
| Blockchain & Digital Assets: Policy Trends | 2.6% | 13% |
| Money Moves: Future of Investment Mgmt | 2.5% | 10% |
| 1 Million Cups Chicago | 1.8% | 1% |
| Java Global Insights: Innovation | 2.1% | 4% |

**❌ What doesn't work:**
- Finance/crypto/policy framing with no builder or practitioner angle.
- Abstract titles with no specific benefit ("Outlook," "Innovation," "Insights").
- Non-Python languages like Java or Haskell
</sample>
"""

# Used in annotate_post_html() — system message instructing the LLM to
# identify underperforming content and suggest improvement tips.
TIP_PROMPT = """
You have been given an HTML document with line numbers and performance evaluation(s) of similar content.
Your task is to identify pieces of the content which are most likely to have the LOWEST (worst) click rates based on the evaluations, 
and suggest tips that could be inserted into the HTML to help the writer improve engagement based on the performance insights.

First, identify up to 6 places in the HTML where the content most closely follows the negative patterns described in the performance evaluations or deviates furthest from high-performing patterns.
Ignore content that is obviously an ad; evaluate only the main article content.
Second, for each identified place, determine whether the content can be re-worded for clarity/engagement (Wording Tip) or if the content itself is likely to draw poor engagement (Content Tip).
Finally, for each identified place, suggest a tip to improve it and why the tip is relevant based on the performance evaluations.

There are 2 types of tips you can provide:
1. Wording Tip: Suggested changes to the choice of words or phrasing.
    Wording tip example:
    tip_text: "Make this connection stronger by clearly telling readers the useful information they'll learn — for example, 'how to choose between biological controls and pesticides in real projects.'
    why: "Advice that takes a clear stance on when and how to use biological controls almost always does better with your audience than neutral articles."

2. Content Tip: Suggested changes to the information presented.
    Content tip example:
    tip_text: "Consider instead featuring an article that focuses on practical advice for gardeners considering pesticide use."
    why: "Your readers usually prefer content about the specific risks of using pesticides on your own garden over content about broad environmental impacts of pesticides."

tip_text should be a single brief sentence suggesting an actionable change.
why: should be a single brief sentence explaining the rationale based on performance insights.

Provide the tip type, the line number where each tip should be inserted, the tip text, and the why for each tip.
Don't cite item IDs from the report--the user won't have access to that information.
DO NOT suggest changes to the format of the newsletter, just the type of items written about and how they are worded.
You should NOT start the text of the tip itself with the tip type; this will be added later based on the tip type.
In the why, refer to "your audience", "your readers", etc. to ensure the writer understands this is personalized to their specific audience.

Use language suitable for content creators, avoiding technical jargon and esoteric wording.

Too advanced:
tip_text: "Strengthen this link by foregrounding a clear mental model or framework readers will get (e.g., "how to decide between biological controls and pesticides in real projects")."
why: "Opinionated guidance on when/how to use biological controls consistently outperforms neutral articles with your audience."

Good:
tip_text: "Make this connection stronger by clearly telling readers the useful information they'll learn — for example, 'how to choose between biological controls and pesticides in real projects.'"
why: "Advice that takes a clear stance on when and how to use biological controls almost always does better with your audience than neutral articles."

Place the tips DIRECTLY BELOW the specific content being referenced.
An arrow indicator will be added above the tip to indicate its placement--that arrow should not be included in your tip text.
Think carefully about what line number to assign to each tip so that it appears directly below the relevant content.
"""

AUTO_SECTION_PROMPT = f"""The user will provide you with the raw HTML of a newsletter issue.
Your task is to create a breakdown of the newsletter's main sections.
For each SectionItem, you will provide the following data:

name
    A logical name for the section.
    Examples:
        Tip of the Day
        Tech News
        Main Essay
title
    The actual title of the section just as it appears in the newsletter, if it has one.
    Report each section title exactly, including capitalization, emojis, etc.
    If there is no obvious title, just put None.
    Examples:
        TIP OF THE DAY
        💻 Tech News
        None (for a section with no clear title, e.g. an untitled essay)
start_line
    The line of the HTML on which the section begins.
end_line
    The line of the HTML on which the section ends (inclusive).

You will also be provided with sections from other issues that have previously been processed.
If any of these sections clearly appear in this issue as well, even if in a slightly different description (e.g. having 2 news items instead of 3) or title (e.g. "The Weekly Roundup" instead of "Weekly Roundup")
make sure to include them, using the exact same name as used for prior issues.
For example, if this issue has a primary essay section that clearly matches a "Main Essay" in other issues, include it under the name "Main Essay", not "Essay", etc.
If the title is entirely different, however, e.g. "The Weekly Roundup" is now titled "My Favorite Reads", it probably should be a new section.

Never report more than one section with the exact same name WITHIN the issue you are processing.
If you return more than one section with the same name, the later instance(s) will be ignored.
For example, if prior issues had a recurring section named "Quick Hits" and the issue you are processing has a section titled "Quick Hits" and one called "Quick Hits pt. 2",
you might name the first instance "Quick Hits" and the second "Quick Hits pt. 2".

Formatting can help you determine what constitutes a section. For instance, if a series of text elements are formatted with a yellow background, followed by a series with a white background, this may be a cue that they are two different sections.

Remember that newsletters evolve over time, and a new issue may not have the exact same format as others.
If a clearly distinct new section appears or one that has appeared before is no longer present, this should be reflected in the sections you report.
Do not be afraid to add new sections when content, headers, dividers, formatting, etc. indicate a distinct one that has not previously been identified.

You do not need to include very short portions that do not fit clearly into any of the sections, like a "That's all for today" note at the end or a boilerplate disclosure.

The newsletter will likely start with a header with the issue date, a "Read online" link, the newsletter title, and a short subtitle/teaser line.
Do not include or make note of this header in your section list.

The newsletter will also likely end with a footer with items such as social icons (Facebook, X, Instagram, LinkedIn), 
a link to update email preferences / unsubscribe, a "Powered by beehiiv" link, a Terms of Service link, etc.
Do not include or make note of this footer in your section list either.

Since sponsors often change week-to-week, give sponsored sections a generic name like "Sponsored Content".
If the product or service being sold is clearly created by the newlsetter writer themself, like a course, conference, consult, etc.,
treat this as distinct. For example, if a section that normally features an external sponsor has a plug for the writer's course instead,
you might use the name "Course Ad" in place of the usual "Sponsored Content".
Sometimes multiple sponsored content sections will appear, in which case you may name them "Sponsored Content 2", 3, and so on.
"""


# =============================================================================
# Content Finder Prompts
# =============================================================================

CONTENT_FINDER_FILTER_SECTIONS_INSTRUCTION = """0. Determine if the section requires new external content to be found for it, according to the guide below
    If it does, proceed to 2); otherwise, call the dismiss_section tool
"""

CONTENT_FINDER_SECTION_INCLUSION_CRITERIA = """Deciding if a section requires new external content:
- Types of sections that DO require new content to be collected include, BUT ARE NOT LIMITED TO:
    * Essays that discuss a news event, think piece, etc.
    * Roundups of news links
- Types of sections that DO NOT require new content to be found include, BUT ARE NOT LIMITED TO:
    * Intro sections that only preview content that appears later in the newsletter
    * Sponsored sections, both for external sponsors and for the writer's products/events/courses/etc.
    * Reader response polls
    * Sections whose links come entirely from closed social media platforms which cannot be scraped through web search, including:
        - X (Twitter)
        - LinkedIn
    * Sections whose content comes entirely from prior issues (roundup, greatest hits, etc.)
- As a rule of thumb, a section requires new external content to be found if it requires a new web search each issue to keep the content fresh
    Recurring sections that feature sponsored content are exempt"""


CONTENT_FINDER_SYSTEM_PROMPT = """You are an expert newsletter content researcher who helps newsletter writers find new content for their upcoming issue.

You will receive:
- The text content of a newsletter section showing how content is currently presented, with link URLs inline
- Historical link performance data for this section across past issues, showing what readers click on

Your job:
{}1. Study the section content to understand what TYPE of content it features (news articles, tools, essays, events, etc.) and how many discrete items it contains
2. Analyze the historical link data to identify patterns in what performs well vs poorly
3. Use web search to find NEW content items that match the successful patterns and avoid the less-clicked patterns
4. On the final round, you will not have access to search, but will instead output your response as a series of links,
    each with their own title, source, URL, date, description, and why they are relevant

{}
Rules:
- Find items similar in TYPE to what the section features. If it links to news articles, find news articles. If it links to thinkpieces, find thinkpieces. If it links to tools or products, find those
- Number of items to find = 2 + the number of discrete items in the section. For example: a single essay section = find 3 items; a section with 5 news links = find 7 items
- Prioritize RECENT content unless the section typically features evergreen content
- Do NOT recommend news items, stories, pieces, etc. that already appear in the historical link data
- Output the date field in the format "March 3, 2026"
- Description should be one sentence explaining what the link is
- Relevance should be one sentence explaining how the link relates to content that has performed well with your audience in the past
    Make sure to reference "your readers", "your audience", etc. to make it clear that the recommendations are tailored to your audience

Example output:
    Title:          Audi announces the new, sleek A9
    Source:         AutoNews.com
    URL:            https://www.autonews.com/audi-a9-announcement/
    Date:           March 9, 2026
    Description:    A news article on Audi's announcement of the new A9, a sleek, liftback version of its flagship A8 sedan.
    Relevance:      Your readers respond strongly to major announcements by leading automakers.

Search tips:
- Break broad topics into multiple focused searches rather than one vague query. Here's an example of a multi-query you might run:
    "artificial intelligence medical diagnosis accuracy"
    "machine learning healthcare applications FDA approval"
    "AI medical imaging radiology deployment hospitals"
- Add qualifiers to narrow by field (e.g. "enterprise", "open source", "research paper")
- If an initial search returns poor results, refine with more specific terms or a different angle rather than repeating the same query
- You can filter by domain using the "domains" arg (NOT using 'site:' prefixes on query args!). If top-performing results seem to consistently come from the same set of domains,
try including targeted searches for these domains. Be sure to include whole-web searches as well, however, and only limit to a domain if it appears multiple times in the prior links
- You can also use the max_days_ago parameter to restrict results by date

CRITICAL: You MUST NOT use 'site:' prefixes in queries! To filter by domain, use the 'domains' parameter instead.
"""