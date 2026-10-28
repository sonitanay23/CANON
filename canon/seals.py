"""Signed approval seals and public verification (Round 3, improvement 2).

When a piece is approved, Canon issues a seal: a short code plus an HMAC-SHA256
signature over the content ID, version, a hash of the exact approved text and
a hash of the approved image. Anyone can check a seal code, paste text or
upload an image on the public verify page and learn whether it is:

  approved   - exactly what was approved (seal valid)
  outdated   - it was approved, but a newer version replaced it
  modified   - close to approved content, but changed (with a diff)
  unknown    - nothing like it was approved

The signature means a seal can't be forged or edited in the database without
the server's secret, and changing one character of the text breaks the match.
"""
import base64
import difflib
import hashlib
import hmac
import os
import re

from .db import ex, latest_version, now, q

QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
MODIFIED_MIN = 0.55   # word-level similarity needed to call something a modified copy


def _secret():
    return (os.environ.get("SEAL_SECRET") or os.environ.get("SECRET_KEY") or "canon-dev-secret-change-me").encode()


def normalise(text):
    """Whitespace and quote style don't change meaning; everything else does."""
    return re.sub(r"\s+", " ", (text or "").translate(QUOTES)).strip()


def text_sha(text):
    return hashlib.sha256(normalise(text).encode()).hexdigest()


def _payload(content_id, version_n, tsha, isha, issued_at):
    return f"{content_id}|{version_n}|{tsha}|{isha or '-'}|{issued_at}"


def sign(content_id, version_n, tsha, isha, issued_at):
    return hmac.new(_secret(), _payload(content_id, version_n, tsha, isha, issued_at).encode(), hashlib.sha256).hexdigest()


def _code(signature, conn):
    raw = base64.b32encode(bytes.fromhex(signature)).decode().replace("=", "")
    for i in range(0, len(raw) - 8):
        code = f"VLM-{raw[i:i + 4]}-{raw[i + 4:i + 8]}"
        if not q(conn, "SELECT 1 FROM seals WHERE code=?", (code,), one=True):
            return code
    raise RuntimeError("could not make a unique seal code")


def issue(conn, content_id, user, issued_at=None):
    v = latest_version(conn, content_id)
    asset = q(conn, "SELECT * FROM assets WHERE content_id=? ORDER BY n DESC LIMIT 1", (content_id,), one=True)
    issued_at = issued_at or now()
    tsha = text_sha(v["body"])
    isha = asset["sha"] if asset else None
    sig = sign(content_id, v["n"], tsha, isha, issued_at)
    supersede(conn, content_id)
    code = _code(sig, conn)
    ex(conn, "INSERT INTO seals(code,content_id,version_n,text_sha,image_sha,image_phash,signature,issued_by,issued_at,status) "
             "VALUES(?,?,?,?,?,?,?,?,?,'valid')",
       (code, content_id, v["n"], tsha, isha, asset["phash"] if asset else None, sig, user["id"], issued_at))
    return code


def supersede(conn, content_id):
    ex(conn, "UPDATE seals SET status='superseded' WHERE content_id=? AND status='valid'", (content_id,))


def signature_ok(seal):
    expected = sign(seal["content_id"], seal["version_n"], seal["text_sha"], seal["image_sha"], seal["issued_at"])
    return hmac.compare_digest(expected, seal["signature"])


def _details(conn, seal):
    c = q(conn, "SELECT c.*, u.name AS author_name, u.team AS author_team FROM content c "
                "JOIN users u ON u.id=c.author_id WHERE c.id=?", (seal["content_id"],), one=True)
    v = q(conn, "SELECT * FROM versions WHERE content_id=? AND n=?", (seal["content_id"], seal["version_n"]), one=True)
    approver = q(conn, "SELECT name FROM users WHERE id=?", (seal["issued_by"],), one=True)
    return {"seal": seal, "content": c, "text": v["body"] if v else "", "approver": approver["name"] if approver else "",
            "signature_ok": signature_ok(seal)}


def by_code(conn, code):
    code = (code or "").strip().upper()
    if not re.fullmatch(r"VLM-[A-Z2-7]{4}-[A-Z2-7]{4}", code):
        return None
    seal = q(conn, "SELECT * FROM seals WHERE code=?", (code,), one=True)
    return _details(conn, seal) if seal else None


def _verdict(d):
    if not d["signature_ok"]:
        return "tampered"
    return "approved" if d["seal"]["status"] == "valid" else "outdated"


def check_code(conn, code):
    d = by_code(conn, code)
    if not d:
        return {"verdict": "unknown", "reason": "No seal with that code exists."}
    return dict(d, verdict=_verdict(d))


def word_diff_ops(a, b):
    ta, tb = re.findall(r"\S+|\s+", a), re.findall(r"\S+|\s+", b)
    return ta, tb, difflib.SequenceMatcher(None, ta, tb, autojunk=False)


def check_text(conn, text):
    """Is this text approved content? Exact match first, then the closest sealed piece."""
    if not normalise(text):
        return {"verdict": "empty"}
    tsha = text_sha(text)
    exact = q(conn, "SELECT * FROM seals WHERE text_sha=? ORDER BY status='valid' DESC, issued_at DESC", (tsha,))
    if exact:
        d = _details(conn, exact[0])
        return dict(d, verdict=_verdict(d))
    best, best_ratio = None, 0.0
    norm = normalise(text)
    for seal in q(conn, "SELECT * FROM seals WHERE status='valid'"):
        v = q(conn, "SELECT body FROM versions WHERE content_id=? AND n=?", (seal["content_id"], seal["version_n"]), one=True)
        if not v:
            continue
        approved = normalise(v["body"])
        if len(norm) >= 40 and norm in approved:
            d = _details(conn, seal)
            return dict(d, verdict="excerpt")
        ratio = difflib.SequenceMatcher(None, approved.split(), norm.split(), autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = seal, ratio
    if best and best_ratio >= MODIFIED_MIN:
        d = _details(conn, best)
        return dict(d, verdict="modified", similarity=round(best_ratio * 100), submitted=text)
    return {"verdict": "unknown", "reason": "Nothing approved by this brand matches that text."}


def check_image(conn, data, read_text=None):
    """Exact file match, else a near-identical image whose text we compare."""
    from . import vision
    sha = vision.sha(data)
    exact = q(conn, "SELECT * FROM seals WHERE image_sha=? ORDER BY status='valid' DESC, issued_at DESC", (sha,))
    if exact:
        d = _details(conn, exact[0])
        return dict(d, verdict=_verdict(d), kind="image")
    im = vision.load(data)
    ph = vision.dhash(im)
    near = [(vision.hash_distance(ph, s["image_phash"]), s) for s in q(conn, "SELECT * FROM seals WHERE status='valid' "
                                                                               "AND image_phash IS NOT NULL")]
    near = sorted([n for n in near if n[0] <= 10], key=lambda n: n[0])
    if not near:
        return {"verdict": "unknown", "kind": "image", "reason": "This image doesn't match any approved artwork."}
    seal = near[0][1]
    d = _details(conn, seal)
    approved_asset = q(conn, "SELECT * FROM assets WHERE content_id=? AND sha=?", (seal["content_id"], seal["image_sha"]), one=True)
    approved_text = (approved_asset["ocr_text"] or "") if approved_asset else ""
    lines = read_text(im) if read_text else None
    if lines is None:
        return dict(d, verdict="similar", kind="image",
                    reason="It looks like approved artwork, but it isn't the same file and its text couldn't be read to compare.")
    uploaded_text = "\n".join(lines)
    if normalise(uploaded_text) == normalise(approved_text):
        return dict(d, verdict="approved_copy", kind="image")
    return dict(d, verdict="modified", kind="image", approved_text=approved_text, submitted=uploaded_text,
                similarity=round(difflib.SequenceMatcher(None, normalise(approved_text).split(),
                                                         normalise(uploaded_text).split()).ratio() * 100))
