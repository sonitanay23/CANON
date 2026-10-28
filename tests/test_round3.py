"""Round 3 tests: visual checks, seals and verification, risk routing, partner
workspace, governance dashboard, and robustness (bad input, repeats, refresh).

Runs on its own fresh database, with AI off and no network.
Run:  python -m unittest discover -s tests -v
"""
import io
import os
import re
import sys
import tempfile
import unittest

os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["GEMINI_API_KEY"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as canon_app  # noqa: E402
from canon import db as canon_db, seals, services, vision  # noqa: E402
from canon.db import connect, q  # noqa: E402
from canon.seed import seed  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

DEMO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "demo")


def demo(name):
    with open(os.path.join(DEMO, name), "rb") as fh:
        return fh.read()


def png(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old = canon_db.DB_PATH
        canon_db.DB_PATH = os.path.join(tempfile.mkdtemp(), "r3.db")
        conn = connect()
        seed(conn)
        conn.close()
        canon_app.app.config["TESTING"] = True
        cls.c = canon_app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        canon_db.DB_PATH = cls._old

    def login(self, uid):
        self.assertEqual(self.c.post("/login", data={"uid": uid}).status_code, 302)

    def db(self):
        return connect()

    def cid(self, title):
        return q(self.db(), "SELECT id FROM content WHERE title=?", (title,), one=True)["id"]


class Vision(unittest.TestCase):
    """Measurements, no database."""

    def test_contrast_ratio_matches_wcag_reference_values(self):
        self.assertAlmostEqual(vision.contrast_ratio((0, 0, 0), (255, 255, 255)), 21.0, places=1)
        self.assertAlmostEqual(vision.contrast_ratio((118, 118, 118), (255, 255, 255)), 4.54, places=1)
        self.assertAlmostEqual(vision.contrast_ratio((255, 255, 255), (255, 255, 255)), 1.0, places=2)

    def test_low_contrast_line_is_flagged_and_dark_line_passes(self):
        im = vision.load(demo("diwali-banner.png"))
        lines = [{"text": "Guaranteed returns on every rupee", "box": [82, 607, 823, 648]},
                 {"text": "Velora Pro now just Rs. 399 a month", "box": [81, 665, 877, 717]}]
        res = {r["text"]: r for r in vision.check_contrast(im, lines)}
        self.assertFalse(res["Guaranteed returns on every rupee"]["ok"])
        self.assertLess(res["Guaranteed returns on every rupee"]["ratio"], 2)
        self.assertTrue(res["Velora Pro now just Rs. 399 a month"]["ok"])

    def test_retired_logo_found_and_current_logo_not_confused_with_it(self):
        retired = [{"id": 1, "name": "old", "data": demo("logo-retired.png")}]
        current = [{"id": 2, "name": "new", "data": demo("logo-current.png")}]
        found = vision.analyse(vision.load(demo("diwali-banner.png")), retired, ocr_lines=[], current_logos=current)["logos"]
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0]["score"], 0.85)
        clean = vision.analyse(vision.load(demo("pro-banner.png")), retired, ocr_lines=[], current_logos=current)["logos"]
        self.assertEqual(clean, [])

    def test_retired_logo_found_when_small_and_on_another_background(self):
        logo = Image.open(io.BytesIO(demo("logo-retired.png"))).convert("RGB")
        canvas = Image.new("RGB", (900, 600), (24, 30, 60))
        small = logo.resize((logo.width // 3, logo.height // 3))
        canvas.paste(small, (600, 470))
        score, box = vision.find_logo(canvas, logo)
        self.assertGreater(score, vision.LOGO_THRESHOLD)
        self.assertTrue(vision.overlaps(box, [600, 470, 600 + small.width, 470 + small.height]))

    def test_blank_image_has_no_logo(self):
        logo = Image.open(io.BytesIO(demo("logo-retired.png"))).convert("RGB")
        score, _ = vision.find_logo(Image.new("RGB", (800, 800), (250, 250, 250)), logo)
        self.assertLess(score, vision.LOGO_THRESHOLD)

    def test_alt_text_rules(self):
        self.assertIsNotNone(vision.alt_problem(""))
        self.assertIsNotNone(vision.alt_problem("image"))
        self.assertIsNotNone(vision.alt_problem("banner-final.png"))
        self.assertIsNone(vision.alt_problem("Velora Pro banner: clarity over your money, Rs. 499 a month."))

    def test_bad_images_are_rejected_with_plain_reasons(self):
        for data in (b"", b"not an image", b"GIF89a" + b"\0" * 40):
            with self.assertRaises(vision.BadImage):
                vision.load(data)
        with self.assertRaises(vision.BadImage):
            vision.load(png(Image.new("RGB", (10, 10))))

    def test_fingerprint_survives_resize_but_sha_does_not(self):
        im = vision.load(demo("pro-banner.png"))
        resized = im.resize((540, 540))
        self.assertLessEqual(vision.hash_distance(vision.dhash(im), vision.dhash(resized)), 4)
        self.assertNotEqual(vision.sha(png(im)), vision.sha(png(resized)))


class VisionAI(unittest.TestCase):
    """The AI text-reading path used where Tesseract isn't installed (e.g. Render), with the network mocked."""

    def setUp(self):
        self.env = dict(os.environ)
        os.environ["GEMINI_API_KEY"] = "test-key"
        os.environ["CANON_NO_TESSERACT"] = "1"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def test_gemini_request_carries_the_image_and_boxes_become_pixels(self):
        from unittest import mock
        from canon import ai
        sent = {}

        def fake_http(url, body=None, headers=None, method="POST"):
            sent["url"], sent["body"] = url, body
            return {"candidates": [{"content": {"parts": [{"text":
                '{"lines": [{"text": "Guaranteed returns on every rupee", "box": [562, 76, 600, 762]}],'
                ' "alt": "Diwali banner from Velora Money."}'}]}}]}
        with mock.patch.object(ai, "_http", side_effect=fake_http):
            im = vision.load(demo("diwali-banner.png"))
            lines, method, alt, note = vision.read_text(im)
        self.assertEqual(method, "ai")
        part = sent["body"]["contents"][0]["parts"][0]["inline_data"]
        self.assertEqual(part["mime_type"], "image/png")
        self.assertGreater(len(part["data"]), 1000)
        self.assertEqual(lines[0]["box"], [82, 606, 822, 648])     # 0-1000 scale -> pixels on 1080x1080
        self.assertEqual(alt, "Diwali banner from Velora Money.")
        res = vision.check_contrast(im, lines)
        self.assertFalse(res[0]["ok"])                               # the measurement still flags low contrast

    def test_ai_failure_falls_back_to_typing_the_text(self):
        from unittest import mock
        from canon import ai
        with mock.patch.object(ai, "_http", side_effect=ai.AIUnavailable("AI service busy")), \
                mock.patch.object(ai.time, "sleep"):
            lines, method, _alt, note = vision.read_text(vision.load(demo("pro-banner.png")))
        self.assertEqual((lines, method), ([], "none"))
        self.assertIn("Type the text", note)


class ImageChecks(Base):
    def test_a_demo_banner_gets_every_kind_of_visual_finding(self):
        self.login("u-writer")
        cid = self.cid("Diwali banner")
        r = self.c.post(f"/content/{cid}/check", follow_redirects=True)
        self.assertIn(b"Check complete", r.data)
        refs = {f["ref"]: f for f in q(self.db(), "SELECT * FROM findings WHERE content_id=? AND status='open'", (cid,))}
        for ref in ("R-01", "F-01", "R-14", "R-15", "R-16", "R-07"):
            self.assertIn(ref, refs)
        self.assertEqual(refs["R-16"]["target"], "image")
        self.assertEqual(refs["R-07"]["target"], "text")      # the disclaimer goes in the caption
        self.assertIn(b'class="box', r.data)                  # overlay boxes on the artwork

    def test_artwork_issue_cannot_be_accepted_as_text_but_alt_text_can(self):
        self.login("u-writer")
        cid = self.cid("Diwali banner")
        self.c.post(f"/content/{cid}/check")
        logo = q(self.db(), "SELECT * FROM findings WHERE content_id=? AND ref='R-16' AND status='open'", (cid,), one=True)
        r = self.c.post(f"/finding/{logo['id']}/decide", data={"action": "accept"}, follow_redirects=True)
        self.assertIn(b"Upload a corrected image", r.data)
        alt = q(self.db(), "SELECT * FROM findings WHERE content_id=? AND ref='R-15' AND status='open'", (cid,), one=True)
        r = self.c.post(f"/finding/{alt['id']}/decide", data={"action": "edit", "fix": "img"}, follow_redirects=True)
        self.assertIn(b"Write alt text that describes the image", r.data)
        r = self.c.post(f"/finding/{alt['id']}/decide",
                        data={"action": "edit", "fix": "Diwali banner from Velora Money with the Velora Pro price."},
                        follow_redirects=True)
        self.assertIn(b"Your alt text was saved", r.data)
        # the same decision again is harmless
        r = self.c.post(f"/finding/{alt['id']}/decide", data={"action": "accept"}, follow_redirects=True)
        self.assertIn(b"already handled", r.data)

    def test_uploading_corrected_artwork_clears_visual_issues(self):
        self.login("u-writer")
        cid = self.cid("Diwali banner")
        r = self.c.post(f"/content/{cid}/image", data={"image": (io.BytesIO(demo("pro-banner.png")), "fixed.png"),
                                                        "alt": "Velora Pro banner: clarity over your money, Rs. 499 a month."},
                        content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"New image saved and checked", r.data)
        refs = {f["ref"] for f in q(self.db(), "SELECT ref FROM findings WHERE content_id=? AND status='open'", (cid,))}
        self.assertFalse(refs & {"R-14", "R-15", "R-16"}, refs)
        self.assertEqual(q(self.db(), "SELECT MAX(n) n FROM assets WHERE content_id=?", (cid,), one=True)["n"], 2)

    def test_image_text_can_be_corrected_by_hand(self):
        self.login("u-writer")
        r = self.c.post("/content/new", data={"title": "Typed text", "channel": "Ad", "body": "",
                                              "image": (io.BytesIO(png(Image.new("RGB", (400, 300), "white"))), "blank.png"),
                                              "alt": "A plain white test card with no text on it."},
                        content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        cid = int(re.search(r"/content/(\d+)/check", r.data.decode()).group(1))
        r = self.c.post(f"/content/{cid}/imagetext", data={"ocr_text": "Guaranteed savings\nVelora Pro at Rs. 399"},
                        follow_redirects=True)
        self.assertIn(b"Image text saved and re-checked", r.data)
        refs = {f["ref"] for f in q(self.db(), "SELECT ref FROM findings WHERE content_id=? AND status='open'", (cid,))}
        self.assertTrue({"R-01", "F-01"} <= refs, refs)

    def test_asset_privacy(self):
        draft_asset = q(self.db(), "SELECT a.id FROM assets a JOIN content c ON c.id=a.content_id "
                                   "WHERE c.title='Diwali banner' ORDER BY a.n LIMIT 1", one=True)["id"]
        sealed_asset = q(self.db(), "SELECT a.id FROM assets a JOIN content c ON c.id=a.content_id "
                                    "WHERE c.title='Pro plan banner'", one=True)["id"]
        anon = canon_app.app.test_client()
        self.assertEqual(anon.get(f"/asset/{draft_asset}").status_code, 404)   # drafts stay private
        self.assertEqual(anon.get(f"/asset/{sealed_asset}").status_code, 200)  # approved artwork is public
        self.login("u-agency")
        self.assertEqual(self.c.get(f"/asset/{draft_asset}").status_code, 403)


class SealsAndVerify(Base):
    def test_seal_signature_and_tamper_detection(self):
        db = self.db()
        seal = q(db, "SELECT * FROM seals WHERE status='valid' LIMIT 1", one=True)
        self.assertTrue(seals.signature_ok(seal))
        forged = dict(seal, text_sha="0" * 64)
        self.assertFalse(seals.signature_ok(forged))
        db.execute("UPDATE seals SET text_sha=? WHERE code=?", ("0" * 64, seal["code"]))
        db.commit()
        self.assertEqual(seals.check_code(db, seal["code"])["verdict"], "tampered")
        db.execute("UPDATE seals SET text_sha=? WHERE code=?", (seal["text_sha"], seal["code"]))
        db.commit()
        self.assertEqual(seals.check_code(db, seal["code"])["verdict"], "approved")

    def verdict(self, **data):
        r = canon_app.app.test_client().post("/verify", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        m = re.search(r'class="verdict (\w+)"', r.data.decode())
        return m.group(1) if m else None

    def test_verify_text_image_and_code_without_signing_in(self):
        blurb = "Velora Pro costs ₹499 per month. You can cancel anytime from the app."
        self.assertEqual(self.verdict(mode="text", text=blurb), "approved")
        self.assertEqual(self.verdict(mode="text", text=blurb.replace("’", "'") + "  "), "approved")
        self.assertEqual(self.verdict(mode="text", text=blurb.replace("499", "399")), "modified")
        self.assertEqual(self.verdict(mode="text", text="Totally unrelated words about cricket."), "unknown")
        self.assertEqual(self.verdict(mode="text", text=""), "empty")
        self.assertEqual(self.verdict(mode="image", sample="approved"), "approved")
        self.assertEqual(self.verdict(mode="image", sample="edited"), "modified")
        self.assertEqual(self.verdict(mode="image", sample="draft"), "unknown")
        self.assertEqual(self.verdict(mode="image", image=(io.BytesIO(b"junk"), "x.png")), "bad")
        code = q(self.db(), "SELECT code FROM seals WHERE status='valid' LIMIT 1", one=True)["code"]
        self.assertEqual(self.verdict(mode="code", code=code.lower()), "approved")
        self.assertEqual(self.verdict(mode="code", code="VLM-AAAA-AAAA"), "unknown")
        self.assertEqual(self.verdict(mode="code", code="'; DROP TABLE seals; --"), "unknown")
        anon = canon_app.app.test_client()
        self.assertEqual(anon.get(f"/seal/{code}").status_code, 200)
        self.assertEqual(anon.get("/seal/nope").status_code, 404)
        self.assertIn(b"Approved", anon.get(f"/seal/{code}/badge.svg").data)

    def test_resaved_copy_of_approved_artwork_is_recognised(self):
        im = vision.load(demo("pro-banner.png"))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        conn = self.db()
        stored = ["@ Velora Money", "Clarity over", "your money.", "Velora Pro: Rs. 499 a month",
                  "Priority support and monthly insights"]
        approved_text = q(conn, "SELECT ocr_text FROM assets a JOIN content c ON c.id=a.content_id "
                                "WHERE c.title='Pro plan banner'", one=True)["ocr_text"].split("\n")
        res = seals.check_image(conn, buf.getvalue(), read_text=lambda _im: approved_text)
        self.assertEqual(res["verdict"], "approved_copy")
        res = seals.check_image(conn, buf.getvalue(), read_text=lambda _im: [s.replace("499", "299") for s in stored])
        self.assertEqual(res["verdict"], "modified")


class Routing(Base):
    def test_clean_piece_takes_fast_lane_and_batch_approval_seals_it(self):
        self.login("u-writer")
        r = self.c.post("/content/new", data={"title": "Help tip", "channel": "Help centre",
                                              "body": "You can change your plan from Settings at any time."},
                        follow_redirects=True)
        cid = int(re.search(r"/content/(\d+)/check", r.data.decode()).group(1))
        r = self.c.post(f"/content/{cid}/status", data={"action": "submit"}, follow_redirects=True)
        self.assertIn(b"fast lane", r.data)
        r = self.c.post(f"/content/{cid}/status", data={"action": "submit"}, follow_redirects=True)
        self.assertIn(b"available for this item any more", r.data)   # submitting twice is harmless
        self.login("u-reviewer")
        self.assertIn(b"Help tip", self.c.get("/queue").data)
        r = self.c.post("/queue/fast-approve", data={"cid": [str(cid)]}, follow_redirects=True)
        self.assertIn(b"Approved and sealed 1 fast-lane piece", r.data)
        self.assertEqual(q(self.db(), "SELECT status FROM content WHERE id=?", (cid,), one=True)["status"], "approved")
        self.assertIsNotNone(q(self.db(), "SELECT 1 FROM seals WHERE content_id=? AND status='valid'", (cid,), one=True))
        r = self.c.post("/queue/fast-approve", data={"cid": [str(cid)]}, follow_redirects=True)
        self.assertIn(b"Nothing was approved", r.data)                       # approving twice is harmless

    def test_risk_reasons_are_explained(self):
        cid = self.cid("Festive campaign caption")
        score, lane, reasons = services.assess_risk(self.db(), cid)
        self.assertEqual(lane, "legal")
        self.assertTrue(any("agency" in why for _p, why in reasons))
        self.assertTrue(any("interest" in why for _p, why in reasons))

    def test_creators_cannot_reach_the_queue(self):
        self.login("u-writer")
        self.assertEqual(self.c.get("/queue").status_code, 403)
        self.assertEqual(self.c.post("/queue/fast-approve", data={"cid": ["1"]}).status_code, 403)


class PartnerAndInsights(Base):
    def test_agency_workspace_brief_to_draft(self):
        self.login("u-agency")
        page = self.c.get("/partner").data.decode()
        self.assertIn("Velora Plus explainer carousel", page)
        self.assertNotIn("unreleased", page)                                  # internal facts stay hidden
        bid = q(self.db(), "SELECT id FROM briefs WHERE title='Velora Plus explainer carousel'", one=True)["id"]
        r = self.c.post("/content/new", data={"brief": str(bid), "title": "Plus carousel", "channel": "Instagram",
                                              "body": "Velora Plus costs ₹199 a month and shows where your money goes."},
                        follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(q(self.db(), "SELECT brief_id FROM content WHERE title='Plus carousel'", one=True)["brief_id"], bid)
        self.assertEqual(self.c.get("/briefs").status_code, 403)

    def test_admin_sends_brief_and_registers_a_logo(self):
        self.login("u-admin")
        r = self.c.post("/briefs", data={"title": "Reel captions", "body": "Three captions for the Plus reel.",
                                         "channel": "Instagram", "due_on": "not-a-date"}, follow_redirects=True)
        self.assertIn(b"Brief sent", r.data)
        r = self.c.post("/briefs", data={"title": "", "body": ""}, follow_redirects=True)
        self.assertIn(b"needs a title", r.data)
        r = self.c.post("/logos", data={"name": "Bad", "status": "retired", "logo": (io.BytesIO(b"x"), "x.png")},
                        content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"isn&#39;t an image", r.data)
        r = self.c.post("/logos", data={"name": "Test mark", "status": "retired",
                                        "logo": (io.BytesIO(demo("logo-retired.png")), "m.png")},
                        content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Logo added as retired", r.data)

    def test_governance_dashboard(self):
        self.login("u-reviewer")
        r = self.c.get("/governance")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Issues per week", r.data)
        self.assertIn(b"generated sample activity", r.data)   # sample history is labelled as such
        self.login("u-agency")
        self.assertEqual(self.c.get("/governance").status_code, 403)


class Robustness(Base):
    def test_bad_and_empty_input(self):
        self.login("u-writer")
        self.assertEqual(self.c.post("/content/new", data={"title": "", "body": ""}).status_code, 400)
        self.assertEqual(self.c.post("/content/new", data={"title": "x", "body": "a" * 20001}).status_code, 400)
        r = self.c.post("/content/new", data={"title": "x", "pdf": (io.BytesIO(b"%PDF-broken"), "a.pdf")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/content/new", data={"title": "x", "pdf": (io.BytesIO(b"hello"), "a.txt")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/content/new", data={"title": "x", "body": "", "image": (io.BytesIO(b"\x89PNG broken"), "a.png")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        # the form keeps what was typed after an error
        r = self.c.post("/content/new", data={"title": "Keep me", "body": "a" * 20001})
        self.assertIn(b"Keep me", r.data)

    def test_missing_things_give_404_not_500(self):
        self.login("u-admin")
        for path in ("/content/99999", "/content/99999/history", "/asset/99999", "/logo/99999.png", "/impact/99999",
                     "/seal/VLM-ZZZZ-ZZZZ", "/verify/sample/nope", "/nope"):
            self.assertEqual(self.c.get(path).status_code, 404, path)
        self.assertEqual(self.c.post("/finding/99999/decide", data={"action": "accept"}).status_code, 404)
        self.assertEqual(self.c.post("/facts/F-99", data={"value": "1"}).status_code, 404)

    def test_repeated_and_refreshed_actions_are_safe(self):
        self.login("u-writer")
        cid = self.cid("Diwali savings post")
        for _ in range(3):  # pressing Check repeatedly just re-checks
            self.assertEqual(self.c.post(f"/content/{cid}/check").status_code, 302)
        open_n = q(self.db(), "SELECT COUNT(*) n FROM findings WHERE content_id=? AND status='open'", (cid,), one=True)["n"]
        self.assertGreater(open_n, 0)
        self.assertEqual(open_n, len({(f["quote"], f["ref"]) for f in q(
            self.db(), "SELECT quote, ref FROM findings WHERE content_id=? AND status='open'", (cid,))}))
        self.assertEqual(self.c.get(f"/content/{cid}").status_code, 200)   # refresh
        self.login("u-admin")
        r = self.c.post("/facts/F-02", data={"value": "₹199"}, follow_redirects=True)   # same value again
        self.assertIn(b"Saved", r.data)
        r = self.c.post("/facts/F-02", data={"value": "  "}, follow_redirects=True)
        self.assertIn(b"needs a value", r.data)
        r = self.c.post("/facts/F-02", data={"value": "₹199", "expires_on": "tomorrow"}, follow_redirects=True)
        self.assertIn(b"YYYY-MM-DD", r.data)

    def test_unknown_action_and_wrong_role(self):
        self.login("u-writer")
        cid = self.cid("Diwali savings post")
        self.c.post(f"/content/{cid}/check")
        f = q(self.db(), "SELECT id FROM findings WHERE content_id=? AND status='open' LIMIT 1", (cid,), one=True)
        self.assertEqual(self.c.post(f"/finding/{f['id']}/decide", data={"action": "explode"}).status_code, 400)
        self.assertEqual(self.c.post("/facts/F-01", data={"value": "₹1"}).status_code, 403)
        self.assertEqual(self.c.post("/logos", data={}).status_code, 403)
        r = self.c.post(f"/content/{cid}/status", data={"action": "approve"}, follow_redirects=True)
        self.assertIn(b"available for this item any more", r.data)


if __name__ == "__main__":
    unittest.main()
