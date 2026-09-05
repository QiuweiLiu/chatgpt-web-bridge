"""Pure-function tests for bridge.py. No browser, no MCP session needed.

Run:  python3 tests/test_bridge_pure.py   (needs Python 3.10+)
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "chatgpt-bridge" / "scripts"))

import bridge

PASS = 0


def check(name, condition):
    global PASS
    assert condition, f"FAILED: {name}"
    PASS += 1
    print(f"  ok: {name}")


def make_ns(**overrides):
    base = dict(
        operation="send",
        conversation_url="https://chatgpt.com/c/abc123",
        new_conversation=False,
        message_file=None,
        file=None,
        confirm_upload=False,
        continue_upload_on_error=False,
        confirm_send=True,
        confirm_close=False,
        confirm_model=False,
        confirm_batch=False,
        verified_high=True,
        verified_before_submit=False,
        verified_after_response=False,
        auto_save_report=False,
        model="GPT-5.6 Sol",
        model_basis="high_ui_mapping",
        effort="High",
        batch_file=None,
        response_file=None,
        project_root=Path("."),
        output=None,
        overwrite=False,
        topic=None,
        conversation_id=None,
        generation=1,
        status="unverified",
        reason=None,
        confirm_download=False,
        output_dir=None,
        overwrite_downloads=False,
        target_name=None,
        continue_download_on_error=False,
        continue_on_error=False,
        limit=200,
        timeout=120,
        navigation_timeout=30000,
        cdp_url="http://127.0.0.1:9222",
        mcp_command="npx",
        mcp_package="chrome-devtools-mcp@1.8.0",
        mcp_package_root=None,
        mcp_python=Path(sys.executable),
    )
    base.update(overrides)
    return argparse.Namespace(**base)


print("== squashed ==")
check("collapses newlines", bridge.squashed("a\n\nb") == "a b")
check("trims", bridge.squashed("  x  ") == "x")
check("empty stays empty", bridge.squashed("") == "")
check("cjk kept", bridge.squashed("兼职\n 项目") == "兼职 项目")

print("== canonical_conversation_url ==")
check("strips query", bridge.canonical_conversation_url("https://chatgpt.com/c/abc?messageId=x") == "https://chatgpt.com/c/abc")
check("strips fragment+slash", bridge.canonical_conversation_url("https://chatgpt.com/c/abc/#frag") == "https://chatgpt.com/c/abc")
check("lowercases host", bridge.canonical_conversation_url("https://ChatGPT.COM/c/abc") == "https://chatgpt.com/c/abc")
check("none passthrough", bridge.canonical_conversation_url(None) is None)
check("equal after normalize", bridge.canonical_conversation_url("https://chatgpt.com/c/abc/") == bridge.canonical_conversation_url("https://chatgpt.com/c/abc?x=1"))

print("== url shapes ==")
check("convo url", bridge.is_conversation_url("https://chatgpt.com/c/abc123"))
check("project convo url", bridge.is_conversation_url("https://chatgpt.com/g/g-p-xyz/c/abc123"))
check("landing is landing", bridge.is_landing_url("https://chatgpt.com/"))
check("landing not convo", not bridge.is_conversation_url("https://chatgpt.com/"))
check("foreign rejected", not bridge.is_conversation_url("https://example.com/") and not bridge.is_landing_url("https://example.com/"))

print("== error_message ==")
check("plain", bridge.error_message(ValueError("boom")) == "boom")
check("empty falls back", bridge.error_message(RuntimeError("")) == "")
group = BaseExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("inner-code: detail")])
check("unwraps group", bridge.error_message(group) == "inner-code: detail")
outer = RuntimeError("wrapper (2 sub-exceptions)")
check("skips wrapper summary", bridge.error_message(BaseExceptionGroup("eg", [outer, ValueError("leaf-code: x")])) == "leaf-code: x")
chained = RuntimeError("teardown noise")
chained.__cause__ = ValueError("real-code: root cause")
check("outer meaningful wins", bridge.error_message(chained) == "teardown noise")
wrapped_cause = RuntimeError("unhandled errors in a TaskGroup (1 sub-exception)")
wrapped_cause.__cause__ = ValueError("real-code: root cause")
check("wrapper descends to cause", bridge.error_message(wrapped_cause) == "real-code: root cause")

print("== preflight_url_shape ==")
for op in ("send", "status", "doctor"):
    ns = make_ns(operation=op, conversation_url="https://example.com/evil")
    try:
        bridge.preflight_url_shape(ns)
        check(f"{op} foreign rejected", False)
    except RuntimeError as e:
        check(f"{op} foreign rejected", "conversation_url_unavailable" in str(e))

print("== preflight_args ==")
with tempfile.TemporaryDirectory() as tmp:
    msg = Path(tmp) / "m.txt"
    msg.write_text("hello", encoding="utf-8")
    ns = make_ns(operation="send", message_file=msg, timeout=0)
    try:
        bridge.preflight_args(ns)
        check("send timeout 0 rejected", False)
    except ValueError as e:
        check("send timeout 0 rejected", "positive" in str(e))
    ns = make_ns(operation="send", message_file=msg, conversation_url="", new_conversation=False)
    try:
        bridge.preflight_args(ns)
        check("send empty url rejected", False)
    except RuntimeError as e:
        check("send empty url rejected", "conversation_url_unavailable" in str(e))
    batch = Path(tmp) / "b.json"
    batch.write_text(json.dumps({"items": [{"conversation_url": "https://chatgpt.com/c/abc", "message_file": str(msg)}]}), encoding="utf-8")
    ns = make_ns(operation="batch-send", batch_file=batch, confirm_batch=True, timeout=0)
    try:
        bridge.preflight_args(ns)
        check("batch parent timeout 0 rejected", False)
    except ValueError as e:
        check("batch parent timeout 0 rejected", "positive effective timeout" in str(e))
    empty = Path(tmp) / "e.txt"
    empty.write_text("   \n", encoding="utf-8")
    batch2 = Path(tmp) / "b2.json"
    batch2.write_text(json.dumps({"items": [{"conversation_url": "https://chatgpt.com/c/abc", "message_file": str(empty)}]}), encoding="utf-8")
    ns = make_ns(operation="batch-send", batch_file=batch2, confirm_batch=True)
    try:
        bridge.preflight_args(ns)
        check("batch empty message rejected", False)
    except ValueError as e:
        check("batch empty message rejected", "non-empty" in str(e))

print("== composer_insert_script escaping ==")
tricky = 'quote " backslash \\ newline\n cjk 兼职 <tag>'
script = bridge.composer_insert_script(tricky)
check("json literal embedded", json.dumps(tricky, ensure_ascii=False) in script)
check("no raw interpolation", "<tag>" in script)  # inside JSON string only

print(f"\nALL {PASS} CHECKS PASSED")
