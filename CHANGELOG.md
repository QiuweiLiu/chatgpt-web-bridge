# Changelog

All notable changes to the `chatgpt-bridge` skill are documented here.
Version identity also lives in code as `BRIDGE_VERSION` (`bridge.py --version`).

## [Unreleased] — toward v1.0.0

Queued correctness gates (all specified, not yet merged).

## [0.9.0] — 2026-09-05 (pre-release)

First public, installable shape.

### Added

- Attach-only Chrome CDP bridge (`skills/chatgpt-bridge/scripts/bridge.py`)
  driving pinned `chrome-devtools-mcp@1.8.0`; never launches or terminates
  the Chrome process.
- 15 operations: `doctor`, `inspect`, `new-page`, `status`, `wait`,
  `close-duplicates`, `select-model`, `list-conversations`,
  `switch-conversation`, `batch-send`, `upload`, `save-report`,
  `export-conversation`, `download-attachments`, `send`.
- `doctor`: read-only environment checklist (interpreter, deps, Node
  version, CDP, tabs, bound page), including a no-SDK report path.
- Trusted composer fill (select-all + `insertText`) with legacy fallback.
- Pre-session preflight: confirm flags, files, positive timeouts (including
  batch effective timeouts), dependency presence, URL shapes.
- Exact-page guards (`conversation_moved`) before send clicks and each upload.
- Final send gate: tab id + exact URL + exact composer text + live send
  control verified in one phase before clicking.
- `tests/test_bridge_pure.py`: pure + mocked-guard checks, no browser needed.

### Security

- Auth inherits the signed-in browser session; the bridge never reads or
  stores cookies, storage, or tokens.
- Uploads are explicit per-file with caller-enforced refusal of secrets;
  every external action needs action-time `--confirm-*`.
- See `SECURITY.md` for private vulnerability reporting.
