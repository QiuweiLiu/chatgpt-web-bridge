# chatgpt-bridge-skill

Out-of-the-box, dual-compatible (opencode + codex) skill that delegates bounded,
source-grounded research to your signed-in ChatGPT Web through an attach-only
Chrome CDP bridge. No API keys, no cookies handling, no second browser controller.

- Skill: `skills/chatgpt-bridge/SKILL.md` (canonical trigger: `$chatgpt-bridge`, legacy alias: `$chatgpt-web-research`)
- Bridge: `skills/chatgpt-bridge/scripts/bridge.py` (spawns pinned `chrome-devtools-mcp@1.8.0` with `--browserUrl`, never launches/closes your Chrome)
- Codex surface: `skills/chatgpt-bridge/agents/openai.yaml`
- Env docs: `skills/chatgpt-bridge/references/env.md`

## Prerequisites

1. Google Chrome (or Chrome for Testing; other Chromiums are best-effort) with a
   **dedicated persistent profile** for automation, signed into ChatGPT there once.
   Since Chrome 136, `--remote-debugging-port` is ignored for the default data
   directory, so a custom `--user-data-dir` is required. Keep the port on
   loopback; never expose 9222 beyond your machine:
   ```sh
   # macOS
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
     --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-chatgpt-bridge"
   # Linux
   google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-chatgpt-bridge"
   # Windows (PowerShell)
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:USERPROFILE\.chrome-chatgpt-bridge"
   ```
2. `npx` (Node.js) in `PATH`.
3. Python 3.10+ with the tested SDK: `pip install "mcp==1.12.2"`
   (MCP Python SDK v2 compatibility is unverified).
4. A ChatGPT account/workspace whose picker actually exposes the requested
   GPT-5.6 Sol + High state — otherwise the model gate will (correctly) refuse.
5. That's it. No `.env`, no tokens.

## 30-second smoke test

```sh
python3 skills/chatgpt-bridge/scripts/bridge.py doctor
python3 skills/chatgpt-bridge/scripts/bridge.py inspect
python3 skills/chatgpt-bridge/scripts/bridge.py --help
```

`doctor`/`inspect`/`status`/`wait` are read-only. `new-page` opens ChatGPT without sending.
`send`/`batch-send`/`upload` require explicit `--confirm-*` + `--verified-high`.

## Install

```sh
# recommended: user-global, scanned by both current OpenCode and Codex
mkdir -p ~/.agents/skills && cp -r skills/chatgpt-bridge ~/.agents/skills/
# then quit and restart your host (configs/skills load once at startup)

# per-project (both hosts scan this too)
mkdir -p ./.agents/skills && cp -r skills/chatgpt-bridge ./.agents/skills/

# legacy alternatives
cp -r skills/chatgpt-bridge ~/.config/opencode/skills/  # opencode-only global
cp -r skills/chatgpt-bridge ~/.codex/skills/             # codex-only global (deprecated upstream)

# opencode, reference without copying (opencode.jsonc)
# { "skills": { "paths": ["/abs/path/to/chatgpt-bridge-skill/skills/chatgpt-bridge"] } }
```

## Usage

```sh
BRIDGE=skills/chatgpt-bridge/scripts/bridge.py
python3 "$BRIDGE" inspect
python3 "$BRIDGE" status --conversation-url "https://chatgpt.com/c/<exact-id>"
python3 "$BRIDGE" select-model --conversation-url "$BOUND_URL" \
  --model "GPT-5.6 Sol" --effort High --confirm-model
python3 "$BRIDGE" send --conversation-url "$BOUND_URL" \
  --message-file ./brief.txt --verified-high --confirm-send
```

Full workflow, safety rules, binding policy, and report contract live in
`skills/chatgpt-bridge/SKILL.md`. Start there, not here.

## Security model

- Attach-only: the bridge never launches or terminates the Chrome process
  (it can open/close tabs in the attached browser).
- Auth = your existing browser session. The skill never reads/saves cookies, storage, or tokens.
- CDP attachment is privileged by nature: use the dedicated automation profile
  above, don't browse sensitive accounts in it, and keep port 9222 on loopback.
- Briefs travel via `--message-file`, never CLI argv.
- Uploads are explicit per-file (max 20), never directories/wildcards. Policy
  forbids secrets/`.env`/private source — enforced by the caller, not by a
  filename blocklist in the bridge: passing `--confirm-upload` asserts you
  already refused such files.
- Every send/upload/download needs action-time `--confirm-*`.

## Known limitations

- `--mcp-package-root` mode falls back to `/usr/local/bin/node` when `node` is
  not in `PATH`; keep Node in `PATH` on Linux/Windows/Homebrew setups.
- `$chatgpt-web-research` is a documentation alias only; the installable skill
  name is `chatgpt-bridge`.
- Fixed in this tree (kept here as behavior notes): non-positive `send`/batch
  timeouts are rejected before any side effect; `send`/`upload` re-check the
  exact bound URL immediately before acting (`conversation_moved`); `send`
  fills the composer through the trusted editing path so ProseMirror registers
  it; caller-side errors surface with exact codes before the MCP session opens.
- Still caller-asserted: `--auto-save-report` / `save-report --status valid`
  records your verification claim. A stable response does not prove the model
  stayed on Sol + High — re-check visible model state after the response, and
  use `invalid`/`unverified` otherwise.

## License

MIT — see `LICENSE`.
