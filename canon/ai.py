"""AI review using Google Gemini (default) or the Claude API.

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
import time
import urllib.error
import urllib.request

TIMEOUT = int(os.environ.get("CANON_AI_TIMEOUT", "45"))
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULTS = {"gemini": "gemini-3.1-flash-lite", "anthropic": "claude-haiku-5-5"}
RETRY_WAITS = (2, 5)  # seconds to wait after a rate-limit or overload response


def provider():
    """gemini or anthropic, chosen by which key is set (or CANON_AI_PROVIDER)."""
    forced = os.environ.get("CANON_AI_PROVIDER", "").strip().lower()
    if forced in DEFAULTS:
        return forced
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def _key():
    p = provider()
    return os.environ.get("GEMINI_API_KEY" if p == "gemini" else "ANTHROPIC_API_KEY", "").strip()


_resolved_model = {}


def model():
    p = provider()
    return _resolved_model.get(p) or os.environ.get("CANON_MODEL") or DEFAULTS.get(p, "")


def label():
    p = provider()
    if not p:
        return ""
    return f"{'Google Gemini' if p == 'gemini' else 'Claude'} · {model()}"


def available():
    return bool(provider() and _key())


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


def _http(url, body=None, headers=None, method="POST"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"content-type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def _error_detail(e):
    try:
        body = json.loads(e.read())
        err = body.get("error", {})
        return err.get("status") or err.get("type") or err.get("message", "")[:80]
    except Exception:  # noqa: BLE001 - best effort only
        return ""


def _with_retries(fn):
    """Retry on rate limits (429) and overload (500/503/529); fail fast otherwise."""
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503, 529) and attempt < len(RETRY_WAITS):
                time.sleep(RETRY_WAITS[attempt])
                continue
            detail = _error_detail(e)
            hint = {401: "API key rejected", 403: "API key not allowed for this API", 404: "model not found",
                    429: "rate limit reached, try again in a minute", 500: "AI service busy",
                    503: "AI service busy", 529: "AI service busy"}.get(e.code, "API error")
            raise AIUnavailable(f"{hint} ({e.code}{', ' + detail if detail else ''})") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise AIUnavailable(f"AI service unreachable: {e}") from e


def _gemini_pick_model(exclude=()):
    """Pick another available Flash model (used when the configured one is missing or overloaded)."""
    data = _with_retries(lambda: _http(f"{GEMINI_BASE}/models?pageSize=200",
                                       headers={"x-goog-api-key": _key()}, method="GET"))
    names = [m["name"].split("/", 1)[-1] for m in data.get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n and n not in exclude
             and not any(x in n for x in ("image", "tts", "live", "audio"))]
    stable = [n for n in flash if "preview" not in n and "exp" not in n]
    pool = stable or flash
    if not pool:
        raise AIUnavailable("no Gemini Flash model available for this key")
    return sorted(pool, reverse=True)[0]


def _gemini_call(system, user, max_tokens):
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2,
                             "maxOutputTokens": max(max_tokens, 4096)},
    }

    def go():
        return _http(f"{GEMINI_BASE}/models/{model()}:generateContent", body, {"x-goog-api-key": _key()})

    try:
        data = _with_retries(go)
    except AIUnavailable as e:
        if "model not found" not in str(e) and "busy" not in str(e):
            raise
        # the model is gone or overloaded: switch to another available Flash model and try once more
        _resolved_model["gemini"] = _gemini_pick_model(exclude={model()})
        data = _with_retries(go)
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no response")
        raise AIUnavailable(f"Gemini returned no answer ({reason})")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise AIUnavailable(f"Gemini returned an empty answer ({cands[0].get('finishReason', 'unknown')})")
    return text


def _anthropic_call(system, user, max_tokens):
    body = {"model": model(), "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}]}
    data = _with_retries(lambda: _http(ANTHROPIC_URL, body, {"x-api-key": _key(),
                                                             "anthropic-version": "2023-06-01"}))
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _call(system, user, max_tokens=2500):
    if not available():
        raise AIUnavailable("no API key configured")
    if provider() == "gemini":
        return _gemini_call(system, user, max_tokens)
    return _anthropic_call(system, user, max_tokens)


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
