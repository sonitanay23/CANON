"""Quick test of your AI key, from the project folder:

    GEMINI_API_KEY=your-key python -m canon.check_key

Runs one real check on a short sample and prints what came back.
"""
import sys

from . import env  # noqa: F401
from . import ai
from .db import connect
from .seed import seed

SAMPLE = "Velora Money will double your savings in a year. Hurry, act now before you lose out."


def main():
    if not ai.available():
        print("No key found. Set GEMINI_API_KEY (or ANTHROPIC_API_KEY) first.")
        return 1
    print(f"Provider: {ai.label()}")
    conn = connect(":memory:")
    seed(conn)
    rules = conn.execute("SELECT * FROM rules").fetchall()
    facts = conn.execute("SELECT * FROM facts WHERE visibility!='internal'").fetchall()
    try:
        kept, dropped = ai.review(SAMPLE, "Calm, clear, never hype.", rules, facts, [], ["Project Nova"])
    except ai.AIUnavailable as e:
        print(f"FAILED: {e}")
        return 1
    print(f"Model used: {ai.model()}")
    print(f"OK: {len(kept)} finding(s) kept, {dropped} dropped by validation")
    for f in kept:
        print(f"  {f['ref']} [{f['severity']}, {int(f['confidence'] * 100)}%] \"{f['quote']}\" -> \"{f['fix']}\"")
    for reason, f in ai.LAST_DROPPED:
        quote = f.get("quote", "") if isinstance(f, dict) else ""
        print(f"  dropped: {reason}: \"{quote}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
