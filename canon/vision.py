"""Visual and accessibility checks for images (Round 3, improvement 1).

- Reads the text in an image (OCR), so it goes through the same rule engine
  and AI review as typed text.
    1. Tesseract, if the binary is installed (local runs).
    2. Otherwise the AI provider's vision model (Gemini or Claude).
    3. Otherwise nothing: the user types the text in the image themselves.
- Measures text contrast for every line of text it found (WCAG 2.1 AA:
  4.5:1 for normal text, 3:1 for large text).
- Finds retired logos with multi-scale template matching on edge maps
  (background colour doesn't matter, only shape).
- Checks alt text is present and meaningful.
- Fingerprints images (SHA-256 and a 64-bit difference hash) for the public
  verify page.

Uses Pillow and numpy only. No AI decides a visual finding: contrast and logo
matches are measurements.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess

import numpy as np
from PIL import Image, ImageOps

from . import ai

ALLOWED = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MAX_SIDE = 2400           # larger uploads are scaled down before analysis
LOGO_THRESHOLD = 0.65     # edge-map correlation for a match: true matches score 0.7-0.95, busy look-alikes stay under 0.5
GENERIC_ALT = re.compile(r"^(image|picture|photo|banner|graphic|img|screenshot|untitled)?[\s_\-\d]*"
                         r"(\.(png|jpe?g|webp|gif))?$", re.I)


class BadImage(Exception):
    pass


# ---------- loading and fingerprints ----------

def load(data):
    """Open and normalise an uploaded image. Raises BadImage with a plain-language reason."""
    if not data:
        raise BadImage("The file is empty.")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:  # noqa: BLE001 - Pillow raises many types
        raise BadImage("That file isn't an image Canon can read. Use PNG, JPG or WebP.") from e
    if im.format not in ("PNG", "JPEG", "WEBP"):
        raise BadImage("Only PNG, JPG and WebP images are supported.")
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        im = bg
    im = im.convert("RGB")
    if min(im.size) < 32:
        raise BadImage("The image is too small to check (under 32 pixels).")
    if max(im.size) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE))
    return im


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dhash(im, size=8):
    """64-bit difference hash: survives resizing and recompression, changes when content changes."""
    g = np.asarray(im.convert("L").resize((size + 1, size), Image.LANCZOS), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return "%016x" % int("".join("1" if b else "0" for b in bits), 2)


def hash_distance(a, b):
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


# ---------- OCR ----------

def has_tesseract():
    return shutil.which("tesseract") is not None and not os.environ.get("CANON_NO_TESSERACT")


def _ocr_tesseract(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    try:
        out = subprocess.run(["tesseract", "stdin", "stdout", "tsv"], input=buf.getvalue(),
                             capture_output=True, timeout=60, check=True).stdout.decode("utf-8", "ignore")
    except (subprocess.SubprocessError, OSError):
        return None
    lines = {}
    for row in out.splitlines()[1:]:
        p = row.split("\t")
        if len(p) < 12 or not p[11].strip():
            continue
        try:
            conf = float(p[10])
        except ValueError:
            continue
        if conf < 30:
            continue
        key = (p[2], p[3], p[4])
        x, y, w, h = (int(v) for v in p[6:10])
        ln = lines.setdefault(key, {"words": [], "box": [x, y, x + w, y + h]})
        ln["words"].append(p[11])
        b = ln["box"]
        ln["box"] = [min(b[0], x), min(b[1], y), max(b[2], x + w), max(b[3], y + h)]
    out_lines = [{"text": " ".join(v["words"]), "box": v["box"]} for v in lines.values()]
    return sorted(out_lines, key=lambda ln: (ln["box"][1], ln["box"][0]))


VISION_SYSTEM = """You read marketing images for a brand-review tool. Return JSON only:
{"lines": [{"text": "...", "box": [ymin, xmin, ymax, xmax]}], "alt": "..."}
- "lines": every line of visible text, top to bottom, copied exactly as written (keep currency
  symbols, numbers and punctuation). "box" is the line's bounding box on a 0-1000 scale.
  Skip text that is part of a logo.
- "alt": one plain sentence of alt text for a screen-reader user, describing what the image
  shows and including its key text. No "image of"."""


def _ocr_ai(im):
    """Vision model OCR. Returns (lines with pixel boxes, alt suggestion)."""
    small = im.copy()
    small.thumbnail((1280, 1280))
    buf = io.BytesIO()
    small.save(buf, format="PNG")
    data = ai.vision_json(VISION_SYSTEM, "Read this image.", buf.getvalue(), "image/png")
    W, H = im.size
    lines = []
    for ln in (data.get("lines") or []) if isinstance(data, dict) else []:
        text = str(ln.get("text", "")).strip() if isinstance(ln, dict) else ""
        box = ln.get("box") if isinstance(ln, dict) else None
        if not text:
            continue
        try:
            y0, x0, y1, x1 = (float(v) for v in box)
            box = [int(x0 / 1000 * W), int(y0 / 1000 * H), int(x1 / 1000 * W), int(y1 / 1000 * H)]
            if box[2] <= box[0] or box[3] <= box[1]:
                box = None
        except (TypeError, ValueError):
            box = None
        lines.append({"text": text, "box": box})
    alt = str(data.get("alt", "")).strip() if isinstance(data, dict) else ""
    return lines, alt


def read_text(im):
    """Returns (lines, method, alt_suggestion, note)."""
    if has_tesseract():
        lines = _ocr_tesseract(im)
        if lines is not None:
            return lines, "tesseract", "", ""
    if ai.available():
        try:
            lines, alt = _ocr_ai(im)
            return lines, "ai", alt, ""
        except ai.AIUnavailable as e:
            return [], "none", "", f"Text reading was unavailable ({e}). Type the text in the image below."
    return [], "none", "", "No text reader is set up in this deployment. Type the text in the image below."


def suggest_alt(im):
    if not ai.available():
        return ""
    try:
        _lines, alt = _ocr_ai(im)
        return alt
    except ai.AIUnavailable:
        return ""


# ---------- contrast (WCAG 2.1) ----------

def _lin(c):
    c = c / 255.0
    return np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def luminance(rgb):
    r, g, b = _lin(np.asarray(rgb, dtype=float))
    return float(0.2126 * r + 0.7152 * g + 0.0722 * b)


def contrast_ratio(c1, c2):
    l1, l2 = sorted((luminance(c1), luminance(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def two_colours(region):
    """Split a text region's pixels into text and background colours (2-means). Returns (fg, bg)."""
    px = np.asarray(region, dtype=float).reshape(-1, 3)
    if len(px) > 6000:
        px = px[np.linspace(0, len(px) - 1, 6000).astype(int)]
    lum = px @ np.array([0.2126, 0.7152, 0.0722])
    centres = np.array([px[lum.argmin()], px[lum.argmax()]])
    for _ in range(8):
        d = ((px[:, None, :] - centres[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        for k in (0, 1):
            if (lab == k).any():
                centres[k] = px[lab == k].mean(0)
    counts = np.bincount(lab, minlength=2)
    fg, bg = (0, 1) if counts[0] < counts[1] else (1, 0)
    return tuple(int(v) for v in centres[fg]), tuple(int(v) for v in centres[bg])


def hexc(c):
    return "#%02X%02X%02X" % tuple(c)


def check_contrast(im, lines):
    """One result per text line: ratio, colours, required minimum, pass/fail."""
    W, H = im.size
    out = []
    for ln in lines:
        box = ln.get("box")
        if not box:
            continue
        x0, y0, x1, y1 = box
        pad = max(2, int((y1 - y0) * 0.15))
        crop = im.crop((max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)))
        if crop.size[0] < 4 or crop.size[1] < 4:
            continue
        fg, bg = two_colours(crop)
        ratio = contrast_ratio(fg, bg)
        # WCAG large text is 18pt (24px) regular or 14pt bold; scale to how big the line is on a 1080px canvas
        height = (y1 - y0) * 1080 / max(W, 1)
        large = height >= 36
        need = 3.0 if large else 4.5
        out.append({"text": ln["text"], "box": box, "fg": hexc(fg), "bg": hexc(bg), "ratio": round(ratio, 2),
                    "need": need, "large": large, "ok": ratio >= need,
                    "suggest": "#1A2233" if luminance(bg) > 0.18 else "#FFFFFF",
                    "suggest_ratio": round(contrast_ratio((26, 34, 51) if luminance(bg) > 0.18 else (255, 255, 255), bg), 1)})
    return out


# ---------- retired-logo detection ----------

def _edges(gray):
    g = gray.astype(float)
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    return np.hypot(gx, gy)


def _ncc(image, tmpl):
    """Normalised cross-correlation of tmpl over image (valid positions), via FFT."""
    H, W = image.shape
    h, w = tmpl.shape
    if h > H or w > W:
        return None
    t = tmpl - tmpl.mean()
    tn = np.sqrt((t ** 2).sum())
    if tn == 0:
        return None
    shape = (H + h - 1, W + w - 1)
    corr = np.fft.irfft2(np.fft.rfft2(image, shape) * np.fft.rfft2(t[::-1, ::-1], shape), shape)
    corr = corr[h - 1:H, w - 1:W]
    ii = np.pad(image, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    ii2 = np.pad(image ** 2, ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def wsum(a):
        return a[h:, w:] - a[:-h, w:] - a[h:, :-w] + a[:-h, :-w]
    n = h * w
    s, s2 = wsum(ii), wsum(ii2)
    var = np.maximum(s2 - s * s / n, 1e-9)
    return corr / (np.sqrt(var) * tn)


def find_logo(im, logo_im, work=420):
    """Best match of a logo anywhere in the image, at any reasonable size.
    Returns (score, box in image pixels) or (0, None)."""
    W, H = im.size
    scale = work / max(W, H)
    small = im.convert("L").resize((max(1, int(W * scale)), max(1, int(H * scale))), Image.LANCZOS)
    E = _edges(np.asarray(small))
    logo = logo_im.convert("L")
    bbox = ImageOps.invert(logo).getbbox()  # crop the logo's empty margin
    if bbox:
        logo = logo.crop(bbox)
    best = (0.0, None)
    lw = 0.08 * small.size[0]
    while lw <= 0.75 * small.size[0]:
        lh = max(4, int(logo.size[1] * lw / logo.size[0]))
        if lh >= small.size[1]:
            break
        T = _edges(np.asarray(logo.resize((int(lw), lh), Image.LANCZOS)))
        r = _ncc(E, T)
        if r is not None and r.size:
            i = int(r.argmax())
            y, x = divmod(i, r.shape[1])
            if r.flat[i] > best[0]:
                best = (float(r.flat[i]), [int(x / scale), int(y / scale), int((x + lw) / scale), int((y + lh) / scale)])
        lw *= 1.1
    return best


# ---------- alt text ----------

def alt_problem(alt, filename=""):
    alt = (alt or "").strip()
    if not alt:
        return "This image has no alt text, so screen-reader users get nothing."
    looks_like_file = re.fullmatch(r"[\w\-. ]+\.(png|jpe?g|webp|gif)", alt, re.I)
    if len(alt) < 12 or GENERIC_ALT.match(alt) or looks_like_file or (filename and alt.lower() == filename.lower()):
        return f"The alt text \"{alt}\" doesn't describe the image."
    return None


# ---------- whole-image analysis ----------

def overlaps(a, b):
    return bool(a and b) and not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _open_logo(lg):
    try:
        return Image.open(io.BytesIO(lg["data"])).convert("RGB")
    except Exception:  # noqa: BLE001 - a broken logo file shouldn't stop the check
        return None


def analyse(im, retired_logos, ocr_lines=None, current_logos=()):
    """Measure an image. Returns a dict stored with the asset (JSON-safe)."""
    logos = []
    current = [c for c in (_open_logo(lg) for lg in current_logos) if c is not None]
    current_hits = [m for m in (find_logo(im, c) for c in current) if m[1] and m[0] >= LOGO_THRESHOLD]
    for lg in retired_logos:
        limg = _open_logo(lg)
        if limg is None:
            continue
        score, box = find_logo(im, limg)
        if not box or score < LOGO_THRESHOLD:
            continue
        # if the current logo fits that spot even better, it's the current logo, not the retired one
        if any(overlaps(m[1], box) and m[0] > score for m in current_hits):
            continue
        logos.append({"logo_id": lg["id"], "name": lg["name"], "score": round(score, 2), "box": box})
    if ocr_lines is None:
        lines, method, alt_suggestion, note = read_text(im)
    else:
        lines, method, alt_suggestion, note = ocr_lines, "stored", "", ""
    # text that belongs to a detected logo isn't copy
    logo_boxes = [lg["box"] for lg in logos] + [m[1] for m in current_hits]
    lines = [ln for ln in lines if not any(overlaps(ln.get("box"), b) for b in logo_boxes)]
    return {"size": list(im.size), "lines": lines, "ocr_method": method, "ocr_note": note,
            "alt_suggestion": alt_suggestion, "contrast": check_contrast(im, lines), "logos": logos}


def to_json(d):
    return json.dumps(d, ensure_ascii=False)
