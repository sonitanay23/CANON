"""Canon: brand governance before you publish.

Flask app: server-rendered pages, SQLite, rule engine + AI review (Gemini or Claude).
Run locally:  ./start.sh   (or: python app.py, then open http://localhost:5000)
"""
import difflib
import html
import io
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from statistics import median

from flask import Flask, Response, abort, flash, g, redirect, render_template, request, session, url_for
from markupsafe import Markup

from canon import env  # noqa: F401  (loads .env before anything reads settings)
from canon import ai, charts, seals, services, testset, vision
from canon.db import (DB_PATH, add_version, audit, close_db, connect, ex, get_db, latest_version, now, q,
                      setting, visible_facts)
from canon.seed import seed

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "canon-dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024
MAX_BODY = 20000
MAX_TITLE = 120
app.teardown_appcontext(close_db)

with app.app_context():
    _c = connect()
    seed(_c)
    _c.close()

CHANNELS = ["LinkedIn", "Instagram", "Email", "Website", "Blog", "Ad", "Push", "Sales", "Help centre"]
STATUS_LABELS = {"draft": "Draft", "in_review": "In review", "changes_requested": "Changes requested",
                 "approved": "Approved"}
ROLE_LABELS = {"admin": "Brand admin", "reviewer": "Reviewer", "creator": "Creator", "agency": "Agency creator"}
USER_ORDER = ("SELECT * FROM users ORDER BY CASE role WHEN 'creator' THEN 0 WHEN 'reviewer' THEN 1 "
              "WHEN 'admin' THEN 2 ELSE 3 END, team")


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


def avatar(u):
    kind = "legal" if (u["team"] or "").startswith("Legal") else u["role"]
    parts = (u["name"] or "?").split()
    return Markup(f'<span class="avatar {kind}">{html.escape(parts[0][0] + parts[-1][0])}</span>')


def role_label(u):
    if u["role"] == "reviewer" and (u["team"] or "").startswith("Legal"):
        return "Legal reviewer"
    return ROLE_LABELS.get(u["role"], u["role"])


def queue_count():
    u = g.get("user")
    if not u or u["role"] not in ("admin", "reviewer"):
        return 0
    return q(get_db(), "SELECT COUNT(*) n FROM content WHERE status='in_review'", one=True)["n"]


@app.context_processor
def inject():
    return {"me": g.get("user"), "ai_on": ai.available(), "ai_model": ai.label(), "ai_provider": ai.provider(),
            "status_labels": STATUS_LABELS, "role_labels": ROLE_LABELS, "role_label": role_label, "avatar": avatar,
            "queue_count": queue_count(),
            "lanes": services.LANES,
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
        "drafts": q(db, "SELECT COUNT(*) n FROM content WHERE status!='archived'", one=True)["n"],
        "seals": q(db, "SELECT COUNT(*) n FROM seals WHERE status='valid'", one=True)["n"],
        "approved": q(db, "SELECT COUNT(*) n FROM content WHERE status='approved'", one=True)["n"],
        "rules": q(db, "SELECT COUNT(*) n FROM rules WHERE active=1", one=True)["n"],
        "facts": q(db, "SELECT COUNT(*) n FROM facts", one=True)["n"],
    }
    return render_template("home.html", stats=stats, users=q(db, USER_ORDER))


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
        if nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        return redirect(landing(u))
    return render_template("login.html", users=q(db, USER_ORDER), next=request.args.get("next", ""))


def landing(u):
    return url_for({"agency": "partner", "reviewer": "queue"}.get(u["role"], "content_list"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/content")
@login_required
def content_list():
    db = get_db()
    sql = ("SELECT c.*, u.name AS author_name, u.team AS author_team, "
           "(SELECT COUNT(*) FROM findings f WHERE f.content_id=c.id AND f.status='open') AS open_issues, "
           "(SELECT COUNT(*) FROM check_runs r WHERE r.content_id=c.id) AS checks, "
           "(SELECT id FROM assets a WHERE a.content_id=c.id ORDER BY n DESC LIMIT 1) AS asset_id "
           "FROM content c JOIN users u ON u.id=c.author_id WHERE c.status!='archived' ")
    if g.user["role"] in ("admin", "reviewer"):
        rows = q(db, sql + "ORDER BY c.updated_at DESC")
    else:
        rows = q(db, sql + "AND c.author_id=? ORDER BY c.updated_at DESC", (g.user["id"],))
    groups = {k: [r for r in rows if r["status"] == k] for k in STATUS_LABELS}
    return render_template("content_list.html", groups=groups, total=len(rows))


@app.route("/content/new", methods=["GET", "POST"])
@roles("creator", "agency", "admin")
def content_new():
    db = get_db()
    brief = None
    brief_id = request.values.get("brief", type=int)
    if brief_id:
        brief = q(db, "SELECT * FROM briefs WHERE id=?", (brief_id,), one=True)
    if request.method == "POST":
        form = request.form
        title = form.get("title", "").strip()[:MAX_TITLE] or "Untitled draft"
        channel = form.get("channel") if form.get("channel") in CHANNELS else "Website"
        body = form.get("body", "").replace("\r\n", "\n").strip()
        upload = request.files.get("pdf")
        image = request.files.get("image")
        image_data = None

        def back(msg):
            flash(msg, "error")
            return render_template("content_new.html", channels=CHANNELS, brief=brief, form=form), 400
        if upload and upload.filename:
            if not upload.filename.lower().endswith(".pdf"):
                return back("Only PDF files can be uploaded as documents. You can also paste text.")
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(upload.read()))
                body = "\n\n".join((p.extract_text() or "").strip() for p in reader.pages).strip()
            except Exception:
                return back("That PDF could not be read. Try pasting the text instead.")
            if not body:
                return back("No text found in that PDF (it may be a scanned image). Upload it as an image instead.")
        if image and image.filename:
            image_data = image.read()
            try:
                vision.load(image_data)
            except vision.BadImage as e:
                return back(str(e))
        if not body and not image_data:
            return back("Add some text, upload a PDF, or add an image.")
        if len(body) > MAX_BODY:
            return back(f"That's {len(body):,} characters. Canon checks up to {MAX_BODY:,} at a time; split it into parts.")
        cid = ex(db, "INSERT INTO content(title,channel,author_id,status,created_at,updated_at,brief_id) VALUES(?,?,?,?,?,?,?)",
                 (title, channel, g.user["id"], "draft", now(), now(), brief["id"] if brief else None))
        add_version(db, cid, body, g.user["id"], note="First draft")
        audit(db, g.user["id"], "created draft", f"content {cid}")
        if image_data:
            services.add_asset(db, cid, image_data, image.filename, form.get("alt", ""), g.user)
        services.run_check(db, cid, g.user)
        return redirect(url_for("content_view", cid=cid))
    return render_template("content_new.html", channels=CHANNELS, brief=brief, form={})


@app.route("/content/<int:cid>")
@login_required
def content_view(cid):
    db = get_db()
    c = content_or_404(cid)
    v = latest_version(db, cid)
    findings = q(db, "SELECT * FROM findings WHERE content_id=? AND status='open' ORDER BY id", (cid,))
    # number and list issues in reading order, like notes in the margin of a proof
    body = v["body"]

    def position(f):
        at = body.find(f["quote"])
        return (1 if at == -1 else 0, at, f["id"])
    text_findings = sorted([f for f in findings if (f["target"] or "text") == "text"], key=position)
    image_findings = sorted([f for f in findings if f["target"] == "image"],
                            key=lambda f: (json.loads(f["box"])[1] if f["box"] else 99999, f["id"]))
    findings = text_findings + image_findings
    asset = services.latest_asset(db, cid)
    visual = json.loads(asset["visual_json"]) if asset and asset["visual_json"] else None
    seal = q(db, "SELECT * FROM seals WHERE content_id=? ORDER BY issued_at DESC LIMIT 1", (cid,), one=True)
    assignee = q(db, "SELECT * FROM users WHERE id=?", (c["assignee_id"],), one=True) if c["assignee_id"] else None
    brief = q(db, "SELECT * FROM briefs WHERE id=?", (c["brief_id"],), one=True) if c["brief_id"] else None
    handled = q(db, "SELECT * FROM findings WHERE content_id=? AND status IN ('accepted','edited','rejected','resolved') "
                    "ORDER BY id DESC", (cid,))
    run = q(db, "SELECT * FROM check_runs WHERE content_id=? ORDER BY id DESC LIMIT 1", (cid,), one=True)
    sources = {f["ref"]: services.source_text(db, f["ref"]) for f in list(findings) + list(handled)}
    comments = q(db, "SELECT cm.*, u.name, u.role, u.team FROM comments cm JOIN users u ON u.id=cm.user_id "
                     "WHERE content_id=? ORDER BY cm.id", (cid,))
    stale = run is None or run["version_n"] != v["n"]
    score = max(0, 100 - sum({"high": 12, "medium": 6, "low": 2}.get(f["severity"], 4) for f in findings))
    return render_template("content_view.html", c=c, v=v, findings=findings, handled=handled, run=run,
                           sources=sources, segs=highlight(v["body"], text_findings), word_diff=word_diff,
                           comments=comments, stale=stale, asset=asset, visual=visual, seal=seal,
                           assignee=assignee, brief=brief, score=score, boxes=boxes_for(image_findings, asset),
                           risk_reasons=json.loads(c["risk_reasons"]) if c["risk_reasons"] else [],
                           can_approve=services.can_approve(g.user, c), json=json)


def boxes_for(findings, asset):
    """Image findings as percentage boxes for the overlay on the artwork."""
    if not asset:
        return []
    out = []
    for i, f in enumerate(findings):
        if not f["box"]:
            continue
        x0, y0, x1, y1 = json.loads(f["box"])
        W, H = asset["width"] or 1, asset["height"] or 1
        out.append({"id": f["id"], "sev": f["severity"], "left": x0 / W * 100, "top": y0 / H * 100,
                    "w": (x1 - x0) / W * 100, "h": (y1 - y0) / H * 100})
    return out


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
    body = request.form.get("body", "").replace("\r\n", "\n").strip()
    db = get_db()
    if len(body) > MAX_BODY:
        flash(f"Canon checks up to {MAX_BODY:,} characters at a time.", "error")
        return redirect(url_for("content_view", cid=cid))
    if not body and not services.latest_asset(db, cid):
        flash("The text can't be empty.", "error")
        return redirect(url_for("content_view", cid=cid))
    if body != latest_version(db, cid)["body"]:
        add_version(db, cid, body, g.user["id"], note="Edited by hand")
        services.run_check(db, cid, g.user)
        flash("Saved as a new version and re-checked.", "success")
    else:
        flash("No changes to save.", "info")
    return redirect(url_for("content_view", cid=cid))


@app.route("/content/<int:cid>/image", methods=["POST"])
@login_required
def content_image(cid):
    c = content_or_404(cid)
    if c["status"] == "approved" or (c["author_id"] != g.user["id"] and g.user["role"] != "admin"):
        abort(403)
    db = get_db()
    up = request.files.get("image")
    if not up or not up.filename:
        flash("Choose an image to upload.", "error")
        return redirect(url_for("content_view", cid=cid))
    try:
        services.add_asset(db, cid, up.read(), up.filename, request.form.get("alt") or None, g.user)
    except vision.BadImage as e:
        flash(str(e), "error")
        return redirect(url_for("content_view", cid=cid))
    n, status = services.run_check(db, cid, g.user)
    flash(f"New image saved and checked: {n} issue{'s' if n != 1 else ''} found.", "success")
    return redirect(url_for("content_view", cid=cid))


@app.route("/content/<int:cid>/imagetext", methods=["POST"])
@login_required
def content_imagetext(cid):
    """Let a person correct what the text reader saw in the image (or type it when no reader is available)."""
    c = content_or_404(cid)
    if c["status"] == "approved" or (c["author_id"] != g.user["id"] and g.user["role"] != "admin"):
        abort(403)
    db = get_db()
    asset = services.latest_asset(db, cid)
    if not asset:
        abort(404)
    text = request.form.get("ocr_text", "").replace("\r\n", "\n").strip()[:4000]
    old = {ln["text"]: ln.get("box") for ln in json.loads(asset["ocr_lines"] or "[]")}
    lines = [{"text": t.strip(), "box": old.get(t.strip())} for t in text.split("\n") if t.strip()]
    visual = json.loads(asset["visual_json"] or "{}")
    visual["lines"] = lines
    ex(db, "UPDATE assets SET ocr_text=?, ocr_lines=?, ocr_method='edited', visual_json=? WHERE id=?",
       ("\n".join(ln["text"] for ln in lines), json.dumps(lines), json.dumps(visual), asset["id"]))
    audit(db, g.user["id"], "image text corrected", f"content {cid}")
    services.run_check(db, cid, g.user)
    flash("Image text saved and re-checked.", "success")
    return redirect(url_for("content_view", cid=cid))


@app.route("/asset/<int:aid>")
def asset_file(aid):
    db = get_db()
    a = q(db, "SELECT * FROM assets WHERE id=?", (aid,), one=True)
    if not a:
        abort(404)
    public = q(db, "SELECT 1 FROM seals WHERE content_id=? AND image_sha=?", (a["content_id"], a["sha"]), one=True)
    if not public:
        if not g.user:
            abort(404)
        content_or_404(a["content_id"])
    return Response(a["data"], mimetype=a["mime"], headers={"Cache-Control": "private, max-age=3600"})


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
    alt = f["action"] == "alt"
    flash(err or {"accept": "Alt text added." if alt else "Fix applied. A new version was saved.",
                  "edit": "Your alt text was saved." if alt else "Your edited fix was applied.",
                  "reject": "Kept the original. Your reason was recorded."}[action],
          "error" if err else "success")
    return redirect(url_for("content_view", cid=c["id"]) + "#issues")


@app.route("/finding/<int:fid>/regenerate", methods=["POST"])
@login_required
def finding_regenerate(fid):
    db = get_db()
    f = q(db, "SELECT * FROM findings WHERE id=?", (fid,), one=True)
    if not f:
        abort(404)
    c = content_or_404(f["content_id"])
    if f["status"] != "open":
        flash("That issue was already handled.", "info")
        return redirect(url_for("content_view", cid=c["id"]))
    intent = request.form.get("intent", "").strip()[:300] or f["intent"]
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
        ex(db, "UPDATE content SET status='in_review', updated_at=?, submitted_at=? WHERE id=?", (now(), now(), cid))
        lane = services.route(db, cid)
        audit(db, g.user["id"], "submitted for review", f"content {cid} ({lane} lane)")
        flash({"fast": "Sent to review in the fast lane: low risk, any reviewer can approve it in one click.",
               "standard": "Sent to the standard review queue.",
               "legal": "Sent to Legal review because it makes financial claims. Meera Iyer (Legal) will review it."}[lane],
              "success")
    elif action == "approve" and c["status"] == "in_review" and services.can_approve(g.user, c):
        if open_high and not request.form.get("confirm"):
            flash(f"{open_high} high-severity issue(s) are still open. Resolve them, or confirm to approve anyway.", "error")
            return redirect(url_for("content_view", cid=cid))
        code = services.approve(db, cid, g.user)
        flash(f"Approved and sealed ({code}). Anyone can now verify this piece on the public verify page.", "success")
    elif action == "approve" and c["status"] == "in_review":
        flash("This piece makes financial claims, so it was routed to Legal. Only Legal or a brand admin can approve it.",
              "error")
    elif action == "request_changes" and g.user["role"] in ("admin", "reviewer") and c["status"] == "in_review":
        note = request.form.get("note", "").strip()[:2000]
        ex(db, "UPDATE content SET status='changes_requested', updated_at=? WHERE id=?", (now(), cid))
        if note:
            ex(db, "INSERT INTO comments(content_id,user_id,body,created_at) VALUES(?,?,?,?)", (cid, g.user["id"], note, now()))
        audit(db, g.user["id"], "requested changes", f"content {cid}")
        flash("Sent back to the creator with your note.", "success")
    elif c["status"] == "approved" and action == "approve":
        flash("This piece is already approved.", "info")
    else:
        flash("That action isn't available for this item any more. The page has been refreshed.", "info")
    return redirect(url_for("content_view", cid=cid))


@app.route("/content/<int:cid>/comment", methods=["POST"])
@login_required
def content_comment(cid):
    content_or_404(cid)
    body = request.form.get("body", "").strip()[:2000]
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
    logos = q(db, "SELECT id, name, status, created_at FROM logos ORDER BY status, id")
    return render_template("brand.html", rules=rules, facts=facts, changes=changes, hidden=hidden,
                           voice=setting(db, "voice", ""), terms=setting(db, "confidential_terms", []),
                           today=now()[:10], logos=logos)


@app.route("/logo/<int:lid>.png")
def logo_file(lid):
    lg = q(get_db(), "SELECT * FROM logos WHERE id=?", (lid,), one=True)
    if not lg:
        abort(404)
    return Response(lg["data"], mimetype=lg["mime"], headers={"Cache-Control": "public, max-age=3600"})


@app.route("/logos", methods=["POST"])
@roles("admin")
def logo_new():
    db = get_db()
    up = request.files.get("logo")
    name = request.form.get("name", "").strip()[:80]
    status = request.form.get("status") if request.form.get("status") in ("current", "retired") else "retired"
    if not up or not up.filename or not name:
        flash("Give the logo a name and choose an image file.", "error")
        return redirect(url_for("brand") + "#logos")
    data = up.read()
    try:
        im = vision.load(data)
    except vision.BadImage as e:
        flash(str(e), "error")
        return redirect(url_for("brand") + "#logos")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    ex(db, "INSERT INTO logos(name,status,mime,data,created_at) VALUES(?,?,?,?,?)",
       (name, status, "image/png", buf.getvalue(), now()))
    audit(db, g.user["id"], "logo added", f"{name} ({status})")
    flash(f"Logo added as {status}. Every image check from now on looks for it.", "success")
    return redirect(url_for("brand") + "#logos")


@app.route("/logos/<int:lid>", methods=["POST"])
@roles("admin")
def logo_update(lid):
    db = get_db()
    lg = q(db, "SELECT * FROM logos WHERE id=?", (lid,), one=True)
    if not lg:
        abort(404)
    if request.form.get("action") == "delete":
        ex(db, "DELETE FROM logos WHERE id=?", (lid,))
        audit(db, g.user["id"], "logo removed", lg["name"])
        flash("Logo removed.", "success")
    else:
        new = "current" if lg["status"] == "retired" else "retired"
        ex(db, "UPDATE logos SET status=? WHERE id=?", (new, lid))
        audit(db, g.user["id"], "logo status changed", f"{lg['name']}: {new}")
        flash(f"{lg['name']} is now marked {new}.", "success")
    return redirect(url_for("brand") + "#logos")


@app.route("/facts/<fid>", methods=["POST"])
@roles("admin")
def fact_update(fid):
    db = get_db()
    f = q(db, "SELECT * FROM facts WHERE id=?", (fid,), one=True)
    if not f:
        abort(404)
    expires = request.form.get("expires_on", "").strip() or None
    if expires and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires):
        flash("Expiry dates use the format YYYY-MM-DD.", "error")
        return redirect(url_for("brand") + "#facts")
    if expires != f["expires_on"]:
        ex(db, "UPDATE facts SET expires_on=?, updated_at=? WHERE id=?", (expires, now(), fid))
        audit(db, g.user["id"], "fact expiry changed", f"{fid}: {f['expires_on']} -> {expires}")
    new_value = request.form.get("value", "").strip()[:80]
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
        text = request.form.get("text", "").strip()[:600]
        if not text:
            flash("A rule needs a description.", "error")
            return redirect(url_for("brand") + "#rules")
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
        if sent:
            flash(f"{sent} updated piece{'s' if sent != 1 else ''} re-checked and sent to the review queue. "
                  "Their old approval seals now show as outdated.", "success")
        else:
            flash("Nothing was sent. Either nothing was selected, or these pieces were already updated.", "info")
        return redirect(url_for("queue"))
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


# ---------- improvement 3: risk-based review queue ----------

def hours_since(ts):
    try:
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return 0
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600


@app.route("/queue")
@roles("admin", "reviewer")
def queue():
    db = get_db()
    for r in q(db, "SELECT id FROM content WHERE status='in_review' AND lane IS NULL"):
        services.route(db, r["id"])  # submitted before routing existed
    rows = q(db, "SELECT c.*, u.name AS author_name, u.team AS author_team, a.name AS assignee_name, "
                 "(SELECT COUNT(*) FROM findings f WHERE f.content_id=c.id AND f.status='open') AS open_issues, "
                 "(SELECT COUNT(*) FROM assets x WHERE x.content_id=c.id) AS images "
                 "FROM content c JOIN users u ON u.id=c.author_id LEFT JOIN users a ON a.id=c.assignee_id "
                 "WHERE c.status='in_review' ORDER BY c.risk DESC, c.submitted_at")
    items = []
    for r in rows:
        waited = hours_since(r["submitted_at"] or r["updated_at"])
        sla = services.LANES[r["lane"]]["sla_hours"]
        items.append({"c": r, "reasons": json.loads(r["risk_reasons"] or "[]"), "waited": waited,
                      "overdue": waited > sla, "mine": r["assignee_id"] == g.user["id"],
                      "can": services.can_approve(g.user, r)})
    by_lane = {k: [i for i in items if i["c"]["lane"] == k] for k in ("legal", "standard", "fast")}
    mine = sum(1 for i in items if i["mine"])
    return render_template("queue.html", by_lane=by_lane, total=len(items), mine=mine)


@app.route("/queue/fast-approve", methods=["POST"])
@roles("admin", "reviewer")
def fast_approve():
    db = get_db()
    done = []
    for cid in request.form.getlist("cid"):
        c = q(db, "SELECT * FROM content WHERE id=?", (cid,), one=True)
        if not c or c["status"] != "in_review" or c["lane"] != "fast" or not services.can_approve(g.user, c):
            continue
        open_n = q(db, "SELECT COUNT(*) n FROM findings WHERE content_id=? AND status='open'", (cid,), one=True)["n"]
        if open_n:
            continue
        done.append(services.approve(db, c["id"], g.user))
    if done:
        flash(f"Approved and sealed {len(done)} fast-lane piece{'s' if len(done) != 1 else ''}.", "success")
    else:
        flash("Nothing was approved. Select at least one fast-lane piece with no open issues.", "info")
    return redirect(url_for("queue"))


# ---------- improvement 2: partner workspace and briefs ----------

@app.route("/partner")
@roles("agency", "admin")
def partner():
    db = get_db()
    team = g.user["team"] if g.user["role"] == "agency" else "BrightLeaf Studio (agency)"
    briefs = q(db, "SELECT b.*, (SELECT COUNT(*) FROM content c WHERE c.brief_id=b.id) AS drafts FROM briefs b "
                   "WHERE team=? ORDER BY status='done', due_on", (team,))
    author_ids = [u["id"] for u in q(db, "SELECT id FROM users WHERE team=?", (team,))]
    marks = ",".join("?" * len(author_ids)) or "''"
    work = q(db, f"SELECT c.*, (SELECT COUNT(*) FROM findings f WHERE f.content_id=c.id AND f.status='open') AS open_issues, "
                 f"(SELECT body FROM comments m WHERE m.content_id=c.id AND m.user_id!=c.author_id ORDER BY m.id DESC LIMIT 1) AS feedback, "
                 f"(SELECT code FROM seals s WHERE s.content_id=c.id AND s.status='valid') AS seal "
                 f"FROM content c WHERE c.author_id IN ({marks}) AND c.status!='archived' ORDER BY c.updated_at DESC", author_ids)
    facts = visible_facts(db, "agency")
    hidden = q(db, "SELECT COUNT(*) n FROM facts WHERE visibility='internal'", one=True)["n"]
    rules = q(db, "SELECT * FROM rules WHERE active=1 ORDER BY id")
    logos = q(db, "SELECT id, name, status FROM logos ORDER BY status")
    decided = q(db, f"SELECT f.status FROM findings f JOIN content c ON c.id=f.content_id WHERE c.author_id IN ({marks}) "
                    f"AND c.status!='archived' AND f.status IN ('accepted','edited','rejected','resolved')", author_ids)
    stats = {"briefs": sum(1 for b in briefs if b["status"] == "open"),
             "in_review": sum(1 for w in work if w["status"] == "in_review"),
             "approved": sum(1 for w in work if w["status"] == "approved"),
             "fixed": sum(1 for d in decided if d["status"] != "rejected")}
    return render_template("partner.html", team=team, briefs=briefs, work=work, facts=facts, hidden=hidden,
                           rules=rules, logos=logos, voice=setting(db, "voice", ""), stats=stats, today=now()[:10])


@app.route("/briefs", methods=["GET", "POST"])
@roles("admin", "reviewer")
def briefs():
    db = get_db()
    if request.method == "POST":
        if g.user["role"] != "admin":
            abort(403)
        title = request.form.get("title", "").strip()[:MAX_TITLE]
        body = request.form.get("body", "").strip()[:2000]
        due = request.form.get("due_on", "").strip()
        channel = request.form.get("channel") if request.form.get("channel") in CHANNELS else "Instagram"
        if not title or not body:
            flash("A brief needs a title and what you want made.", "error")
            return redirect(url_for("briefs"))
        if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            due = ""
        ex(db, "INSERT INTO briefs(title,body,channel,team,due_on,created_by,created_at,status) VALUES(?,?,?,?,?,?,?,'open')",
           (title, body, channel, "BrightLeaf Studio (agency)", due or None, g.user["id"], now()))
        audit(db, g.user["id"], "brief sent", title)
        flash("Brief sent to BrightLeaf Studio. It appears in their partner workspace now.", "success")
        return redirect(url_for("briefs"))
    rows = q(db, "SELECT b.*, (SELECT COUNT(*) FROM content c WHERE c.brief_id=b.id) AS drafts FROM briefs b ORDER BY b.id DESC")
    return render_template("briefs.html", briefs=rows, channels=CHANNELS, today=now()[:10])


@app.route("/briefs/<int:bid>/done", methods=["POST"])
@roles("admin")
def brief_done(bid):
    db = get_db()
    if q(db, "SELECT 1 FROM briefs WHERE id=?", (bid,), one=True):
        ex(db, "UPDATE briefs SET status=CASE status WHEN 'open' THEN 'done' ELSE 'open' END WHERE id=?", (bid,))
    return redirect(url_for("briefs"))


# ---------- improvement 3: governance insights ----------

CATEGORIES = {
    "Claims & facts": lambda r: r.startswith("F-") or r in ("R-01", "R-02", "R-03", "R-10", "R-13"),
    "Disclaimers": lambda r: r in ("R-07", "R-08"),
    "Voice & wording": lambda r: r in ("R-04", "R-05", "R-06", "R-09", "R-11", "R-12"),
    "Visual & accessibility": lambda r: r in ("R-14", "R-15", "R-16"),
}


def category(ref):
    return next((k for k, fn in CATEGORIES.items() if fn(ref)), "Voice & wording")


@app.route("/governance")
@roles("admin", "reviewer")
def governance():
    db = get_db()
    today = datetime.now(timezone.utc).date()
    week_starts = [today - timedelta(days=today.weekday()) - timedelta(weeks=w) for w in range(8, -1, -1)]
    labels = [d.strftime("%d %b") for d in week_starts]

    def week_index(ts):
        try:
            d = datetime.strptime(ts[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None
        for i in range(len(week_starts) - 1, -1, -1):
            if d >= week_starts[i]:
                return i
        return None
    findings = q(db, "SELECT f.ref, f.status, f.created_at, f.content_id, u.team FROM findings f "
                     "JOIN content c ON c.id=f.content_id JOIN users u ON u.id=c.author_id WHERE f.status!='superseded'")
    by_cat = {k: [0] * len(labels) for k in CATEGORIES}
    accepted, decided = [0] * len(labels), [0] * len(labels)
    pieces = [set() for _ in labels]
    for f in findings:
        i = week_index(f["created_at"])
        if i is None:
            continue
        by_cat[category(f["ref"])][i] += 1
        pieces[i].add(f["content_id"])
        if f["status"] in ("accepted", "edited", "resolved", "rejected"):
            decided[i] += 1
            accepted[i] += f["status"] != "rejected"
    acceptance = [round(a / d * 100) if d else None for a, d in zip(accepted, decided)]
    checked = [q(db, "SELECT COUNT(DISTINCT content_id) n FROM findings WHERE created_at>=? AND created_at<?",
                 (str(week_starts[i]), str(week_starts[i] + timedelta(weeks=1))), one=True)["n"] for i in range(len(labels))]
    per_piece = [round(sum(by_cat[k][i] for k in by_cat) / checked[i], 1) if checked[i] else None for i in range(len(labels))]
    top = Counter(f["ref"] for f in findings).most_common(8)
    names = {r["id"]: r["title"] for r in q(db, "SELECT id, title FROM rules")}
    names.update({x["id"]: x["label"] for x in q(db, "SELECT id, label FROM facts")})
    teams = Counter(f["team"] for f in findings)
    lane_times = defaultdict(list)
    for c in q(db, "SELECT lane, submitted_at, approved_at FROM content WHERE approved_at IS NOT NULL AND submitted_at IS NOT NULL"):
        try:
            a = datetime.strptime(c["approved_at"], "%Y-%m-%d %H:%M:%S")
            b = datetime.strptime(c["submitted_at"], "%Y-%m-%d %H:%M:%S")
            lane_times[c["lane"] or "standard"].append(max(0, (a - b).total_seconds() / 3600))
        except (TypeError, ValueError):
            continue
    lanes_out = [{"lane": k, "label": services.LANES[k]["label"], "n": len(lane_times[k]),
                  "median": round(median(lane_times[k]), 1) if lane_times[k] else None,
                  "sla": services.LANES[k]["sla_hours"]} for k in ("fast", "standard", "legal")]
    rel = list(setting(db, "reliability_history", []))
    for r in q(db, "SELECT * FROM reliability_runs WHERE status='done' ORDER BY id"):
        sm = json.loads(r["summary_json"] or "{}")
        if sm:
            rel.append({"date": (r["finished_at"] or "")[:10], "label": "Live run" + (" with AI" if r["with_ai"] else ", rules only"),
                        "detection": round(sm.get("detection", 0) * 100), "false_flags": sm.get("false_flags", 0)})
    total_dec = sum(decided)
    kpi = {"pieces": sum(checked), "issues": sum(sum(v) for v in by_cat.values()),
           "acceptance": round(sum(accepted) / total_dec * 100) if total_dec else None,
           "fast_share": round(len(lane_times["fast"]) / max(1, sum(len(v) for v in lane_times.values())) * 100),
           "sealed": q(db, "SELECT COUNT(*) n FROM seals WHERE status='valid'", one=True)["n"]}
    data = {"teams": [{"team": t, "n": n} for t, n in teams.most_common()], "lanes": lanes_out}
    colours = {"Claims & facts": "#d1361b", "Disclaimers": "#b8650a", "Voice & wording": "#3b5bdb",
               "Visual & accessibility": "#0f8b8d"}
    ramp = ["#3b5bdb", "#4c55e6", "#5d50f2", "#6d4aff", "#8a52f0", "#a85ae0", "#d2645f", "#ff6b4a"]
    figs = {
        "cat": charts.stacked_bars(labels, by_cat, colours, "Issues per week by kind"),
        "cat_legend": charts.legend(list(colours.items())),
        "acc": charts.lines(labels, [("Accepted", acceptance, "#1f8a57", False)], "Suggestion acceptance per week",
                            top=100, suffix="%"),
        "per": charts.lines(labels, [("Issues per piece", per_piece, "#6d4aff", False)], "Issues per piece per week"),
        "top": charts.hbars([(f"{r}  {names.get(r, r)}", n) for r, n in top], ramp, "Most recurring issues"),
        "lane": charts.hbars([(f"{x['label']} (target {x['sla']} h)", x["median"]) for x in lanes_out],
                             ["#1f8a57", "#3b5bdb", "#ff6b4a"], "Median hours to approval by lane", " h"),
        "rel": charts.combo([f"{r['label']} ({r['date'][5:]})" for r in rel],
                            ("False flags", [r["false_flags"] for r in rel], "#ff6b4a"),
                            ("Planted issues caught", [r["detection"] for r in rel], "#6d4aff"),
                            "Detection rate and false flags per reliability run"),
        "rel_legend": charts.legend([("Planted issues caught (left axis)", "#6d4aff"), ("False flags (right axis)", "#ff6b4a")]),
    }
    return render_template("governance.html", data=data, kpi=kpi, figs=figs, unclear=sum(
        1 for st in services.rule_stats(db) if st["state"] == "unclear"))


# ---------- improvement 2: public verification ----------

SAMPLES = {"approved": ("pro-banner.png", "Approved Pro banner"), "edited": ("pro-banner-edited.png", "Pro banner, price edited after approval"),
           "draft": ("diwali-banner.png", "Diwali banner draft (never approved)")}


def _read_upload_text(im):
    lines, method, _alt, _note = vision.read_text(im)
    if method == "none":
        return None
    return [ln["text"] for ln in lines]


@app.route("/verify", methods=["GET", "POST"])
def verify():
    db = get_db()
    result, mode, text, code = None, request.values.get("mode", "text"), "", ""
    if request.method == "POST":
        if mode == "code":
            code = request.form.get("code", "").strip()[:40]
            result = seals.check_code(db, code) if code else {"verdict": "empty"}
        elif mode == "image":
            up = request.files.get("image")
            sample = request.form.get("sample")
            data = None
            if sample in SAMPLES:
                with open(os.path.join(app.static_folder, "demo", SAMPLES[sample][0]), "rb") as fh:
                    data = fh.read()
            elif up and up.filename:
                data = up.read()
            if not data:
                result = {"verdict": "empty"}
            else:
                try:
                    stored = json.load(open(os.path.join(app.static_folder, "demo", "ocr.json")))
                    sample_lines = {k: [ln["text"] for ln in stored.get(v[0][:-4], [])] for k, v in SAMPLES.items()}

                    def reader(im):
                        if sample in SAMPLES and sample_lines.get(sample):
                            return sample_lines[sample]  # the demo samples' text was read once in advance
                        return _read_upload_text(im)
                    result = seals.check_image(db, data, read_text=reader)
                except vision.BadImage as e:
                    result = {"verdict": "bad", "reason": str(e)}
        else:
            text = request.form.get("text", "")[:MAX_BODY]
            result = seals.check_text(db, text)
        audit(db, g.user["id"] if g.user else "public", "verify", f"{mode}: {result.get('verdict')}")
    if result and result.get("verdict") == "modified":
        approved = result.get("approved_text") if result.get("kind") == "image" else result.get("text", "")
        result["diff"] = word_diff(approved or "", result.get("submitted", ""))
    samples_text = q(db, "SELECT v.body FROM seals s JOIN versions v ON v.content_id=s.content_id AND v.n=s.version_n "
                         "JOIN content c ON c.id=s.content_id WHERE s.status='valid' AND c.title='Pricing page blurb'", one=True)
    return render_template("verify.html", result=result, mode=mode, text=text, code=code, samples=SAMPLES,
                           sample_text=samples_text["body"] if samples_text else "")


@app.route("/seal/<code>")
def seal_page(code):
    db = get_db()
    d = seals.by_code(db, code)
    if not d:
        abort(404)
    asset = None
    if d["seal"]["image_sha"]:
        asset = q(db, "SELECT id, alt_text FROM assets WHERE content_id=? AND sha=?", (d["seal"]["content_id"], d["seal"]["image_sha"]), one=True)
    newer = None
    if d["seal"]["status"] != "valid":
        newer = q(db, "SELECT code FROM seals WHERE content_id=? AND status='valid'", (d["seal"]["content_id"],), one=True)
    return render_template("seal.html", d=d, verdict=seals._verdict(d), asset=asset, newer=newer)


@app.route("/seal/<code>/badge.svg")
def seal_badge(code):
    d = seals.by_code(get_db(), code)
    verdict = seals._verdict(d) if d else "unknown"
    colour, label = {"approved": ("#2F7A4E", "Approved"), "outdated": ("#A8620B", "Outdated"),
                     "tampered": ("#C2361F", "Invalid")}.get(verdict, ("#667085", "Unknown"))
    code_txt = html.escape(code.upper()[:14])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="236" height="28" role="img" aria-label="Canon seal {code_txt}: {label}">'
           f'<rect width="236" height="28" rx="6" fill="#1A2233"/><rect x="130" width="106" height="28" rx="6" fill="{colour}"/>'
           f'<rect x="130" width="10" height="28" fill="{colour}"/>'
           f'<text x="12" y="18" fill="#fff" font-family="Verdana,sans-serif" font-size="11">Canon · {code_txt}</text>'
           f'<text x="183" y="18" fill="#fff" font-family="Verdana,sans-serif" font-size="11" font-weight="bold" text-anchor="middle">{label}</text></svg>')
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "no-cache"})


@app.route("/verify/sample/<name>")
def verify_sample(name):
    if name not in SAMPLES:
        abort(404)
    return redirect(url_for("static", filename="demo/" + SAMPLES[name][0]))


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
    return render_template("error.html", code=413, msg="That file is larger than 6 MB. Try a smaller or compressed version."), 413


@app.errorhandler(400)
def bad_request(_e):
    return render_template("error.html", code=400, msg="Something in that request was missing or malformed. Go back and try again."), 400


@app.errorhandler(500)
def server_error(_e):
    return render_template("error.html", code=500, msg="Something went wrong on our side. Your work up to the last save is safe; please try again."), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))
