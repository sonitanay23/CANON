"""Demo data for a fictional fintech brand, Velora Money.

Everything here is invented for the demo: the brand, people, agency,
facts and content. Nothing refers to a real company or customer.
"""
from datetime import datetime, timedelta, timezone

from . import rules as engine
from .db import ex, init_db, now, q, set_setting

USERS = [
    ("u-admin", "Ananya Rao", "admin@demo", "admin", "Brand team"),
    ("u-reviewer", "Kabir Mehta", "reviewer@demo", "reviewer", "Marketing"),
    ("u-writer", "Riya Sharma", "writer@demo", "creator", "Content team"),
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
]

# id, label, value, keyword, value_pattern, source, owner, expires_on, visibility
FACTS = [
    ("F-01", "Velora Pro monthly price", "₹499", r"Velora Pro|Pro plan", r"₹\s?[\d,]+",
     "Pricing sheet, Q4 2026", "Product team", None, "public"),
    ("F-02", "Velora Plus monthly price", "₹199", r"Velora Plus|Plus plan", r"₹\s?[\d,]+",
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

    facts = q(conn, "SELECT * FROM facts")
    for i, (title, channel, body) in enumerate(APPROVED):
        cid = _content(conn, title, channel, "u-writer", "approved", body, days_ago=30 - i * 3)
        for link in engine.claim_links(body, facts):
            ex(conn, "INSERT INTO claim_links(content_id,fact_id,quote,value) VALUES(?,?,?,?)",
               (cid, link["fact_id"], link["quote"], link["value"]))

    _content(conn, "Diwali savings post", "Instagram", "u-writer", "draft", DEMO_DRAFT, days_ago=0)
    agency_id = _content(conn, "Festive campaign caption", "Instagram", "u-agency", "in_review", AGENCY_DRAFT, days_ago=1)
    # the agency draft arrives already checked by the rule engine, so the reviewer queue has something to act on
    from .services import analyse  # local import avoids a cycle
    findings, _status, _dropped = analyse(conn, AGENCY_DRAFT, "agency", use_ai=False)
    from .services import save_finding
    for f in findings:
        save_finding(conn, agency_id, 1, f)
    ex(conn, "INSERT INTO comments(content_id,user_id,body,created_at) VALUES(?,?,?,?)",
       (agency_id, "u-agency", "Submitted for the festive push. Happy to adjust the rating line if needed.", _ago(1)))

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
