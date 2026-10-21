"""AI review using the Claude API.

Design rules:
- Confidential terms are masked before anything leaves the server.
- The model must cite a rule or fact ID the user is allowed to see.
- Every finding is validated; anything that fails is dropped and counted.
- If the API key is missing or the call fails, callers fall back to
  rule-engine results and show "AI review unavailable".
"""
import json
import os
import re
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("CANON_MODEL", "claude-sonnet-5-5")
TIMEOUT = int(os.environ.get("CANON_AI_TIMEOUT", "60"))


def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class AIUnavailable(Exception):
    pass


def redact(text, terms):
    """Replace confidential terms with tokens. Returns (masked_text, mapping)."""
    mapping = {}
    for i, term in enumerate(t for t in terms if t):
        token = f"[CONFIDENTIAL-{i + 1}]"
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        if pattern.search(text):
            mapping[token] = term
            text = pattern.sub(token, text)
    return text, mapping


def unredact(text, mapping):
    for token, term in mapping.items():
        text = (text or "").replace(token, term)
    return text


def _call(system, user, max_tokens=2500):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise AIUnavailable("No ANTHROPIC_API_KEY configured")
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(API_URL, data=payload, method="POST", headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise AIUnavailable(f"Claude API error {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AIUnavailable(f"Claude API unreachable: {e}") from e
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _parse_json(text):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1] or [0])
    return json.loads(text[start:])


def call_json(system, user, max_tokens=2500):
    """Call the model and parse JSON; retry once on malformed output."""
    last = None
    for _ in range(2):
        raw = _call(system, user, max_tokens)
        try:
            return _parse_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            user = user + "\n\nYour previous reply was not valid JSON. Reply with JSON only."
    raise AIUnavailable(f"Model returned invalid JSON twice: {last}")


REVIEW_SYSTEM = """You are Canon, a brand-governance reviewer. You check a draft against the brand's
approved rules and facts ONLY. You never invent rules or facts.

Return JSON only, in this shape:
{"findings": [{"quote": "...", "issue": "...", "ref": "R-01 or F-01", "severity": "low|medium|high",
"confidence": 0.0-1.0, "intent": "...", "fix": "..."}]}

Instructions:
- "quote" must be copied character-for-character from the draft and be as short as possible
  (a word, phrase or single sentence).
- "ref" must be one ID from the lists provided. If you cannot point to a listed rule or fact, do not report it.
- "intent": one short line describing what the writer was trying to say.
- "fix": the smallest replacement for "quote" that keeps that intent and follows the rule.
  Never add a number, claim or promise that is not in the approved facts.
- Focus on tone, unsupported or exaggerated claims, and facts stated differently from the approved list.
- Do not report spelling preferences, banned words or disclaimers already listed in "Already found".
- If the draft is fine, return {"findings": []}."""


def build_review_prompt(draft, voice, rules, facts, already):
    rule_lines = "\n".join(f"{r['id']} [{r['type']}] {r['title']}: {r['text']}" for r in rules if r["active"])
    fact_lines = "\n".join(f"{f['id']} {f['label']}: {f['value']}"
                           + (f" (expired {f['expires_on']})" if f["expires_on"] else "") for f in facts)
    found = "\n".join(f"- {a['ref']}: {a['quote']}" for a in already) or "- none"
    return (f"Brand voice:\n{voice}\n\nRules:\n{rule_lines}\n\nApproved facts:\n{fact_lines}\n\n"
            f"Already found by the rule engine:\n{found}\n\nDraft:\n<<<\n{draft}\n>>>")


def validate(raw_findings, original_text, mapping, valid_refs):
    """Keep only findings that quote the draft and cite a visible rule or fact."""
    kept, dropped = [], 0
    for f in raw_findings if isinstance(raw_findings, list) else []:
        if not isinstance(f, dict):
            dropped += 1
            continue
        quote = unredact(str(f.get("quote", "")).strip(), mapping)
        fix = unredact(str(f.get("fix", "")).strip(), mapping)
        ref = str(f.get("ref", "")).strip().upper()
        if not quote or quote not in original_text or ref not in valid_refs or not fix or fix == quote:
            dropped += 1
            continue
        try:
            conf = max(0.0, min(1.0, float(f.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        sev = f.get("severity") if f.get("severity") in ("low", "medium", "high") else "medium"
        kept.append({
            "quote": quote, "issue": str(f.get("issue", "")).strip()[:300], "ref": ref,
            "method": "ai", "action": "replace", "severity": sev, "confidence": round(conf, 2),
            "intent": unredact(str(f.get("intent", "")).strip(), mapping)[:200], "fix": fix,
            "needs_review": 1 if (conf < 0.6 or sev == "high") else 0,
        })
    return kept, dropped


def review(draft, voice, rules, facts, already, confidential_terms):
    masked, mapping = redact(draft, confidential_terms)
    prompt = build_review_prompt(masked, voice, rules, facts, already)
    data = call_json(REVIEW_SYSTEM, prompt)
    raw = data.get("findings", []) if isinstance(data, dict) else data
    refs = {r["id"] for r in rules} | {f["id"] for f in facts}
    return validate(raw, draft, mapping, refs)


FIX_SYSTEM = """You rewrite one short span of brand content. Return JSON only:
{"fix": "..."}. The fix replaces the quoted span, keeps the writer's stated intent,
follows the cited rule or fact, and adds no new claims or numbers."""


def regenerate_fix(quote, issue, source_text, intent, context, confidential_terms):
    masked_ctx, mapping = redact(context, confidential_terms)
    masked_quote, _ = redact(quote, confidential_terms)
    prompt = (f"Span: {masked_quote}\nProblem: {issue}\nRule or fact: {source_text}\n"
              f"Writer's intent: {intent}\nSurrounding text: {masked_ctx[:1500]}")
    data = call_json(FIX_SYSTEM, prompt, max_tokens=400)
    fix = unredact(str(data.get("fix", "")).strip(), mapping) if isinstance(data, dict) else ""
    if not fix:
        raise AIUnavailable("Model returned an empty fix")
    return fix


REWRITE_SYSTEM = """You help a brand team clarify a guideline that reviewers keep overriding.
Return JSON only: {"summary": "one sentence on why people override it",
"proposal": "the clearer rule, 1-3 sentences, including when it does and does not apply",
"examples": ["Do: ...", "Don't: ..."]}. Base it only on the rule and the reasons given."""


def propose_rewrite(rule, reasons):
    reason_lines = "\n".join(f"- {r}" for r in reasons if r) or "- (no reasons recorded)"
    data = call_json(REWRITE_SYSTEM, f"Rule {rule['id']}: {rule['text']}\n\nOverride reasons:\n{reason_lines}",
                     max_tokens=600)
    if not isinstance(data, dict) or not data.get("proposal"):
        raise AIUnavailable("Model returned no proposal")
    examples = data.get("examples") or []
    proposal = data["proposal"].strip()
    if examples:
        proposal += "\n" + "\n".join(str(e) for e in examples[:4])
    return str(data.get("summary", "")).strip(), proposal
