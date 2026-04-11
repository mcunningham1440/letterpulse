"""
Prompt templates for LLM calls used throughout the analytics app.
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