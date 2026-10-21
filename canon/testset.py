"""Labelled test set for the reliability scorecard.

15 pieces with planted issues and 10 clean pieces. Each planted piece lists
the rule or fact IDs Canon is expected to cite ("expect"). "also_ok" lists
other IDs that are fair to raise on that piece, so they are not counted as
false flags. The answer key is fixed before any check is run.
"""
import json
import threading

from .db import connect, ex, now, q

TESTSET = [
    # planted: facts stated wrongly
    ("T01", "LinkedIn", "Velora Pro now costs just ₹449 a month.", ["F-01"], []),
    ("T02", "Email", "Upgrade to the Plus plan for ₹249 per month and enjoy priority support.", ["F-02"], ["R-10"]),
    ("T03", "Instagram", "Trusted by 4 lakh customers across India.", ["F-03"], ["R-10"]),
    ("T04", "Ad", "Rated 4.9 on the Play Store by thousands of happy customers.", ["F-04"], ["R-10"]),
    ("T05", "Blog", "Earn up to 8% interest p.a. on your savings with Velora Money.", ["F-05"], ["R-10"]),
    ("T06", "Ad", "Get 5% cashback on every UPI payment this week. T&C apply.", ["F-06"], ["R-10"]),
    # planted: banned words
    ("T07", "LinkedIn", "Our app gives you guaranteed savings every month.", ["R-01"], ["R-10"]),
    ("T08", "Ad", "Velora Money is the best in India for everyday banking.", ["R-03"], ["R-10"]),
    # planted: terminology
    ("T09", "Email", "Our users love how simple the app feels.", ["R-04"], ["R-10"]),
    ("T10", "Instagram", "Meet VeloraPro, your money's new best friend.", ["R-05"], ["R-09"]),
    # planted: missing disclaimers
    ("T11", "Blog", "Start a SIP with Velora Money and grow your wealth with steady returns.", ["R-07"], ["R-10", "R-01"]),
    ("T12", "Push", "Big festive offer on Velora Plus this weekend.", ["R-08"], ["R-09"]),
    # planted: style, tone and claims
    ("T13", "Email", "Your account is ready! Let's get started!", ["R-11"], ["R-09"]),
    ("T14", "Ad", "Hurry, last chance. Prices go up tonight, so act now before you lose out.", ["R-12"], ["R-09", "R-10"]),
    ("T15", "Blog", "Velora Money will double your savings in a year.", ["R-10"], ["R-01", "R-09"]),
    # clean pieces: nothing should be flagged
    ("C01", "Website", "Velora Pro costs ₹499 a month and you can cancel anytime from the app.", [], []),
    ("C02", "LinkedIn", "We now serve 3.2 lakh customers across 120 cities.", [], []),
    ("C03", "Email", "Velora Plus is ₹199 a month and includes free UPI transfers.", [], []),
    ("C04", "Instagram", "Track every expense in one place, with insights that are easy to read.", [], []),
    ("C05", "Blog", "Earn up to 7% interest p.a. on your savings balance.", [], []),
    ("C06", "Website", "Founded in 2021, Velora Money builds simple tools for everyday money.", [], []),
    ("C07", "Help centre", "Our support team is here to help through the in-app chat.", [], []),
    ("C08", "Push", "Your monthly summary is ready in the app.", [], []),
    ("C09", "Ad", "Rated 4.6 on the Play Store by our customers.", [], []),
    ("C10", "Email", "Need help choosing a plan? Compare Velora Plus and Velora Pro in the app.", [], []),
]


def method_for(ref, rules_by_id):
    """Which layer is expected to catch a planted issue."""
    r = rules_by_id.get(ref)
    return "ai" if r and r["type"] in ("tone", "claim") else "rule"


def score(results, rules_by_id):
    expected_total = found_expected = 0
    by_method = {"rule": [0, 0], "ai": [0, 0]}
    flags = false_flags = clean_flags = 0
    for r in results:
        refs = set(r["found"])
        exp = set(r["expect"])
        ok = exp | set(r["also_ok"])
        for e in exp:
            m = method_for(e, rules_by_id)
            by_method[m][1] += 1
            expected_total += 1
            if e in refs:
                found_expected += 1
                by_method[m][0] += 1
        extra = [x for x in r["found"] if x not in ok]
        r["missed"] = sorted(exp - refs)
        r["extra"] = extra
        flags += len(r["found"])
        false_flags += len(extra)
        if not exp:
            clean_flags += len(r["found"])
    return {
        "expected": expected_total, "detected": found_expected,
        "detection": found_expected / expected_total if expected_total else 0,
        "rule_detected": by_method["rule"][0], "rule_expected": by_method["rule"][1],
        "ai_detected": by_method["ai"][0], "ai_expected": by_method["ai"][1],
        "flags": flags, "false_flags": false_flags,
        "false_share": false_flags / flags if flags else 0,
        "clean_flags": clean_flags,
    }


def run(run_id, use_ai, db_path=None):
    """Run every test piece through the same pipeline judges use. Runs in a thread."""
    from .seed import seed
    from .services import analyse
    conn = connect(db_path)
    # score against a fresh copy of the demo brand, so the fixed answer key stays valid
    # even after admins change facts or rules in the live demo
    base = connect(":memory:")
    seed(base)
    try:
        rules_by_id = {r["id"]: r for r in q(base, "SELECT * FROM rules")}
        results, dropped_total = [], 0
        for i, (tid, channel, text, expect, also_ok) in enumerate(TESTSET):
            findings, ai_status, dropped = analyse(base, text, "creator", use_ai=use_ai)
            dropped_total += dropped
            results.append({"id": tid, "channel": channel, "text": text, "expect": expect, "also_ok": also_ok,
                            "found": [f["ref"] for f in findings], "methods": [f["method"] for f in findings],
                            "ai_status": ai_status})
            ex(conn, "UPDATE reliability_runs SET progress=? WHERE id=?", (i + 1, run_id))
        summary = score(results, rules_by_id)
        summary["ai_dropped"] = dropped_total
        summary["ai_status"] = "ok" if any(r["ai_status"] == "ok" for r in results) else results[0]["ai_status"]
        ex(conn, "UPDATE reliability_runs SET status='done', results_json=?, summary_json=?, finished_at=? WHERE id=?",
           (json.dumps(results), json.dumps(summary), now(), run_id))
    except Exception as e:  # keep the page usable even if a run fails
        ex(conn, "UPDATE reliability_runs SET status=?, finished_at=? WHERE id=?", (f"failed: {e}", now(), run_id))
    finally:
        base.close()
        conn.close()


def start(conn, use_ai, db_path=None):
    run_id = ex(conn, "INSERT INTO reliability_runs(status,progress,total,with_ai,created_at) VALUES('running',0,?,?,?)",
                (len(TESTSET), 1 if use_ai else 0, now()))
    threading.Thread(target=run, args=(run_id, use_ai, db_path), daemon=True).start()
    return run_id
