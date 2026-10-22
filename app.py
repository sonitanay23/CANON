"""Canon: brand governance before you publish.

Flask app: server-rendered pages, SQLite, rule engine + Claude API.
Run locally:  python app.py   (then open http://localhost:5000)
"""
import difflib
import html
import io
import os
import re
from functools import wraps

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from markupsafe import Markup

from canon import ai, services, testset
from canon.db import (DB_PATH, add_version, audit, close_db, connect, ex, get_db, latest_version, now, q,
                      setting, visible_facts)
from canon.seed import seed

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "canon-dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.teardown_appcontext(close_db)

with app.app_context():
    _c = connect()
    seed(_c)
    _c.close()

CHANNELS = ["LinkedIn", "Instagram", "Email", "Website", "Blog", "Ad", "Push", "Sales", "Help centre"]
STATUS_LABELS = {"draft": "Draft", "in_review": "In review", "changes_requested": "Changes requested",
                 "approved": "Approved"}
ROLE_LABELS = {"admin": "Brand admin", "reviewer": "Reviewer", "creator": "Creator", "agency": "Agency creator"}


# ---------- auth ----------

@app.before_request
def load_user():
    uid = session.get("uid")
    g.user = q(get_db(), "SELECT * FROM users WHERE id=?", (uid,), one=True) if uid else None


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapped


def roles(*allowed):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if not g.user:
                return redirect(url_for("login", next=request.path))
            if g.user["role"] not in allowed:
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def can_see(content):
    if g.user["role"] in ("admin", "reviewer"):
        return True
    return content["author_id"] == g.user["id"]


@app.context_processor
def inject():
    return {"me": g.get("user"), "ai_on": ai.available(), "ai_model": ai.label(), "ai_provider": ai.provider(),
            "status_labels": STATUS_LABELS, "role_labels": ROLE_LABELS,
            "brand": setting(get_db(), "brand", "Velora Money")}


# ---------- helpers ----------

def word_diff(a, b):
    """Inline diff for a fix: removed words struck through, added words highlighted."""
    ta, tb = re.findall(r"\S+|\s+", a), re.findall(r"\S+|\s+", b)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb).get_opcodes():
        if op == "equal":
            out.append(html.escape("".join(ta[i1:i2])))
        if op in ("delete", "replace"):
            out.append(f"<del>{html.escape(''.join(ta[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins>{html.escape(''.join(tb[j1:j2]))}</ins>")
    return Markup("".join(out))


def highlight(body, findings):
    """Split the body into plain and flagged segments (by first unclaimed occurrence)."""
    spans = []
    for f in findings:
        if f["action"] == "append":
            needle = f["quote"]
        else:
            needle = f["quote"]
        start = 0
        while True:
            idx = body.find(needle, start)
            if idx == -1:
                break
            if not any(idx < e and idx + len(needle) > s for s, e, _ in spans):
                spans.append((idx, idx + len(needle), f))
                break
            start = idx + 1
    spans.sort(key=lambda x: x[0])
    segs, pos = [], 0
    for s, e, f in spans:
        if s > pos:
            segs.append((body[pos:s], None))
        segs.append((body[s:e], f))
        pos = e
    segs.append((body[pos:], None))
    return segs


def content_or_404(cid):
    c = q(get_db(), "SELECT c.*, u.name AS author_name, u.role AS author_role, u.team AS author_team "
                    "FROM content c JOIN users u ON u.id=c.author_id WHERE c.id=?", (cid,), one=True)
    if not c:
        abort(404)
    if not can_see(c):
        abort(403)
    return c


# ---------- pages ----------

@app.route("/")
def home():
    db = get_db()
    stats = {
        "drafts": q(db, "SELECT COUNT(*) n FROM content", one=True)["n"],
        "approved": q(db, "SELECT COUNT(*) n FROM content WHERE status='approved'", one=True)["n"],
        "rules": q(db, "SELECT COUNT(*) n FROM rules WHERE active=1", one=True)["n"],
        "facts": q(db, "SELECT COUNT(*) n FROM facts", one=True)["n"],
    }
    users = q(db, "SELECT * FROM users ORDER BY CASE role WHEN 'creator' THEN 0 WHEN 'reviewer' THEN 1 "
                  "WHEN 'admin' THEN 2 ELSE 3 END")
    return render_template("home.html", stats=stats, users=users)


@app.route("/login", methods=["GET", "POST"])
def login():
    db = get_db()
    if request.method == "POST":
        u = q(db, "SELECT * FROM users WHERE id=?", (request.form.get("uid"),), one=True)
        if not u:
            flash("Pick one of the demo accounts.", "error")
            return redirect(url_for("login"))
        session["uid"] = u["id"]
        audit(db, u["id"], "signed in")
        nxt = request.form.get("next") or ""
        return redirect(nxt if nxt.startswith("/") else url_for("content_list"))
    users = q(db, "SELECT * FROM users ORDER BY CASE role WHEN 'creator' THEN 0 WHEN 'reviewer' THEN 1 "
                  "WHEN 'admin' THEN 2 ELSE 3 END")
    return render_template("login.html", users=users, next=request.args.get("next", ""))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/content")
@login_required
def content_list():
    db = get_db()
    sql = ("SELECT c.*, u.name AS author_name, u.team AS author_team, "
           "(SELECT COUNT(*) FROM findings f WHERE f.content_id=c.id AND f.status='open') AS open_issues "
           "FROM content c JOIN users u ON u.id=c.author_id ")
    if g.user["role"] in ("admin", "reviewer"):
        rows = q(db, sql + "ORDER BY c.updated_at DESC")
    else:
        rows = q(db, sql + "WHERE c.author_id=? ORDER BY c.updated_at DESC", (g.user["id"],))
    groups = {k: [r for r in rows if r["status"] == k] for k in STATUS_LABELS}
    return render_template("content_list.html", groups=groups, total=len(rows))


@app.route("/content/new", methods=["GET", "POST"])
@roles("creator", "agency", "admin")
def content_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip() or "Untitled draft"
        channel = request.form.get("channel", "Website")
        body = request.form.get("body", "").strip()
        upload = request.files.get("pdf")
        if upload and upload.filename:
            if not upload.filename.lower().endswith(".pdf"):
                flash("Only PDF files can be uploaded. You can also paste text.", "error")
                return redirect(url_for("content_new"))
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(upload.read()))
                body = "\n\n".join((p.extract_text() or "").strip() for p in reader.pages).strip()
            except Exception:
                flash("That PDF could not be read. Try pasting the text instead.", "error")
                return redirect(url_for("content_new"))
            if not body:
                flash("No text found in that PDF (it may be a scanned image). Paste the text instead.", "error")
                return redirect(url_for("content_new"))
        if not body:
            flash("Add some text or upload a PDF.", "error")
            return redirect(url_for("content_new"))
        db = get_db()
        cid = ex(db, "INSERT INTO content(title,channel,author_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                 (title, channel, g.user["id"], "draft", now(), now()))
        add_version(db, cid, body, g.user["id"], note="First draft")
        audit(db, g.user["id"], "created draft", f"content {cid}")
        services.run_check(db, cid, g.user)
        return redirect(url_for("content_view", cid=cid))
    return render_template("content_new.html", channels=CHANNELS)


@app.route("/content/<int:cid>")
@login_required
def content_view(cid):
    db = get_db()
    c = content_or_404(cid)
    v = latest_version(db, cid)
    findings = q(db, "SELECT * FROM findings WHERE content_id=? AND status='open' "
                     "ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id", (cid,))
    handled = q(db, "SELECT * FROM findings WHERE content_id=? AND status IN ('accepted','edited','rejected','resolved') "
                    "ORDER BY id DESC", (cid,))
    run = q(db, "SELECT * FROM check_runs WHERE content_id=? ORDER BY id DESC LIMIT 1", (cid,), one=True)
    sources = {f["ref"]: services.source_text(db, f["ref"]) for f in list(findings) + list(handled)}
    comments = q(db, "SELECT cm.*, u.name, u.role FROM comments cm JOIN users u ON u.id=cm.user_id "
                     "WHERE content_id=? ORDER BY cm.id", (cid,))
    stale = run is None or run["version_n"] != v["n"]
    return render_template("content_view.html", c=c, v=v, findings=findings, handled=handled, run=run,
                           sources=sources, segs=highlight(v["body"], findings), word_diff=word_diff,
                           comments=comments, stale=stale)


@app.route("/content/<int:cid>/check", methods=["POST"])
@login_required
def content_check(cid):
    c = content_or_404(cid)
    if c["status"] == "approved":
        flash("Approved content is locked. It is re-checked automatically when a fact changes.", "info")
        return redirect(url_for("content_view", cid=cid))
    n, ai_status = services.run_check(get_db(), cid, g.user)
    msg = f"Check complete: {n} issue{'s' if n != 1 else ''} found."
    if ai_status != "ok":
        msg += " AI review was unavailable, so only rule-based checks ran."
    flash(msg, "success" if ai_status == "ok" else "info")
    return redirect(url_for("content_view", cid=cid))


@app.route("/content/<int:cid>/edit", methods=["POST"])
@login_required
def content_edit(cid):
    c = content_or_404(cid)
    if c["status"] == "approved" or (c["author_id"] != g.user["id"] and g.user["role"] != "admin"):
        abort(403)
    body = request.form.get("body", "").strip()
    db = get_db()
    if body and body != latest_version(db, cid)["body"]:
        add_version(db, cid, body, g.user["id"], note="Edited by hand")
        services.run_check(db, cid, g.user)
        flash("Saved as a new version and re-checked.", "success")
    return redirect(url_for("content_view", cid=cid))


@app.route("/finding/<int:fid>/decide", methods=["POST"])
@login_required
def finding_decide(fid):
    db = get_db()
    f = q(db, "SELECT * FROM findings WHERE id=?", (fid,), one=True)
    if not f:
        abort(404)
    c = content_or_404(f["content_id"])
    if c["status"] == "approved":
        abort(403)
    action = request.form.get("action")
    if action not in ("accept", "edit", "reject"):
        abort(400)
    err = services.decide(db, fid, action, g.user, reason=request.form.get("reason", ""),
                          edited_fix=request.form.get("fix"))
    flash(err or {"accept": "Fix applied. A new version was saved.",
                  "edit": "Your edited fix was applied.",
                  "reject": "Kept the original. Your reason was recorded."}[action],
          "error" if err else "success")
    return redirect(url_for("content_view", cid=c["id"]) + "#issues")


@app.route("/finding/<int:fid>/regenerate", methods=["POST"])
@login_required
def finding_regenerate(fid):
    db = get_db()
    f = q(db, "SELECT * FROM findings WHERE id=?", (fid,), one=True)
    if not f or f["status"] != "open":
        abort(404)
    c = content_or_404(f["content_id"])
    intent = request.form.get("intent", "").strip() or f["intent"]
    title, text = services.source_text(db, f["ref"])
    try:
        fix = ai.regenerate_fix(f["quote"], f["issue"], text, intent, latest_version(db, c["id"])["body"],
                                setting(db, "confidential_terms", []))
        ex(db, "UPDATE findings SET fix=?, intent=? WHERE id=?", (fix, intent, fid))
        audit(db, g.user["id"], "regenerated fix", f"finding {fid}")
        flash("New fix written from your intent.", "success")
    except ai.AIUnavailable as e:
        ex(db, "UPDATE findings SET intent=? WHERE id=?", (intent, fid))
        flash(f"Intent saved, but the AI is unavailable ({e}). You can still edit the fix by hand.", "info")
    return redirect(url_for("content_view", cid=c["id"]) + f"#f{fid}")


@app.route("/content/<int:cid>/status", methods=["POST"])
@login_required
def content_status(cid):
    db = get_db()
    c = content_or_404(cid)
    action = request.form.get("action")
    open_high = q(db, "SELECT COUNT(*) n FROM findings WHERE content_id=? AND status='open' AND severity='high'",
                  (cid,), one=True)["n"]
    if action == "submit" and c["author_id"] == g.user["id"] and c["status"] in ("draft", "changes_requested"):
        ex(db, "UPDATE content SET status='in_review', updated_at=? WHERE id=?", (now(), cid))
        audit(db, g.user["id"], "submitted for review", f"content {cid}")
        flash("Sent to the review queue.", "success")
    elif action == "approve" and g.user["role"] in ("admin", "reviewer") and c["status"] == "in_review":
        if open_high and not request.form.get("confirm"):
            flash(f"{open_high} high-severity issue(s) are still open. Resolve them, or confirm to approve anyway.", "error")
            return redirect(url_for("content_view", cid=cid))
        services.approve(db, cid, g.user)
        flash("Approved. Fact references were recorded so Canon can find this piece if a fact changes.", "success")
    elif action == "request_changes" and g.user["role"] in ("admin", "reviewer") and c["status"] == "in_review":
        note = request.form.get("note", "").strip()
        ex(db, "UPDATE content SET status='changes_requested', updated_at=? WHERE id=?", (now(), cid))
        if note:
            ex(db, "INSERT INTO comments(content_id,user_id,body,created_at) VALUES(?,?,?,?)", (cid, g.user["id"], note, now()))
        audit(db, g.user["id"], "requested changes", f"content {cid}")
        flash("Sent back to the creator with your note.", "success")
    else:
        flash("That action isn't available for this item.", "error")
    return redirect(url_for("content_view", cid=cid))


@app.route("/content/<int:cid>/comment", methods=["POST"])
@login_required
def content_comment(cid):
    content_or_404(cid)
    body = request.form.get("body", "").strip()
    if body:
        ex(get_db(), "INSERT INTO comments(content_id,user_id,body,created_at) VALUES(?,?,?,?)", (cid, g.user["id"], body, now()))
    return redirect(url_for("content_view", cid=cid) + "#comments")


@app.route("/content/<int:cid>/history")
@login_required
def content_history(cid):
    db = get_db()
    c = content_or_404(cid)
    versions = q(db, "SELECT v.*, u.name FROM versions v LEFT JOIN users u ON u.id=v.created_by "
                     "WHERE content_id=? ORDER BY n DESC", (cid,))
    diffs = {}
    for i, v in enumerate(versions[:-1]):
        diffs[v["n"]] = word_diff(versions[i + 1]["body"], v["body"])
    decisions = q(db, "SELECT d.*, u.name, f.quote, f.fix FROM decisions d JOIN users u ON u.id=d.user_id "
                      "LEFT JOIN findings f ON f.id=d.finding_id WHERE d.content_id=? ORDER BY d.id DESC", (cid,))
    log = q(db, "SELECT a.*, u.name FROM audit_log a LEFT JOIN users u ON u.id=a.user_id "
                "WHERE detail LIKE ? ORDER BY a.id DESC", (f"%content {cid}%",))
    return render_template("history.html", c=c, versions=versions, diffs=diffs, decisions=decisions, log=log)


# ---------- brand setup ----------

@app.route("/brand")
@login_required
def brand():
    db = get_db()
    rules = q(db, "SELECT * FROM rules ORDER BY id")
    facts = visible_facts(db, g.user["role"])
    changes = q(db, "SELECT h.*, f.label, u.name FROM fact_history h JOIN facts f ON f.id=h.fact_id "
                    "LEFT JOIN users u ON u.id=h.changed_by ORDER BY h.id DESC LIMIT 8")
    hidden = q(db, "SELECT COUNT(*) n FROM facts WHERE visibility='internal'", one=True)["n"] if g.user["role"] == "agency" else 0
    return render_template("brand.html", rules=rules, facts=facts, changes=changes, hidden=hidden,
                           voice=setting(db, "voice", ""), terms=setting(db, "confidential_terms", []),
                           today=now()[:10])


@app.route("/facts/<fid>", methods=["POST"])
@roles("admin")
def fact_update(fid):
    db = get_db()
    f = q(db, "SELECT * FROM facts WHERE id=?", (fid,), one=True)
    if not f:
        abort(404)
    expires = request.form.get("expires_on", "").strip() or None
    if expires != f["expires_on"]:
        ex(db, "UPDATE facts SET expires_on=?, updated_at=? WHERE id=?", (expires, now(), fid))
        audit(db, g.user["id"], "fact expiry changed", f"{fid}: {f['expires_on']} -> {expires}")
    new_value = request.form.get("value", "").strip()
    if not new_value:
        flash("A fact needs a value.", "error")
        return redirect(url_for("brand") + "#facts")
    hid = services.change_fact(db, fid, new_value, g.user)
    if hid:
        return redirect(url_for("impact", hid=hid))
    flash("Saved.", "success")
    return redirect(url_for("brand") + "#facts")


@app.route("/rules/<rid>", methods=["POST"])
@roles("admin")
def rule_update(rid):
    db = get_db()
    r = q(db, "SELECT * FROM rules WHERE id=?", (rid,), one=True)
    if not r:
        abort(404)
    if request.form.get("toggle"):
        ex(db, "UPDATE rules SET active=?, updated_at=? WHERE id=?", (0 if r["active"] else 1, now(), rid))
        audit(db, g.user["id"], "rule toggled", f"{rid} {'off' if r['active'] else 'on'}")
    else:
        text = request.form.get("text", "").strip()
        if text:
            ex(db, "UPDATE rules SET text=?, updated_at=? WHERE id=?", (text, now(), rid))
            audit(db, g.user["id"], "rule edited", rid)
    flash("Rule updated.", "success")
    return redirect(url_for("brand") + "#rules")


@app.route("/rules/new", methods=["POST"])
@roles("admin")
def rule_new():
    db = get_db()
    kind = request.form.get("type")
    title = request.form.get("title", "").strip()
    text = request.form.get("text", "").strip()
    if kind not in ("banned", "terminology", "tone", "claim") or not title or not text:
        flash("Give the rule a type, a title and a description.", "error")
        return redirect(url_for("brand") + "#rules")
    word = request.form.get("word", "").strip()
    pattern = rf"\b{re.escape(word)}\b" if word and kind in ("banned", "terminology") else None
    if kind in ("banned", "terminology") and not pattern:
        flash("Banned-word and terminology rules need the word to look for.", "error")
        return redirect(url_for("brand") + "#rules")
    n = q(db, "SELECT COUNT(*) n FROM rules", one=True)["n"] + 1
    rid = f"R-{n:02d}"
    while q(db, "SELECT 1 FROM rules WHERE id=?", (rid,), one=True):
        n += 1
        rid = f"R-{n:02d}"
    ex(db, "INSERT INTO rules(id,type,title,text,pattern,replacement,trigger,severity,active,updated_at) "
           "VALUES(?,?,?,?,?,?,NULL,?,1,?)",
       (rid, kind, title, text, pattern, request.form.get("replacement", "").strip() or None,
        request.form.get("severity", "medium"), now()))
    audit(db, g.user["id"], "rule added", rid)
    flash(f"Rule {rid} added. It applies to every check from now on.", "success")
    return redirect(url_for("brand") + "#rules")


@app.route("/impact/<int:hid>", methods=["GET", "POST"])
@roles("admin", "reviewer")
def impact(hid):
    db = get_db()
    if not q(db, "SELECT 1 FROM fact_history WHERE id=?", (hid,), one=True):
        abort(404)
    if request.method == "POST":
        sent = services.send_fixes(db, hid, request.form.getlist("cid"), g.user)
        flash(f"{sent} updated piece{'s' if sent != 1 else ''} sent to the review queue.", "success")
        return redirect(url_for("content_list"))
    h, fact, items = services.affected_by(db, hid)
    return render_template("impact.html", h=h, fact=fact, items=items, word_diff=word_diff)


# ---------- improvement 2: guideline debugger ----------

@app.route("/insights")
@roles("admin", "reviewer")
def insights():
    db = get_db()
    stats = services.rule_stats(db)
    by_ref = q(db, "SELECT ref, COUNT(*) n FROM findings WHERE status!='superseded' GROUP BY ref ORDER BY n DESC LIMIT 6")
    by_team = q(db, "SELECT u.team, COUNT(*) n FROM findings f JOIN content c ON c.id=f.content_id "
                    "JOIN users u ON u.id=c.author_id WHERE f.status!='superseded' GROUP BY u.team ORDER BY n DESC")
    names = {r["id"]: r["title"] for r in q(db, "SELECT id, title FROM rules")}
    names.update({f["id"]: f["label"] for f in q(db, "SELECT id, label FROM facts")})
    return render_template("insights.html", stats=stats, by_ref=by_ref, by_team=by_team, names=names,
                           min_n=services.FLAG_MIN_DECISIONS, rate=int(services.FLAG_OVERRIDE_RATE * 100),
                           group_reasons=services.group_reasons)


@app.route("/insights/<rid>/propose", methods=["POST"])
@roles("admin")
def propose(rid):
    db = get_db()
    stat = next((s for s in services.rule_stats(db) if s["rule"]["id"] == rid), None)
    if not stat:
        abort(404)
    try:
        summary, proposal = ai.propose_rewrite(stat["rule"], stat["reasons"])
    except ai.AIUnavailable as e:
        flash(f"AI is unavailable ({e}). You can still write a clearer rule yourself below.", "info")
        return redirect(url_for("insights") + f"#{rid}")
    ex(db, "INSERT INTO rule_rewrites(rule_id,proposal,summary,status,created_at) VALUES(?,?,?,'proposed',?)",
       (rid, proposal, summary, now()))
    audit(db, g.user["id"], "rewrite proposed", rid)
    flash("A clearer version is ready for your review.", "success")
    return redirect(url_for("insights") + f"#{rid}")


@app.route("/insights/<rid>/rewrite", methods=["POST"])
@roles("admin")
def rewrite(rid):
    db = get_db()
    action = request.form.get("action")
    text = request.form.get("text", "").strip()
    rw = q(db, "SELECT * FROM rule_rewrites WHERE rule_id=? AND status='proposed' ORDER BY id DESC LIMIT 1", (rid,), one=True)
    if action == "approve" and text:
        ex(db, "UPDATE rules SET text=?, updated_at=? WHERE id=?", (text, now(), rid))
        if rw:
            ex(db, "UPDATE rule_rewrites SET status='approved', decided_by=? WHERE id=?", (g.user["id"], rw["id"]))
        else:
            ex(db, "INSERT INTO rule_rewrites(rule_id,proposal,summary,status,created_at,decided_by) "
                   "VALUES(?,?,?,'approved',?,?)", (rid, text, "Written by an admin", now(), g.user["id"]))
        audit(db, g.user["id"], "rule rewritten", rid)
        flash(f"{rid} updated. Future checks use the clearer wording.", "success")
    elif action == "dismiss" and rw:
        ex(db, "UPDATE rule_rewrites SET status='dismissed', decided_by=? WHERE id=?", (g.user["id"], rw["id"]))
        flash("Proposal dismissed. The rule is unchanged.", "info")
    return redirect(url_for("insights") + f"#{rid}")


# ---------- reliability ----------

@app.route("/reliability", methods=["GET", "POST"])
@login_required
def reliability():
    db = get_db()
    if request.method == "POST":
        running = q(db, "SELECT 1 FROM reliability_runs WHERE status='running'", one=True)
        if not running:
            testset.start(db, use_ai=bool(request.form.get("with_ai")) and ai.available(), db_path=DB_PATH)
        return redirect(url_for("reliability"))
    import json
    run = q(db, "SELECT * FROM reliability_runs ORDER BY id DESC LIMIT 1", one=True)
    results = json.loads(run["results_json"]) if run and run["results_json"] else []
    summary = json.loads(run["summary_json"]) if run and run["summary_json"] else None
    return render_template("reliability.html", run=run, results=results, summary=summary,
                           total=len(testset.TESTSET))


# ---------- trust & admin ----------

@app.route("/trust")
def trust():
    return render_template("trust.html", terms_count=len(setting(get_db(), "confidential_terms", [])))


@app.route("/audit")
@roles("admin")
def audit_view():
    log = q(get_db(), "SELECT a.*, u.name FROM audit_log a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 200")
    return render_template("audit.html", log=log)


@app.route("/reset", methods=["POST"])
@roles("admin")
def reset_demo():
    db = get_db()
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        db.execute(f"DROP TABLE IF EXISTS {name}")
    db.commit()
    seed(db)
    session.clear()
    flash("Demo data has been reset to its starting state.", "success")
    return redirect(url_for("home"))


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403, msg="Your role can't open this page. Switch to another demo account."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, msg="That page doesn't exist."), 404


@app.errorhandler(413)
def too_large(_e):
    return render_template("error.html", code=413, msg="That file is larger than 5 MB."), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))
