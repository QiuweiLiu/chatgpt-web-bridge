# Environment — chatgpt-bridge (portable, no hardcoded home paths)

All settings are optional environment variables. CLI flags override them.

| Variable | Default | Meaning |
|---|---|---|
| `CDP_URL` | `http://127.0.0.1:9222` | Existing Chrome/Chromium CDP endpoint. Start Chrome with `--remote-debugging-port=9222` and keep your ChatGPT login in that instance. |
| `CHROME_DEVTOOLS_MCP_COMMAND` | `npx` | MCP launcher. |
| `CHROME_DEVTOOLS_MCP_PACKAGE` | `chrome-devtools-mcp@1.8.0` | Pinned so `@latest` cannot silently change transport. Override only with a tested version. |
| `CHATGPT_MCP_PYTHON` | `sys.executable` (the Python running `bridge.py`) | Python with the `mcp` client SDK installed (`pip install mcp`). |

Equivalent CLI flags: `--cdp-url`, `--mcp-command`, `--mcp-package`, `--mcp-package-root`, `--mcp-python`.

No cookies, tokens, `.env` files, or browser profiles are read or stored by this skill.
Authentication always comes from your existing signed-in browser session.
