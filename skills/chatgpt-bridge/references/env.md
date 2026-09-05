# Environment — chatgpt-bridge (portable, no hardcoded home paths)

All settings are optional environment variables. CLI flags override them.

| Variable | Default | Meaning |
|---|---|---|
| `CDP_URL` | `http://127.0.0.1:9222` | Existing Chrome CDP endpoint on loopback. Start Chrome with `--remote-debugging-port=9222` plus a dedicated `--user-data-dir`, and keep your ChatGPT login in that profile. |
| `CHROME_DEVTOOLS_MCP_COMMAND` | `npx` | MCP launcher. |
| `CHROME_DEVTOOLS_MCP_PACKAGE` | `chrome-devtools-mcp@1.8.0` | Pinned so `@latest` cannot silently change transport. Override only with a tested version. |
| `CHATGPT_MCP_PYTHON` | `sys.executable` (the Python running `bridge.py`) | Existence-checked only — it is NOT re-executed. The bridge always runs under the interpreter you launch it with, so that interpreter itself needs the dependencies from the repo-root `requirements.txt` (`mcp==1.12.2`, `websockets>=15.0.1`; tested; SDK v2 compat unverified). Setting this variable cannot rescue a `python3` that lacks them. |

Equivalent CLI flags: `--cdp-url`, `--mcp-command`, `--mcp-package`, `--mcp-package-root`, `--mcp-python`.

No cookies, tokens, `.env` files, or browser profiles are read or stored by this skill.
Authentication always comes from your existing signed-in browser session.
