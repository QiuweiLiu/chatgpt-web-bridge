# Architecture Map — `bridge.py` (single file, intentionally not modularized)

Entry: `main()` → `build_parser()` → `run(args)` → per-operation `*_operation()`.

```
CLI (argparse operations + --version)
 │
 ├─ pre-session (no browser): preflight_args() — confirm flags, files,
 │   positive timeouts, dependency presence, URL shapes; save-report runs here
 │
 ├─ session: stdio_client → ClientSession → exactly one *_operation()
 │
 └─ output: {"ok": ...} JSON; failures carry stable codes (send_refused,
    conversation_moved, composer_changed, submission_unknown, ...)
```

## Layers (top to bottom in file)

1. **Constants & version** — `BRIDGE_VERSION`, pinned `DEFAULT_MCP_PACKAGE`,
   timeouts, limits.
2. **Page-model queries** (`evaluate_script` JS builders) — `PAGE_STATE_SCRIPT`,
   `MESSAGE_STATE_SCRIPT`, `CONVERSATION_LIST_SCRIPT`,
   `composer_insert_script()`, `upload_state_script()`.
3. **Snapshot helpers** — `find_composer()`, `composer_text()`,
   `find_send_button()`, `find_reasoning_controls()`, intelligence-menu
   matchers, upload/download target resolvers.
4. **Guards** — `high_state_is_verified()`, `validate_conversation_page()`,
   `require_exact_page()`, `require_selected_page()`, `final_send_gate()`.
5. **Operations** — `inspect/status/wait/new-page`, `list/switch-conversations`,
   `select_model_operation()`, `batch_send_operation()` (delegates items into
   `send_operation()`), `upload_operation()`, `save_report_operation()`,
   `export_conversation_operation()`, `download_attachments_operation()`,
   `send_operation()`, `doctor_operation()` (+ `doctor_local_checks()`).
6. **Transport** — `server_parameters()` (always `--browserUrl`, attach-only),
   `call_tool()` (bounded timeout), `error_message()` (TaskGroup unwrap).

## Key invariants (current)

- The bridge never launches or terminates Chrome; tabs may be opened/closed.
- Nothing sends/uploads/downloads without action-time `--confirm-*`.
- `send` verifies selected tab id + exact URL + exact composer text
  (`normalized_text`, line-preserving) + a live send control in
  `final_send_gate()`; one `message_state()` refresh still sits between the
  gate and the click (see v1.0 target below).
- Briefs travel via `--message-file`, never CLI argv.
- `save-report` / `export` / `download` require a `.project/` control plane.

## v1.0 target invariants (not yet merged)

- Single-phase final gate with no awaits between verification and click.
- High/model state re-confirmed inside the final gate.
- Message correlation (`user_message_matches`) unified on `normalized_text`
  instead of whitespace-collapsed compare.
