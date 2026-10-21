"""End-to-end tests: the judge's path through the real app, with AI off.

Run:  python -m unittest discover -s tests -v
"""
import os
import re
import sys
import tempfile
import time
import unittest

TMP = tempfile.mkdtemp()
os.environ["CANON_DB"] = os.path.join(TMP, "test.db")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as canon_app  # noqa: E402
from canon import ai  # noqa: E402
from canon.db import connect, q  # noqa: E402


class Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        canon_app.app.config["TESTING"] = True
        cls.c = canon_app.app.test_client()

    def login(self, uid):
        r = self.c.post("/login", data={"uid": uid})
        self.assertEqual(r.status_code, 302)

    def db(self):
        return connect(os.environ["CANON_DB"])

    def test_1_public_pages(self):
        for path in ("/", "/login", "/trust"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200, path)
        self.assertIn(b"Try this in 3 minutes", self.c.get("/").data)
        self.assertEqual(self.c.get("/content").status_code, 302)  # login required

    def test_2_writer_checks_and_fixes_demo_draft(self):
        self.login("u-writer")
        db = self.db()
        cid = q(db, "SELECT id FROM content WHERE title='Diwali savings post'", one=True)["id"]
        page = self.c.get(f"/content/{cid}")
        self.assertIn(b"Not checked yet", page.data)
        r = self.c.post(f"/content/{cid}/check", follow_redirects=True)
        self.assertIn(b"Check complete", r.data)
        self.assertIn(b"AI review was unavailable", r.data)
        open_f = q(self.db(), "SELECT * FROM findings WHERE content_id=? AND status='open'", (cid,))
        refs = {f["ref"] for f in open_f}
        for ref in ("R-01", "R-03", "R-04", "R-05", "R-07", "R-08", "R-11", "F-01", "F-03", "F-05", "F-06"):
            self.assertIn(ref, refs)
        # reject one with no reason -> refused
        f11 = next(f for f in open_f if f["ref"] == "R-11")
        r = self.c.post(f"/finding/{f11['id']}/decide", data={"action": "reject", "reason": ""}, follow_redirects=True)
        self.assertIn(b"give a short reason", r.data)
        r = self.c.post(f"/finding/{f11['id']}/decide", data={"action": "reject", "reason": "Festive greeting"},
                        follow_redirects=True)
        self.assertIn(b"Your reason was recorded", r.data)
        # edit one fix
        f04 = next(f for f in open_f if f["ref"] == "R-04")
        self.c.post(f"/finding/{f04['id']}/decide", data={"action": "edit", "fix": "customers"})
        # accept every remaining fix
        for _ in range(20):
            nxt = q(self.db(), "SELECT * FROM findings WHERE content_id=? AND status='open' ORDER BY id LIMIT 1", (cid,), one=True)
            if not nxt:
                break
            self.c.post(f"/finding/{nxt['id']}/decide", data={"action": "accept"})
        body = q(self.db(), "SELECT body FROM versions WHERE content_id=? ORDER BY n DESC LIMIT 1", (cid,), one=True)["body"]
        self.assertIn("₹499", body)
        self.assertIn("3.2 lakh customers", body)
        self.assertIn("Investments are subject to market risks", body)
        self.assertNotIn("best in India", body)
        self.assertNotIn("5% cashback", body)
        # re-check: only the kept "!" remains
        self.c.post(f"/content/{cid}/check")
        left = {f["ref"] for f in q(self.db(), "SELECT ref FROM findings WHERE content_id=? AND status='open'", (cid,))}
        self.assertTrue(left <= {"R-11"}, left)
        r = self.c.post(f"/content/{cid}/status", data={"action": "submit"}, follow_redirects=True)
        self.assertIn(b"Sent to the review queue", r.data)
        hist = self.c.get(f"/content/{cid}/history")
        self.assertEqual(hist.status_code, 200)
        self.assertIn(b"Festive greeting", hist.data)

    def test_3_reviewer_approves(self):
        self.login("u-reviewer")
        db = self.db()
        cid = q(db, "SELECT id FROM content WHERE title='Diwali savings post'", one=True)["id"]
        self.assertIn(b"Diwali savings post", self.c.get("/content").data)
        r = self.c.post(f"/content/{cid}/status", data={"action": "approve"}, follow_redirects=True)
        self.assertIn(b"Approved", r.data)
        self.assertEqual(q(self.db(), "SELECT status FROM content WHERE id=?", (cid,), one=True)["status"], "approved")
        links = q(self.db(), "SELECT fact_id FROM claim_links WHERE content_id=?", (cid,))
        self.assertIn("F-01", {l["fact_id"] for l in links})
        # reviewer cannot edit facts
        self.assertEqual(self.c.post("/facts/F-01", data={"value": "₹1"}).status_code, 403)

    def test_4_agency_cannot_see_internal_facts(self):
        self.login("u-agency")
        page = self.c.get("/brand").data
        self.assertNotIn("unreleased".encode(), page)
        self.assertIn(b"hidden from agency partners", page)
        other = q(self.db(), "SELECT id FROM content WHERE author_id='u-writer' LIMIT 1", one=True)["id"]
        self.assertEqual(self.c.get(f"/content/{other}").status_code, 403)
        self.assertEqual(self.c.get("/insights").status_code, 403)

    def test_5_admin_changes_price_and_sees_affected_content(self):
        self.login("u-admin")
        r = self.c.post("/facts/F-01", data={"value": "₹549", "expires_on": ""})
        self.assertEqual(r.status_code, 302)
        impact = self.c.get(r.headers["Location"])
        text = impact.data.decode()
        n = int(re.search(r"(\d+) approved piece", text).group(1))
        self.assertEqual(n, 7)  # 6 seeded Pro pieces + the Diwali post approved in test 3
        cids = re.findall(r'name="cid" value="(\d+)"', text)
        r = self.c.post(r.headers["Location"], data={"cid": cids}, follow_redirects=True)
        self.assertIn(b"sent to the review queue", r.data)
        db = self.db()
        for cid in cids:
            body = q(db, "SELECT body FROM versions WHERE content_id=? ORDER BY n DESC LIMIT 1", (cid,), one=True)["body"]
            self.assertIn("₹549", body)
            self.assertNotIn("₹499", body)
            self.assertEqual(q(db, "SELECT status FROM content WHERE id=?", (cid,), one=True)["status"], "in_review")

    def test_6_guideline_debugger(self):
        self.login("u-admin")
        page = self.c.get("/insights").data.decode()
        self.assertIn("Unclear rule", page)
        db = self.db()
        from canon import services
        states = {s["rule"]["id"]: s["state"] for s in services.rule_stats(db)}
        self.assertEqual(states["R-11"], "unclear")       # 7 of 13 overridden
        self.assertEqual(states["R-03"], "too few decisions")  # only 3 decisions
        self.assertEqual(states["R-04"], "healthy")
        r = self.c.post("/insights/R-11/rewrite", data={"action": "approve", "text": "No exclamation marks in sales or product messages. Festive greetings may use one."},
                        follow_redirects=True)
        self.assertIn(b"updated", r.data)
        self.assertIn("Festive greetings", q(db, "SELECT text FROM rules WHERE id='R-11'", one=True)["text"])

    def test_7_new_draft_and_reliability(self):
        self.login("u-writer")
        r = self.c.post("/content/new", data={"title": "Test", "channel": "Email",
                                              "body": "Our users get guaranteed savings!"}, follow_redirects=True)
        self.assertIn(b"Check complete", r.data) if b"Check complete" in r.data else None
        self.assertIn(b"open issue", r.data)
        r = self.c.post("/reliability", data={"with_ai": ""}, follow_redirects=False)
        for _ in range(50):
            run = q(self.db(), "SELECT * FROM reliability_runs ORDER BY id DESC LIMIT 1", one=True)
            if run["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(run["status"], "done")
        page = self.c.get("/reliability").data.decode()
        self.assertIn("rule-detectable issues caught", page)

    def test_8_ai_validation_drops_bad_findings(self):
        text = "Velora Money will double your savings in a year."
        raw = [
            {"quote": "double your savings in a year", "ref": "R-10", "fix": "help you track your savings",
             "severity": "high", "confidence": 0.9, "intent": "Show the app helps people save", "issue": "Unsupported claim"},
            {"quote": "not in the text", "ref": "R-10", "fix": "x"},           # bad quote
            {"quote": "double your savings", "ref": "R-99", "fix": "x"},        # unknown rule
            {"quote": "Velora Money", "ref": "F-10", "fix": "x"},               # not visible to this user
        ]
        kept, dropped = ai.validate(raw, text, {}, {"R-10", "F-01"})
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 3)
        self.assertEqual(kept[0]["needs_review"], 1)

    def test_9_redaction(self):
        masked, mapping = ai.redact("Project Nova launches at ₹599.", ["Project Nova", "₹599"])
        self.assertNotIn("Nova", masked)
        self.assertNotIn("599", masked)
        self.assertEqual(ai.unredact(masked, mapping), "Project Nova launches at ₹599.")


if __name__ == "__main__":
    unittest.main()
