"""Deterministic checks: no AI involved.

Every finding returned here cites a rule ID (R-xx) or fact ID (F-xx) and
carries a ready-made minimal fix, so it is fully explainable.
"""
import re
from datetime import date

SENTENCE_RE = re.compile(r"[^.!?\n]*[.!?]?", re.UNICODE)

RULE_INTENT = "Keep the same message, using approved brand wording."


CURRENCY = re.compile(r"\b(?:rs\.?|inr)\s?", re.I)


def _norm(value):
    """Compare values loosely: spacing, commas and the rupee written as Rs./INR don't matter."""
    return re.sub(r"[\s,+]", "", CURRENCY.sub("₹", (value or "").lower()))


def sentence_around(text, start, end):
    """Return the sentence that contains text[start:end]."""
    left = max(text.rfind(c, 0, start) for c in ".!?\n")
    s = left + 1
    rights = [i for i in (text.find(c, end) for c in ".!?\n") if i != -1]
    e = (min(rights) + 1) if rights else len(text)
    return text[s:e].strip()


def _compile(pattern):
    if not pattern:
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def _finding(quote, issue, ref, severity, fix, intent=RULE_INTENT, confidence=1.0, action="replace"):
    return {
        "quote": quote, "issue": issue, "ref": ref, "method": "rule", "action": action,
        "severity": severity, "confidence": confidence, "intent": intent,
        "fix": fix, "needs_review": 1 if severity == "high" else 0,
    }


def check_rules(text, rules):
    findings = []
    for r in rules:
        if not r["active"]:
            continue
        kind = r["type"]
        if kind in ("banned", "terminology", "pattern"):
            rx = _compile(r["pattern"] or "")
            if not rx:
                continue
            for m in rx.finditer(text):
                start = m.start()
                if not any(ch.isalnum() for ch in m.group(0)):
                    # punctuation-only match (e.g. "!"): include the word before it so each hit is unique
                    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "'’"):
                        start -= 1
                quote = text[start:m.end()]
                repl = r["replacement"] if r["replacement"] is not None else ""
                # keep sentence case when replacing the first word of a sentence
                if quote[:1].isupper() and repl[:1].islower():
                    repl = repl[:1].upper() + repl[1:]
                if start < m.start():
                    repl = text[start:m.start()] + repl
                findings.append(_finding(quote, r["title"], r["id"], r["severity"], repl))
        elif kind == "disclaimer":
            trig = _compile(r["trigger"] or "")
            required = (r["replacement"] or "").strip()
            if not trig or not required:
                continue
            m = trig.search(text)
            if m and required.lower().rstrip(".") not in text.lower():
                findings.append(_finding(m.group(0), f"{r['title']}: \"{m.group(0)}\" needs the disclaimer",
                                         r["id"], r["severity"], required,
                                         intent="Keep the message and add the required disclaimer at the end.",
                                         action="append"))
    return findings


def _nearest_value(text, kw_match, vrx, window=70):
    lo = max(0, kw_match.start() - window)
    hi = min(len(text), kw_match.end() + window)
    best = None
    for vm in vrx.finditer(text, lo, hi):
        if vm.end() <= kw_match.start():
            dist = kw_match.start() - vm.end() + 5  # small bias towards values after the keyword
        elif vm.start() >= kw_match.end():
            dist = vm.start() - kw_match.end()
        else:
            dist = 0
        if best is None or dist < best[0]:
            best = (dist, vm)
    return best[1] if best else None


def fact_mentions(text, fact):
    """Yield (value_match, sentence) for each place the text states this fact."""
    krx = _compile(fact["keyword"] or "")
    vrx = _compile(fact["value_pattern"] or "")
    if not krx or not vrx:
        return
    seen = set()
    for km in krx.finditer(text):
        vm = _nearest_value(text, km, vrx)
        if vm and vm.start() not in seen:
            seen.add(vm.start())
            yield vm, sentence_around(text, vm.start(), vm.end())


def check_facts(text, facts, today=None):
    today = today or date.today().isoformat()
    findings = []
    for f in facts:
        expired = bool(f["expires_on"]) and f["expires_on"] < today
        for vm, sent in fact_mentions(text, f):
            found = vm.group(0).strip()
            if _norm(found) != _norm(f["value"]):
                findings.append(_finding(
                    found,
                    f"States {found} but the approved value for \"{f['label']}\" is {f['value']}",
                    f["id"], "high", f["value"]))
            elif expired:
                findings.append(_finding(
                    sent,
                    f"\"{f['label']}\" expired on {f['expires_on']}; it should no longer be promoted",
                    f["id"], "high", "",
                    intent="Remove an offer that is no longer valid."))
    return findings


def claim_links(text, facts):
    """Sentences that state a fact's current approved value (used at approval)."""
    links = []
    for f in facts:
        for vm, sent in fact_mentions(text, f):
            if _norm(vm.group(0)) == _norm(f["value"]):
                links.append({"fact_id": f["id"], "quote": sent, "value": vm.group(0).strip()})
    return links


def run_rule_engine(text, rules, facts, today=None):
    out = check_rules(text, rules) + check_facts(text, facts, today)
    # one finding per (quote, ref)
    seen, unique = set(), []
    for f in out:
        key = (f["quote"].lower(), f["ref"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique
