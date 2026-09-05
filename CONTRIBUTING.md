# Contributing

One page. The skill stays small on purpose; match that energy.

## Setup

- Python 3.10+, Node.js with `npx`.
- `python -m pip install -r requirements.txt`
- A Chrome profile for manual smoke tests (see README prerequisites).

## Tests

```sh
python tests/test_bridge_pure.py
```

Pure + mocked-guard checks, no browser or credentials needed. Browser paths
(`send`, `upload`, model picker) are verified by manual smoke test against
your own signed-in session — never commit session material, and never ask
contributors for credentials.

## Pull Requests

- State the tested environment (Python version, `mcp` version, Chrome
  version, OS).
- Bug reports must be redacted: no cookies, tokens, private conversation
  URLs/contents, or raw profiles (see `SECURITY.md`).
- Keep `README.md`, `SKILL.md`, and `references/env.md` consistent when
  behavior changes; keep `CHANGELOG.md` updated.
