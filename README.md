# chatgpt-bridge-skill

Out-of-the-box, dual-compatible (opencode + codex) skill that delegates bounded,
source-grounded research to your signed-in ChatGPT Web through an attach-only
Chrome CDP bridge. No API keys, no cookies handling, no second browser controller.

- Skill: `skills/chatgpt-bridge/SKILL.md` (canonical trigger: `$chatgpt-bridge`, legacy alias: `$chatgpt-web-research`)
- Bridge: `skills/chatgpt-bridge/scripts/bridge.py` (spawns pinned `chrome-devtools-mcp@1.8.0` with `--browserUrl`, never launches/closes your Chrome)
- Codex surface: `skills/chatgpt-bridge/agents/openai.yaml`
- Env docs: `skills/chatgpt-bridge/references/env.md`

## Prerequisites

1. Chrome/Chromium with remote debugging, logged into ChatGPT there:
   ```sh
   google-chrome --remote-debugging-port=9222
   ```
2. `npx` (Node.js) in `PATH`.
3. Python with `pip install mcp`.
4. That's it. No `.env`, no tokens.

## 30-second smoke test

```sh
python3 skills/chatgpt-bridge/scripts/bridge.py inspect
python3 skills/chatgpt-bridge/scripts/bridge.py --help
```

`inspect`/`status`/`wait` are read-only. `new-page` opens ChatGPT without sending.
`send`/`batch-send`/`upload` require explicit `--confirm-*` + `--verified-high`.

## Install

```sh
# opencode, global
cp -r skills/chatgpt-bridge ~/.config/opencode/skills/
# then quit and restart opencode (config loads once at startup)

# opencode, per-project
cp -r skills/chatgpt-bridge ./.opencode/skills/

# codex
cp -r skills/chatgpt-bridge ~/.codex/skills/

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

- Attach-only: the bridge never launches or closes Chrome.
- Auth = your existing browser session. The skill never reads/saves cookies, storage, or tokens.
- Briefs travel via `--message-file`, never CLI argv.
- Uploads are explicit per-file (max 20), never directories/wildcards. Policy
  forbids secrets/`.env`/private source — enforced by the caller, not by a
  filename blocklist in the bridge: passing `--confirm-upload` asserts you
  already refused such files.
- Every send/upload/download needs action-time `--confirm-*`.

## Known limitations (inherited from upstream bridge behavior, v1 documents instead of forking)

- Non-positive `--timeout` (`send --timeout 0`, batch item `timeout: 0`) still
  performs fill/click before reporting `submission_unknown`. Always use a
  positive timeout; never auto-retry an ambiguous submission without `status`.
- `--auto-save-report` / `save-report --status valid` records caller-asserted
  validity. A stable assistant response does not prove the model stayed on
  Sol + High — re-check visible model state after the response before claiming
  `valid`, and use `invalid`/`unverified` otherwise.
- The bridge checks the page is still a ChatGPT conversation before side
  effects, not that it is still the exact bound URL. Re-run `status
  --conversation-url "$BOUND_URL"` immediately before `send`/`upload` if the
  tab may have navigated.
- `--mcp-package-root` mode falls back to `/usr/local/bin/node` when `node` is
  not in `PATH`; keep Node in `PATH` on Linux/Windows/Homebrew setups.
- `$chatgpt-web-research` is a documentation alias only; the installable skill
  name is `chatgpt-bridge`.

## License

MIT — see `LICENSE`.
