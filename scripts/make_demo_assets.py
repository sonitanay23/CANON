"""Draws the demo artwork for the fictional Velora Money brand.

Run once:  python scripts/make_demo_assets.py
Outputs PNGs into static/demo/. All marks are original drawings for the demo.
"""
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "demo")
FONT_DIRS = ["/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/google-fonts"]


def font(name, size):
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


INK, GOLD = (26, 34, 51), (232, 199, 122)


def current_logo(scale=1.0):
    w, h = int(300 * scale), int(84 * scale)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = int(64 * scale)
    y0 = (h - s) // 2
    d.rounded_rectangle([0, y0, s, y0 + s], radius=int(16 * scale), fill=INK)
    # a check-like "V" mark
    d.line([(s * .26, y0 + s * .34), (s * .5, y0 + s * .72), (s * .76, y0 + s * .3)], fill=(125, 160, 240),
           width=max(2, int(7 * scale)), joint="curve")
    d.text((s + 14 * scale, h / 2), "Velora Money", font=font("Inter-Bold.otf", int(30 * scale)), fill=INK, anchor="lm")
    return im


def retired_logo(scale=1.0):
    w, h = int(240 * scale), int(84 * scale)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = int(64 * scale)
    y0 = (h - s) // 2
    d.ellipse([0, y0, s, y0 + s], fill=(46, 139, 87))
    d.text((s / 2, y0 + s / 2 - 3 * scale), "v", font=font("Poppins-Bold.ttf", int(40 * scale)), fill="white", anchor="mm")
    d.text((s + 12 * scale, h / 2), "velora", font=font("Poppins-Bold.ttf", int(34 * scale)), fill=(46, 139, 87), anchor="lm")
    return im


def flat(im, bg=(255, 255, 255)):
    base = Image.new("RGB", im.size, bg)
    base.paste(im, mask=im.split()[3])
    return base


def banner(logo, lines, path, bg=(255, 246, 229)):
    W = 1080
    im = Image.new("RGB", (W, W), bg)
    d = ImageDraw.Draw(im)
    for i in range(W):  # soft diagonal glow
        a = i / W
        d.line([(0, i), (W, i)], fill=tuple(int(c * (1 - .06 * a)) for c in bg))
    for cx, cy, r, col in ((900, 170, 120, (255, 214, 140)), (980, 300, 60, (255, 190, 110)), (160, 940, 90, (255, 222, 160))):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    im.paste(logo, (80, 80), logo)
    y = 330
    for text, size, colour, weight in lines:
        f = font("Inter-Bold.otf" if weight == "bold" else "Inter-SemiBold.otf", size)
        d.text((80, y), text, font=f, fill=colour)
        y += int(size * 1.45)
    im.save(path, optimize=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    flat(current_logo(2)).save(os.path.join(OUT, "logo-current.png"))
    flat(retired_logo(2)).save(os.path.join(OUT, "logo-retired.png"))
    # the draft banner: retired logo, a low-contrast line, a wrong price and a banned promise
    banner(retired_logo(1.6), [
        ("Diwali just got", 92, INK, "bold"),
        ("brighter.", 92, INK, "bold"),
        ("Guaranteed returns on every rupee", 44, GOLD, "semi"),
        ("Velora Pro now just Rs. 399 a month", 46, INK, "semi"),
    ], os.path.join(OUT, "diwali-banner.png"))
    # an approved banner: current logo, readable text, approved price
    banner(current_logo(1.4), [
        ("Clarity over", 92, INK, "bold"),
        ("your money.", 92, INK, "bold"),
        ("Velora Pro: Rs. 499 a month", 46, (60, 69, 88), "semi"),
        ("Priority support and monthly insights", 40, (60, 69, 88), "semi"),
    ], os.path.join(OUT, "pro-banner.png"), bg=(236, 242, 255))
    # the approved banner with its price edited after approval (for the public verify demo)
    banner(current_logo(1.4), [
        ("Clarity over", 92, INK, "bold"),
        ("your money.", 92, INK, "bold"),
        ("Velora Pro: Rs. 299 a month", 46, (60, 69, 88), "semi"),
        ("Priority support and monthly insights", 40, (60, 69, 88), "semi"),
    ], os.path.join(OUT, "pro-banner-edited.png"), bg=(236, 242, 255))
    print("written to", OUT)




def store_ocr():
    """Record Tesseract's reading of the demo images, so the seeded demo works on hosts without it."""
    import json
    import sys
    sys.path.insert(0, os.path.dirname(OUT.rsplit(os.sep, 2)[0] + os.sep))
    from canon import vision
    out = {}
    for name in ("diwali-banner", "pro-banner", "pro-banner-edited"):
        with open(os.path.join(OUT, name + ".png"), "rb") as fh:
            out[name] = vision._ocr_tesseract(vision.load(fh.read()))
    with open(os.path.join(OUT, "ocr.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print("stored OCR for", ", ".join(out))


if __name__ == "__main__":
    main()
    store_ocr()
