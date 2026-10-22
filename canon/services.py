"""Core workflows: running checks, applying fixes, approval, fact changes and
the guideline debugger. Routes call these; they hold no HTTP logic."""
import copy
import hashlib
import json
import re
from collections import OrderedDict

from . import ai, rules as engine
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
                intent,fix,action,needs_review,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'open',?)""",
       (content_id, version_n, f["quote"], f["issue"], f["ref"], f["method"], f["severity"],
        f["confidence"], f["intent"], f["fix"], f.get("action", "replace"), f["needs_review"], now()))


def run_check(conn, content_id, user):
    v = latest_version(conn, content_id)
    author = q(conn, "SELECT u.role FROM content c JOIN users u ON u.id=c.author_id WHERE c.id=?",
               (content_id,), one=True)
    findings, ai_status, dropped = analyse(conn, v["body"], author["role"] if author else user["role"])
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
    if action in ("accept", "edit"):
        fix = edited_fix if action == "edit" else f["fix"]
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
    if status in ("accepted", "edited"):
        # other open issues whose text disappeared with this edit are resolved by it
        body = latest_version(conn, f["content_id"])["body"]
        for other in q(conn, "SELECT id, quote FROM findings WHERE content_id=? AND status='open'", (f["content_id"],)):
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
    ex(conn, "UPDATE content SET status='approved', updated_at=? WHERE id=?", (now(), content_id))
    audit(conn, user["id"], "approved", f"content {content_id} v{v['n']}")


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
        hits = []
        for vm, sent in engine.fact_mentions(body, old_fact):
            if engine._norm(vm.group(0)) == engine._norm(h["old_value"]):
                hits.append({"quote": sent, "old": vm.group(0).strip(),
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
        for hit in item["hits"]:
            body = apply_fix(body, hit["quote"], hit["fix"]) or body
        add_version(conn, cid, body, user["id"], note=f"Updated {h['fact_id']}: {h['old_value']} → {h['new_value']}")
        ex(conn, "UPDATE content SET status='in_review', updated_at=? WHERE id=?", (now(), cid))
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
