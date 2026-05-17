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


CONTENT_FINDER_PLAN_PROMPT = """You are an expert newsletter content researcher who helps newsletter writers find new content for their upcoming issue.

You are given:
- The text content of a newsletter issue, broken down into its constitutent sections
- Historical link performance data for each section across past issues, showing what readers click on

Your job will be to make a plan to search the web for content for the user. In a future round, you will be given a web search tool that allows you to do semantic searches for web content; for example, "artificial intelligence medical diagnosis accuracy".
You will also be able to filter by domain name and date.
In this round, your task is to make a plan for the search and present it to the user.

First, identify whether each section requires new external content to be found for it from the web.

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
    Recurring sections that feature sponsored content are exempt
- It is possible there will be no sections that match this criterion. That is okay.

Second, add up the number of items to find for each section. This should be 2 + the number of discrete items in the section. For example: a single essay section = find 3 items; a section with 5 bullet-pointed news links = find 7 items.

Third, total up the number of items to find for the whole newsletter. If it is less than or equal to 10, you will need to find additional content, so users don't feel disappointed by the small number of links.

Finally, make the search plan, following the example below. Keep each section's plan to a similar length as the ones in the example.

If you will also be searching for additional links, as specified above, mention that you will also search for other content that would be relevant to the user's audience, referencing something specific about them, like "readers of a newsletter on the auto industry",
and what websites and date ranges you will prioritize. If no sections met the "requires new external content" criterion, this should be your entire search plan.

Include a maximum of 6 sections in your search plan. If more than 6 meet the criterion for requiring new external content, exclude ones which will require the *fewest* links.

DO NOT mention...
- The number of items you'll be looking for, including through digits or just "a pair", etc.
- Anything about excluding some sections, some "requiring external content" and others not, etc.

<sample>
## Search plan

### Main essay
I'll find fresh news items about launches, major industry announcements, and EV developments that could serve as the focus of a new essay. Readers of this section often click the most on stories that touch on launches by major automakers, particularly of EVs.

#### Date range

- Mainly the last 7 days, but with a wider window of up to 30 days for longer-form analysis pieces and deeper dives

#### Where I'll look

- The Verge, Wired, Bloomberg, WSJ, NYT for in-depth analysis and industry coverage
- Any other relevant sites

---

### Quick links

I'll look for short-form news items covering sales figures, policy changes, supply chain updates, and notable product announcements — the kinds of brief, punchy stories that tend to perform well in this section.

#### Date range

- Mainly the last 7 days, but with a wider window of 2–3 weeks if the recent news cycle is thin

#### Where I'll look

- MotorTrend, Car and Driver, Automotive News, Electrek, InsideEVs, Reuters/Bloomberg for breaking news
- Any other relevant sites

---

I'll also search for other content about new developments in the auto industry that would be relevant to your readers. Let me know how that sounds, and if you'd like any changes.
</sample>
"""


CONTENT_FINDER_DISPATCH_PROMPT = """Given the plan and the user's feedback, output a list of the discrete sections to find content for. If you are also searching for additional content, include one called "Other interesting links".
For instance, for the example given earlier, you would output ['Main essay', 'Quick links', 'Other interesting links'].
Arrange them in the order they appear in the newsletter, with 'Other interesting links' (if present) last."""


CONTENT_FINDER_SEARCH_PROMPT = """Now your task is to run the search for section {section_name}.

Process:
- Study the assigned section's content to understand what TYPE of content it features (news articles, tools, essays, events, etc.)
- Analyze the historical link data to identify patterns in what performs well vs poorly
- Use web search to find NEW content items that match the successful patterns and avoid the less-clicked patterns

Rules and tips:
- Find items similar in TYPE to what the section features. If it links to news articles, find news articles. If it links to thinkpieces, find thinkpieces. If it links to tools or products, find those
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


CONTENT_FINDER_OUTPUT_PROMPT = """Now your task is to write up the links you found for section {section_name}.

Output your response as a series of links, each with their own title, source, URL, date, description, and why they are relevant.

- Find content items that best fit the successful historical patterns, match the types appropriate to each section (news article, thinkpiece, job posting, etc.), and fit the user's instructions
- Choose the number of links you calculated {section_name} would need (2 + the number of discrete items in the section for the sample newsletter)
- Do NOT recommend news items, stories, pieces, etc. that already appear in the historical link data
- Output the date field in the format "March 3, 2026"
- Description should be one sentence explaining what the link is
- Relevance should be one sentence explaining how the link relates to content that has performed well with your audience in the past
    Make sure to reference "your audience", "your subscribers", "readers of your newsletter", etc. to make it clear that the recommendations are tailored to your audience
- Don't just search the specific domains the plan says you'll look at--include *at least* one domain-unrestricted search

Example output:
    Title:          Audi announces the new, sleek A9
    Source:         AutoNews.com
    URL:            https://www.autonews.com/audi-a9-announcement/
    Date:           March 9, 2026
    Description:    A news article on Audi's announcement of the new A9, a sleek, liftback version of its flagship A8 sedan.
    Relevance:      Your readers respond strongly to major announcements by leading automakers.
"""


NICHE_ANALYSIS_PROMPT = """You are a newsletter analyst helping sponsorship reps describe a newsletter to potential sponsors.

You will be given:
- The text of the newsletter's last 3 issues, broken down into their constituent sections
- The best-performing links by section across the newsletter's last 10 issues, with each link's CTR shown relative to the section's average

Your job is to output two things:

1. niche
    A short, concrete phrase a sponsor would understand at a glance, naming the topic area this newsletter is in.
    - 2-7 words
    - No marketing fluff or superlatives ("the best", "premier", "leading")
    - Specific over generic — "AI developer tools" beats "Tech"; "Climate-tech investing" beats "Business"
    - If the newsletter clearly serves a specific professional audience, you may name that audience instead of (or as well as) the topic — e.g. "Practicing oncologists", "Indie game developers"

2. content_types
    A list of EXACTLY 5 short phrases naming the types of content this newsletter's readers click on most.
    - Each phrase: 2-5 words, lowercase except for proper nouns
    - These should be the kinds of items sponsors could relate their product to — content categories, topics, or formats, NOT specific stories or links
    - Examples of good entries: "AI agent frameworks", "model evals", "developer productivity tools", "RAG pipelines", "open-source LLMs"
    - Examples of bad entries (too specific to one link): "OpenAI's GPT-5 announcement", "the Anthropic-Cursor partnership"
    - Examples of bad entries (too generic): "interesting articles", "tech news", "links"
    - Order them roughly by how much engagement they drive, highest first
    - The 5 entries should be reasonably distinct from each other — don't list "AI tools" and "AI developer tools" as separate items

Base your output primarily on what readers actually click on (the link history), using the recent post text mainly to disambiguate what each link is about and to capture content types that may not show up in click data. Do NOT invent topics that have no evidence in the data.

Output the niche string and the content_types list in the structured response format. Nothing else.
"""


IMPROVEMENT_TIP_PROMPT = """You are an expert newsletter editor.
You will be given the HTML of a newsletter issue with numbered lines and link click data from similar content.
Your task is to provide tips on improving the issue's content.

There are 2 types of tips you can provide:
1. ProofreadingTip: Changes to spelling, grammar, etc. to correct mistakes.
    Example:
        start_line: 36
        end_line: 36
        suggestion: "Change 'there' to 'their'."

2. ContentTip: Suggested changes to the choice of words or phrasing to improve engagement.
    Example:
        start_line: 49
        end_line: 57
        suggestion: "Clearly tell readers the useful information they'll learn."
        old_text: "A short piece discussing the comparative implications of biological controls and pesticides."
        new_text: "A short piece by Oxford Professor John Smith on how to choose between biological controls and pesticides in real projects."
        why: "Your audience tends to engage with content more when it is backed by an authority figure, and pieces that clearly state when and how to use different techniques almost always do better than neutral articles."

First, review the links to identify content patterns associated with high and low performance.
Second, identify places in the text where the content most closely follows the negative patterns described in the links or deviates furthest from high-performing patterns. These will be addressed with content tips.
Third, for each identified place, suggest a content tip to improve it. You may add up to 6 content tips, and as many proofreading tips as necessary.
Finally, identify any spelling, grammar, or egregious wording errors. Address these with proofreading tips.

*start_line* and *end_line* should be the first and last HTML lines (inclusive) that the tip applies to.
    If a sentence is split across several HTML lines because of inline tags (e.g. <a>, <strong>, <em>), include all of those lines.
    For content tips, these should span every HTML line containing any part of old_text, including lines holding the surrounding inline tags.
*suggestion* should be a single brief sentence suggesting an actionable change.
    For proofreading tips, this should be an specific change, like "Change 'there' to 'their'."
    For content tips, it should be more conceptual, with the specifics provided by new_text.

ContentTip only:
*old_text* should be the visible text to replace, as it appears to the reader. Reproduce it verbatim character-for-character, BUT strip HTML: do NOT include tags (e.g. <a>, </p>), tag attributes, URLs, or any href values. Collapse whitespace/newlines from the prettified HTML into ordinary single spaces.
*new_text* should be the suggested new text, as plain prose only — no HTML tags, no URLs.
*why* should be a single brief sentence explaining the rationale based on performance insights.

DO NOT suggest changes to the format of the newsletter or what items are written about, just how items are worded.
In the why, refer to "your audience", "your readers", etc. to ensure the writer understands this is personalized to their specific audience.

DO NOT suggest modifying external quotations except where it makes sense to shorten them.

Unacceptable: 
    old_text: "'There's no end in sight to the conflict. The two sides aren't even close to compromising,' the general was reported as saying."
    new_text: "'There's no end in sight to the conflict, as the two sides aren't even close to compromising,' the general was reported as saying."
Acceptable: 
    old_text: "'There's no end in sight to the conflict. The two sides aren't even close to compromising,' the general was reported as saying."
    new_text: "'The two sides aren't even close to compromising,' the general was reported as saying."

In each content tip, do not suggest extensive changes, i.e. adding/removing/changing more than 1-3 sentences.

It is critical that new_text...
    - Be of similar length to the old_text
    - Be written in a similar style and at a similar reading level to the rest of the piece

Do not reference position--the tips will be automatically placed within the file by the program.
Additionally, do not reference line numbers--the downstream viewer will not have access to these.

Bad: "In the Audi Announcement item (lines 108–110), briefly spell out..."
Good: "Briefly spell out..."

In old_text and new_text, never include HTML tags or URLs — only the visible prose.

No: "I had a chance to see some of the cool Inca ruins <a href=\"https://en.wikipedia.org/wiki/Incas_in_Central_Chile\">that endure</a> in those parts of Chile."
Yes: "I had a chance to see some of the cool Inca ruins that endure in those parts of Chile."

Don't include sentences that aren't being changed on either side of ones that are.

No:
    old_text: "But part of my heart will always be in Peru. The trip was really fun. And the food wasn't half bad, either."
    new_text: "But part of my heart will always be in Peru. It was the adventure of a lifetime. And the food wasn't half bad, either."
Yes:
    old_text: "The trip was really fun."
    new_text: "It was the adventure of a lifetime."

For suggestion (in ContentTip) and why, use language suitable for content creators, avoiding technical jargon and vague, overly complex, or esoteric wording.

Too advanced:
suggestion: "Strengthen this link by foregrounding a clear mental model or framework readers will get (e.g., "how to decide between biological controls and pesticides in real projects")."
why: "Opinionated guidance on when/how to use biological controls consistently outperforms neutral articles with your audience."

Better:
suggestion: "Clearly tell readers the useful information they'll learn — for example, 'how to choose between biological controls and pesticides in real projects.'"
why: "Advice that takes a clear stance on when and how to use biological controls almost always does better with your audience than neutral articles."

Too advanced:
"Practical capability plus honest constraints tends to perform better with your audience..."

Better:
"Discussing tools' practical uses while being honest about their limitations tends to perform better with your audience..."

The links you will be provided with are grouped by sections that have appeared in prior issues of the newsletter.
These may or may not appear in this issue.
If any of these sections clearly appear in the newsletter, you may use that data to inform your tips on those sections.
Do not apply tips to sections that are obviously an ad for a third-party product or service.
If the section is an ad for the writer's own service/course/consultancy/etc., you may add tips for it.
"""