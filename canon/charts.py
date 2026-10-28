"""Small server-rendered SVG charts for the governance dashboard.

No JavaScript library or CDN: the charts render even offline, scale with the
page, have hover titles, and animate in with CSS.
"""
from html import escape

from markupsafe import Markup

INK, MUTED, GRID = "#141b2d", "#646e85", "#e7eaf1"


def _nice_max(v):
    if v <= 0:
        return 1
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000):
        if v <= step * 4:
            return step * 4 if v > step * 2 else step * 2 if v > step else step
    return v


def _frame(w, h, body, label):
    return Markup(f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(label)}" '
                  f'preserveAspectRatio="xMidYMid meet">{body}</svg>')


def _y_axis(x0, x1, y0, y1, top, ticks=4, fmt="{:g}"):
    out = []
    for i in range(ticks + 1):
        v = top * i / ticks
        y = y1 - (y1 - y0) * i / ticks
        out.append(f'<line x1="{x0}" x2="{x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}"/>'
                   f'<text x="{x0 - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="{MUTED}">{fmt.format(round(v, 1))}</text>')
    return "".join(out)


def legend(items):
    return Markup('<div class="chart-legend">' + "".join(
        f'<span><i style="background:{c}"></i>{escape(n)}</span>' for n, c in items) + "</div>")


def stacked_bars(labels, series, colors, label="Stacked bar chart"):
    w, h, L, R, T, B = 640, 260, 40, 10, 12, 30
    n = max(1, len(labels))
    totals = [sum(series[k][i] or 0 for k in series) for i in range(n)]
    top = _nice_max(max(totals or [1]))
    slot = (w - L - R) / n
    bw = slot * 0.58
    body = [_y_axis(L, w - R, T, h - B, top)]
    for i in range(n):
        x = L + slot * i + (slot - bw) / 2
        y = h - B
        for k, vals in series.items():
            v = vals[i] or 0
            if not v:
                continue
            bh = (h - B - T) * v / top
            y -= bh
            body.append(f'<rect class="bar" style="--i:{i}" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" '
                        f'fill="{colors.get(k, INK)}"><title>{escape(labels[i])} · {escape(k)}: {v}</title></rect>')
        body.append(f'<text x="{x + bw / 2:.1f}" y="{h - 10}" text-anchor="middle" font-size="11" fill="{MUTED}">{escape(labels[i])}</text>')
    return _frame(w, h, "".join(body), label)


def lines(labels, series, label="Line chart", top=None, suffix=""):
    """series: list of (name, values, colour, dashed). Values may contain None (gaps)."""
    w, h, L, R, T, B = 640, 260, 44, 14, 12, 30
    n = max(1, len(labels))
    vals = [v for _n, s, _c, _d in series for v in s if v is not None]
    top = top or _nice_max(max(vals or [1]))
    step = (w - L - R) / max(1, n - 1)
    body = [_y_axis(L, w - R, T, h - B, top, fmt="{:g}" + suffix)]
    for i, lab in enumerate(labels):
        body.append(f'<text x="{L + step * i:.1f}" y="{h - 10}" text-anchor="middle" font-size="11" fill="{MUTED}">{escape(lab)}</text>')
    for name, s, colour, dashed in series:
        pts = [(L + step * i, h - B - (h - B - T) * v / top, v) for i, v in enumerate(s) if v is not None]
        if not pts:
            continue
        d = " ".join(f"{'M' if j == 0 else 'L'}{x:.1f},{y:.1f}" for j, (x, y, _v) in enumerate(pts))
        if not dashed:
            area = d + f" L{pts[-1][0]:.1f},{h - B} L{pts[0][0]:.1f},{h - B} Z"
            body.append(f'<path d="{area}" fill="{colour}" opacity=".08"/>')
        dash = ' stroke-dasharray="6 5"' if dashed else ' pathLength="1"'
        body.append(f'<path class="{"" if dashed else "line"}" d="{d}" fill="none" stroke="{colour}" stroke-width="2.5" '
                    f'stroke-linejoin="round" stroke-linecap="round"{dash}/>')
        for x, y, v in pts:
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#fff" stroke="{colour}" stroke-width="2">'
                        f'<title>{escape(name)}: {v}{suffix}</title></circle>')
    return _frame(w, h, "".join(body), label)


def hbars(items, colour_list, label="Bar chart", value_suffix=""):
    """items: list of (label, value)."""
    row = 30
    w, L, R = 640, 230, 46
    h = max(row * len(items) + 10, 40)
    top = _nice_max(max([v or 0 for _l, v in items] or [1]))
    body = []
    for i, (lab, v) in enumerate(items):
        y = 6 + i * row
        bw = (w - L - R) * (v or 0) / top
        colour = colour_list[i % len(colour_list)]
        short = lab if len(lab) <= 34 else lab[:33] + "…"
        body.append(f'<text x="{L - 8}" y="{y + 17}" text-anchor="end" font-size="12" fill="{INK}">{escape(short)}<title>{escape(lab)}</title></text>'
                    f'<rect x="{L}" y="{y + 4}" width="{w - L - R}" height="18" rx="5" fill="#f2f4f8"/>'
                    f'<rect class="hbar" style="--i:{i}" x="{L}" y="{y + 4}" width="{bw:.1f}" height="18" rx="5" fill="{colour}">'
                    f'<title>{escape(lab)}: {v}{value_suffix}</title></rect>'
                    f'<text x="{L + bw + 6:.1f}" y="{y + 17}" font-size="12" font-weight="700" fill="{INK}">{"–" if v is None else v}{value_suffix}</text>')
    return _frame(w, h, "".join(body), label)


def combo(labels, bars, line, label="Chart", line_top=100, line_suffix="%"):
    """Bars (own scale, right axis) plus a line (0-line_top, left axis): used for reliability runs."""
    w, h, L, R, T, B = 640, 260, 44, 40, 12, 44
    n = max(1, len(labels))
    slot = (w - L - R) / n
    bar_name, bar_vals, bar_colour = bars
    line_name, line_vals, line_colour = line
    btop = _nice_max(max([v or 0 for v in bar_vals] or [1]))
    body = [_y_axis(L, w - R, T, h - B, line_top, fmt="{:g}" + line_suffix)]
    for i in range(5):
        y = h - B - (h - B - T) * i / 4
        body.append(f'<text x="{w - R + 6}" y="{y + 4:.1f}" font-size="11" fill="{bar_colour}">{round(btop * i / 4, 1):g}</text>')
    pts = []
    for i, lab in enumerate(labels):
        cx = L + slot * i + slot / 2
        bv = bar_vals[i] or 0
        bh = (h - B - T) * bv / btop
        body.append(f'<rect class="bar" style="--i:{i}" x="{cx - slot * 0.18:.1f}" y="{h - B - bh:.1f}" width="{slot * 0.36:.1f}" '
                    f'height="{bh:.1f}" rx="3" fill="{bar_colour}" opacity=".75"><title>{escape(lab)} · {escape(bar_name)}: {bv}</title></rect>')
        parts = lab.split(" (")
        body.append(f'<text x="{cx:.1f}" y="{h - 24}" text-anchor="middle" font-size="10.5" fill="{MUTED}">{escape(parts[0])}</text>')
        if len(parts) > 1:
            body.append(f'<text x="{cx:.1f}" y="{h - 10}" text-anchor="middle" font-size="10" fill="{MUTED}">({escape(parts[1])}</text>')
        if line_vals[i] is not None:
            pts.append((cx, h - B - (h - B - T) * line_vals[i] / line_top, line_vals[i], lab))
    if pts:
        d = " ".join(f"{'M' if j == 0 else 'L'}{x:.1f},{y:.1f}" for j, (x, y, _v, _l) in enumerate(pts))
        body.append(f'<path class="line" d="{d}" fill="none" stroke="{line_colour}" stroke-width="2.5" pathLength="1"/>')
        for x, y, v, lab in pts:
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#fff" stroke="{line_colour}" stroke-width="2.5">'
                        f'<title>{escape(lab)} · {escape(line_name)}: {v}{line_suffix}</title></circle>')
    return _frame(w, h, "".join(body), label)
