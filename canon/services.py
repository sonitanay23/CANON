"""Core workflows: running checks, applying fixes, approval, fact changes and
the guideline debugger. Routes call these; they hold no HTTP logic."""
import copy
import hashlib
import json
import re
from collections import OrderedDict

from . import ai, rules as engine, seals, vision
from .db import add_version, audit, ex, latest_version, now, q, setting, visible_facts

FLAG_MIN_DECISIONS = 10
FLAG_OVERRIDE_RATE = 0.40


# ---------- checking ----------

def _overlaps(a, b):
    a, b = a.lower(), b.lower()
    return a in b or b in a


def merge(rule_findings, ai_findings):
    merged = list(rule_findings)
    for f in ai_findings:
        if any(r["ref"] == f["ref"] or (r["action"] == "replace" and _overlaps(f["quote"], r["quote"]))
               for r in rule_findings):
            continue  # the rule engine's deterministic finding wins
        if any(_overlaps(f["quote"], m["quote"]) and f["ref"] == m["ref"] for m in merged):
            continue
        merged.append(f)
    return merged


_ai_cache = OrderedDict()  # recent AI answers, so re-checking identical text doesn't spend API calls
AI_CACHE_SIZE = 200


def _cache_key(text, voice, rules, facts, terms):
    parts = [ai.model(), text, voice, json.dumps(terms)]
    parts += [f"{r['id']}|{r['type']}|{r['text']}" for r in rules]
    parts += [f"{f['id']}|{f['value']}|{f['expires_on']}" for f in facts]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def analyse(conn, text, role, use_ai=True):
    """Run rule engine + AI. Returns (findings, ai_status, dropped)."""
    rules = q(conn, "SELECT * FROM rules WHERE active=1 ORDER BY id")
    facts = visible_facts(conn, role)
    rule_findings = engine.run_rule_engine(text, rules, facts)
    ai_status, dropped, ai_findings = "off", 0, []
    if use_ai and ai.available():
        voice, terms = setting(conn, "voice", ""), setting(conn, "confidential_terms", [])
        key = _cache_key(text, voice, rules, facts, terms)
        if key in _ai_cache:
            _ai_cache.move_to_end(key)
            ai_findings, dropped = copy.deepcopy(_ai_cache[key])
            ai_status = "ok"
        else:
            try:
                ai_findings, dropped = ai.review(text, voice, rules, facts, rule_findings, terms)
                ai_status = "ok"
                _ai_cache[key] = copy.deepcopy((ai_findings, dropped))
                if len(_ai_cache) > AI_CACHE_SIZE:
                    _ai_cache.popitem(last=False)
            except ai.AIUnavailable as e:
                ai_status = f"unavailable: {e}"
    elif use_ai:
        ai_status = "unavailable: no API key configured"
    return merge(rule_findings, ai_findings), ai_status, dropped


def save_finding(conn, content_id, version_n, f):
    ex(conn, """INSERT INTO findings(content_id,version_n,quote,issue,ref,method,severity,confidence,
                intent,fix,action,needs_review,status,created_at,target,box) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?)""",
       (content_id, version_n, f["quote"], f["issue"], f["ref"], f["method"], f["severity"],
        f["confidence"], f["intent"], f["fix"], f.get("action", "replace"), f["needs_review"], now(),
        f.get("target", "text"), json.dumps(f["box"]) if f.get("box") else None))


def latest_asset(conn, content_id):
    return q(conn, "SELECT * FROM assets WHERE content_id=? ORDER BY n DESC LIMIT 1", (content_id,), one=True)


def add_asset(conn, content_id, data, filename, alt_text, user, ocr_lines=None):
    """Store a new image version for a piece of content and measure it. Raises vision.BadImage."""
    im = vision.load(data)
    retired = q(conn, "SELECT * FROM logos WHERE status='retired'")
    current = q(conn, "SELECT * FROM logos WHERE status='current'")
    result = vision.analyse(im, retired, ocr_lines=ocr_lines, current_logos=current)
    prev = latest_asset(conn, content_id)
    n = (prev["n"] + 1) if prev else 1
    if alt_text is None and prev:
        alt_text = prev["alt_text"]
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
        vision.Image.open(__import__("io").BytesIO(data)).format, "image/png")
    aid = ex(conn, """INSERT INTO assets(content_id,n,filename,mime,data,sha,phash,width,height,alt_text,ocr_text,
                     ocr_method,ocr_lines,visual_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (content_id, n, (filename or "image")[:120], mime, data, vision.sha(data), vision.dhash(im),
              im.size[0], im.size[1], (alt_text or "").strip(), "\n".join(ln["text"] for ln in result["lines"]),
              result["ocr_method"], vision.to_json(result["lines"]), vision.to_json(result),
              user["id"], now()))
    ex(conn, "UPDATE content SET updated_at=? WHERE id=?", (now(), content_id))
    audit(conn, user["id"], "image added", f"content {content_id} image v{n} ({result['ocr_method']} text reading)")
    return aid


def _box_for(quote, lines):
    ql = quote.lower()
    for ln in lines:
        if ln.get("box") and (ql in ln["text"].lower() or ln["text"].lower() in ql):
            return ln["box"]
    return None


def image_findings(conn, asset, role, body):
    """Findings for the image: its text through the normal checks, then contrast, logo and alt text."""
    result = json.loads(asset["visual_json"] or "{}")
    lines = json.loads(asset["ocr_lines"] or "[]")
    active = {r["id"] for r in q(conn, "SELECT id FROM rules WHERE active=1")}
    out, ai_status, dropped = [], "off", 0
    if (asset["ocr_text"] or "").strip():
        found, ai_status, dropped = analyse(conn, asset["ocr_text"], role)
        for f in found:
            if f.get("action") == "append":
                # a disclaimer the image needs goes in the caption, unless the caption already has it
                if f["fix"].lower().rstrip(".") in (body or "").lower():
                    continue
                f["issue"] += " (triggered by text in the image)"
                f["target"] = "text"
            else:
                f["target"], f["action"] = "image", "artwork"
                f["box"] = _box_for(f["quote"], lines)
            out.append(f)
    if "R-14" in active:
        for c in result.get("contrast", []):
            if c["ok"]:
                continue
            out.append({"quote": c["text"], "ref": "R-14", "method": "rule", "action": "artwork", "target": "image",
                        "issue": f"Text contrast is {c['ratio']}:1 ({c['fg']} on {c['bg']}); WCAG AA needs {c['need']:g}:1"
                                 f" for {'large' if c['large'] else 'normal'} text",
                        "severity": "high" if c["ratio"] < 3 else "medium", "confidence": 1.0, "needs_review": 0,
                        "intent": "Keep the line, make it readable for everyone.",
                        "fix": f"Set this line in {c['suggest']} (contrast {c['suggest_ratio']}:1 on this background)",
                        "box": c["box"]})
    if "R-16" in active:
        for lg in result.get("logos", []):
            out.append({"quote": f"Retired logo: {lg['name']}", "ref": "R-16", "method": "rule", "action": "artwork",
                        "target": "image", "issue": f"Uses the retired logo \"{lg['name']}\" ({int(lg['score'] * 100)}% shape match)",
                        "severity": "high", "confidence": lg["score"], "needs_review": 0,
                        "intent": "Show the brand mark.", "fix": "Replace it with the current Velora Money logo",
                        "box": lg["box"]})
    if "R-15" in active:
        problem = vision.alt_problem(asset["alt_text"], asset["filename"])
        if problem:
            suggestion = result.get("alt_suggestion") or ""
            out.append({"quote": asset["alt_text"] or "(no alt text)", "ref": "R-15", "method": "rule", "action": "alt",
                        "target": "image", "issue": problem, "severity": "medium", "confidence": 1.0,
                        "needs_review": 0, "intent": "Describe the image for people who can't see it.",
                        "fix": suggestion, "box": None})
    return out, ai_status, dropped


def run_check(conn, content_id, user):
    v = latest_version(conn, content_id)
    author = q(conn, "SELECT u.role FROM content c JOIN users u ON u.id=c.author_id WHERE c.id=?",
               (content_id,), one=True)
    role = author["role"] if author else user["role"]
    findings, ai_status, dropped = analyse(conn, v["body"], role) if v["body"].strip() else ([], "off", 0)
    asset = latest_asset(conn, content_id)
    if asset:
        img, img_status, img_dropped = image_findings(conn, asset, role, v["body"])
        have = {(f["ref"], f.get("action")) for f in findings if f.get("action") == "append"}
        findings += [f for f in img if not (f.get("action") == "append" and (f["ref"], "append") in have)]
        dropped += img_dropped
        if ai_status == "off" or (ai_status == "ok" and img_status not in ("ok", "off")):
            ai_status = img_status
    ex(conn, "UPDATE findings SET status='superseded' WHERE content_id=? AND status='open'", (content_id,))
    for f in findings:
        save_finding(conn, content_id, v["n"], f)
    ex(conn, "INSERT INTO check_runs(content_id,version_n,ai_status,ai_dropped,created_at) VALUES(?,?,?,?,?)",
       (content_id, v["n"], ai_status, dropped, now()))
    audit(conn, user["id"], "check", f"content {content_id} v{v['n']}: {len(findings)} findings, AI {ai_status}")
    return len(findings), ai_status


def source_text(conn, ref):
    if ref.startswith("R-"):
        r = q(conn, "SELECT * FROM rules WHERE id=?", (ref,), one=True)
        return (f"{r['id']} · {r['title']}", r["text"]) if r else (ref, "")
    f = q(conn, "SELECT * FROM facts WHERE id=?", (ref,), one=True)
    return (f"{f['id']} · {f['label']}", f"Approved value: {f['value']} (source: {f['source']})") if f else (ref, "")


# ---------- decisions ----------

def apply_fix(body, quote, fix, action="replace"):
    if action == "append":
        return body if fix.lower().rstrip(".") in body.lower() else body.rstrip() + "\n\n" + fix
    idx = body.find(quote)
    if idx == -1:
        return None
    out = body[:idx] + fix + body[idx + len(quote):]
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r" +([.,!?])", r"\1", out).strip()


def decide(conn, finding_id, action, user, reason="", edited_fix=None):
    f = q(conn, "SELECT * FROM findings WHERE id=?", (finding_id,), one=True)
    if not f or f["status"] != "open":
        return "This issue was already handled."
    if action in ("accept", "edit") and f["action"] == "alt":
        alt = (edited_fix if action == "edit" else f["fix"]) or ""
        if vision.alt_problem(alt):
            return "Write alt text that describes the image (at least a short sentence)."
        asset = latest_asset(conn, f["content_id"])
        ex(conn, "UPDATE assets SET alt_text=? WHERE id=?", (alt.strip(), asset["id"]))
        status = "accepted" if action == "accept" else "edited"
    elif action in ("accept", "edit") and f["action"] == "artwork":
        return "This is in the artwork itself. Upload a corrected image, or keep it and give a reason."
    elif action in ("accept", "edit"):
        fix = edited_fix if action == "edit" else f["fix"]
        if not (fix or "").strip() and f["action"] == "append":
            return "The fix can't be empty."
        v = latest_version(conn, f["content_id"])
        new_body = apply_fix(v["body"], f["quote"], fix, f["action"] or "replace")
        if new_body is None:
            return "The flagged text has changed since the check. Run the check again."
        add_version(conn, f["content_id"], new_body, user["id"],
                    note=f"{'Applied' if action == 'accept' else 'Edited and applied'} fix for {f['ref']}")
        status = "accepted" if action == "accept" else "edited"
    else:
        if not reason.strip():
            return "Please give a short reason for keeping the original text."
        status = "rejected"
    ex(conn, "UPDATE findings SET status=? WHERE id=?", (status, finding_id))
    if status in ("accepted", "edited") and f["action"] in ("replace", "append"):
        # other open issues whose text disappeared with this edit are resolved by it
        body = latest_version(conn, f["content_id"])["body"]
        for other in q(conn, "SELECT id, quote FROM findings WHERE content_id=? AND status='open' "
                             "AND COALESCE(target,'text')='text' AND action!='append'", (f["content_id"],)):
            if other["quote"] not in body:
                ex(conn, "UPDATE findings SET status='resolved' WHERE id=?", (other["id"],))
    ex(conn, "INSERT INTO decisions(finding_id,content_id,rule_ref,action,reason,user_id,created_at) VALUES(?,?,?,?,?,?,?)",
       (finding_id, f["content_id"], f["ref"], status, reason.strip(), user["id"], now()))
    audit(conn, user["id"], f"finding {status}", f"finding {finding_id} ({f['ref']}) on content {f['content_id']}")
    return None


# ---------- workflow ----------

def approve(conn, content_id, user):
    v = latest_version(conn, content_id)
    facts = q(conn, "SELECT * FROM facts")
    ex(conn, "DELETE FROM claim_links WHERE content_id=?", (content_id,))
    for link in engine.claim_links(v["body"], facts):
        ex(conn, "INSERT INTO claim_links(content_id,fact_id,quote,value) VALUES(?,?,?,?)",
           (content_id, link["fact_id"], link["quote"], link["value"]))
    ex(conn, "UPDATE content SET status='approved', updated_at=?, approved_at=? WHERE id=?", (now(), now(), content_id))
    code = seals.issue(conn, content_id, user)
    audit(conn, user["id"], "approved", f"content {content_id} v{v['n']}, seal {code}")
    return code


# ---------- improvement 1: fact change -> affected content ----------

def change_fact(conn, fact_id, new_value, user):
    f = q(conn, "SELECT * FROM facts WHERE id=?", (fact_id,), one=True)
    old = f["value"]
    if new_value.strip() == old:
        return None
    ex(conn, "UPDATE facts SET value=?, version=version+1, updated_at=? WHERE id=?", (new_value.strip(), now(), fact_id))
    hid = ex(conn, "INSERT INTO fact_history(fact_id,old_value,new_value,changed_by,changed_at) VALUES(?,?,?,?,?)",
             (fact_id, old, new_value.strip(), user["id"], now()))
    audit(conn, user["id"], "fact changed", f"{fact_id}: {old} -> {new_value.strip()}")
    return hid


def affected_by(conn, history_id):
    """Approved content that still states the old value of a changed fact."""
    h = q(conn, "SELECT * FROM fact_history WHERE id=?", (history_id,), one=True)
    fact = dict(q(conn, "SELECT * FROM facts WHERE id=?", (h["fact_id"],), one=True))
    old_fact = dict(fact, value=h["old_value"])
    items = []
    for c in q(conn, "SELECT * FROM content WHERE status IN ('approved','in_review') ORDER BY id"):
        body = latest_version(conn, c["id"])["body"]
        asset = latest_asset(conn, c["id"])
        hits = []
        sources = [("text", body)] + ([("image", asset["ocr_text"] or "")] if asset else [])
        for where, text in sources:
            for vm, sent in engine.fact_mentions(text, old_fact):
                if engine._norm(vm.group(0)) == engine._norm(h["old_value"]):
                    hits.append({"quote": sent, "old": vm.group(0).strip(), "where": where,
                                 "fix": sent.replace(vm.group(0).strip(), h["new_value"], 1)})
        linked = q(conn, "SELECT 1 FROM claim_links WHERE content_id=? AND fact_id=?", (c["id"], h["fact_id"]), one=True)
        if hits:
            items.append({"content": c, "hits": hits, "linked": bool(linked)})
    return h, fact, items


def send_fixes(conn, history_id, content_ids, user):
    h, _fact, items = affected_by(conn, history_id)
    sent = 0
    for item in items:
        cid = item["content"]["id"]
        if str(cid) not in content_ids:
            continue
        body = latest_version(conn, cid)["body"]
        text_hits = [x for x in item["hits"] if x["where"] == "text"]
        image_hits = [x for x in item["hits"] if x["where"] == "image"]
        for hit in text_hits:
            body = apply_fix(body, hit["quote"], hit["fix"]) or body
        if text_hits:
            add_version(conn, cid, body, user["id"], note=f"Updated {h['fact_id']}: {h['old_value']} → {h['new_value']}")
        seals.supersede(conn, cid)
        if image_hits:
            # artwork can't be edited automatically: send it back to its author with exact instructions
            ex(conn, "UPDATE content SET status='changes_requested', updated_at=? WHERE id=?", (now(), cid))
            lines = "; ".join(f"\"{x['quote']}\" should read \"{x['fix']}\"" for x in image_hits)
            ex(conn, "INSERT INTO comments(content_id,user_id,body,created_at) VALUES(?,?,?,?)",
               (cid, user["id"], f"{h['fact_id']} changed from {h['old_value']} to {h['new_value']}. "
                                 f"The image still shows the old value: {lines}. Please upload updated artwork.", now()))
            run_check(conn, cid, user)
            audit(conn, user["id"], "fix-forward", f"content {cid} sent back for new artwork after {h['fact_id']} change")
        else:
            ex(conn, "UPDATE content SET status='in_review', updated_at=?, submitted_at=? WHERE id=?", (now(), now(), cid))
            run_check(conn, cid, user)
            route(conn, cid)
            audit(conn, user["id"], "fix-forward", f"content {cid} sent to review after {h['fact_id']} change")
        sent += 1
    return sent


# ---------- improvement 2: guideline debugger ----------

def rule_stats(conn):
    stats = []
    for r in q(conn, "SELECT * FROM rules ORDER BY id"):
        rows = q(conn, "SELECT action, reason FROM decisions WHERE rule_ref=?", (r["id"],))
        total = len(rows)
        overrides = [x for x in rows if x["action"] == "rejected"]
        rate = len(overrides) / total if total else 0
        if total >= FLAG_MIN_DECISIONS and rate >= FLAG_OVERRIDE_RATE:
            state = "unclear"
        elif total < FLAG_MIN_DECISIONS and overrides:
            state = "too few decisions"
        else:
            state = "healthy"
        rewrite = q(conn, "SELECT * FROM rule_rewrites WHERE rule_id=? ORDER BY id DESC LIMIT 1", (r["id"],), one=True)
        stats.append({"rule": r, "total": total, "overrides": len(overrides), "rate": rate, "state": state,
                      "reasons": [x["reason"] for x in overrides if x["reason"]], "rewrite": rewrite})
    order = {"unclear": 0, "too few decisions": 1, "healthy": 2}
    return sorted(stats, key=lambda s: (order[s["state"]], -s["rate"], -s["total"]))


def group_reasons(reasons):
    """Plain grouping of identical-looking reasons, used with or without AI."""
    groups = {}
    for r in reasons:
        key = re.sub(r"[^a-z ]", "", r.lower()).strip()[:60]
        groups.setdefault(key, []).append(r)
    return sorted(([v[0], len(v)] for v in groups.values()), key=lambda x: -x[1])


# ---------- improvement 3: risk-based routing ----------

LEGAL_REFS = {"R-01", "R-02", "R-07", "R-10", "R-13"}
LEGAL_WORDS = re.compile(r"\b(?:invest\w*|returns?|interest|p\.a\.|guarantee\w*|risk[- ]free|insur\w*|loan|credit)\b", re.I)
HIGH_REACH = {"Ad", "Email", "Website", "Sales"}
LANES = {
    "fast": {"label": "Fast lane", "sla_hours": 4, "who": "Any reviewer, one click"},
    "standard": {"label": "Standard review", "sla_hours": 24, "who": "Marketing reviewer"},
    "legal": {"label": "Legal review", "sla_hours": 48, "who": "Legal & compliance"},
}


def assess_risk(conn, content_id):
    """Score a piece before review. Every point has a stated reason, so routing is explainable."""
    c = q(conn, "SELECT c.*, u.role AS author_role FROM content c JOIN users u ON u.id=c.author_id WHERE c.id=?",
          (content_id,), one=True)
    v = latest_version(conn, content_id)
    asset = latest_asset(conn, content_id)
    text = v["body"] + "\n" + ((asset["ocr_text"] or "") if asset else "")
    open_f = q(conn, "SELECT * FROM findings WHERE content_id=? AND status='open'", (content_id,))
    kept = q(conn, "SELECT * FROM findings WHERE content_id=? AND status='rejected' AND version_n<=?", (content_id, v["n"]))
    run = q(conn, "SELECT * FROM check_runs WHERE content_id=? ORDER BY id DESC LIMIT 1", (content_id,), one=True)
    score, reasons, legal = 0, [], []

    def add(points, why):
        nonlocal score
        score += points
        reasons.append([points, why])
    if not run or run["version_n"] != v["n"]:
        add(3, "The latest version hasn't been checked")
    high = [f for f in open_f if f["severity"] == "high"]
    if high:
        add(3 * len(high), f"{len(high)} high-severity issue{'s' if len(high) != 1 else ''} still open")
    other = len(open_f) - len(high)
    if other:
        add(other, f"{other} other open issue{'s' if other != 1 else ''}")
    if kept:
        add(len(kept), f"{len(kept)} suggestion{'s' if len(kept) != 1 else ''} overridden by the writer")
    if c["channel"] in HIGH_REACH:
        add(1, f"{c['channel']} is a high-reach channel")
    if c["author_role"] == "agency":
        add(1, "Written by an outside agency")
    if asset:
        add(1, "Includes an image")
    if run and run["ai_status"] not in ("ok", "off") and "no API key" not in run["ai_status"]:
        add(1, "AI review was unavailable, so only rule checks ran")
    m = LEGAL_WORDS.search(text)
    if m:
        legal.append(f"Mentions \"{m.group(0)}\" (financial claim language)")
    claim_refs = {f["ref"] for f in list(open_f) + list(kept)} & LEGAL_REFS
    if claim_refs:
        legal.append("Has open or overridden claim/disclaimer issues (" + ", ".join(sorted(claim_refs)) + ")")
    if legal:
        lane = "legal"
        reasons += [[0, why] for why in legal]
    elif score <= 2 and not open_f:
        lane = "fast"
    else:
        lane = "standard"
    return min(score, 20), lane, reasons


def route(conn, content_id):
    score, lane, reasons = assess_risk(conn, content_id)
    if lane == "legal":
        who = q(conn, "SELECT id FROM users WHERE role='reviewer' AND team LIKE 'Legal%' LIMIT 1", one=True)
    else:
        who = q(conn, "SELECT id FROM users WHERE role='reviewer' AND team NOT LIKE 'Legal%' LIMIT 1", one=True)
    ex(conn, "UPDATE content SET lane=?, risk=?, risk_reasons=?, assignee_id=? WHERE id=?",
       (lane, score, json.dumps(reasons), who["id"] if who else None, content_id))
    return lane


def can_approve(user, content):
    """Legal-lane content needs Legal (or a brand admin); everything else any reviewer."""
    if user["role"] == "admin":
        return True
    if user["role"] != "reviewer":
        return False
    if content["lane"] == "legal":
        return (user["team"] or "").startswith("Legal")
    return True
