"""
Prompt templates for LLM calls used throughout the analytics app.
"""

# Legacy single-description prompt — used by extract_items() for backward compatibility.
PARSING_PROMPT_SINGLE = """You are given an HTML document with line numbers.
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

In this case, if the user asked for "new product releases", you would make each one of the four news items into a separate item, unless specifically instructed otherwise.
Make sure to include all of them, for instance, in this case, you would return four pairs of start and end line numbers.

In other cases, the user may be referring to a single item, as in Example2.

<Example2>
**New product release**
Audi introduces the A7 NeoSport, a sleek fastback hybrid that pairs a 2.0L turbo engine with a next-gen ultracapacitor boost system, delivering instantaneous torque without relying on traditional lithium-ion packs. Early testers report near-zero lag during acceleration and a smoother handoff between electric assist and combustion power than any previous Audi hybrid.

The NeoSport also debuts Audi's new "HoloHUD" panoramic projection system, which layers 3D navigation cues, lane boundaries, and contextual alerts directly onto the windshield. The display dynamically adapts to sunlight, fog, and glare, giving drivers a floating augmented-reality interface that feels more like a fighter jet cockpit than a dashboard.
</Example2>

In this case, if the user asked for "new product release", you would make it a single item, returning a single pair of start and end line numbers.

Use your judgement and the content description to determine whether to extract multiple items or a single item.

Remember that portions of text that do not contain links, especially if they are very short, probably aren't good candidates for items.
For instance, if asked to find the "Ad" section, you would parse the example below as one single item, 
not as "In partnership with AnyCorp" as a first item and the rest as a second, unless specifically instructed otherwise.

<Example3>
In partnership with AnyCorp

Have you ever had an issue with one of your products?
AnyCorp [link] offers a comprehensive solution suite for all kinds of business issues.
Visit AnyCorp.com [link] to have all your corporate problems solved.
</Example3>
"""

# Used in extract_items() for backward compatibility
PARSING_PROMPT = PARSING_PROMPT_SINGLE

# Used in extract_sections() — system message for multi-section extraction.
SECTION_PARSING_PROMPT = """You are given an HTML document with line numbers.
Your task is to identify items within the HTML that belong to each of the named sections listed at the bottom.
Each section has a name and a description of the content to look for.

For each section, provide the start and end line numbers (inclusive) for every item matching that section's description.
Make each discrete item a separate pair of start and end line numbers.
Do not include any items that do not match a section's description.
Group items by section name.

Sometimes, a section description may refer to multiple similar items, as in Example1.

<Example1>
Section: "Product Releases"
Description: "New product release items"

**New product releases**
-Tesla unveils the Roadster X-Plus, a lightweight carbon-ceramic edition with a 0–60 time of 1.7 seconds and a 700-mile solid-state battery pack.
-BMW releases the i5 Touring ActiveHybrid, featuring adaptive solar-roof charging and an AI-driven energy-routing system for long-distance commuters.
-Toyota launches the Land Cruiser Micro-Hybrid, a compact off-road SUV aimed at urban explorers, with a detachable roof rack drone for scouting terrain.
-Rivian debuts the R1T TrailForge Edition, adding magnetically adjustable suspension plates and an onboard terrain-mapping assistant trained on 40M trail miles.
</Example1>

In this case, for the "Product Releases" section, you would make each of the four news items into a separate item within that section.
Make sure to include all of them.

In other cases, a section description may refer to a single item, as in Example2.

<Example2>
Section: "Featured Release"
Description: "The main featured product release"

**New product release**
Audi introduces the A7 NeoSport, a sleek fastback hybrid that pairs a 2.0L turbo engine with a next-gen ultracapacitor boost system.
The NeoSport also debuts Audi's new "HoloHUD" panoramic projection system.
</Example2>

In this case, for the "Featured Release" section, you would make it a single item.

Use your judgement and each section's description to determine whether to extract multiple items or a single item per section.
It is possible for a section to have zero items if no matching content is found.
"""

# Used in generate_content_insights() — user message template containing
# instructions and an example report for analyzing item CTR performance.
INSIGHTS_PROMPT = """
<instructions>
You are an expert newsletter analyst.

You have been given newsletter items grouped by section. Each item has a name/description, CTR, a percentile rank among all items, and a percentile rank within its section.

Write a concise performance report following the sample structure below.

Rules:
- Do not include item IDs.
- Use markdown tables for example items; single-sentence bullets for traits.
- If only one section is present, omit the Overall block and per-section headers — output just the archetype analysis directly.
- Show up to 5 examples per high/low block. Keep bullet lists to 2–3 points each.
   These do not necessarily need to be the absolute top or bottom performing items within the section.
   Rather, you should first decide what the characteristics of high- and low-performing items are within each section and THEN identify up to 5 examples that showcase this trend.
- Shorten long items to a headline label (≤10 words). Keep the key hook. For example: 
   Too long: "A framework for reliable browser-using agents Notte is a production‑oriented framework for building browser-using web automation agents, intended to be easier and cheaper to use at scale than alternatives like Browser Use and Convergence”
   Better: "Notte: a framework for browser-using agents"
- Always use 💡 for Content Insights and 🌐 for Across all sections (if it is present, i.e. if there are multiple sections).
   Choose a suitable emoji for each other section title.
</instructions>

<sample>
# 💡 Content Insights

## 🌐 Across all sections

### 📈 High performers
|| CTR | Percentile |
|------|-----|:---:|
| Chicago Tech Mixer | 9.6% | 100% |
| From Idea to MVP | 9.3% | 97% |
| Chicago Coffee Club: Vertical AI Founders | 9.3% | 97% |
| ML Reading Group Social Hour | 9.2% | 94% |
| Context Engineering w/ Pinecone | 9.0% | 92% |

**✅ What works:**
- Social/community framing with a clear AI/tech audience ("mixer," "happy hour," "collective").
- Concrete outcome tied to builder goals: "Idea to MVP," "Building an MCP."
- Credible attached brand or known community (Drive Capital, Pinecone, AI Tinkerers).

### 📉 Low performers
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

---

## 🔗 Quick Links section

### 📈 High performers
|| CTR | Percentile (all items) | Percentile (Quick Links items) |
|------|-----|:---:|:---:|
| Chicago Tech Mixer | 9.6% | 100% | 97% |
| Chicago Coffee Club: Vertical AI Founders | 9.3% | 97% | 96% |
| The AI Collective | 9.2% | 94% | 95% |
| Emerging Tech: AI Day | 8.7% | 90% | 100% |
| AI Tinkerers #21 (Drive Capital) | 8.2% | 88% | 94% |

**✅ What works:**
- AI-specific focus with a named brand or practitioner community.
- "Insider" positioning (tinkerers, practitioners) over generic conference language.

### 📉 Low performers
|| CTR | Percentile (all items) | Percentile (Quick Links items) |
|------|-----|:---:|:---:|
| ChiTech Fall: Gravity Outlook | 2.1% | 4% | 8% |
| Java Global Insights: Innovation | 2.1% | 4% | 6% |
| Bitwise Crypto Diligence Summit | 3.2% | 22% | 15% |
| VC/LP Gallery Series | 2.6% | 13% | 10% |
| Hispanic Heritage Month: 1871 × LIT | 2.4% | 8% | 7% |

**❌ What doesn't work:**
- Broad tech-adjacent topics that don't speak directly to AI/ML practitioners.
- Vague framing with no concrete skill or outcome promised.

---

## 🤿 Deep Dives section

### 📈 High performers
|| CTR | Percentile (all items) | Percentile (Deep Dives items) |
|------|-----|:---:|:---:|
| From Idea to MVP | 9.3% | 97% | 99% |
| Context Engineering w/ Pinecone | 9.0% | 92% | 98% |
| Vibe Coding: App Building with Databricks | 7.5% | 85% | 100% |
| AI in Healthcare: Innovation & Infrastructure | 6.8% | 74% | 88% |
| Building an MCP in Node.js | 6.7% | 70% | 92% |

**✅ What works:**
- Step-by-step "how to build X" framing with named tools.
- Strong appeal to founders and engineers trying to ship something concrete.

### 📉 Low performers
|| CTR | Percentile (all items) | Percentile (Deep Dives items) |
|------|-----|:---:|:---:|
| Money Moves: Future of Investment Mgmt | 2.5% | 10% | 15% |
| Blockchain Policy Trends & 2026 Outlook | 2.6% | 13% | 12% |
| Connect & Grow Chicago | 4.9% | 47% | 52% |
| Navigate the Patient Landscape | 4.6% | 41% | 48% |
| ChiTech Fall: Gravity Outlook | 2.1% | 4% | 5% |

**❌ What doesn't work:**
- Finance and healthcare topics consistently underperform vs. AI/tech for this audience.
- Generic outcome language without a specific tool or skill named.
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