# ChatGPT Bridge

`chatgpt-web-bridge` is the public repository for the `chatgpt-bridge`
OpenCode/Codex skill: delegate bounded, source-grounded research to your
signed-in ChatGPT Web through an attach-only Chrome CDP bridge.
No API keys, no cookies handling, no second browser controller.

> Status: **pre-1.0 release candidate** (`BRIDGE_VERSION`, see CHANGELOG.md).
> Not yet recommended as v1.0.

- Skill: `skills/chatgpt-bridge/SKILL.md` (canonical trigger: `$chatgpt-bridge`, legacy alias: `$chatgpt-web-research`)
- Bridge: `skills/chatgpt-bridge/scripts/bridge.py` (spawns pinned `chrome-devtools-mcp@1.8.0` with `--browserUrl`, never launches/terminates your Chrome process)
- Architecture map: `skills/chatgpt-bridge/references/architecture.md`
- Codex surface: `skills/chatgpt-bridge/agents/openai.yaml`
- Env docs: `skills/chatgpt-bridge/references/env.md`

When it works, the flow is always:

1. `doctor` passes (interpreter, deps, CDP, tabs);
2. the exact ChatGPT tab is selected (never guessed);
3. model/reasoning is verified (GPT-5.6 Sol + High);
4. the brief file is sent from `--message-file`;
5. the exact conversation URL is returned for reuse.

| Tested stack | Version |
|---|---|
| Python | 3.13.5 (requires 3.10+) |
| `mcp` SDK | 1.12.2 (v2 compat unverified) |
| `websockets` | ≥15.0.1 (download-attachments only) |
| Node.js | 24.15.0 (requires ^20.19 / ^22.12 / ≥23) |
| `chrome-devtools-mcp` | 1.8.0 (pinned) |
| Chrome | 152 (dedicated automation profile) |

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
3. Python 3.10+ with the tested dependencies (see `requirements.txt`):
   ```sh
   python -m pip install -r requirements.txt
   ```
   (`mcp==1.12.2`, `websockets>=15.0.1`; SDK v2 compatibility is unverified).
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

# reproducible install from a tagged release (dependencies travel with the skill)
git clone https://github.com/QiuweiLiu/chatgpt-web-bridge.git
cd chatgpt-web-bridge && git checkout v0.9.0
python -m pip install -r requirements.txt
mkdir -p ~/.agents/skills && cp -r skills/chatgpt-bridge ~/.agents/skills/

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
  timeouts are rejected before any side effect; `send` re-checks tab id, exact
  URL, exact composer text, and a live send control in one final phase before
  clicking (`composer_changed`/`conversation_moved`); `upload_file` is preceded
  by a fresh exact-page check per file; landing-without-`--new-conversation`
  and foreign URLs fail pre-session; `send` fills the composer through the
  trusted editing path so ProseMirror registers it; caller-side errors surface
  with exact codes before the MCP session opens.
- Still caller-asserted: `--auto-save-report` / `save-report --status valid`
  records your verification claim. A stable response does not prove the model
  stayed on Sol + High — re-check visible model state after the response, and
  use `invalid`/`unverified` otherwise.

## License

MIT — see `LICENSE`.
