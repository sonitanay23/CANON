# Canon: brand governance before you publish

**Team LazyCoderz** · Tanay Soni (team leader), Heet Mirani, Pratham Shah
**Problem statement:** Inkloom AI, brand governance · **Round 3: the final product**
**Live:** https://canon-yzsz.onrender.com · **Code:** https://github.com/sonitanay23/CANON

Canon checks every draft, PDF and image against a brand's approved rules and facts before it is published. It explains each issue with the exact rule or fact behind it and suggests the smallest fix that keeps what the writer meant. Submissions are scored for risk and routed to the right reviewer. Every approval gets a signed seal that anyone can verify. People make every final call: nothing is applied, approved or published without a click.

The demo brand, **Velora Money**, is a fictional Indian fintech. All people, facts, logos and content in the demo are invented.

---

## Contents

1. [Try it in five minutes](#try-it-in-five-minutes)
2. [Run it yourself](#run-it-yourself)
3. [Complete feature list](#complete-feature-list)
4. [The three Round 3 improvements](#the-three-round-3-improvements)
5. [Other improvements after Round 2](#other-improvements-after-round-2)
6. [Architecture and implementation](#architecture-and-implementation)
7. [Testing](#testing)
8. [Known limitations](#known-limitations)
9. [Roadmap](#roadmap)
10. [Disclosures](#disclosures)

---

## Try it in five minutes

Open the live link. The free host sleeps when idle, so the first load can take up to a minute. No sign-up: pick a demo account.

| Step | Account | What to do | What you'll see |
|---|---|---|---|
| 1 | **Riya Sharma** (creator) | Open **Diwali savings post** → **Check** | 12 issues marked on the draft: wrong price, expired cashback, missing disclaimer, "best in India", pressure tactics. Each one cites its rule or fact, with a minimal fix |
| 2 | Riya | Open **Diwali banner** → **Check** | Canon reads the text in the image and finds a retired logo, a 1.37:1 contrast line, a wrong price in the artwork, "Guaranteed", and missing alt text. Numbered boxes on the artwork |
| 3 | Riya | Accept or edit fixes, keep one with a reason, **Submit for review** | Canon scores the risk and routes it. This one makes financial claims, so it goes to **Legal** |
| 4 | **Kabir Mehta** (reviewer) | **Review queue** | Work sorted by risk into Legal / Standard / Fast lanes, each with reasons. Kabir can't approve legal-lane work |
| 5 | **Meera Iyer** (legal reviewer) | Open the piece → **Approve and seal** | A seal code like `VLM-XXXX-XXXX`, a public seal page and a live badge |
| 6 | Anyone, signed out | **Verify** → *Fill an edited copy* → **Verify text**; or try the image samples | "Changed after approval", with the exact difference highlighted. The approved banner shows **Approved**; the edited one shows **Modified** |
| 7 | **Ananya Rao** (brand admin) | **Brand** → change Velora Pro price ₹499 → ₹549 | Every approved piece that still says ₹499 (text **and** images) with drafted updates. Sending them marks old seals **outdated** |
| 8 | Ananya | **Insights** | Trend charts, recurring issues, lane speed, accuracy over time, and the rule debugger flagging "No exclamation marks" as unclear |
| 9 | **Dev Malhotra** (agency) | **Partner workspace** | Briefs from the brand, a brand kit with logos, partner-visible facts only, and reviewer feedback. **Start a draft** from a brief |

An admin can reset the demo from the home page.

---

## Run it yourself

You need **Python 3.9 or newer** and **git**. Everything else is installed for you.

### macOS / Linux: one command

```bash
git clone https://github.com/sonitanay23/CANON.git canon
cd canon
./start.sh            # creates .venv, installs requirements, starts on http://localhost:5050
```

### Any OS: step by step

```bash
git clone https://github.com/sonitanay23/CANON.git canon
cd canon
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py                        # http://localhost:5000  (set PORT=5050 on macOS if AirPlay uses 5000)
```

The database is created and seeded with the demo brand on first start. Delete `canon.db` to start fresh.

### Turn on AI review (optional)

Without a key, Canon runs every rule-based and visual check and says that AI review is off. To turn AI on, get a free key at https://aistudio.google.com/apikey and either:

```bash
cp .env.example .env        # then paste your key into .env:  GEMINI_API_KEY=...
python -m canon.check_key   # makes one real AI call to confirm the key works
```

or `export GEMINI_API_KEY=...` before starting. `.env` is git-ignored; never commit a key.

### Optional: local OCR

If [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) is installed (`brew install tesseract`, `apt install tesseract-ocr`), Canon reads text in images locally, with no AI call. Without it, Canon uses the AI's vision model, and without either you can type the image's text yourself.

### Settings

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_API_KEY` | AI review and image reading with Google Gemini | unset (rules and visual checks only) |
| `ANTHROPIC_API_KEY` | Alternative AI provider (Claude) | unset |
| `CANON_MODEL` | Model ID override | `gemini-3.1-flash-lite` / `claude-haiku-5-5` |
| `CANON_AI_TIMEOUT` | Seconds before an AI call gives up | `45` |
| `SECRET_KEY` | Signs session cookies and approval seals | dev value; set in production |
| `SEAL_SECRET` | Separate key for seals (optional) | `SECRET_KEY` |
| `CANON_DB` | SQLite file path | `canon.db` |
| `CANON_NO_TESSERACT` | Ignore a local Tesseract (to test the AI path) | unset |

### Deploy (Render)

**New → Blueprint**, pick the repository; `render.yaml` sets up a free Python web service with gunicorn. Add `GEMINI_API_KEY` in the service's environment settings.

---

## Complete feature list

**Accounts and roles**
- Five demo roles: brand admin, reviewer, legal reviewer, creator, agency creator. Permissions are enforced on the server for every page and action
- Role-specific landing pages: creators → their drafts, reviewers → the queue, agencies → the partner workspace

**Brand source of truth**
- Brand voice; 16 rules across banned words, preferred terms, disclaimers, punctuation, tone, claims and visual checks; admins can add rules, edit wording and switch rules on or off
- Fact ledger: 10 facts, each with an owner, source, expiry and visibility (everyone / team + agencies / internal only)
- Logo registry: current and retired logos; admins upload, retire or remove logos
- Confidential terms masked before any AI call

**Input**
- Paste text, upload a text PDF, or upload an image (PNG, JPG, WebP up to 6 MB) with alt text; any combination
- Drafts can start from an agency brief

**Checks**
- Rule engine (no AI): banned words, preferred terms, required disclaimers, punctuation, numbers that contradict the fact ledger (including "Rs." and "INR" prices), expired facts
- AI review: tone, pressure tactics, unsupported claims, contradictions of approved facts. Every AI finding must quote the draft exactly and cite a rule or fact the author can see; anything else is dropped and counted
- **Image checks:** text in images through the same rule engine and AI review; WCAG 2.1 AA contrast per line of text; retired-logo detection at any size and on any background; missing or meaningless alt text
- Each finding shows the flagged text, reason, rule or fact ID, severity, which layer found it, confidence, and the writer's likely intent
- A brand-fit score per piece, and numbered marks on the text and boxes on the artwork

**Fixing**
- Minimal word-level fixes: accept, edit, or keep the original with a required reason
- "That's not what I meant": the AI rewrites the fix from an edited intent
- Alt text: accept Canon's suggestion or write your own
- Artwork: upload a corrected image and re-check; correct the text Canon read from an image

**Review workflow**
- Draft → in review → changes requested → approved, with comments
- **Risk scoring and routing:** every submission gets a 0–20 risk score with stated reasons and a lane: fast (4 h target), standard (24 h) or legal (48 h). Financial-claim language sends work to Legal, and only Legal or an admin can approve it
- **Review queue** by lane, with assignee, waiting time and overdue flags; batch approval for clean fast-lane work
- Approval warns when high-severity issues are still open

**Approval seals and public verification**
- Every approval issues an HMAC-SHA256-signed seal over the exact text, image and version
- Public **Verify** page (no sign-in): paste text, upload an image or enter a seal code → *approved*, *approved copy* (re-saved image), *excerpt*, *outdated*, *modified* (with a word diff) or *unknown*; tampered seal records are detected
- Public seal page and a live SVG badge for embedding

**Keeping content current**
- Living fact ledger: approval records which facts each piece uses. Changing a fact lists every approved or in-review piece that still uses the old value, in text or in images, with drafted updates
- Sending updates re-checks and re-routes text pieces, sends artwork back to its author with exact instructions, and marks old seals outdated

**Agency partners**
- Partner workspace: briefs with due dates, the agency's work with status, open issues, reviewer feedback and seals, the brand kit (voice, logos, rules) and partner-visible facts only
- Internal facts are never shown to agencies and never sent to the AI for their drafts
- Admins send briefs and mark them delivered

**Insights**
- Governance dashboard: issues per week by kind, suggestion acceptance per week, issues per checked piece, most recurring issues, median time to approval per lane, issues by team, and reliability-test accuracy over time
- Guideline debugger: override rate per rule; flags a rule as unclear at 10+ decisions and 40%+ overridden; groups the reasons; the AI drafts a clearer rule for admin approval

**Trust and accountability**
- Reliability scorecard: 25 labelled test pieces (15 with planted issues, 10 clean), run on demand; reports detection, false flags and dropped AI suggestions
- Version history with word diffs, every decision with its reason, a full audit log, and a public "How Canon uses AI" page

---

## The three Round 3 improvements

These are the three we named at Round 2. All three are delivered.

### 1. Visual and accessibility checks ✅

| Promised at Round 2 | Delivered |
|---|---|
| Image upload | PNG, JPG, WebP up to 6 MB, on a new draft or as a corrected version of an existing one; image versions are kept |
| OCR of text in images through the same rule engine | Tesseract when installed; otherwise the AI's vision model returns each line with its position. The text goes through the same rule engine and AI review as typed text, and people can correct it |
| WCAG contrast checks | Each line of text is split into text and background colours (2-means clustering); the WCAG 2.1 contrast ratio is checked against 4.5:1, or 3:1 for large text. Findings give both colours, the ratio and a colour that passes |
| Missing alt text | Empty, generic ("image", "banner") or file-name alt text is flagged; the AI drafts alt text that can be accepted in one click |
| Retired-logo detection | Multi-scale normalised cross-correlation on edge maps (numpy FFT). Matches score 0.70–0.95; busy look-alike images stay under 0.50; the threshold is 0.65. If a current logo fits the same spot better, the match is discarded |

### 2. Agency portal and public verification ✅

| Promised at Round 2 | Delivered |
|---|---|
| A dedicated partner workspace | A separate workspace and navigation for agency accounts: briefs, their work and feedback, the brand kit, partner-visible facts. Admins send briefs |
| A signed approval seal | HMAC-SHA256 over content ID, version, the SHA-256 of the normalised approved text, the SHA-256 of the approved image, and the issue time. Short code, public seal page, live badge |
| A public page showing approved, modified (with a diff) or unknown | `/verify` for text, images and seal codes. It also reports *outdated* (replaced after a fact change), *excerpt*, *re-saved copy* (by perceptual hash and text comparison) and *tampered* |

### 3. Risk-based routing and governance insights ✅

| Promised at Round 2 | Delivered |
|---|---|
| Low-risk content takes a fast lane | A score of 2 or less with no open issues → fast lane: 4-hour target, any reviewer, batch approval. Still approved by a person |
| Risky content routed to the right reviewer (claims to legal) | Financial-claim language, or open or overridden claim and disclaimer issues → Legal lane, assigned to the legal reviewer; only Legal or an admin can approve |
| A dashboard of recurring issues and accuracy over time | Governance dashboard with seven views, including suggestion acceptance (a precision signal) and reliability-test accuracy per run |

---

## Other improvements after Round 2

- **Visual refresh:** a richer colour system, a dark animated hero, animated proof marks, count-up figures, score rings, a progress overlay while checks run, and motion that respects reduced-motion settings
- **Robustness:** length limits on every input; plain-language errors for bad PDFs and images; forms keep what you typed after an error; double-submit protection; repeating an action (check, submit, approve, decide, fix-forward) is harmless; friendly 400/403/404/413/500 pages
- **Prices** written as "Rs." or "INR" now match the fact ledger
- **Fact changes** now reach image text as well as copy
- **Charts** are server-rendered SVG, so the dashboard has no CDN dependency
- **Tests:** from 24 to 52

---

## Architecture and implementation

```
Browser ──> Flask app (app.py, server-rendered Jinja pages, plain CSS, a little vanilla JS)
              ├─ canon/rules.py     rule engine: patterns, disclaimers, fact matching, claim links
              ├─ canon/ai.py        Gemini / Claude calls (text and images), retries, model fallback,
              │                     redaction, JSON parsing, validation
              ├─ canon/vision.py    image loading, OCR, WCAG contrast, logo matching, alt text, fingerprints
              ├─ canon/seals.py     signed approval seals and verification (text, image, code)
              ├─ canon/services.py  checks, fixes, approval, risk routing, fact changes, guideline debugger
              ├─ canon/charts.py    server-rendered SVG charts
              ├─ canon/testset.py   labelled test set and scorer (background thread)
              ├─ canon/seed.py      fictional demo brand, content, logos and sample history
              └─ canon/db.py        SQLite schema, migrations and helpers
```

**How a check runs:** the latest text goes through the rule engine, then the AI (tone and claim rules only, with confidential terms masked). AI findings are validated and the rule engine wins on overlap. If there is an image, its stored measurements (contrast, logos) and its text go through the same pipeline; image findings carry their position for the overlay. AI answers are cached for identical inputs.

**How routing works:** `services.assess_risk` adds points with a stated reason for each: unchecked version +3, each open high issue +3, other open issues +1 each, overridden suggestions +1 each, high-reach channel +1, agency author +1, image +1, AI unavailable +1. Legal triggers override the score.

**How seals work:** `seals.issue` runs inside approval. Text is normalised (whitespace and quote styles only) and hashed; the signature covers the hashes, version and time. Verification recomputes the signature, so an edited database record shows as *tampered*. Images match by exact SHA-256 first, then by a 64-bit difference hash; near matches have their text read and compared.

**Stack:** Python 3.12 (3.9+ supported), Flask 3, Jinja, SQLite, pypdf, Pillow, numpy, gunicorn. No front-end build and no JavaScript libraries. AI: Google Gemini API (`gemini-3.1-flash-lite`, native `generateContent` endpoint, images as inline data) over HTTPS with the standard library; Claude API supported as an alternative.

**Data:** SQLite, created and seeded on first start, with migrations for older local databases. On Render's free tier the disk is temporary, so the demo returns to its starting state when the service restarts.

---

## Testing

```bash
python -m unittest discover -s tests -v      # 52 tests, about 30 seconds, no network or API key needed
```

| File | What it covers |
|---|---|
| `tests/test_flow.py` (9) | The main journey end to end: check, fix, keep with reason, submit (routed to Legal), reviewer blocked, Legal approves and seals, agency can't see internal facts, price change finds 8 pieces including an image, sends updates and outdates seals, guideline debugger, new draft, reliability run, AI validation, redaction |
| `tests/test_ai.py` (15) | The AI layer with the network mocked: provider choice, retries on 429/503, model fallback, invalid JSON, tolerant quote matching, validation rules |
| `tests/test_round3.py` (28) | Contrast against WCAG reference values; retired logo found (large, small, on dark backgrounds) and not confused with the current logo; no logo found in a blank image; alt-text rules; bad images rejected; fingerprints; the Gemini image request and the box-to-pixel conversion (mocked); AI-failure fallback; every visual finding on the demo banner; artwork vs alt-text decisions; uploading corrected artwork; correcting image text; image privacy; seal signatures and tamper detection; every verify verdict for text, images and codes, including an injection attempt; re-saved artwork; fast-lane routing and batch approval; risk reasons; role checks; agency brief-to-draft; briefs and logo upload validation; the governance page; empty, oversized and malformed input; 404s instead of 500s; repeated and refreshed actions |

We also ran the suite with Tesseract disabled (`CANON_NO_TESSERACT=1`) to match the hosted setup, checked logo matching against 12 randomly generated busy images (highest score 0.46, threshold 0.65), and clicked through every page as all five roles at desktop and phone widths.

### Reliability results (measured 22 Oct, Round 2)

| Run | Planted issues caught | Rule-detectable | Tone & claim (AI) | False flags |
|---|---|---|---|---|
| Rule engine only | 13 of 15 (87%) | 13 of 13 | 0 of 2 | 0 |
| Gemini run 1 | 13 of 15 (87%) | 13 of 13 | 0 of 2 | 0 |
| Gemini run 2 | 14 of 15 (93%) | 13 of 13 | 1 of 2 | 6 of 24 flags |
| Gemini run 3 | 15 of 15 (100%) | 13 of 13 | 2 of 2 | 1 of 21 flags |

What changed between runs is in the Round 2 notes in the git history. AI results vary from run to run; re-run the test set from the **Reliability** page, and the result appears on the dashboard.

---

## Known limitations

- **Text in images on the hosted demo is read by the AI** (the free host has no Tesseract), so on Gemini's free tier it can hit rate limits. If it does, Canon says so and you can type the text. The seeded demo images were read once when the demo was built, so they always work.
- **Logo matching** finds a logo's shape at any size and on any background, but not heavily rotated, redrawn or partly hidden logos.
- **Contrast** splits each text line into two colours; text over photos or strong gradients is approximated.
- **Seals use a shared secret (HMAC):** only Canon can verify them. Public-key signatures are on the roadmap.
- **Masking** can't hide confidential text inside an image sent to the AI.
- **Demo accounts have no passwords**, and there is one brand per deployment.
- **Sample history:** weeks before the demo was loaded contain generated sample activity for the fictional brand, labelled on the dashboard. The reliability figures are real measurements.

---

## Roadmap

| Horizon | What | Why |
|---|---|---|
| Next 3 months | Real sign-in (SSO), PostgreSQL, background job queue | Production use by a real team |
| | Connectors: Google Docs, Notion and Figma plugins to check where people write and design | Catch issues before content reaches Canon |
| | Public-key seals (Ed25519) and C2PA content credentials | Anyone can verify offline, without trusting Canon's server |
| | Learned routing thresholds from real approval outcomes | Fast-lane size grows only as accuracy is proven |
| 3–6 months | Multi-brand workspaces and sub-brands | Agencies and groups with several brands |
| | Video and audio: frame sampling and transcripts through the same checks | Reels and ads |
| | Regional languages (Hindi, Tamil, Marathi) for rules and facts | Indian brands publish in many languages |
| 6–12 months | Regulatory packs (e.g. SEBI, RBI, ASCI advertising codes) as rule sets | Compliance out of the box for regulated sectors |
| | Pre-publish API and CMS webhooks that block unsealed content | Make the seal the gate to publishing |
| | On-premise model option | Brands that can't send drafts to an external AI |

---

## Disclosures

- **AI assistance:** this codebase was written with substantial help from Claude (Anthropic), and reviewed and tested by the team.
- **External service:** the Google Gemini API (free tier) for AI review and reading text in images, when a key is configured. Under Google's terms for unpaid services, submitted content may be used to improve Google's products and may be read by human reviewers. That's acceptable here because all demo data is fictional; a real deployment would use a paid tier.
- **Libraries:** Flask, Jinja2, pypdf, Pillow, numpy, gunicorn, all open source and used under their licences. Optional: Tesseract OCR. No templates or boilerplate projects were used.
- **Fonts:** Instrument Sans and Source Serif 4 from Google Fonts, with system fallbacks. The demo artwork uses Inter and Poppins (SIL Open Font License).
- **Data:** all demo data, logos and artwork are fictional and were made for this project. No real customer or personal data is used.
- **Build window:** the Round 3 work was built and committed during the Round 3 window (27–30 Oct 2026); see the git history.
