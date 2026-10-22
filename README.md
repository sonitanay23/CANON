# Canon: brand governance before you publish

**Team LazyCoderz** · Tanay Soni (team leader), Heet Mirani, Pratham Shah
**Problem statement:** Inkloom AI, brand governance · Round 2: The Build

Canon checks every draft against a brand's approved rules and facts before it is published. It explains each issue with the exact rule or fact behind it, and suggests the smallest fix that keeps what the writer meant. People make every final call: nothing is applied, approved or published without a click.

The demo brand, **Velora Money**, is a fictional Indian fintech. All people, facts and content in the demo are invented.

---

## Try it in 3 minutes

1. Open the app and sign in as **Riya Sharma (Creator)**. No password is needed.
2. Open **Diwali savings post** and click **Check**. Canon finds 12 issues, including a wrong price, an expired cashback offer, a missing investment disclaimer and an unproven "best in India" claim. Each issue shows the rule or fact it breaks and a minimal fix.
3. Accept a few fixes, keep one original with a reason, then **Submit for review**.
4. Switch to **Kabir Mehta (Reviewer)** and approve it.
5. Switch to **Ananya Rao (Admin)**. In **Brand rules & facts**, change the Velora Pro price from ₹499 to ₹549. Canon lists every approved piece that still says ₹499 and drafts the update for each.
6. Open **Rule insights** to see the guideline debugger: the "No exclamation marks" rule is overridden in half of its decisions, so Canon flags it as unclear.

An admin can reset the demo to its starting state from the home page.

---

## What works in this build

| Area | Feature |
|---|---|
| Accounts | Four demo roles: brand admin, reviewer, creator, agency creator. Permissions are enforced on the server |
| Brand setup | Brand voice, 13 rules, a 10-fact ledger with owner, source, expiry and visibility; admins can add rules, edit wording, switch rules on or off and change facts |
| Input | Paste text, or upload a PDF (text is extracted) |
| Checks: rule engine | Banned words, preferred terms, required disclaimers, punctuation rules, numbers that don't match the fact ledger, use of expired facts |
| Checks: AI review | Tone, unsupported claims and fact consistency via the Google Gemini API (Claude API also supported), with every finding validated (see below) |
| Findings | The flagged text, reason, cited rule or fact ID, severity, which layer found it, confidence, and the writer's likely intent |
| Fixes | Minimal word-level diff; accept, edit, or keep the original with a required reason; "that's not what I meant" rewrites the fix from an edited intent (AI) |
| Workflow | Draft → in review → changes requested → approved; comments; approval warns when high-severity issues are still open |
| History | Every version with a word diff, every decision with its reason, and an activity log |
| Improvement 1: living fact ledger | Approval records which facts each piece uses; changing a fact lists every affected approved piece with a drafted fix, and sends the selected ones back to review |
| Improvement 2: guideline debugger | Override rate per rule; flags a rule as unclear only at 10+ decisions and 40%+ overridden; groups override reasons; AI drafts a clearer rule for admin approval |
| Improvement 3: intent-first fixes | Each fix states what the writer meant and changes only the flagged words |
| Agency access | Agency partners never see internal facts, and their drafts are never checked against them by the AI |
| Reliability scorecard | 25 labelled test pieces (15 with planted issues, 10 clean); reports detection, false flags and AI suggestions dropped by validation |
| Accountability | Full audit log for admins; a public "How Canon uses AI" page |

---

## How the AI is kept honest

- **Rule engine first.** Objective checks (banned words, disclaimers, numbers vs. the fact ledger) never use AI. If the rule engine and the AI flag the same text, the rule engine's finding wins.
- **Validation.** An AI finding is kept only if its quote appears in the draft exactly and it cites a rule or fact ID that exists and that the author is allowed to see. Everything else is dropped, and the count is shown.
- **Redaction.** Terms on the brand's confidential list are replaced with placeholders before any text is sent to the AI.
- **Human review.** Findings with confidence under 0.6, or with high severity, are marked for human review. Nothing is applied automatically.
- **Graceful failure.** If there's no API key, the call fails, or the model returns invalid JSON twice, Canon still shows the rule-engine results and says the AI review was unavailable.

## Reliability results (rule engine only, measured)

On the 25-piece test set with AI off: **13 of 13** rule-detectable planted issues caught, **0** false flags, **0** flags on the 10 clean pieces. The 2 tone and claim issues (T14, T15) need the AI and are reported as missed when it is off. Run the test set from the **Reliability** page, with or without AI.

---

## Architecture

```
Browser ──> Flask app (app.py, server-rendered pages)
              ├─ canon/rules.py     rule engine: patterns, disclaimers, fact matching, claim links
              ├─ canon/ai.py        Gemini / Claude API calls, retries, redaction, JSON parsing, validation
              ├─ canon/services.py  checks, fixes, approval, fact changes, guideline debugger
              ├─ canon/testset.py   labelled test set and scorer (runs in a background thread)
              ├─ canon/seed.py      fictional demo brand and content
              └─ canon/db.py        SQLite schema and helpers
```

**Stack:** Python 3.12, Flask, Jinja templates, plain CSS (no front-end build), SQLite, pypdf, gunicorn. AI: Google Gemini API (`gemini-3.1-flash-lite` by default, native `generateContent` endpoint), called over HTTPS with the standard library. The Claude API is supported as an alternative by setting `ANTHROPIC_API_KEY` instead. Rate-limit responses are retried twice with a short wait; if the configured model is unavailable, Canon picks an available Gemini Flash model.

**Data:** SQLite file, created and seeded on first start. On hosts with an ephemeral disk (such as Render's free tier), the demo resets to its starting state whenever the service restarts.

---

## Run locally

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=your-key        # optional; without it Canon runs rule-based checks only
python -m canon.check_key             # one real AI check, to confirm the key works
python app.py                         # http://localhost:5000
```

Tests (end-to-end flow with AI off, plus the AI layer with the network mocked):

```bash
python -m unittest discover -s tests -v
```

## Deploy (Render)

1. Push this repository to GitHub.
2. On Render: **New → Blueprint**, pick the repository. `render.yaml` sets everything up.
3. Add `GEMINI_API_KEY` in the service's environment settings. Never commit the key to the repository.

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_API_KEY` | Enables AI review with Google Gemini | unset (rule engine only) |
| `ANTHROPIC_API_KEY` | Alternative: AI review with Claude | unset |
| `CANON_MODEL` | Model ID override | `gemini-3.1-flash-lite` (Gemini) or `claude-haiku-5-5` (Claude) |
| `CANON_AI_TIMEOUT` | Seconds before an AI call gives up | `45` |
| `SECRET_KEY` | Signs session cookies | dev value; set in production |
| `CANON_DB` | SQLite file path | `canon.db` in the project folder |

---

## Changes from the Round 1 plan

| Round 1 plan | Round 2 build | Why |
|---|---|---|
| Hosted LLM (Claude planned) | Google Gemini API (free tier); Claude still supported | Free tier for the hackathon build; provider is one setting |
| Next.js frontend + FastAPI backend | One Flask app with server-rendered pages | One service, no build step, quicker and more reliable to ship in 72 hours |
| PostgreSQL + pgvector | SQLite | The demo needs no vector search; fact links are exact, which is more reliable for the affected-content search |
| Image OCR, contrast and logo checks | Moved to Round 3 | Kept out of the critical path |
| Separate agency and public portals | Agency role with restricted fact visibility; public verification moved to Round 3 | Core workflow first |
| Risk-based fast lane | Moved to Round 3 | Needs real usage data to set thresholds |

## Three improvements for Round 3

1. **Visual and accessibility checks:** image upload, OCR of text in images through the same rule engine, WCAG contrast checks, missing alt text, retired-logo detection.
2. **Agency portal and public verification:** a dedicated partner workspace, plus a signed approval seal and a public page that shows whether a piece of content is approved, modified (with a diff) or unknown.
3. **Risk-based routing and governance insights:** low-risk content takes a fast lane, risky content is routed to the right reviewer (claims to legal), and a dashboard tracks recurring issues and accuracy over time.

---

## Disclosures

- **AI assistance:** this codebase was written with substantial help from Claude (Anthropic), reviewed and tested by the team.
- **External service:** the Google Gemini API (free tier) for AI review, when an API key is configured. Under Google's terms for unpaid services, submitted content may be used to improve Google's products and may be read by human reviewers; that's acceptable for this demo because all data is fictional, and a real deployment would use a paid tier.
- **Libraries:** Flask, Jinja2, pypdf, gunicorn (open source, used under their licences). No templates or boilerplate projects were used.
- **Data:** all demo data is fictional. No real customer or personal data is used.
