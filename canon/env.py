"""Load settings from a local .env file (KEY=value per line) if one exists.

The .env file is listed in .gitignore, so secrets in it are never committed.
Values already set in the environment always win.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path=None):
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("'").strip('"')
            if key.strip() and value:
                os.environ.setdefault(key.strip(), value)


load()
