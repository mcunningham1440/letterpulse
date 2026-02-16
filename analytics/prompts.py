"""
Prompt templates for LLM calls used throughout the analytics app.
"""

# Used in extract_items() — system message instructing the LLM to identify
# HTML line ranges matching a user's content description.
PARSING_PROMPT = """You are given an HTML document with line numbers.
Your task is to identify items within the HTML that correspond to the ContentDescription at bottom.
Provide the start and end line numbers (inclusive) for each item that matches the description.
Make each discrete item a separate pair of start and end line numbers.
Do not include any items that do not match the description.

Sometimes, the user may be referring to a section which contains multiple similar items, as in Example1.

<Example1>
**New product releases**
-Tesla unveils the Roadster X-Plus, a lightweight carbon-ceramic edition with a 0–60 time of 1.7 seconds and a 700-mile solid-state battery pack.
-BMW releases the i5 Touring ActiveHybrid, featuring adaptive solar-roof charging and an AI-driven energy-routing system for long-distance commuters.
-Toyota launches the Land Cruiser Micro-Hybrid, a compact off-road SUV aimed at urban explorers, with a detachable roof rack drone for scouting terrain.
-Rivian debuts the R1T TrailForge Edition, adding magnetically adjustable suspension plates and an onboard terrain-mapping assistant trained on 40M trail miles.
</Example1>

In this case, if the user asked for “new product releases”, you would make each one of the four news items into a separate item, unless specifically instructed otherwise.
Make sure to include all of them, for instance, in this case, you would return four pairs of start and end line numbers.

In other cases, the user may be referring to a single item, as in Example2.

<Example2>
**New product release**
Audi introduces the A7 NeoSport, a sleek fastback hybrid that pairs a 2.0L turbo engine with a next-gen ultracapacitor boost system, delivering instantaneous torque without relying on traditional lithium-ion packs. Early testers report near-zero lag during acceleration and a smoother handoff between electric assist and combustion power than any previous Audi hybrid.

The NeoSport also debuts Audi’s new “HoloHUD” panoramic projection system, which layers 3D navigation cues, lane boundaries, and contextual alerts directly onto the windshield. The display dynamically adapts to sunlight, fog, and glare, giving drivers a floating augmented-reality interface that feels more like a fighter jet cockpit than a dashboard.
</Example2>

In this case, if the user asked for “new product release”, you would make it a single item, returning a single pair of start and end line numbers.

Use your judgement and the content description to determine whether to extract multiple items or a single item.
"""

SUMMARIZATION_PROMPT = """Provide a 3-sentence summary of the following newsletter post.
If the post has a main essay, particularly if it gives the issue its title, 
focus on that, ignoring other extraneous sections. 
Always ignore ads.
Be concise and informative.

<example>
AI agents are improving at a pace far faster than traditional computing progress, as shown by METR’s finding that the length of tasks agents can complete (measured in human-equivalent work time) is doubling roughly every seven months—about four times faster than Moore’s law.

Recent benchmarks show top models like GPT‑5 can now reliably handle tasks equivalent to over two hours of human work, with simple projections suggesting agents capable of month-, year-, or even career-length tasks within the next decade.

This trend, however, faces major caveats—current success rates, focus on purely digital tasks, and the possibility that progress will slow or break at more complex, longer-horizon work—but this could still dramatically and quickly transform the nature of work.
</example>

Don't say "The essay argues", "The author concludes that", etc.

Bad: "The author points out that dogs have a uniquely powerful sense of smell that enables them to..."
Good: "Dogs have a uniquely powerful sense of smell that enables them to..."

"""

# Used in generate_content_insights() — user message template containing
# instructions and an example report for analyzing item CTR performance.
ANALYSIS_PROMPT = """
<instructions>
You are an expert data analyst.
You have been given a list of items featured in a newsletter, each with an associated click-through rate (CTR) and percentile ranking among all items.
Produce a report analyzing the dataset of items and their click-through rates (CTR), following the provided template.
Aim for ~3 top performing item archetypes, each supported by multiple examples from the data, and 1-3 underperforming archetypes for contrast. 
Highlight key insights and patterns. 
Set your "top tier" threshold to include 5-10 of the top items by CTR.
Use markdown formatting with headings, bullet points, and sections as shown in the template.
</instructions>

<template>
Here’s what stands out when you look at the highest‑click-through rate (CTR) links (90th percentile and above) and compare them to the rest.

Top tier (≥90th percentile):

- ID 53 – Chicago Tech Mixer and Social (Tech / AI / Data) – 9.6% (100%)
- ID 7 – From Idea to MVP – 9.3% (97%)
- ID 27 – Chicago Coffee Club | Vertical AI Founders & Funders – 9.3% (97%)
- ID 4 – Machine Learning Reading Group Social Hour – 9.2% (94%)
- ID 48 – The AI Collective – 9.2% (94%)
- ID 50 – Fireside Chat: Unlocking the Power of Context Engineering w/ Pinecone – 9.0% (92%)
- ID 43 – Emerging Tech Inno Week: AI Day – 8.7% (90%)


---

### 1. Community‑first, social AI/tech gatherings perform extremely well

High performers with this flavor:

- ID 53 – Chicago Tech Mixer and Social (Tech / AI / Data) – 9.6% (100%)
- ID 4 – Machine Learning Reading Group Social Hour – 9.2% (94%)
- ID 48 – The AI Collective – 9.2% (94%)
- ID 27 – Chicago Coffee Club | Vertical AI Founders & Funders – 9.3% (97%)
- ID 29 – Chicago Data Happy Hour – 7.2% (80%)
- ID 54 – Chicago Tech Connect Breakfast – 7.3% (83%)
- ID 5 – Chicago – International Generalist Day Meetup – 7.0% (76%)

**Common traits:**

- The framing is explicitly social or communal: “Mixer and Social,” “Happy Hour,” “Breakfast,” “Coffee Club,” “Collective,” “Social Hour.”
- Often broad but targeted topics: “Tech / AI / Data,” “Machine Learning,” “Vertical AI” rather than a hyper‑narrow niche.
- Implied low barrier to entry: you can “drop in,” meet people, and benefit even if you’re not deeply technical or prepared.
- Titles emphasize the *community* more than a specific talk title or speaker.

**Event type that works:**  
Community‑centric meetups and socials for AI / tech / data people, with clear networking and “come hang out” positioning.


---

### 2. Builder‑focused, “ship something” sessions are very strong

High performers in this category:

- ID 7 – From Idea to MVP – 9.3% (97%)
- ID 0 – Vibe Coding Unlocked: Effortless App Building with Databricks – 7.5% (85%)
- ID 31 – Building an MCP in Node.js & Using WebAssembly to Safely Run Unsafe Code – 6.7% (70%)
- ID 30 – AI in Healthcare: Innovation, Infrastructure & Human-Centered Trust – 6.8% (74%)

**Common traits:**

- Clear “you will build / create / launch” promise: “Idea to MVP,” “App Building,” “Building an MCP.”
- Concrete outcomes or skills implied, often around modern stacks (Databricks, Node.js, WebAssembly) that builders care about.
- Strong appeal to early‑stage founders and technical product people.

**Event type that works:**  
Hands‑on or concept‑to‑product sessions that speak directly to founders, makers, and engineers trying to get from idea to something real.


---

### 3. AI‑specific and infra‑deep‑dive content pops, especially with credible brands

Strong examples:

- ID 50 – Fireside Chat: Unlocking the Power of Context Engineering w/ Pinecone – 9.0% (92%)
- ID 43 – Emerging Tech Inno Week: AI Day – 8.7% (90%)
- ID 37 – AI Tinkerers #21 Hosted by Drive Capital – 8.2% (88%)

**Common traits:**

- Explicit AI focus, often on infrastructure or cutting‑edge concepts: “Context Engineering,” “AI Day,” “AI Tinkerers.”
- Involvement of recognizable tech brands (Pinecone, NVIDIA, Supermicro) or known communities (AI Tinkerers).
- Framed as deep dives or insider discussions (e.g., “Fireside Chat,” “Insights,” “Tinkerers”) rather than generic panels.

**Event type that works:**  
AI‑forward, infra‑oriented sessions with a clear advanced topic and credible technical brand or community.


---


### What underperforms by comparison

Lower‑CTR events cluster around a few themes:

1. **Finance/crypto/policy‑heavy without a builder angle**

   - ID 44 – Chicago Stablecoin Social – 2.0% (2%)
   - ID 40 – Blockchain & Digital Assets: US Policy Trends & 2026 Outlook – 2.6% (13%)
   - ID 46 – Money Moves: The Future of Investment Management – 2.5% (10%)
   - ID 32 – Bitwise Crypto Diligence Summit – 3.2% (22%)
   - ID 33 – VC / LP Gallery Series w Private Chef: II – 2.6% (13%)

   These skew investor/finance/policy‑oriented, with little in the title about how founders or builders will benefit directly.

2. **Generic networking / corporate events with vague outcomes**

   - ID 56 – Connect & Grow Chicago – 4.9% (47%)
   - ID 24 – Hispanic Heritage Month Celebration 1871 X LIT – 2.4% (8%)
   - ID 55 – Navigate the Patient Landscape – 4.6% (41%)

   These may be valuable for community or mission reasons, but the title doesn’t promise a sharp, actionable benefit to a founder/AI/tech builder audience.

3. **Recurring programs without a specific topical hook**

   - IDs 6, 17, 57 – 1 Million Cups Chicago – low CTRs across the board.
   - ID 11 – ChiTech Fall: Gravity Outlook – 2.1% (4%)
   - ID 42 – Java Global Insights: Innovation w/ Discover and Brazilian Experts – 2.1% (4%)

   The framing is abstract (“Outlook,” “Insights,” “Innovation”) and not clearly tied to what this specific audience will learn, build, or who they’ll meet.

These types of events may still be important for diversity of programming, ecosystem health, or specific partner commitments, but they are not your primary CTR drivers.


---

### Summary: Top‑performing link archetypes

Based on CTR and percentiles, the consistently high‑engagement link types are:

1. **Community‑centric AI/tech socials**
   - Mixers, happy hours, breakfasts, “collectives,” and “clubs” with a clear AI/tech/data focus and a strong networking/social promise.

2. **Builder‑oriented sessions**
   - Events that promise movement from idea → MVP, app building, or concrete technical outcomes that appeal to founders and engineers.

3. **AI‑infra and advanced topic deep dives with strong brands**
   - AI Day, AI Tinkerers, context engineering, infra for financial services—especially when co‑branded with known vendors (Pinecone, NVIDIA, etc.).

If you’re optimizing for engagement, skew your programming and naming toward these patterns, and treat generic finance/policy events, broad “innovation” talks, and unspecific recurring programs as secondary or as vehicles for other goals (e.g., ecosystem signaling, partner relations) rather than CTR workhorses.
</template>
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