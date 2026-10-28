"""Demo data for a fictional fintech brand, Velora Money.

Everything here is invented for the demo: the brand, people, agency,
facts and content. Nothing refers to a real company or customer.
"""
import json
import os
import random
from datetime import datetime, timedelta, timezone

from . import rules as engine, seals
from .db import ex, init_db, now, q, set_setting

USERS = [
    ("u-admin", "Ananya Rao", "admin@demo", "admin", "Brand team"),
    ("u-reviewer", "Kabir Mehta", "reviewer@demo", "reviewer", "Marketing"),
    ("u-writer", "Riya Sharma", "writer@demo", "creator", "Content team"),
    ("u-legal", "Meera Iyer", "legal@demo", "reviewer", "Legal & compliance"),
    ("u-agency", "Dev Malhotra", "agency@demo", "agency", "BrightLeaf Studio (agency)"),
]

VOICE = ("Velora Money speaks like a calm, knowledgeable friend: clear, warm and confident. "
         "We explain, we never hype. Short sentences, plain words, no slang and no pressure tactics. "
         "Good: \"See where your money goes, in one simple view.\" "
         "Bad: \"INSANE savings!!! Don't miss out!!!\"")

INVEST_DISCLAIMER = "Investments are subject to market risks. Read all scheme-related documents carefully."

# id, type, title, text, pattern, replacement, trigger, severity
RULES = [
    ("R-01", "banned", "No guaranteed outcomes",
     "Never promise guaranteed returns, savings or results.",
     r"\bguarantee(?:d|s)?\b", "potential", None, "high"),
    ("R-02", "banned", "No \"risk-free\" claims",
     "Financial products are never described as risk-free.",
     r"\brisk[- ]free\b", "transparent", None, "high"),
    ("R-03", "banned", "No unproven superlatives",
     "Avoid \"best\", \"No. 1\" or \"India's #1\" unless an approved fact proves it.",
     r"\b(?:the )?best in India\b|\bIndia's #1\b|\bnumber one\b|\bNo\. ?1\b", "trusted", None, "high"),
    ("R-04", "terminology", "Say \"customers\", not \"users\"",
     "We refer to the people we serve as customers.",
     r"\busers\b", "customers", None, "low"),
    ("R-05", "terminology", "Write the plan name as \"Velora Pro\"",
     "Product names are written exactly: Velora Pro, Velora Plus.",
     r"(?-i:\bVeloraPro\b|\bVelora pro\b|\bvelora pro\b|\bVELORA PRO\b)", "Velora Pro", None, "low"),
    ("R-06", "terminology", "Say \"affordable\", not \"cheap\"",
     "\"Cheap\" undermines trust in a financial brand.",
     r"\bcheap(?:est)?\b", "affordable", None, "low"),
    ("R-07", "disclaimer", "Investment disclaimer required",
     "Any mention of investing, SIPs or returns must carry the market-risk disclaimer.",
     None, INVEST_DISCLAIMER, r"\b(?:invest(?:ing|ment|ments)?|returns|SIPs?|mutual funds?)\b", "high"),
    ("R-08", "disclaimer", "Offers need \"T&C apply\"",
     "Any offer or cashback must say T&C apply.",
     None, "T&C apply.", r"\b(?:cashback|offer)\b", "medium"),
    ("R-09", "tone", "Calm, clear voice",
     "Calm, clear and confident. No hype, no slang, no shouting in capitals.",
     None, None, None, "medium"),
    ("R-10", "claim", "Claims must match approved facts",
     "Every performance, savings, speed or comparative claim must be backed by an approved fact.",
     None, None, None, "high"),
    ("R-11", "pattern", "No exclamation marks",
     "No exclamation marks in customer communication.",
     r"!+", ".", None, "low"),
    ("R-12", "tone", "No pressure tactics",
     "Never use urgency or fear, such as \"act now\", \"last chance\" or \"don't miss out\".",
     None, None, None, "medium"),
    ("R-13", "claim", "Interest rates need \"up to\" and \"p.a.\"",
     "Any interest rate must say \"up to\" and \"p.a.\".",
     None, None, None, "medium"),
    ("R-14", "visual", "Readable text contrast (WCAG AA)",
     "Text in images needs a contrast ratio of at least 4.5:1, or 3:1 for large text.",
     None, None, None, "medium"),
    ("R-15", "visual", "Every image needs alt text",
     "Describe what the image shows and repeat its key text, for screen-reader users.",
     None, None, None, "medium"),
    ("R-16", "visual", "Don't use retired logos",
     "Only the current Velora Money logo may appear. Retired marks are listed under Logos.",
     None, None, None, "high"),
]

# id, label, value, keyword, value_pattern, source, owner, expires_on, visibility
FACTS = [
    ("F-01", "Velora Pro monthly price", "₹499", r"Velora Pro|Pro plan", r"(?:₹|\bRs\.?|\bINR)\s?[\d,]+",
     "Pricing sheet, Q4 2026", "Product team", None, "public"),
    ("F-02", "Velora Plus monthly price", "₹199", r"Velora Plus|Plus plan", r"(?:₹|\bRs\.?|\bINR)\s?[\d,]+",
     "Pricing sheet, Q4 2026", "Product team", None, "public"),
    ("F-03", "Active customers", "3.2 lakh", r"customers|users|members", r"\d+(?:\.\d+)?\s?lakh",
     "Monthly metrics report, Sep 2026", "Marketing", None, "public"),
    ("F-04", "Play Store rating", "4.6", r"\brat(?:ed|ing)\b|\bstars\b", r"\b\d\.\d\b",
     "Google Play listing, Oct 2026", "Marketing", None, "public"),
    ("F-05", "Max savings interest rate (p.a.)", "7%", r"\binterest\b", r"\d+(?:\.\d+)?\s?%",
     "Partner bank rate card, Oct 2026", "Finance", None, "public"),
    ("F-06", "UPI cashback (festive offer)", "5%", r"\bcashback\b", r"\d+(?:\.\d+)?\s?%",
     "Offer approval note #14", "Growth team", "2026-10-15", "public"),
    ("F-07", "Cities served", "120", r"\bcities\b", r"\b\d+\+?",
     "Operations report, Sep 2026", "Operations", None, "public"),
    ("F-08", "Year founded", "2021", r"\bfounded\b|\bsince\b", r"\b20\d{2}\b",
     "Company registration", "Legal", None, "public"),
    ("F-09", "Partner bank", "Harbor Bank", r"partner bank", r"\b[A-Z][a-z]+ Bank\b",
     "Partnership agreement", "Legal", None, "partner"),
    ("F-10", "Velora Pro price from 1 Dec 2026 (unreleased)", "₹599", None, None,
     "Board note, not yet announced", "Product team", None, "internal"),
]

CONFIDENTIAL_TERMS = ["Project Nova", "₹599"]

APPROVED = [
    ("Pro plan launch", "LinkedIn",
     "Velora Pro is here. For ₹499 a month, you get priority support, zero-fee UPI transfers and monthly "
     "spending insights. Built for freelancers and small businesses who want clarity over their money."),
    ("Pricing page blurb", "Website",
     "Velora Pro costs ₹499 per month. You can cancel anytime from the app."),
    ("Welcome email: Velora Pro", "Email",
     "Thanks for choosing Velora Pro. Your plan renews at ₹499 each month, and you can change it whenever you like."),
    ("Pro features caption", "Instagram",
     "Smarter money, less effort. Velora Pro at ₹499/month gives you insights that actually make sense."),
    ("Sales one-pager summary", "Sales",
     "Velora Money serves 3.2 lakh customers across 120 cities. The Pro plan, at ₹499 a month, adds priority support."),
    ("Help article: upgrading", "Help centre",
     "To upgrade, open Settings and choose Velora Pro (₹499 per month). Your new features unlock straight away."),
    ("About us", "Website",
     "Founded in 2021, Velora Money builds simple tools for everyday money. Today we serve 3.2 lakh customers."),
]

DEMO_DRAFT = ("Diwali just got brighter! Velora Money is the best in India for savings, with guaranteed returns "
              "of 9% interest on your balance. Join 5 lakh users who already trust us. Our Velora pro plan is now "
              "just ₹399 a month. Plus, get 5% cashback on every UPI payment. Act now, this offer won't last!")

AGENCY_DRAFT = ("Make this festive season count. Open a Velora account in 5 minutes and earn up to 7% interest p.a. "
                "on savings. Rated 4.8 stars by our users.")

# rule, accepted count, rejected reasons (seeded review history for the guideline debugger)
DECISION_HISTORY = [
    ("R-11", 6, ["Festive greeting, warmth matters here", "Festive greeting, warmth matters here",
                 "Celebrating a customer milestone", "Exclamation is inside a quoted customer review",
                 "Diwali wishes post, not a sales message", "Event invite, informal by design"]),
    ("R-04", 10, ["Quoting a third-party report that uses the word users"]),
    ("R-06", 8, ["Competitor quote in a comparison table", "Word appears in a customer testimonial"]),
    ("R-03", 1, ["Award citation from an external body", "Quoting the award title exactly"]),
]


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _content(conn, title, channel, author, status, body, days_ago=0):
    cid = ex(conn, "INSERT INTO content(title,channel,author_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
             (title, channel, author, status, _ago(days_ago), _ago(days_ago)))
    ex(conn, "INSERT INTO versions(content_id,n,body,note,created_by,created_at) VALUES(?,?,?,?,?,?)",
       (cid, 1, body, "First draft", author, _ago(days_ago)))
    return cid


DEMO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "demo")


def _demo_file(name):
    with open(os.path.join(DEMO_DIR, name), "rb") as fh:
        return fh.read()


def _demo_ocr():
    with open(os.path.join(DEMO_DIR, "ocr.json")) as fh:
        return json.load(fh)


IMAGE_CAPTION = "Celebrate Diwali with Velora Money. See what's new on Velora Pro in the app."
PRO_BANNER_ALT = ("Velora Money banner on a pale blue background: \"Clarity over your money. Velora Pro: Rs. 499 a month. "
                  "Priority support and monthly insights.\"")

BRIEFS = [
    ("Festive campaign caption", "Instagram caption for the festive savings push. Lead with the savings interest rate "
     "and our Play Store rating. Calm voice, no pressure tactics.", "Instagram", 5, "done"),
    ("Velora Plus explainer carousel", "Five-slide Instagram carousel explaining what Velora Plus includes and what it "
     "costs. One idea per slide, plain words, include alt text for every slide.", "Instagram", -5, "open"),
    ("Year-end email to Pro customers", "Short email thanking Pro customers and pointing to the new monthly insights "
     "report. No new offers in this one.", "Email", -9, "open"),
]


def _sample_history(conn):
    """Eight weeks of generated sample activity for the governance dashboard (labelled as sample in the UI)."""
    rnd = random.Random(2026)
    channels = ["Instagram", "LinkedIn", "Email", "Website", "Ad", "Help centre"]
    refs = [("R-11", "low", 7), ("R-04", "low", 6), ("R-03", "high", 4), ("R-08", "medium", 4), ("F-01", "high", 3),
            ("R-12", "medium", 4), ("R-07", "high", 3), ("R-09", "medium", 3), ("R-14", "medium", 2),
            ("R-15", "medium", 3), ("R-06", "low", 2), ("F-06", "high", 1), ("R-16", "high", 1)]
    pool = [r for r in refs for _ in range(r[2])]
    for week in range(8, 0, -1):
        for i in range(rnd.randint(4, 7)):
            days = week * 7 - rnd.uniform(0, 6.5)
            author = rnd.choice(["u-writer", "u-writer", "u-agency"])
            lane = rnd.choices(["fast", "standard", "legal"], [3, 5, 2])[0]
            cid = ex(conn, "INSERT INTO content(title,channel,author_id,status,created_at,updated_at,lane,risk,"
                           "submitted_at,approved_at,assignee_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (f"Sample piece {week}-{i + 1}", rnd.choice(channels), author, "archived", _ago(days), _ago(days),
                      lane, {"fast": 1, "standard": 5, "legal": 9}[lane], _ago(days - 0.1),
                      _ago(days - 0.1 - {"fast": 0.1, "standard": 0.7, "legal": 1.6}[lane] * rnd.uniform(.5, 1.5)),
                      "u-legal" if lane == "legal" else "u-reviewer"))
            # slightly fewer issues per piece in recent weeks, with noise
            for _ in range(max(0, int(rnd.gauss(2.6 - (8 - week) * 0.18, 1.1)))):
                ref, sev, _w = rnd.choice(pool)
                status = rnd.choices(["accepted", "edited", "rejected"], [7, 2, 1 if ref != "R-11" else 4])[0]
                ex(conn, "INSERT INTO findings(content_id,version_n,quote,issue,ref,method,severity,confidence,intent,"
                         "fix,action,needs_review,status,created_at,target) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (cid, 1, "(sample)", "Sample finding", ref, "rule", sev, 1.0, "", "", "replace", 0, status,
                    _ago(days), "image" if ref in ("R-14", "R-15", "R-16") else "text"))


def seed(conn):
    init_db(conn)
    if q(conn, "SELECT 1 FROM users LIMIT 1", one=True):
        return False
    for u in USERS:
        ex(conn, "INSERT INTO users VALUES(?,?,?,?,?)", u)
    set_setting(conn, "brand", "Velora Money")
    set_setting(conn, "voice", VOICE)
    set_setting(conn, "confidential_terms", CONFIDENTIAL_TERMS)
    for r in RULES:
        ex(conn, "INSERT INTO rules(id,type,title,text,pattern,replacement,trigger,severity,active,updated_at) "
                 "VALUES(?,?,?,?,?,?,?,?,1,?)", (*r, now()))
    for f in FACTS:
        ex(conn, "INSERT INTO facts(id,label,value,keyword,value_pattern,source,owner,expires_on,visibility,version,updated_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,1,?)", (*f, now()))

    for name, status, fname in (("Velora Money mark (2025)", "current", "logo-current.png"),
                                ("Old green velora mark (retired 2024)", "retired", "logo-retired.png")):
        ex(conn, "INSERT INTO logos(name,status,mime,data,created_at) VALUES(?,?,?,?,?)",
           (name, status, "image/png", _demo_file(fname), now()))

    from .services import add_asset, route  # local import avoids a cycle
    reviewer = {"id": "u-reviewer"}
    facts = q(conn, "SELECT * FROM facts")
    for i, (title, channel, body) in enumerate(APPROVED):
        days = 30 - i * 3
        cid = _content(conn, title, channel, "u-writer", "approved", body, days_ago=days)
        for link in engine.claim_links(body, facts):
            ex(conn, "INSERT INTO claim_links(content_id,fact_id,quote,value) VALUES(?,?,?,?)",
               (cid, link["fact_id"], link["quote"], link["value"]))
        ex(conn, "UPDATE content SET lane='fast', risk=1, submitted_at=?, approved_at=?, assignee_id='u-reviewer' "
                 "WHERE id=?", (_ago(days), _ago(days - 0.1), cid))
        ex(conn, "INSERT INTO check_runs(content_id,version_n,ai_status,ai_dropped,created_at) VALUES(?,?,?,?,?)",
           (cid, 1, "off", 0, _ago(days)))
        seals.issue(conn, cid, reviewer, issued_at=_ago(days - 0.1))

    ocr = _demo_ocr()
    writer = {"id": "u-writer"}
    banner_id = _content(conn, "Pro plan banner", "Instagram", "u-writer", "approved",
                         "Clarity over your money, with Velora Pro.", days_ago=6)
    add_asset(conn, banner_id, _demo_file("pro-banner.png"), "pro-banner.png", PRO_BANNER_ALT, writer,
              ocr_lines=ocr["pro-banner"])
    ex(conn, "UPDATE content SET lane='fast', risk=2, submitted_at=?, approved_at=?, assignee_id='u-reviewer' WHERE id=?",
       (_ago(6), _ago(5.9), banner_id))
    ex(conn, "INSERT INTO check_runs(content_id,version_n,ai_status,ai_dropped,created_at) VALUES(?,?,?,?,?)",
       (banner_id, 1, "off", 0, _ago(6)))
    seals.issue(conn, banner_id, reviewer, issued_at=_ago(5.9))

    _content(conn, "Diwali savings post", "Instagram", "u-writer", "draft", DEMO_DRAFT, days_ago=0)
    image_id = _content(conn, "Diwali banner", "Instagram", "u-writer", "draft", IMAGE_CAPTION, days_ago=0)
    add_asset(conn, image_id, _demo_file("diwali-banner.png"), "diwali-banner.png", "", writer,
              ocr_lines=ocr["diwali-banner"])

    for title, body, channel, due, status in BRIEFS:
        ex(conn, "INSERT INTO briefs(title,body,channel,team,due_on,created_by,created_at,status) VALUES(?,?,?,?,?,?,?,?)",
           (title, body, channel, "BrightLeaf Studio (agency)", _ago(due)[:10], "u-admin", _ago(abs(due) + 3), status))
    agency_id = _content(conn, "Festive campaign caption", "Instagram", "u-agency", "in_review", AGENCY_DRAFT, days_ago=1)
    ex(conn, "UPDATE content SET brief_id=1, submitted_at=? WHERE id=?", (_ago(1), agency_id))
    # the agency draft arrives already checked by the rule engine, so the reviewer queue has something to act on
    from .services import analyse  # local import avoids a cycle
    findings, _status, _dropped = analyse(conn, AGENCY_DRAFT, "agency", use_ai=False)
    from .services import save_finding
    for f in findings:
        save_finding(conn, agency_id, 1, f)
    ex(conn, "INSERT INTO comments(content_id,user_id,body,created_at) VALUES(?,?,?,?)",
       (agency_id, "u-agency", "Submitted for the festive push. Happy to adjust the rating line if needed.", _ago(1)))
    ex(conn, "INSERT INTO check_runs(content_id,version_n,ai_status,ai_dropped,created_at) VALUES(?,?,?,?,?)",
       (agency_id, 1, "off", 0, _ago(1)))
    route(conn, agency_id)
    _sample_history(conn)
    # measured on 22 Oct 2026 in Round 2 (see README); kept so the trend chart starts from real results
    set_setting(conn, "reliability_history", [
        {"date": "2026-10-22", "label": "Rule engine only", "detection": 87, "false_flags": 0},
        {"date": "2026-10-22", "label": "Gemini run 1", "detection": 87, "false_flags": 0},
        {"date": "2026-10-22", "label": "Gemini run 2", "detection": 93, "false_flags": 6},
        {"date": "2026-10-22", "label": "Gemini run 3", "detection": 100, "false_flags": 1},
    ])

    day = 40
    for rule_id, accepted, reasons in DECISION_HISTORY:
        for _ in range(accepted):
            ex(conn, "INSERT INTO decisions(finding_id,content_id,rule_ref,action,reason,user_id,created_at) VALUES(NULL,NULL,?,?,?,?,?)",
               (rule_id, "accepted", "", "u-writer", _ago(day)))
            day -= 0.5
        for reason in reasons:
            ex(conn, "INSERT INTO decisions(finding_id,content_id,rule_ref,action,reason,user_id,created_at) VALUES(NULL,NULL,?,?,?,?,?)",
               (rule_id, "rejected", reason, "u-reviewer", _ago(day)))
            day -= 0.5
    ex(conn, "INSERT INTO audit_log(user_id,action,detail,created_at) VALUES(?,?,?,?)",
       ("system", "seeded", "Velora Money demo data loaded (fictional brand)", now()))
    return True
