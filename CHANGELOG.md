# Changelog

All notable changes to the `chatgpt-bridge` skill are documented here.
Version identity also lives in code as `BRIDGE_VERSION` (`bridge.py --version`).

## [Unreleased] — toward v1.0.0

Queued correctness gates (specified, not yet merged): single-phase final
send gate without awaits before click, High/model re-check inside the gate,
unified line-preserving message correlation, doctor node-failure local-only
path. These will move into the release notes only when merged.

## [0.9.0] — 2026-09-05 (pre-release)

First public, installable shape. Behavior below is what this tag contains;
anything stronger lives in [Unreleased].

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
- Exact-page guards (`conversation_moved`) before send clicks and each upload;
  the final gate re-verifies tab id, exact URL, composer text, and a live
  send control, with one message-state refresh still preceding the click
  (see [Unreleased]).
- `tests/test_bridge_pure.py`: pure + mocked-guard checks, no browser needed.

### Security

- Auth inherits the signed-in browser session; the bridge never reads or
  stores cookies, storage, or tokens.
- Uploads are explicit per-file with caller-enforced refusal of secrets;
  every external action needs action-time `--confirm-*`.
- See `SECURITY.md` for private vulnerability reporting.
