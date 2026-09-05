#!/usr/bin/env python3
"""Use the existing Chrome DevTools MCP for safe ChatGPT Web operations."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.types import ListRootsResult, Root

    _MCP_SDK_AVAILABLE = True
except ImportError:
    # Doctor must still report a missing SDK instead of crashing at import.
    ClientSession = Any
    StdioServerParameters = Any
    stdio_client = None
    ListRootsResult = Any
    Root = Any
    _MCP_SDK_AVAILABLE = False


DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_MCP_PACKAGE = "chrome-devtools-mcp@1.8.0"
MCP_CALL_TIMEOUT_SECONDS = 30
DEFAULT_NAVIGATION_TIMEOUT_MS = 30_000
POST_FILL_WAIT_SECONDS = 5
UPLOAD_VERIFY_WAIT_SECONDS = 10
POLL_INTERVAL_SECONDS = 0.5
RESPONSE_STABILITY_POLLS = 3
MAX_BATCH_ITEMS = 20
MAX_UPLOAD_FILES = 20
CONVERSATION_LIST_LIMIT = 200
DOWNLOAD_STABILITY_POLLS = 2
DOWNLOAD_POLL_INTERVAL_SECONDS = 0.5
DOWNLOAD_CONTROL_TIMEOUT_SECONDS = 5
DEFAULT_MCP_PYTHON = Path(
    os.environ.get("CHATGPT_MCP_PYTHON", sys.executable)
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Use Chrome DevTools MCP without automatic browser launch or message submission."
    )
    parser.add_argument(
        "operation",
        choices=(
            "inspect",
            "new-page",
            "status",
            "wait",
            "close-duplicates",
            "select-model",
            "list-conversations",
            "switch-conversation",
            "batch-send",
            "upload",
            "save-report",
            "export-conversation",
            "download-attachments",
            "send",
            "doctor",
        ),
        help=(
            "Read state, open a background page, read status, wait for completion, "
            "close duplicate target pages, select the model, list or switch conversations, "
            "run guarded batch sends, upload confirmed files, save a report, export a visible "
            "conversation, download a confirmed attachment, send a brief, "
            "or run a read-only environment check."
        ),
    )
    parser.add_argument(
        "--conversation-url",
        help="Exact ChatGPT URL; for first binding, pass https://chatgpt.com/ with --new-conversation.",
    )
    parser.add_argument(
        "--new-conversation",
        action="store_true",
        help="Allow send on the unbound ChatGPT landing page for first binding or rollover.",
    )
    parser.add_argument(
        "--message-file",
        type=Path,
        help="UTF-8 brief file; avoids putting message text in process arguments.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        action="append",
        help=(
            "Exact local document path to upload; repeat --file for a queue. Files never upload "
            "by themselves without confirmation."
        ),
    )
    parser.add_argument(
        "--confirm-upload",
        action="store_true",
        help="Explicit action-time confirmation for the external file upload.",
    )
    parser.add_argument(
        "--continue-upload-on-error",
        action="store_true",
        help="Continue later files after an upload failure; default is stop for safety.",
    )
    parser.add_argument(
        "--confirm-send",
        action="store_true",
        help="Explicit action-time confirmation for the external send.",
    )
    parser.add_argument(
        "--confirm-close",
        action="store_true",
        help="Explicit action-time confirmation for closing duplicate target pages.",
    )
    parser.add_argument(
        "--confirm-model",
        action="store_true",
        help="Explicit action-time confirmation for changing the visible model/reasoning state.",
    )
    parser.add_argument(
        "--confirm-batch",
        action="store_true",
        help="Explicit action-time confirmation for every external send described by the batch file.",
    )
    parser.add_argument(
        "--verified-high",
        action="store_true",
        help="The caller has just visibly verified the requested High reasoning state.",
    )
    parser.add_argument(
        "--verified-before-submit",
        action="store_true",
        help="Record that the model/page gate was verified before the saved research response.",
    )
    parser.add_argument(
        "--verified-after-response",
        action="store_true",
        help="Record that the response was verified after completion before saving the report.",
    )
    parser.add_argument(
        "--auto-save-report",
        action="store_true",
        help="After a verified send, save its response using the report output options.",
    )
    parser.add_argument(
        "--model",
        default="GPT-5.6 Sol",
        help="Requested ChatGPT model for select-model (default: GPT-5.6 Sol).",
    )
    parser.add_argument(
        "--model-basis",
        choices=("explicit_model_label", "high_ui_mapping"),
        default="high_ui_mapping",
        help="Model verification basis recorded by save-report (default: high_ui_mapping).",
    )
    parser.add_argument(
        "--effort",
        default="High",
        help="Requested reasoning level for select-model (default: High).",
    )
    parser.add_argument(
        "--batch-file",
        type=Path,
        help="UTF-8 JSON batch file containing exact conversation_url and message_file items.",
    )
    parser.add_argument(
        "--response-file",
        type=Path,
        help="UTF-8 Markdown or bridge JSON response file for save-report.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="Project OS root for report/export/download outputs (default: current directory).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Exact project-relative or project-contained output path for report/export.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow save-report or export-conversation to replace an existing output file.",
    )
    parser.add_argument(
        "--topic",
        help="Short topic slug used by save-report's default docs/research filename.",
    )
    parser.add_argument(
        "--conversation-id",
        help="Verified opaque conversation ID recorded by save-report when the URL is not supplied.",
    )
    parser.add_argument(
        "--generation",
        type=int,
        default=1,
        help="Conversation generation recorded by save-report (default: 1).",
    )
    parser.add_argument(
        "--status",
        choices=("valid", "invalid", "unverified"),
        default="unverified",
        help="Research report status recorded by save-report (default: unverified).",
    )
    parser.add_argument(
        "--reason",
        help="Optional reason recorded by save-report for an invalid or unverified result.",
    )
    parser.add_argument(
        "--confirm-download",
        action="store_true",
        help="Explicit action-time confirmation for clicking a visible attachment download control.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Project-contained directory for download-attachments (default: outputs/downloads).",
    )
    parser.add_argument(
        "--overwrite-downloads",
        action="store_true",
        help="Allow download-attachments to use an existing output directory and files.",
    )
    parser.add_argument(
        "--target-name",
        action="append",
        help=(
            "Exact or partial semantic download-control label; repeat to download multiple "
            "controls. With no label, exactly one visible target is required."
        ),
    )
    parser.add_argument(
        "--continue-download-on-error",
        action="store_true",
        help="Continue later download controls after a failed attachment download.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue to later batch items after an item fails; default is stop for safety.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=CONVERSATION_LIST_LIMIT,
        help="Maximum visible conversations returned by list-conversations (default: 200).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Maximum response wait in seconds for send or wait (default: 120).",
    )
    parser.add_argument(
        "--navigation-timeout",
        type=int,
        default=DEFAULT_NAVIGATION_TIMEOUT_MS,
        help=(
            "Maximum new-page navigation wait in milliseconds; used only by new-page "
            "(default: 30000)."
        ),
    )
    parser.add_argument(
        "--cdp-url",
        default=os.environ.get("CDP_URL", DEFAULT_CDP_URL),
        help="Existing Chrome CDP endpoint.",
    )
    parser.add_argument(
        "--mcp-command",
        default=os.environ.get("CHROME_DEVTOOLS_MCP_COMMAND", "npx"),
        help="MCP launcher, normally npx.",
    )
    parser.add_argument(
        "--mcp-package",
        default=os.environ.get("CHROME_DEVTOOLS_MCP_PACKAGE", DEFAULT_MCP_PACKAGE),
        help="NPM package spec when using npx.",
    )
    parser.add_argument(
        "--mcp-package-root",
        type=Path,
        help="Optional local chrome-devtools-mcp package root; useful for offline tests.",
    )
    parser.add_argument(
        "--mcp-python",
        type=Path,
        default=Path(os.environ.get("CHATGPT_MCP_PYTHON", DEFAULT_MCP_PYTHON)),
        help="Python executable containing the MCP client SDK.",
    )
    return parser


def text_from_result(result: Any) -> str:
    return "\n".join(
        value
        for content in result.content
        if (value := getattr(content, "text", None)) is not None
    )


def structured_from_result(result: Any) -> dict[str, Any]:
    value = result.structuredContent
    return value if isinstance(value, dict) else {}


async def call_tool(
    session: ClientSession, name: str, arguments: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    try:
        result = await asyncio.wait_for(
            session.call_tool(name, arguments or {}), timeout=MCP_CALL_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"mcp_timeout: {name} exceeded {MCP_CALL_TIMEOUT_SECONDS}s"
        ) from exc
    text = text_from_result(result)
    if result.isError:
        raise RuntimeError(f"{name} failed: {text}")
    return text, structured_from_result(result)


def pages_from_result(text: str, structured: dict[str, Any]) -> list[dict[str, Any]]:
    pages = structured.get("pages")
    if isinstance(pages, list):
        return [page for page in pages if isinstance(page, dict)]

    parsed = []
    pattern = re.compile(r"^(\d+): .*? \((https?://[^)]+)\)( \[selected\])?$")
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            parsed.append(
                {
                    "id": int(match.group(1)),
                    "url": match.group(2),
                    "selected": match.group(3) is not None,
                }
            )
    return parsed


def is_chatgpt_url(url: str | None) -> bool:
    return bool(
        url
        and (
            url.startswith("https://chatgpt.com/")
            or url.startswith("https://chat.openai.com/")
        )
    )


def is_conversation_url(url: str | None) -> bool:
    return bool(
        url
        and re.match(
            r"^https://(?:chatgpt\.com|chat\.openai\.com)/(?:g/[^/?#]+/)?c/[^/?#]+/?(?:[?#].*)?$",
            url,
        )
    )


def select_chat_page(
    pages: list[dict[str, Any]], expected_url: str | None = None
) -> dict[str, Any]:
    chat_pages = [page for page in pages if is_chatgpt_url(page.get("url"))]
    if expected_url is not None:
        matches = [page for page in chat_pages if page.get("url") == expected_url]
        if len(matches) != 1:
            raise RuntimeError(
                f"conversation_url_mismatch: expected {expected_url!r}, "
                f"observed {[page.get('url') for page in chat_pages]!r}"
            )
        return matches[0]
    if len(chat_pages) != 1:
        raise RuntimeError(
            "conversation_unbound: expected exactly one ChatGPT page when no URL was supplied; "
            f"observed {len(chat_pages)}"
        )
    return chat_pages[0]


def validate_new_page_url(target_url: str) -> None:
    if target_url.rstrip("/") in {"https://chatgpt.com", "https://chat.openai.com"}:
        return
    if not is_conversation_url(target_url):
        raise RuntimeError(
            f"conversation_url_unavailable: new-page target must be an exact ChatGPT "
            f"conversation URL or landing page, observed {target_url!r}"
        )


def walk_snapshot(node: dict[str, Any]):
    yield node
    for child in node.get("children", []):
        if isinstance(child, dict):
            yield from walk_snapshot(child)


def snapshot_nodes(snapshot: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [
        node
        for node in walk_snapshot(snapshot)
        if node.get("role") == role and node.get("id")
    ]


def find_composer(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    textboxes = snapshot_nodes(snapshot, "textbox")
    likely_composers = [
        node
        for node in textboxes
        if re.search(r"chat|message|prompt|聊天|消息", str(node.get("name", "")), re.IGNORECASE)
    ]
    return (likely_composers or textboxes)[0] if textboxes else None


def composer_text(node: dict[str, Any]) -> str:
    """Return draft text without treating the accessible placeholder name as content."""
    value = node.get("value")
    if isinstance(value, str) and value.strip():
        return value.strip()

    parts: list[str] = []

    def collect(child: dict[str, Any]) -> None:
        if child.get("role") == "StaticText" and isinstance(child.get("name"), str):
            parts.append(child["name"])
        for grandchild in child.get("children", []):
            if isinstance(grandchild, dict):
                collect(grandchild)

    collect(node)
    return "\n".join(part.strip() for part in parts if part.strip())


def find_reasoning_controls(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    controls = []
    for node in snapshot_nodes(snapshot, "button"):
        name = str(node.get("name", "")).strip()
        if re.search(r"high|\b高\b|reason|thinking|思考", name, re.IGNORECASE):
            controls.append({"id": node["id"], "name": name})
    return controls


MODEL_LABEL_RE = re.compile(
    r"gpt|\bo[1-4]\b|model|模型|sol|terra|luna", re.IGNORECASE
)
EFFORT_LABEL_RE = re.compile(
    r"high|medium|low|instant|reason|thinking|\b高\b|\b中\b|\b低\b|思考",
    re.IGNORECASE,
)
INTELLIGENCE_OPTION_ROLES = {
    "button",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "radio",
}


def snapshot_label(node: dict[str, Any]) -> str:
    return " ".join(
        str(node.get(key, "")).strip()
        for key in ("name", "value", "aria-label")
        if str(node.get(key, "")).strip()
    ).strip()


def selected_marker(node: dict[str, Any]) -> bool:
    values = [
        node.get("checked"),
        node.get("selected"),
        node.get("pressed"),
        node.get("data-state"),
    ]
    return any(
        str(value or "").strip().lower() in {"true", "1", "selected", "checked", "on", "active"}
        for value in values
    )


def find_intelligence_triggers(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Find visible model/reasoning triggers, excluding transient menu options."""
    candidates = []
    for node in snapshot_nodes(snapshot, "button") + snapshot_nodes(snapshot, "combobox"):
        name = snapshot_label(node)
        lowered = name.casefold()
        if not name or re.search(r"send|发送|attach|upload|上传|dictate|语音", lowered):
            continue
        if not (MODEL_LABEL_RE.search(name) or EFFORT_LABEL_RE.search(name)):
            continue
        score = 0
        if MODEL_LABEL_RE.search(name):
            score += 3
        if EFFORT_LABEL_RE.search(name):
            score += 2
        if re.search(r"model|模型|gpt|sol|terra|luna", name, re.IGNORECASE):
            score += 2
        if re.search(r"high|\b高\b|reason|thinking|思考", name, re.IGNORECASE):
            score += 1
        candidates.append({"id": node["id"], "name": name, "score": score})
    return sorted(candidates, key=lambda item: (-item["score"], str(item["id"])))


def find_intelligence_options(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Find semantic options inside a visible menu/listbox/dialog."""
    options: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], in_menu: bool = False) -> None:
        role = str(node.get("role", "")).casefold()
        menu_context = in_menu or role in {"menu", "listbox", "dialog"}
        if menu_context and role in INTELLIGENCE_OPTION_ROLES and node.get("id"):
            name = snapshot_label(node)
            if name and not re.search(r"send|发送|attach|upload|上传", name, re.IGNORECASE):
                options.append(
                    {
                        "id": node["id"],
                        "name": name,
                        "role": node.get("role"),
                        "selected": selected_marker(node),
                    }
                )
        for child in node.get("children", []):
            if isinstance(child, dict):
                walk(child, menu_context)

    walk(snapshot)
    return options


def compact_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").casefold())


def model_label_matches(name: str, requested: str) -> bool:
    requested_key = compact_label(requested)
    if requested_key in {"gpt56sol", "gpt5sol", "sol"}:
        return bool(
            re.search(r"\bgpt\s*[-.]?\s*5(?:\s*[-.]?\s*6)?\s*sol\b", name, re.IGNORECASE)
            or re.search(r"\bsol\b", name, re.IGNORECASE)
        )
    candidate_key = compact_label(name)
    return bool(requested_key and (candidate_key == requested_key or requested_key in candidate_key))


def effort_label_matches(name: str, requested: str) -> bool:
    requested_key = compact_label(requested)
    aliases = {
        "high": {"high", "高"},
        "medium": {"medium", "中"},
        "low": {"low", "低"},
        "instant": {"instant", "即时", "快速"},
    }
    wanted = aliases.get(requested_key, {requested})
    name_key = compact_label(name)
    return any(
        compact_label(alias) == name_key or compact_label(alias) in name_key
        for alias in wanted
    )


def find_send_button(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    for node in snapshot_nodes(snapshot, "button"):
        name = str(node.get("name", "")).strip()
        if re.search(
            r"^(send|发送|提交)(?:\s*(prompt|message|消息|訊息|提示(?:词)?))?$",
            name,
            re.IGNORECASE,
        ):
            return node
    return None


UPLOAD_NAME_RE = re.compile(
    r"\battach\b|\battachments?\b|\bupload\b|\bfiles?\b|\bdocuments?\b|\bphotos?\b|\bimages?\b|"
    r"\badd\b.*\bfiles?\b|添加.*文件|上传|文件|附件|图片",
    re.IGNORECASE,
)
LOCAL_UPLOAD_NAME_RE = re.compile(
    r"从电脑上传|从设备上传|上传文件|upload\s+from\s+(?:your\s+)?computer|"
    r"upload\s+from\s+(?:your\s+)?device|upload\s+files?",
    re.IGNORECASE,
)
DOWNLOAD_NAME_RE = re.compile(
    r"\bdownload\b|\bsave\s+(?:file|attachment|as)\b|下载|保存(?:文件|附件)?",
    re.IGNORECASE,
)
SNAPSHOT_NODE_LINE_RE = re.compile(
    r"uid=(\S+)\s+([^\s]+)\s+\"([^\"]*)\""
)


def find_upload_targets(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Find semantic attachment controls without guessing coordinates or selectors."""
    targets = []
    allowed_roles = {"button", "file", "input", "link", "menuitem", "generic"}
    for node in walk_snapshot(snapshot):
        node_id = node.get("id")
        role = str(node.get("role", "")).lower()
        name = str(node.get("name", "")).strip()
        if not node_id or role not in allowed_roles or not name:
            continue
        if not UPLOAD_NAME_RE.search(name):
            continue
        score = 0
        if role in {"file", "input"}:
            score += 4
        if re.search(r"\battach\b|\battachments?\b|添加.*文件|附件", name, re.IGNORECASE):
            score += 3
        if re.search(
            r"\bupload\b|上传|\badd\b.*\bfiles?\b|\bfiles?\b|\bdocuments?\b|文件",
            name,
            re.IGNORECASE,
        ):
            score += 2
        targets.append(
            {
                "id": node_id,
                "name": name,
                "role": node.get("role"),
                "score": score,
            }
        )
    return sorted(targets, key=lambda target: (-target["score"], str(target["id"])))


def select_upload_target(snapshot: dict[str, Any]) -> dict[str, Any]:
    targets = find_upload_targets(snapshot)
    if not targets:
        raise RuntimeError(
            "upload_control_unavailable: no semantic file or attachment control was found"
        )
    best_score = targets[0]["score"]
    best = [target for target in targets if target["score"] == best_score]
    if len(best) != 1:
        raise RuntimeError(
            "upload_control_ambiguous: multiple equally-ranked attachment controls were found"
        )
    return best[0]


def upload_menu_is_open(snapshot: dict[str, Any]) -> bool:
    """Detect the expanded attachment menu from the latest semantic snapshot."""
    for node in walk_snapshot(snapshot):
        if str(node.get("name", "")).strip() != "添加文件等":
            continue
        expanded = node.get("expanded")
        if expanded is True or str(expanded).strip().lower() == "true":
            return True
    return False


def select_local_upload_target(verbose_snapshot_text: str) -> dict[str, str]:
    """Select ChatGPT's local-upload menu item from a verbose a11y snapshot."""
    candidates: dict[str, dict[str, str]] = {}
    for line in verbose_snapshot_text.splitlines():
        match = SNAPSHOT_NODE_LINE_RE.search(line)
        if not match:
            continue
        node_id, role, name = match.groups()
        if role.casefold() in {"inlinetextbox", "text"}:
            continue
        if not LOCAL_UPLOAD_NAME_RE.search(name):
            continue
        candidates[node_id] = {"id": node_id, "role": role, "name": name}

    if not candidates:
        raise RuntimeError(
            "upload_control_unavailable: expanded attachment menu has no local-upload item"
        )
    if len(candidates) != 1:
        raise RuntimeError(
            "upload_control_ambiguous: expanded attachment menu has multiple local-upload items"
        )
    return next(iter(candidates.values()))


def find_download_targets(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Find visible semantic download controls without selecting by coordinates or href guesses."""
    targets: list[dict[str, Any]] = []
    allowed_roles = {"button", "link", "menuitem", "generic"}
    for node in walk_snapshot(snapshot):
        node_id = node.get("id")
        role = str(node.get("role", "")).casefold()
        name = snapshot_label(node)
        if not node_id or role not in allowed_roles or not name:
            continue
        if not DOWNLOAD_NAME_RE.search(name):
            continue
        score = 1
        if role in {"button", "link", "menuitem"}:
            score += 2
        if re.search(r"\bdownload\b|下载", name, re.IGNORECASE):
            score += 2
        targets.append(
            {
                "id": node_id,
                "name": name,
                "role": node.get("role"),
                "score": score,
            }
        )
    return sorted(targets, key=lambda target: (-target["score"], str(target["id"])))


def select_download_targets(
    snapshot: dict[str, Any], requested_names: list[str] | None
) -> list[dict[str, Any]]:
    targets = find_download_targets(snapshot)
    if not targets:
        raise RuntimeError(
            "download_control_unavailable: no visible semantic download control was found"
        )
    if not requested_names:
        best_score = targets[0]["score"]
        best = [target for target in targets if target["score"] == best_score]
        if len(best) != 1:
            raise RuntimeError(
                "download_control_ambiguous: multiple visible download controls were found; "
                "pass --target-name for each exact or partial label"
            )
        return best

    selected: list[dict[str, Any]] = []
    for requested in requested_names:
        needle = compact_label(requested)
        if not needle:
            raise ValueError("download_target_invalid: --target-name cannot be empty")
        matches = [
            target
            for target in targets
            if needle in compact_label(target["name"])
        ]
        if len(matches) != 1:
            if not matches:
                raise RuntimeError(
                    f"download_target_unavailable: no visible download control matched {requested!r}"
                )
            raise RuntimeError(
                f"download_control_ambiguous: {len(matches)} visible controls matched {requested!r}"
            )
        if matches[0]["id"] in {target["id"] for target in selected}:
            raise ValueError(f"download_target_invalid: duplicate target {requested!r}")
        selected.append(matches[0])
    return selected


async def open_local_upload_target(
    session: ClientSession, page_id: int, snapshot: dict[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    """Open the attachment menu and return its exact local-upload semantic UID."""
    proxy = select_upload_target(snapshot)
    if not upload_menu_is_open(snapshot):
        await call_tool(
            session,
            "click",
            {"pageId": page_id, "uid": proxy["id"], "includeSnapshot": False},
        )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    verbose_text, _ = await call_tool(
        session,
        "take_snapshot",
        {"pageId": page_id, "verbose": True},
    )
    return select_local_upload_target(verbose_text), proxy


def normalized_message(value: Any) -> str:
    """Normalize DOM whitespace for exact-enough visible-message correlation."""
    return " ".join(str(value or "").split())


def state_count(state: dict[str, Any], key: str) -> int:
    try:
        return int(state.get(key, 0))
    except (TypeError, ValueError):
        return 0


def user_message_matches(state: dict[str, Any], message: str) -> bool:
    value = state.get("last_user_text")
    return isinstance(value, str) and normalized_message(value) == normalized_message(message)


def submission_started(
    initial: dict[str, Any], current: dict[str, Any], message: str
) -> bool:
    """Return true only for a visible generation or a newly visible exact user message."""
    if current.get("generating"):
        return True
    return user_message_matches(current, message) and state_count(
        current, "user_count"
    ) > state_count(initial, "user_count")


def response_is_new(
    initial: dict[str, Any], current: dict[str, Any], message: str
) -> bool:
    """Require both a new exact user turn and a new completed assistant turn."""
    if current.get("generating") or not user_message_matches(current, message):
        return False
    if not normalized_message(current.get("last_assistant_text")):
        return False
    new_user = state_count(current, "user_count") > state_count(initial, "user_count")
    new_assistant = (
        state_count(current, "assistant_count")
        > state_count(initial, "assistant_count")
        or (
            current.get("last_assistant_key")
            and current.get("last_assistant_key") != initial.get("last_assistant_key")
        )
        or normalized_message(current.get("last_assistant_text"))
        != normalized_message(initial.get("last_assistant_text"))
    )
    return new_user and new_assistant


def response_signature(state: dict[str, Any]) -> tuple[Any, ...]:
    """Return a DOM-level response identity used for short stability sampling."""
    return (
        state.get("last_assistant_key"),
        normalized_message(state.get("last_assistant_text")),
        state_count(state, "assistant_count"),
    )


def parse_script_json(text: str) -> dict[str, Any]:
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    candidate = match.group(1) if match else text
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"evaluate_script returned non-JSON output: {text}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("evaluate_script returned an unexpected value")
    return value


def conversation_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.match(
        r"^https://(?:chatgpt\.com|chat\.openai\.com)/(?:g/[^/?#]+/)?c/([^/?#]+)",
        url,
    )
    return match.group(1) if match else None


def safe_slug(value: str, default: str = "untitled") -> str:
    compact = re.sub(r"\s+", "-", value.strip())
    compact = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "-", compact)
    compact = re.sub(r"-{2,}", "-", compact).strip(".-")
    return (compact[:80] or default).strip(".-") or default


def require_project_control_plane(args: argparse.Namespace) -> Path:
    root = args.project_root.expanduser().resolve()
    required = (root / ".project" / "PROJECT.md", root / ".project" / "STATE.md")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "project_control_plane_missing: expected .project/PROJECT.md and "
            f".project/STATE.md under {root}; missing {missing}"
        )
    return root


def project_contained_path(root: Path, requested: Path) -> Path:
    candidate = requested.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"output_path_outside_project: {resolved} is not contained by {root}"
        ) from exc
    if not relative.parts:
        raise ValueError("output_path_invalid: output path must be a file below the project root")
    return resolved


def write_text_atomically(path: Path, content: str, overwrite: bool) -> None:
    if path.exists() and path.is_dir():
        raise IsADirectoryError(f"output_path_invalid: {path} is a directory")
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"output_exists: {path} already exists; pass --overwrite only after confirming replacement"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(str(value), ensure_ascii=False)


def frontmatter(values: dict[str, Any]) -> str:
    lines = ["---"]
    lines.extend(f"{key}: {yaml_scalar(value)}" for key, value in values.items())
    lines.append("---")
    return "\n".join(lines)


def load_response_text(response_path: Path) -> tuple[str, dict[str, Any]]:
    path = response_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"report_input_not_found: {path}")
    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, {}
    if isinstance(payload, dict) and isinstance(payload.get("response"), str):
        return payload["response"], payload
    if isinstance(payload, str):
        return payload, {}
    raise ValueError(
        "report_input_invalid: JSON response file must contain a string 'response' field "
        "or be plain Markdown"
    )


def normalize_upload_files(raw_files: Any) -> list[Path]:
    if raw_files is None:
        values: list[Any] = []
    elif isinstance(raw_files, (str, Path)):
        values = [raw_files]
    else:
        values = list(raw_files)
    if not values:
        raise ValueError("upload requires at least one --file")
    if len(values) > MAX_UPLOAD_FILES:
        raise ValueError(
            f"upload_invalid: at most {MAX_UPLOAD_FILES} files are allowed per run"
        )

    files: list[Path] = []
    seen_paths: set[Path] = set()
    seen_names: set[str] = set()
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"upload_file_not_found: {path}")
        if path in seen_paths:
            raise ValueError(f"upload_invalid: duplicate file path {path}")
        if path.name.casefold() in seen_names:
            raise ValueError(
                "upload_invalid: duplicate basenames are not allowed because exact filename "
                f"verification would be ambiguous ({path.name})"
            )
        seen_paths.add(path)
        seen_names.add(path.name.casefold())
        files.append(path)
    return files


def report_output_path(args: argparse.Namespace, root: Path) -> Path:
    if args.output is None:
        if not args.topic or not args.topic.strip():
            raise ValueError(
                "report_output_invalid: pass --topic for the default docs/research path "
                "or provide --output"
            )
        return project_contained_path(
            root,
            Path("docs")
            / "research"
            / f"{datetime.now().date().isoformat()}_{safe_slug(args.topic)}.md",
        )
    return project_contained_path(root, args.output)


def save_report_document(
    args: argparse.Namespace,
    response: str,
    payload: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    if args.generation <= 0:
        raise ValueError("save-report requires a positive --generation")
    if args.status == "valid" and not (
        args.verified_before_submit and args.verified_after_response
    ):
        raise ValueError(
            "report_unverified: --status valid requires both --verified-before-submit "
            "and --verified-after-response"
        )

    if not response.strip():
        raise ValueError("report_input_invalid: response content is empty")

    conversation_url = args.conversation_url
    if conversation_url is None:
        payload_url = payload.get("conversation_url_after") or payload.get("conversation_url")
        if isinstance(payload_url, str) and is_conversation_url(payload_url):
            conversation_url = payload_url
    if conversation_url is not None and not is_conversation_url(conversation_url):
        raise ValueError(
            "conversation_url_unavailable: save-report metadata requires an exact conversation URL"
        )
    derived_id = conversation_id_from_url(conversation_url)
    conversation_id = args.conversation_id or derived_id
    if args.conversation_id and derived_id and args.conversation_id != derived_id:
        raise ValueError(
            "conversation_id_mismatch: --conversation-id does not match --conversation-url"
        )

    output = report_output_path(args, root)

    timestamp = datetime.now().astimezone().isoformat(timespec="minutes")
    metadata: dict[str, Any] = {
        "research_backend": "ChatGPT Web",
        "requested_model": args.model,
        "requested_reasoning": args.effort,
        "model_verification_basis": args.model_basis,
        "conversation_id": conversation_id,
        "conversation_generation": args.generation,
        "conversation_url": conversation_url,
        "verified_before_submit": args.verified_before_submit,
        "verified_after_response": args.verified_after_response,
        "timestamp": timestamp,
        "status": args.status,
    }
    if args.reason:
        metadata["reason"] = args.reason
    content = frontmatter(metadata) + "\n\n# Research Report\n\n" + response.rstrip() + "\n"
    write_text_atomically(output, content, args.overwrite)
    return {
        "operation": "save-report",
        "output_path": str(output),
        "status": args.status,
        "conversation_id": conversation_id,
        "conversation_url": conversation_url,
        "response_characters": len(response),
    }


def save_report_operation(args: argparse.Namespace) -> dict[str, Any]:
    root = require_project_control_plane(args)
    if args.response_file is None:
        raise ValueError("save-report requires --response-file")
    response, payload = load_response_text(args.response_file)
    return save_report_document(args, response, payload, root)


def render_conversation_export(state: dict[str, Any]) -> str:
    conversation_url = state.get("url")
    conversation_id = conversation_id_from_url(conversation_url)
    completeness = state.get("completeness") or "visible_dom_only"
    metadata = {
        "export_backend": "ChatGPT Web DOM",
        "conversation_id": conversation_id,
        "conversation_url": conversation_url,
        "title": state.get("title") or None,
        "source": state.get("source") or "visible_dom",
        "completeness": completeness,
        "message_count": state.get("message_count", 0),
        "timestamp": datetime.now().astimezone().isoformat(timespec="minutes"),
    }
    lines = [frontmatter(metadata), "", "# Conversation Export", ""]
    messages = state.get("messages", [])
    for index, message in enumerate(messages, start=1):
        role = re.sub(r"[\r\n]+", " ", str(message.get("role") or "unknown")).strip()
        role = role or "unknown"
        body = str(message.get("text") or "").strip()
        lines.append(f"## {index}. {role}")
        lines.append(body or "_(empty visible message)_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def cdp_json_url(cdp_url: str, resource: str) -> str:
    base = cdp_url.rstrip("/")
    if not re.match(r"^https?://[^/]+(?:/[^/]*)?$", base):
        raise RuntimeError(
            f"download_control_unavailable: unsupported CDP endpoint {cdp_url!r}"
        )
    return f"{base}/json/{resource.lstrip('/')}"


def read_cdp_json(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_CONTROL_TIMEOUT_SECONDS) as response:
            value = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"download_control_unavailable: could not read {url}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("download_control_unavailable: CDP metadata was not an object")
    return value


async def cdp_browser_command(
    cdp_url: str, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError(
            "download_control_unavailable: the bridge Python environment lacks websockets"
        ) from exc

    version = await asyncio.to_thread(read_cdp_json, cdp_json_url(cdp_url, "version"))
    websocket_url = version.get("webSocketDebuggerUrl")
    if not isinstance(websocket_url, str) or not websocket_url:
        raise RuntimeError(
            "download_control_unavailable: CDP did not expose a browser websocket"
        )
    message_id = 1
    try:
        async with websockets.connect(
            websocket_url,
            open_timeout=DOWNLOAD_CONTROL_TIMEOUT_SECONDS,
            close_timeout=DOWNLOAD_CONTROL_TIMEOUT_SECONDS,
        ) as websocket:
            await websocket.send(
                json.dumps({"id": message_id, "method": method, "params": params})
            )
            deadline = asyncio.get_running_loop().time() + DOWNLOAD_CONTROL_TIMEOUT_SECONDS
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"CDP command {method} timed out")
                raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                event = json.loads(raw)
                if event.get("id") != message_id:
                    continue
                if event.get("error"):
                    raise RuntimeError(str(event["error"]))
                result = event.get("result", {})
                return result if isinstance(result, dict) else {}
    except Exception as exc:
        if isinstance(exc, RuntimeError) and str(exc).startswith(
            "download_control_unavailable:"
        ):
            raise
        raise RuntimeError(f"download_control_unavailable: CDP {method} failed") from exc


async def set_download_behavior(
    cdp_url: str, behavior: str, download_path: Path | None = None
) -> None:
    params: dict[str, Any] = {"behavior": behavior}
    if download_path is not None:
        params["downloadPath"] = str(download_path)
    if behavior == "allow":
        params["eventsEnabled"] = True
    await cdp_browser_command(cdp_url, "Browser.setDownloadBehavior", params)


def download_directory_state(directory: Path) -> dict[Path, tuple[int, int]]:
    state: dict[Path, tuple[int, int]] = {}
    for path in directory.iterdir():
        if not path.is_file():
            continue
        stat = path.stat()
        state[path] = (stat.st_size, stat.st_mtime_ns)
    return state


async def wait_for_download(
    directory: Path, before: dict[Path, tuple[int, int]], timeout: int
) -> list[Path]:
    deadline = asyncio.get_running_loop().time() + timeout
    stable_counts: dict[Path, int] = {}
    previous_signatures: dict[Path, tuple[int, int]] = {}
    while True:
        current = download_directory_state(directory)
        candidates = [
            path
            for path, signature in current.items()
            if path not in before or before[path] != signature
        ]
        partial_before = {
            path for path in before if path.name.endswith(".crdownload")
        }
        partial_now = {
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.name.endswith(".crdownload")
            and path not in partial_before
        }
        if candidates and not partial_now:
            ready: list[Path] = []
            for path in candidates:
                signature = current[path]
                if previous_signatures.get(path) == signature:
                    stable_counts[path] = stable_counts.get(path, 1) + 1
                else:
                    previous_signatures[path] = signature
                    stable_counts[path] = 1
                if stable_counts[path] >= DOWNLOAD_STABILITY_POLLS:
                    ready.append(path)
            if ready:
                return sorted(ready)
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError(
                f"download_verification_failed: no stable new file appeared in {directory} "
                f"within {timeout}s"
            )
        await asyncio.sleep(DOWNLOAD_POLL_INTERVAL_SECONDS)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "file_name": path.name,
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


PAGE_STATE_SCRIPT = r"""() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const controls = Array.from(document.querySelectorAll('button,[role="button"]'))
    .filter(visible)
    .map((el) => ({
      text: (el.innerText || '').trim(),
      aria: el.getAttribute('aria-label') || '',
      pressed: el.getAttribute('aria-pressed'),
      checked: el.getAttribute('aria-checked'),
      selected: el.getAttribute('aria-selected'),
      data_state: el.getAttribute('data-state'),
    }))
    .filter((item) => /high|高|reason|thinking|思考|send|发送|停止|stop/i.test(`${item.text} ${item.aria}`));
  const assistantNodes = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
  const fallbackArticles = Array.from(document.querySelectorAll('main article'));
  const responseNodes = assistantNodes.length ? assistantNodes : fallbackArticles;
  const last = responseNodes.at(-1);
  const generating = controls.some((item) => /stop|停止/i.test(`${item.text} ${item.aria}`));
  const reasoningControls = controls.filter((item) => /high|高|reason|thinking|思考/i.test(`${item.text} ${item.aria}`));
  const isSelected = (item) => [item.pressed, item.checked, item.selected, item.data_state]
    .some((value) => ['true', '1', 'selected', 'on', 'active'].includes(String(value || '').toLowerCase()));
  const hasSelectionMarker = (item) => [item.pressed, item.checked, item.selected]
    .some((value) => value !== null && value !== '');
  const highControls = reasoningControls.filter((item) => /high|高/i.test(`${item.text} ${item.aria}`));
  const explicitHighControls = highControls.filter(hasSelectionMarker);
  return {
    url: location.href,
    title: document.title,
    has_composer: Boolean(document.querySelector('#prompt-textarea, textarea, [contenteditable="true"]')),
    reasoning_controls: reasoningControls,
    high_selected: explicitHighControls.some(isSelected),
    high_selection_explicit: explicitHighControls.length > 0,
    generating,
    assistant_count: responseNodes.length,
    last_assistant_text: last ? (last.innerText || '').trim() : '',
  };
}"""


PENDING_ATTACHMENT_SCRIPT = r"""() => ({
  file_input_names: Array.from(document.querySelectorAll('input[type=file]'))
    .flatMap((input) => Array.from(input.files || []).map((file) => file.name)),
})"""


MESSAGE_STATE_SCRIPT = r"""() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const visibleNodes = (selector) => Array.from(document.querySelectorAll(selector)).filter(visible);
  const userNodes = visibleNodes('[data-message-author-role="user"]');
  const assistantNodes = visibleNodes('[data-message-author-role="assistant"]');
  const fallbackArticles = visibleNodes('main article');
  const responseNodes = assistantNodes.length ? assistantNodes : fallbackArticles;
  const lastText = (nodes) => {
    const last = nodes.at(-1);
    return last ? (last.innerText || '').trim() : '';
  };
  const nodeKey = (node, index) => {
    if (!node) return null;
    for (const attribute of ['data-message-id', 'data-testid', 'id']) {
      const value = node.getAttribute(attribute);
      if (value) return `${attribute}:${value}`;
    }
    return `index:${index}`;
  };
  const stopControls = Array.from(document.querySelectorAll('button,[role="button"]'))
    .filter(visible)
    .filter((el) => /stop|停止/i.test(`${(el.innerText || '').trim()} ${el.getAttribute('aria-label') || ''}`));
  const streamingMarkers = Array.from(document.querySelectorAll(
    'main [aria-busy="true"],main [data-is-streaming="true"],main [data-streaming="true"],main [data-testid*="streaming"]'
  )).filter(visible);
  const lastAssistant = responseNodes.at(-1);
  const completionIndicators = lastAssistant
    ? Array.from(lastAssistant.querySelectorAll('button,[role="button"]'))
        .filter(visible)
        .filter((el) => /copy|good response|bad response|read aloud|重试|复制/i.test(
          `${(el.innerText || '').trim()} ${el.getAttribute('aria-label') || ''}`
        ))
    : [];
  return {
    url: location.href,
    title: document.title,
    generating: stopControls.length > 0 || streamingMarkers.length > 0,
    stop_control_count: stopControls.length,
    streaming_marker_count: streamingMarkers.length,
    completion_indicator_count: completionIndicators.length,
    user_count: userNodes.length,
    last_user_text: lastText(userNodes),
    last_user_key: nodeKey(userNodes.at(-1), userNodes.length - 1),
    assistant_count: responseNodes.length,
    last_assistant_text: lastText(responseNodes),
    last_assistant_key: nodeKey(lastAssistant, responseNodes.length - 1),
  };
}"""


CONVERSATION_EXPORT_SCRIPT = r"""() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const keyFor = (node, index) => {
    for (const attribute of ['data-message-id', 'data-testid', 'id']) {
      const value = node.getAttribute(attribute);
      if (value) return `${attribute}:${value}`;
    }
    return `index:${index}`;
  };
  const authorNodes = Array.from(document.querySelectorAll(
    '[data-message-author-role="user"],[data-message-author-role="assistant"]'
  )).filter(visible);
  const fallbackNodes = Array.from(document.querySelectorAll('main article')).filter(visible);
  const useFallback = authorNodes.length === 0 && fallbackNodes.length > 0;
  const nodes = authorNodes.length ? authorNodes : fallbackNodes;
  const messages = nodes.map((node, index) => ({
    role: node.getAttribute('data-message-author-role') || 'unknown',
    key: keyFor(node, index),
    text: (node.innerText || '').trim(),
    index,
  }));
  return {
    url: location.href,
    title: document.title,
    source: useFallback ? 'visible_dom_fallback' : 'visible_dom',
    completeness: useFallback ? 'visible_dom_fallback' : 'visible_dom_only',
    message_selector: authorNodes.length
      ? '[data-message-author-role="user|assistant"]'
      : 'main article',
    message_count: messages.length,
    messages,
  };
}"""


CONVERSATION_LIST_SCRIPT = r"""() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const normalizeUrl = (href) => {
    try { return new URL(href, location.href).href; } catch (_) { return null; }
  };
  const sidebarRoots = Array.from(document.querySelectorAll(
    'nav,aside,[role="navigation"],[aria-label*="history" i],[aria-label*="chat" i],[aria-label*="聊天"],[aria-label*="会话"]'
  )).filter(visible);
  const seen = new Set();
  const conversations = [];
  const links = sidebarRoots.flatMap((root) => Array.from(root.querySelectorAll('a[href]')));
  for (const link of links.filter(visible)) {
    const url = normalizeUrl(link.getAttribute('href'));
    if (!url || !/^https:\/\/(?:chatgpt\.com|chat\.openai\.com)\/(?:g\/[^/?#]+\/)?c\/[^/?#]+(?:[/?#].*)?$/i.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const title = (link.getAttribute('aria-label') || link.innerText || link.textContent || '')
      .replace(/\s+/g, ' ').trim();
    conversations.push({
      url,
      title: title || '(untitled)',
      selected: url === location.href,
    });
  }
  return {
    url: location.href,
    title: document.title,
    sidebar_found: sidebarRoots.length > 0,
    visible_only: true,
    conversations,
  };
}"""


def composer_insert_script(message: str) -> str:
    expected = json.dumps(message, ensure_ascii=False)
    return f"""() => {{
  const expected = {expected};
  const squash = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const el = document.querySelector('#prompt-textarea')
    || document.querySelector('[role="textbox"][contenteditable="true"]');
  if (!el) return {{ok: false, reason: 'composer_not_found'}};
  el.focus();
  try {{
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
  }} catch (_) {{}}
  let execOk = false;
  try {{ execOk = document.execCommand('insertText', false, expected); }} catch (_) {{ execOk = false; }}
  try {{ el.dispatchEvent(new InputEvent('input', {{bubbles: true, cancelable: true}})); }} catch (_) {{}}
  const text = el.innerText;
  return {{ok: true, exec_ok: !!execOk, matched: text.length > 0 && squash(text) === squash(expected), text_len: text.length}};
}}"""


def upload_state_script(filename: str) -> str:
    expected = json.dumps(filename, ensure_ascii=False)
    return f"""() => {{
  const expected = {expected};
  const normalize = (value) => String(value || '').trim().toLowerCase();
  const visible = (el) => {{
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }};
  const fileInputNames = Array.from(document.querySelectorAll('input[type=file]'))
    .flatMap((input) => Array.from(input.files || []).map((file) => file.name));
  const candidateTexts = Array.from(document.querySelectorAll(
    'button,[role="button"],[role="status"],[role="listitem"],[aria-label],[data-testid]'
  ))
    .filter(visible)
    .map((el) => `${{el.getAttribute('aria-label') || ''}} ${{(el.innerText || '').trim()}}`.trim())
    .filter(Boolean);
  const matches = candidateTexts.filter((value) => normalize(value).includes(normalize(expected)));
  return {{
    file_input_names: fileInputNames,
    matched_visible_text: matches.slice(0, 5),
  }};
}}"""


async def page_state(
    session: ClientSession, page_id: int, snapshot: dict[str, Any] | None = None
) -> dict[str, Any]:
    text, structured = await call_tool(
        session,
        "evaluate_script",
        {"pageId": page_id, "function": PAGE_STATE_SCRIPT, "waitForStableDom": False},
    )
    if structured.get("result") and isinstance(structured["result"], dict):
        state = structured["result"]
    else:
        state = parse_script_json(text)
    if snapshot is not None:
        state["composer"] = find_composer(snapshot)
        state["reasoning_snapshot_controls"] = find_reasoning_controls(snapshot)
    return state


async def pending_attachment_state(
    session: ClientSession, page_id: int
) -> dict[str, Any]:
    text, structured = await call_tool(
        session,
        "evaluate_script",
        {
            "pageId": page_id,
            "function": PENDING_ATTACHMENT_SCRIPT,
            "waitForStableDom": False,
        },
    )
    if structured.get("result") and isinstance(structured["result"], dict):
        return structured["result"]
    return parse_script_json(text)


async def message_state(session: ClientSession, page_id: int) -> dict[str, Any]:
    text, structured = await call_tool(
        session,
        "evaluate_script",
        {"pageId": page_id, "function": MESSAGE_STATE_SCRIPT, "waitForStableDom": False},
    )
    if structured.get("result") and isinstance(structured["result"], dict):
        return structured["result"]
    return parse_script_json(text)


async def conversation_list_state(
    session: ClientSession, page_id: int, limit: int
) -> dict[str, Any]:
    text, structured = await call_tool(
        session,
        "evaluate_script",
        {
            "pageId": page_id,
            "function": CONVERSATION_LIST_SCRIPT,
            "waitForStableDom": False,
        },
    )
    if structured.get("result") and isinstance(structured["result"], dict):
        state = structured["result"]
    else:
        state = parse_script_json(text)
    conversations = state.get("conversations")
    if not isinstance(conversations, list):
        raise RuntimeError("conversation_list_unavailable: page returned no conversation list")
    state["conversations"] = conversations[:limit]
    state["returned_count"] = len(state["conversations"])
    return state


async def conversation_export_state(
    session: ClientSession, page_id: int
) -> dict[str, Any]:
    text, structured = await call_tool(
        session,
        "evaluate_script",
        {
            "pageId": page_id,
            "function": CONVERSATION_EXPORT_SCRIPT,
            "waitForStableDom": False,
        },
    )
    if structured.get("result") and isinstance(structured["result"], dict):
        state = structured["result"]
    else:
        state = parse_script_json(text)
    messages = state.get("messages")
    if not isinstance(messages, list):
        raise RuntimeError("conversation_export_unavailable: page returned no visible messages")
    state["messages"] = [message for message in messages if isinstance(message, dict)]
    state["message_count"] = len(state["messages"])
    return state


def semantic_dom_click_script(name: str, role: str | None = None) -> str:
    expected = json.dumps(name, ensure_ascii=False)
    expected_role = json.dumps(role or "", ensure_ascii=False)
    return f"""() => {{
  const expected = {expected};
  const expectedRole = {expected_role};
  const normalize = (value) => String(value || '').toLowerCase().replace(/[^a-z0-9\\u4e00-\\u9fff]+/g, '');
  const visible = (el) => {{
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }};
  const label = (el) => `${{el.getAttribute('aria-label') || ''}} ${{(el.innerText || el.textContent || '').trim()}}`.trim();
  const elements = Array.from(document.querySelectorAll(
    'button,[role="button"],[role="menuitem"],[role="menuitemradio"],[role="option"],[role="radio"],[role="combobox"]'
  )).filter(visible);
  const targetKey = normalize(expected);
  let candidates = elements.filter((el) => {{
    const elementRole = el.getAttribute('role') || (el.tagName || '').toLowerCase();
    if (expectedRole && expectedRole !== 'button' && elementRole !== expectedRole) return false;
    const value = normalize(label(el));
    return value === targetKey || (targetKey && value.includes(targetKey));
  }});
  if (expectedRole === 'button') {{
    const outsideMenu = candidates.filter((el) => !el.closest('[role="menu"],[role="listbox"],[role="dialog"]'));
    if (outsideMenu.length) candidates = outsideMenu;
  }} else if (expectedRole) {{
    const insideMenu = candidates.filter((el) => el.closest('[role="menu"],[role="listbox"],[role="dialog"]'));
    if (insideMenu.length) candidates = insideMenu;
  }}
  if (candidates.length !== 1) return {{ok: false, error: candidates.length ? 'ambiguous' : 'not_found', count: candidates.length}};
  const target = candidates[0];
  target.click();
  return {{ok: true, role: target.getAttribute('role') || target.tagName.toLowerCase(), label: label(target)}};
}}"""


async def semantic_dom_click(
    session: ClientSession, page_id: int, node: dict[str, Any]
) -> dict[str, Any]:
    text, structured = await call_tool(
        session,
        "evaluate_script",
        {
            "pageId": page_id,
            "function": semantic_dom_click_script(
                snapshot_label(node), str(node.get("role") or "button")
            ),
            "waitForStableDom": False,
        },
    )
    result = structured.get("result") if isinstance(structured.get("result"), dict) else parse_script_json(text)
    if not result.get("ok"):
        raise RuntimeError(
            f"semantic_dom_click_failed: {result.get('error', 'unknown')} ({result.get('count', 0)})"
        )
    return result


async def upload_state(
    session: ClientSession, page_id: int, filename: str
) -> dict[str, Any]:
    text, structured = await call_tool(
        session,
        "evaluate_script",
        {
            "pageId": page_id,
            "function": upload_state_script(filename),
            "waitForStableDom": False,
        },
    )
    if structured.get("result") and isinstance(structured["result"], dict):
        return structured["result"]
    return parse_script_json(text)


def upload_is_verified(state: dict[str, Any], filename: str) -> bool:
    needle = normalized_message(filename).casefold()
    input_names = state.get("file_input_names", [])
    visible_matches = state.get("matched_visible_text", [])
    return any(normalized_message(name).casefold() == needle for name in input_names) or any(
        needle in normalized_message(value).casefold() for value in visible_matches
    )


def upload_evidence(state: dict[str, Any], filename: str) -> str:
    needle = normalized_message(filename).casefold()
    if any(normalized_message(name).casefold() == needle for name in state.get("file_input_names", [])):
        return "file_input"
    if any(needle in normalized_message(value).casefold() for value in state.get("matched_visible_text", [])):
        return "visible_attachment_label"
    return "unknown"


async def take_page_snapshot(session: ClientSession, page_id: int) -> dict[str, Any]:
    _, structured = await call_tool(
        session, "take_snapshot", {"pageId": page_id, "verbose": False}
    )
    snapshot = structured.get("snapshot", {})
    return snapshot if isinstance(snapshot, dict) else {}


def high_state_is_verified(state: dict[str, Any]) -> bool:
    """Honor explicit UI selection markers while preserving caller-verified legacy UIs."""
    if not state.get("reasoning_controls"):
        return False
    if state.get("high_selection_explicit"):
        return bool(state.get("high_selected"))
    return True


def validate_conversation_page(args: argparse.Namespace, actual_url: str | None) -> None:
    landing_urls = {
        "https://chatgpt.com",
        "https://chat.openai.com",
    }
    if args.conversation_url is None:
        if not args.new_conversation or (actual_url or "").rstrip("/") not in landing_urls:
            raise RuntimeError(
                "conversation_url_unavailable: pass the exact bound URL, or "
                "--new-conversation for the landing-page first bind"
            )
    elif (actual_url or "").rstrip("/") in landing_urls:
        if not args.new_conversation:
            raise RuntimeError(
                "conversation_url_unavailable: a landing page requires "
                "--new-conversation for the first bind"
            )
    elif not is_conversation_url(actual_url):
        raise RuntimeError(f"conversation_url_unavailable: observed {actual_url!r}")


async def wait_for_upload_verification(
    session: ClientSession, page_id: int, filename: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + UPLOAD_VERIFY_WAIT_SECONDS
    latest_snapshot: dict[str, Any] = {}
    latest_state: dict[str, Any] = {}
    while True:
        latest_snapshot = await take_page_snapshot(session, page_id)
        latest_state = await upload_state(session, page_id, filename)
        if upload_is_verified(latest_state, filename):
            return latest_snapshot, latest_state
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(
        "upload_verification_failed: the exact filename was not visible after "
        f"{UPLOAD_VERIFY_WAIT_SECONDS}s; inspect before retry"
    )


async def wait_for_send_control_or_submission(
    session: ClientSession,
    page_id: int,
    initial_messages: dict[str, Any],
    message: str,
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any]]:
    """Allow the composer DOM to settle without ever guessing an Enter submission."""
    deadline = asyncio.get_running_loop().time() + POST_FILL_WAIT_SECONDS
    latest_page: dict[str, Any] = {}
    latest_messages: dict[str, Any] = {}
    while True:
        snapshot = await take_page_snapshot(session, page_id)
        latest_page = await page_state(session, page_id, snapshot)
        latest_messages = await message_state(session, page_id)
        send_button = find_send_button(snapshot)
        if submission_started(initial_messages, latest_messages, message):
            return None, latest_page, latest_messages
        if send_button:
            return send_button, latest_page, latest_messages
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(
        "send_control_unavailable: no semantic send button or visible submission signal "
        f"after {POST_FILL_WAIT_SECONDS}s; refusing Enter fallback"
    )


def squashed(value: Any) -> str:
    """Collapse whitespace runs so DOM-rendered text compares equal to source."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def canonical_conversation_url(url: str | None) -> str | None:
    """Strip query/fragment/trailing slash and lowercase scheme+host."""
    if not url:
        return url
    base = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if "://" in base:
        scheme, _, rest = base.partition("://")
        host, sep, path = rest.partition("/")
        base = scheme.lower() + "://" + host.lower() + (sep + path if sep else "")
    return base


def require_exact_page(args: argparse.Namespace, actual_url: str | None) -> None:
    """Refuse side effects when the tab is no longer the exact bound URL.

    The page check above only guarantees "still a ChatGPT page"; without this,
    a tab that navigated to a different /c/... would receive the send/upload.
    """
    requested = args.conversation_url
    if (
        requested is not None
        and is_conversation_url(requested)
        and canonical_conversation_url(actual_url)
        != canonical_conversation_url(requested)
    ):
        raise RuntimeError(
            "conversation_moved: the selected page is no longer the exact "
            "bound URL; inspect before retry"
        )


async def require_selected_page(
    session: ClientSession,
    args: argparse.Namespace,
    page_id: int | None = None,
) -> dict[str, Any]:
    """Re-read which tab is selected immediately before acting.

    Compares both the tab id we are about to act on AND (when bound) the
    exact URL, so neither a tab switch nor a same-tab navigation to a
    different conversation can receive the side effect. First-bind landing
    sends are covered by the id check as well.
    """
    text, structured = await call_tool(session, "list_pages")
    pages = pages_from_result(text, structured)
    current = next((page for page in pages if page.get("selected")), None)
    if current is None:
        raise RuntimeError(
            "conversation_moved: no selected tab is visible; inspect before retry"
        )
    if page_id is not None and current.get("id") != page_id:
        raise RuntimeError(
            "conversation_moved: the selected tab changed before acting; "
            "inspect before retry"
        )
    requested = args.conversation_url
    if requested is not None and is_conversation_url(requested):
        if canonical_conversation_url(current.get("url")) != canonical_conversation_url(requested):
            raise RuntimeError(
                "conversation_moved: the selected tab is no longer the exact "
                "bound URL; inspect before retry"
            )
    elif requested is None or is_landing_url(requested):
        # First-bind sends target the landing tab itself: refuse if it has
        # navigated anywhere else (another conversation or off-site).
        if not is_landing_url(current.get("url")):
            raise RuntimeError(
                "conversation_moved: the landing tab navigated away before "
                "acting; inspect before retry"
            )
    return current


async def insert_composer_text(
    session: ClientSession,
    page_id: int,
    message: str,
    composer_uid: str | None = None,
) -> dict[str, Any]:
    """Fill the composer through the trusted editing path.

    Plain DOM fill does not fire editing events, so ProseMirror-based
    composers keep reporting an empty editor and no send control appears.
    select-all + execCommand('insertText') goes through the browser editing
    engine, which the live ChatGPT UI registers (verified 2026-09-05).
    If the trusted path cannot run, fall back to legacy fill and let the
    send-control wait below be the arbiter; failure stays fail-closed.
    """
    try:
        text, structured = await call_tool(
            session,
            "evaluate_script",
            {
                "pageId": page_id,
                "function": composer_insert_script(message),
                "waitForStableDom": False,
            },
        )
        result = (
            structured.get("result")
            if isinstance(structured.get("result"), dict)
            else parse_script_json(text)
        )
    except Exception:
        result = {}
    if result.get("ok") and result.get("matched"):
        return result
    if composer_uid is None:
        raise RuntimeError(
            "composer_fill_failed: the editor did not register the inserted "
            "text; inspect before retry"
        )
    await call_tool(
        session,
        "fill",
        {"pageId": page_id, "uid": composer_uid, "value": message},
    )
    return {"ok": True, "exec_ok": False, "fallback_fill": True}


def server_parameters(args: argparse.Namespace) -> StdioServerParameters:
    if args.mcp_package_root:
        package_root = args.mcp_package_root.expanduser().resolve()
        node = shutil.which("node") or "/usr/local/bin/node"
        command = node
        command_args = [
            str(package_root / "build" / "src" / "bin" / "chrome-devtools-mcp.js"),
        ]
        cwd = str(package_root)
    else:
        command = args.mcp_command
        command_args = ["-y", args.mcp_package]
        cwd = None

    command_args.extend(
        [
            "--browserUrl",
            args.cdp_url,
            "--no-usage-statistics",
            "--no-performance-crux",
            "--no-category-performance",
            "--experimentalStructuredContent",
        ]
    )
    return StdioServerParameters(command=command, args=command_args, cwd=cwd)


async def list_and_select_page(
    session: ClientSession, expected_url: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    text, structured = await call_tool(session, "list_pages")
    pages = pages_from_result(text, structured)
    page = select_chat_page(pages, expected_url)
    page_id = page.get("id")
    if not isinstance(page_id, int):
        raise RuntimeError(f"page_id_unavailable: {page!r}")
    await call_tool(session, "select_page", {"pageId": page_id, "bringToFront": False})
    return page, {"pages": pages, "page_id": page_id}


async def inspect_operation(session: ClientSession, expected_url: str | None) -> dict[str, Any]:
    page, listing = await list_and_select_page(session, expected_url)
    page_id = listing["page_id"]
    snapshot = await take_page_snapshot(session, page_id)
    state = await page_state(session, page_id, snapshot)
    targets = [
        {key: target[key] for key in ("id", "name", "role")}
        for target in find_upload_targets(snapshot)
    ]
    return {
        "operation": "inspect",
        "page": page,
        "state": state,
        "upload_targets": targets,
        **listing,
    }


def choose_intelligence_trigger(snapshot: dict[str, Any]) -> dict[str, Any]:
    triggers = find_intelligence_triggers(snapshot)
    if not triggers:
        raise RuntimeError(
            "model_control_unavailable: no visible semantic model or reasoning control was found"
        )
    best_score = triggers[0]["score"]
    best = [trigger for trigger in triggers if trigger["score"] == best_score]
    if len(best) != 1:
        raise RuntimeError(
            "model_control_ambiguous: multiple equally-ranked model/reasoning controls were found"
        )
    return best[0]


def matching_model_options(
    options: list[dict[str, Any]], requested_model: str
) -> list[dict[str, Any]]:
    return [option for option in options if model_label_matches(option["name"], requested_model)]


def matching_effort_options(
    options: list[dict[str, Any]], requested_effort: str
) -> list[dict[str, Any]]:
    return [option for option in options if effort_label_matches(option["name"], requested_effort)]


def has_visible_high(state: dict[str, Any]) -> bool:
    names = list(state.get("reasoning_controls", []))
    names.extend(state.get("reasoning_snapshot_controls", []))
    return any(
        re.search(r"high|\b高\b", str(item.get("name", item)), re.IGNORECASE)
        for item in names
    )


async def open_intelligence_menu(
    session: ClientSession,
    page_id: int,
    snapshot: dict[str, Any],
    trigger: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    options = find_intelligence_options(snapshot)
    if options:
        return snapshot, options
    trigger = trigger or choose_intelligence_trigger(snapshot)
    click_error: Exception | None = None
    try:
        await call_tool(
            session,
            "click",
            {"pageId": page_id, "uid": trigger["id"], "includeSnapshot": False},
        )
    except Exception as exc:
        click_error = exc
        try:
            current_snapshot = await take_page_snapshot(session, page_id)
            current_options = find_intelligence_options(current_snapshot)
            if current_options:
                return current_snapshot, current_options
        except Exception:
            pass
        await semantic_dom_click(session, page_id, trigger)
    for _ in range(6):
        await asyncio.sleep(0.2)
        snapshot = await take_page_snapshot(session, page_id)
        options = find_intelligence_options(snapshot)
        if options:
            return snapshot, options
    if click_error is not None:
        raise RuntimeError(
            "model_menu_unavailable: the model/reasoning picker did not open after the MCP click or semantic DOM retry"
        ) from click_error
    raise RuntimeError(
        "model_menu_unavailable: the semantic model/reasoning menu did not open"
    )


async def click_intelligence_option(
    session: ClientSession,
    page_id: int,
    matches: list[dict[str, Any]],
    kind: str,
) -> dict[str, Any]:
    if not matches:
        raise RuntimeError(f"{kind}_unavailable: requested option was not visible")
    if len(matches) != 1:
        raise RuntimeError(f"{kind}_ambiguous: multiple matching options were visible")
    option = matches[0]
    if not option.get("selected"):
        try:
            await call_tool(
                session,
                "click",
                {"pageId": page_id, "uid": option["id"], "includeSnapshot": False},
            )
        except Exception as click_error:
            try:
                current_snapshot = await take_page_snapshot(session, page_id)
                if not any(
                    current_option.get("id") == option["id"]
                    for current_option in find_intelligence_options(current_snapshot)
                ):
                    return option
            except Exception:
                pass
            try:
                await semantic_dom_click(session, page_id, option)
            except Exception as fallback_error:
                raise RuntimeError(
                    f"{kind}_click_failed: semantic option click was not observable"
                ) from click_error
        await asyncio.sleep(0.3)
    return option


async def select_model_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    if not args.confirm_model:
        raise PermissionError(
            "model_selection_refused: use --confirm-model only after confirming the exact target page"
        )
    if args.conversation_url is None or not is_conversation_url(args.conversation_url):
        raise ValueError(
            "conversation_url_unavailable: select-model requires an exact ChatGPT conversation URL"
        )
    if compact_label(args.effort) not in {"high", "medium", "low", "instant", "高", "中", "低"}:
        raise ValueError(
            "reasoning_option_unavailable: supported effort names are High, Medium, Low, or Instant"
        )

    page, listing = await list_and_select_page(session, args.conversation_url)
    page_id = listing["page_id"]
    actual_url = page.get("url")
    validate_conversation_page(args, actual_url)
    snapshot = await take_page_snapshot(session, page_id)
    before = await page_state(session, page_id, snapshot)
    if not before.get("has_composer"):
        raise RuntimeError("page_not_ready: ChatGPT composer is not available")
    if before.get("generating"):
        raise RuntimeError("page_busy: an existing ChatGPT response is still generating")

    requested_model = args.model.strip()
    requested_effort = args.effort.strip()
    model_basis = "explicit_model_label"
    selected_model: str | None = None

    # In the current simplified UI, High is itself the visible current picker
    # control and there may be no transient menu to open. Preserve that verified
    # mapping instead of failing on a menu that the page does not expose.
    simplified_sol_high = (
        compact_label(requested_model) in {"gpt56sol", "gpt5sol", "sol"}
        and compact_label(requested_effort) == "high"
        and has_visible_high(before)
        and not find_intelligence_options(snapshot)
    )
    if simplified_sol_high:
        return {
            "operation": "select-model",
            "conversation_url": actual_url,
            "requested_model": requested_model,
            "requested_effort": requested_effort,
            "selected_model": None,
            "selected_effort": "High",
            "model_verification_basis": "high_ui_mapping",
            "high_visible": True,
            "high_selection_explicit": bool(before.get("high_selection_explicit")),
            "high_selected": bool(before.get("high_selected")),
            "caller_verified_high_required": not bool(before.get("high_selection_explicit")),
            "selection_action": "already_visible",
            "page_after": before,
            **listing,
        }

    trigger = choose_intelligence_trigger(snapshot)

    _, options = await open_intelligence_menu(
        session, page_id, snapshot, trigger
    )
    model_matches = matching_model_options(options, requested_model)
    if model_matches:
        selected = await click_intelligence_option(
            session, page_id, model_matches, "model_option"
        )
        selected_model = selected["name"]
    elif compact_label(requested_model) in {"gpt56sol", "gpt5sol", "sol"}:
        # The current simplified ChatGPT picker exposes only High; the skill's
        # documented mapping treats that visible control as GPT-5.6 Sol.
        model_basis = "high_ui_mapping"
    else:
        raise RuntimeError(
            "model_unavailable: the requested model was not exposed by the visible ChatGPT picker"
        )

    # A model click commonly closes the menu. Reopen it before choosing effort.
    after_model_snapshot = await take_page_snapshot(session, page_id)
    effort_options = find_intelligence_options(after_model_snapshot)
    if not effort_options:
        _, effort_options = await open_intelligence_menu(
            session, page_id, after_model_snapshot
        )
    effort_matches = matching_effort_options(effort_options, requested_effort)
    selected_effort: str | None = None
    if effort_matches:
        selected = await click_intelligence_option(
            session, page_id, effort_matches, "reasoning_option"
        )
        selected_effort = selected["name"]
    elif compact_label(requested_effort) == "high":
        # If High is already rendered as the current simplified control and no
        # transient menu is exposed, retain the visible-control mapping.
        if not any(
            re.search(r"high|\b高\b", str(item.get("name", "")), re.IGNORECASE)
            for item in before.get("reasoning_controls", [])
        ):
            raise RuntimeError(
                "reasoning_option_unavailable: the visible picker did not expose High"
            )
        selected_effort = "High"
    else:
        raise RuntimeError(
            "reasoning_option_unavailable: the requested reasoning level was not exposed"
        )

    final_snapshot = await take_page_snapshot(session, page_id)
    after = await page_state(session, page_id, final_snapshot)
    high_visible = any(
        re.search(r"high|\b高\b", str(item.get("name", "")), re.IGNORECASE)
        for item in after.get("reasoning_snapshot_controls", [])
    ) or any(
        re.search(r"high|\b高\b", str(item.get("name", "")), re.IGNORECASE)
        for item in after.get("reasoning_controls", [])
    )
    if compact_label(requested_effort) == "high" and not high_visible:
        raise RuntimeError(
            "reasoning_state_unverified: High was not visible after model selection"
        )
    return {
        "operation": "select-model",
        "conversation_url": actual_url,
        "requested_model": requested_model,
        "requested_effort": requested_effort,
        "selected_model": selected_model,
        "selected_effort": selected_effort,
        "model_verification_basis": model_basis,
        "high_visible": high_visible,
        "high_selection_explicit": bool(after.get("high_selection_explicit")),
        "high_selected": bool(after.get("high_selected")),
        "caller_verified_high_required": not bool(after.get("high_selection_explicit")),
        "page_after": after,
        **listing,
    }


async def list_conversations_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    if args.limit <= 0:
        raise ValueError("list-conversations requires a positive --limit")
    page, listing = await list_and_select_page(session, args.conversation_url)
    page_id = listing["page_id"]
    actual_url = page.get("url")
    if not is_chatgpt_url(actual_url):
        raise RuntimeError(f"conversation_list_unavailable: observed non-ChatGPT page {actual_url!r}")
    state = await conversation_list_state(session, page_id, args.limit)
    return {
        "operation": "list-conversations",
        "conversation_url": actual_url,
        "list_source": "visible_sidebar_links",
        **state,
        **listing,
    }


async def switch_conversation_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    target_url = args.conversation_url
    if target_url is None or not is_conversation_url(target_url):
        raise ValueError(
            "conversation_url_unavailable: switch-conversation requires the exact target URL returned by list-conversations"
        )
    text, structured = await call_tool(session, "list_pages")
    pages = pages_from_result(text, structured)
    matches = [page for page in pages if page.get("url") == target_url]
    if len(matches) > 1:
        raise RuntimeError(
            "conversation_switch_ambiguous: multiple exact target pages are open; run close-duplicates first"
        )
    if len(matches) == 1:
        page = matches[0]
        page_id = page.get("id")
        if not isinstance(page_id, int):
            raise RuntimeError(f"page_id_unavailable: {page!r}")
        await call_tool(session, "select_page", {"pageId": page_id, "bringToFront": False})
        snapshot = await take_page_snapshot(session, page_id)
        state = await page_state(session, page_id, snapshot)
        return {
            "operation": "switch-conversation",
            "switched_by": "existing_page",
            "conversation_url": target_url,
            "page": page,
            "state": state,
            "pages": pages,
        }

    result = await new_page_operation(session, target_url, args.navigation_timeout)
    result["operation"] = "switch-conversation"
    result["switched_by"] = "new_background_page"
    return result


def load_batch_items(batch_path: Path) -> list[dict[str, Any]]:
    path = batch_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"batch_file_not_found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"batch_invalid: JSON could not be parsed in {path}") from exc
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        raise ValueError("batch_invalid: expected a non-empty JSON array or an object with items")
    if len(items) > MAX_BATCH_ITEMS:
        raise ValueError(f"batch_invalid: at most {MAX_BATCH_ITEMS} items are allowed per run")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"batch_invalid: item {index} is not an object")
        conversation_url = item.get("conversation_url")
        message_file = item.get("message_file")
        if not isinstance(conversation_url, str) or not is_conversation_url(conversation_url):
            raise ValueError(f"batch_invalid: item {index} needs an exact conversation_url")
        if not isinstance(message_file, str) or not message_file.strip():
            raise ValueError(f"batch_invalid: item {index} needs a message_file")
        file_path = Path(message_file).expanduser()
        if not file_path.is_absolute():
            file_path = path.parent / file_path
        file_path = file_path.resolve()
        if not file_path.is_file():
            raise FileNotFoundError(f"batch_message_file_not_found: item {index}: {file_path}")
        item_timeout = item.get("timeout")
        if item_timeout is not None and (
            not isinstance(item_timeout, int) or isinstance(item_timeout, bool) or item_timeout < 0
        ):
            raise ValueError(f"batch_invalid: item {index} timeout must be a non-negative integer")
        normalized.append(
            {
                "index": index,
                "conversation_url": conversation_url,
                "message_file": file_path,
                "timeout": item_timeout,
            }
        )
    return normalized


async def batch_send_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    if not args.confirm_batch:
        raise PermissionError(
            "batch_refused: use --confirm-batch only after confirming every exact file and destination"
        )
    if not args.verified_high:
        raise PermissionError(
            "batch_refused: visibly verify High first, then pass --verified-high"
        )
    if args.new_conversation:
        raise ValueError("batch_invalid: batch-send does not allow --new-conversation")
    if args.batch_file is None:
        raise ValueError("batch-send requires --batch-file")

    items = load_batch_items(args.batch_file)
    results: list[dict[str, Any]] = []
    for item in items:
        child_values = vars(args).copy()
        child_values.update(
            {
                "operation": "send",
                "conversation_url": item["conversation_url"],
                "message_file": item["message_file"],
                "confirm_send": True,
                "confirm_batch": False,
            }
        )
        if item["timeout"] is not None:
            child_values["timeout"] = item["timeout"]
        child = argparse.Namespace(**child_values)
        try:
            result = await send_operation(session, child)
            results.append(
                {
                    "index": item["index"],
                    "ok": True,
                    "conversation_url": item["conversation_url"],
                    "message_file": str(item["message_file"]),
                    "response": result.get("response"),
                    "page_after": result.get("page_after"),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "index": item["index"],
                    "ok": False,
                    "conversation_url": item["conversation_url"],
                    "message_file": str(item["message_file"]),
                    "error": error_message(exc),
                }
            )
            if not args.continue_on_error:
                break
    failed = [result for result in results if not result["ok"]]
    return {
        "operation": "batch-send",
        "requested_count": len(items),
        "completed_count": sum(1 for result in results if result["ok"]),
        "failed_count": len(failed),
        "stopped_on_error": bool(failed) and not args.continue_on_error,
        "results": results,
    }


async def new_page_operation(
    session: ClientSession, expected_url: str | None, navigation_timeout_ms: int
) -> dict[str, Any]:
    if navigation_timeout_ms <= 0:
        raise ValueError("new-page requires a positive --navigation-timeout")
    target_url = expected_url or "https://chatgpt.com/"
    validate_new_page_url(target_url)

    before_text, before_structured = await call_tool(session, "list_pages")
    before_pages = pages_from_result(before_text, before_structured)
    before_ids = {
        page.get("id") for page in before_pages if isinstance(page.get("id"), int)
    }
    text, structured = await call_tool(
        session,
        "new_page",
        {
            "url": target_url,
            "background": True,
            "timeout": navigation_timeout_ms,
        },
    )
    pages = pages_from_result(text, structured)
    new_chat_pages = [
        page
        for page in pages
        if is_chatgpt_url(page.get("url")) and page.get("id") not in before_ids
    ]
    if len(new_chat_pages) != 1:
        raise RuntimeError(
            "conversation_unbound: could not identify exactly one newly opened "
            f"ChatGPT page; observed new pages {new_chat_pages!r}"
        )
    page = new_chat_pages[0]
    actual_url = page.get("url")
    if expected_url is not None and actual_url != expected_url:
        raise RuntimeError(
            f"conversation_url_mismatch: expected {expected_url!r}, "
            f"observed {actual_url!r}"
        )
    page_id = page.get("id")
    if not isinstance(page_id, int):
        raise RuntimeError(f"page_id_unavailable: {page!r}")
    await call_tool(session, "select_page", {"pageId": page_id, "bringToFront": False})
    snapshot = await take_page_snapshot(session, page_id)
    state = await page_state(session, page_id, snapshot)
    return {"operation": "new-page", "page": page, "state": state, "pages": pages}


async def close_duplicate_pages_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    if not args.confirm_close:
        raise PermissionError(
            "close_refused: use --confirm-close only after confirming the exact conversation URL"
        )
    expected_url = args.conversation_url
    if expected_url is None or not is_conversation_url(expected_url):
        raise ValueError(
            "conversation_url_unavailable: close-duplicates requires an exact ChatGPT conversation URL"
        )

    text, structured = await call_tool(session, "list_pages")
    pages = pages_from_result(text, structured)
    matches = [page for page in pages if page.get("url") == expected_url]
    if len(matches) <= 1:
        return {
            "operation": "close-duplicates",
            "conversation_url": expected_url,
            "retained_page_id": matches[0].get("id") if matches else None,
            "closed_page_ids": [],
            "remaining_matches": len(matches),
            "pages": pages,
        }

    inspected: list[dict[str, Any]] = []
    for page in matches:
        page_id = page.get("id")
        if not isinstance(page_id, int):
            raise RuntimeError(f"page_id_unavailable: cannot inspect duplicate page {page!r}")
        await call_tool(
            session, "select_page", {"pageId": page_id, "bringToFront": False}
        )
        snapshot = await take_page_snapshot(session, page_id)
        if not snapshot:
            raise RuntimeError(
                f"close_refused: page state unreadable for duplicate page {page_id}"
            )
        state = await page_state(session, page_id, snapshot)
        if state.get("has_composer") is not True:
            raise RuntimeError(
                f"close_refused: composer state unreadable for duplicate page {page_id}"
            )
        if state.get("generating"):
            raise RuntimeError(
                f"close_refused: duplicate page {page_id} is generating a response"
            )
        composer = state.get("composer")
        draft = composer_text(composer) if isinstance(composer, dict) else ""
        if draft:
            raise RuntimeError(
                f"close_refused: duplicate page {page_id} contains an unsent draft"
            )
        attachments = await pending_attachment_state(session, page_id)
        attachment_names = attachments.get("file_input_names")
        if isinstance(attachment_names, list) and attachment_names:
            raise RuntimeError(
                f"close_refused: duplicate page {page_id} contains unsent attachment(s)"
            )
        inspected.append({"page": page, "state": state})

    selected = [
        item["page"]
        for item in inspected
        if item["page"].get("selected") is True
    ]
    if len(selected) == 1:
        retained = selected[0]
    else:
        retained = min(inspected, key=lambda item: int(item["page"]["id"]))["page"]
    retained_id = retained["id"]

    closed_page_ids: list[int] = []
    for item in inspected:
        page_id = item["page"]["id"]
        if page_id == retained_id:
            continue
        await call_tool(session, "close_page", {"pageId": page_id})
        closed_page_ids.append(page_id)

    await call_tool(
        session, "select_page", {"pageId": retained_id, "bringToFront": False}
    )
    after_text, after_structured = await call_tool(session, "list_pages")
    after_pages = pages_from_result(after_text, after_structured)
    remaining = [page for page in after_pages if page.get("url") == expected_url]
    if len(remaining) != 1:
        raise RuntimeError(
            "close_verification_failed: expected exactly one target page after cleanup; "
            f"observed {remaining!r}"
        )
    return {
        "operation": "close-duplicates",
        "conversation_url": expected_url,
        "retained_page_id": retained_id,
        "closed_page_ids": closed_page_ids,
        "remaining_matches": len(remaining),
        "pages": after_pages,
    }


async def status_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    page, listing = await list_and_select_page(session, args.conversation_url)
    page_id = listing["page_id"]
    actual_url = page.get("url")
    validate_conversation_page(args, actual_url)
    snapshot = await take_page_snapshot(session, page_id)
    state = await page_state(session, page_id, snapshot)
    messages = await message_state(session, page_id)
    return {
        "operation": "status",
        "conversation_url": actual_url,
        "page": page,
        "state": state,
        "messages": messages,
        **listing,
    }


async def wait_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    if args.conversation_url is None:
        raise ValueError("wait requires --conversation-url")
    if args.timeout < 0:
        raise ValueError("wait requires a non-negative --timeout")

    page, listing = await list_and_select_page(session, args.conversation_url)
    page_id = listing["page_id"]
    actual_url = page.get("url")
    validate_conversation_page(args, actual_url)
    initial = await message_state(session, page_id)
    initial_generating = bool(initial.get("generating"))
    deadline = asyncio.get_running_loop().time() + args.timeout
    latest = initial
    while latest.get("generating"):
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError(
                f"wait_timeout: ChatGPT remained generating for {args.timeout}s; inspect before retry"
            )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        latest = await message_state(session, page_id)

    page_after = await page_state(session, page_id)
    return {
        "operation": "wait",
        "conversation_url": actual_url,
        "initial_generating": initial_generating,
        "completed": not bool(latest.get("generating")),
        "messages": latest,
        "page_after": page_after,
        **listing,
    }


async def upload_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    if not args.confirm_upload:
        raise PermissionError(
            "upload_refused: use --confirm-upload only after confirming the exact file and destination"
        )
    if not args.verified_high:
        raise PermissionError(
            "upload_refused: visibly verify High first, then pass --verified-high"
        )
    file_paths = normalize_upload_files(args.file)

    page, listing = await list_and_select_page(session, args.conversation_url)
    page_id = listing["page_id"]
    actual_url = page.get("url")
    validate_conversation_page(args, actual_url)
    require_exact_page(args, actual_url)

    snapshot = await take_page_snapshot(session, page_id)
    before = await page_state(session, page_id, snapshot)
    if not before.get("has_composer"):
        raise RuntimeError("page_not_ready: ChatGPT composer is not available")
    if before.get("generating"):
        raise RuntimeError("page_busy: an existing ChatGPT response is still generating")
    if not high_state_is_verified(before):
        if before.get("reasoning_controls"):
            raise RuntimeError(
                "reasoning_state_unverified: High is visible but its selected state "
                "is not confirmed"
            )
        raise RuntimeError(
            "reasoning_state_unverified: no visible High reasoning control was found"
        )

    # Refuse the whole queue before its first external upload when a selected
    # filename is already present. This avoids a partially duplicated queue.
    for file_path in file_paths:
        filename = file_path.name
        before_upload = await upload_state(session, page_id, filename)
        if upload_is_verified(before_upload, filename):
            raise RuntimeError(
                "upload_already_present: the exact filename is already attached; inspect before retry"
            )

    results: list[dict[str, Any]] = []
    page_after: dict[str, Any] | None = None
    stopped_on_error = False
    for file_path in file_paths:
        filename = file_path.name
        entry: dict[str, Any] = {
            "path": str(file_path),
            "file_name": filename,
        }
        try:
            await require_selected_page(session, args, page_id)
            current_snapshot = await take_page_snapshot(session, page_id)
            target, proxy = await open_local_upload_target(
                session, page_id, current_snapshot
            )
            await call_tool(
                session,
                "upload_file",
                {
                    "pageId": page_id,
                    "uid": target["id"],
                    "filePaths": [str(file_path)],
                    "includeSnapshot": False,
                },
            )
            verified_snapshot, verified_upload = await wait_for_upload_verification(
                session, page_id, filename
            )
            page_after = await page_state(session, page_id, verified_snapshot)
            entry.update(
                {
                    "ok": True,
                    "target": {
                        "name": target["name"],
                        "role": target["role"],
                        "proxy_name": proxy["name"],
                    },
                    "verified_attachment": True,
                    "verification_evidence": upload_evidence(
                        verified_upload, filename
                    ),
                }
            )
        except Exception as exc:
            entry.update({"ok": False, "error": error_message(exc)})
            results.append(entry)
            stopped_on_error = not getattr(args, "continue_upload_on_error", False)
            if stopped_on_error:
                break
            continue
        results.append(entry)

    failed_count = sum(1 for result in results if not result.get("ok"))
    response: dict[str, Any] = {
        "operation": "upload",
        "conversation_url": actual_url,
        "requested_count": len(file_paths),
        "completed_count": sum(1 for result in results if result.get("ok")),
        "failed_count": failed_count,
        "stopped_on_error": stopped_on_error,
        "files": results,
        "verified_attachment": failed_count == 0,
    }
    if len(file_paths) == 1 and results:
        response["file_name"] = file_paths[0].name
        if results[0].get("target"):
            response["target"] = results[0]["target"]
        if results[0].get("verification_evidence"):
            response["verification_evidence"] = results[0]["verification_evidence"]
    if page_after is not None:
        response["page_after"] = page_after
    return response


async def export_conversation_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    if args.conversation_url is None or not is_conversation_url(args.conversation_url):
        raise ValueError(
            "conversation_url_unavailable: export-conversation requires an exact ChatGPT conversation URL"
        )
    root = require_project_control_plane(args)
    page, listing = await list_and_select_page(session, args.conversation_url)
    page_id = listing["page_id"]
    actual_url = page.get("url")
    validate_conversation_page(args, actual_url)
    state = await conversation_export_state(session, page_id)
    if state.get("url") != actual_url:
        raise RuntimeError(
            f"conversation_url_mismatch: export page changed from {actual_url!r} to {state.get('url')!r}"
        )

    conversation_id = conversation_id_from_url(actual_url)
    if args.output is None:
        identifier = conversation_id or safe_slug(str(state.get("title") or "conversation"))
        output = project_contained_path(
            root,
            Path("outputs")
            / "conversations"
            / f"{datetime.now().date().isoformat()}_{safe_slug(identifier)}.md",
        )
    else:
        output = project_contained_path(root, args.output)
    write_text_atomically(output, render_conversation_export(state), args.overwrite)
    return {
        "operation": "export-conversation",
        "conversation_url": actual_url,
        "conversation_id": conversation_id,
        "title": state.get("title"),
        "source": state.get("source"),
        "completeness": state.get("completeness"),
        "message_count": state.get("message_count", 0),
        "output_path": str(output),
    }


def prepare_download_directory(args: argparse.Namespace, root: Path) -> Path:
    requested = args.output_dir or Path("outputs") / "downloads"
    directory = project_contained_path(root, requested)
    if directory.exists() and not directory.is_dir():
        raise NotADirectoryError(f"download_output_invalid: {directory} is not a directory")
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()) and not args.overwrite_downloads:
        raise RuntimeError(
            f"download_output_not_empty: {directory} contains existing entries; "
            "pass --overwrite-downloads only after confirming the directory"
        )
    return directory


def refresh_download_target(
    snapshot: dict[str, Any], original: dict[str, Any]
) -> dict[str, Any]:
    targets = find_download_targets(snapshot)
    by_id = [target for target in targets if target.get("id") == original.get("id")]
    if len(by_id) == 1:
        return by_id[0]
    label = compact_label(original.get("name"))
    matches = [target for target in targets if compact_label(target.get("name")) == label]
    if len(matches) != 1:
        raise RuntimeError(
            "download_control_ambiguous: the requested download control changed or is no longer unique"
        )
    return matches[0]


async def download_attachments_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    if not args.confirm_download:
        raise PermissionError(
            "download_refused: use --confirm-download only after confirming the exact page and target"
        )
    if args.conversation_url is None or not is_conversation_url(args.conversation_url):
        raise ValueError(
            "conversation_url_unavailable: download-attachments requires an exact ChatGPT conversation URL"
        )
    if args.timeout <= 0:
        raise ValueError("download-attachments requires a positive --timeout")
    root = require_project_control_plane(args)
    page, listing = await list_and_select_page(session, args.conversation_url)
    page_id = listing["page_id"]
    actual_url = page.get("url")
    validate_conversation_page(args, actual_url)
    snapshot = await take_page_snapshot(session, page_id)
    state = await page_state(session, page_id, snapshot)
    if state.get("url") != actual_url:
        raise RuntimeError(
            f"conversation_url_mismatch: download page changed from {actual_url!r} to {state.get('url')!r}"
        )
    targets = select_download_targets(snapshot, args.target_name)
    directory = prepare_download_directory(args, root)

    results: list[dict[str, Any]] = []
    configured = False
    restore_error: str | None = None
    try:
        await set_download_behavior(args.cdp_url, "allow", directory)
        configured = True
        for target in targets:
            entry: dict[str, Any] = {
                "target": {
                    "name": target["name"],
                    "role": target["role"],
                }
            }
            try:
                current_snapshot = await take_page_snapshot(session, page_id)
                current_target = refresh_download_target(current_snapshot, target)
                before = download_directory_state(directory)
                await call_tool(
                    session,
                    "click",
                    {
                        "pageId": page_id,
                        "uid": current_target["id"],
                        "includeSnapshot": False,
                    },
                )
                downloaded = await wait_for_download(directory, before, args.timeout)
                entry.update(
                    {
                        "ok": True,
                        "files": [download_file_record(path) for path in downloaded],
                    }
                )
            except Exception as exc:
                entry.update({"ok": False, "error": error_message(exc)})
                results.append(entry)
                if not args.continue_download_on_error:
                    break
                continue
            results.append(entry)
    finally:
        if configured:
            try:
                await set_download_behavior(args.cdp_url, "default")
            except Exception as exc:
                restore_error = error_message(exc)

    failed_count = sum(1 for result in results if not result.get("ok"))
    response: dict[str, Any] = {
        "operation": "download-attachments",
        "conversation_url": actual_url,
        "output_dir": str(directory),
        "requested_count": len(targets),
        "completed_count": sum(1 for result in results if result.get("ok")),
        "failed_count": failed_count,
        "stopped_on_error": bool(failed_count) and not args.continue_download_on_error,
        "results": results,
        "download_behavior_restored": restore_error is None,
    }
    if restore_error:
        response["operation_error"] = f"download_restore_failed: {restore_error}"
    return response


async def send_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    if not args.confirm_send:
        raise PermissionError(
            "send_refused: use --confirm-send only after action-time user confirmation"
        )
    if not args.verified_high:
        raise PermissionError(
            "send_refused: visibly verify High first, then pass --verified-high"
        )
    if args.message_file is None:
        raise ValueError("send requires --message-file")
    message = args.message_file.expanduser().read_text(encoding="utf-8")
    if not message.strip():
        raise ValueError("send requires a non-empty message file")
    auto_report_root: Path | None = None
    if getattr(args, "auto_save_report", False):
        auto_report_root = require_project_control_plane(args)
        report_path = report_output_path(args, auto_report_root)
        if report_path.exists() and not args.overwrite:
            raise FileExistsError(
                f"output_exists: {report_path} already exists; pass --overwrite only after confirming replacement"
            )

    page, listing = await list_and_select_page(session, args.conversation_url)
    page_id = listing["page_id"]
    actual_url = page.get("url")
    validate_conversation_page(args, actual_url)
    require_exact_page(args, actual_url)

    snapshot = await take_page_snapshot(session, page_id)
    before = await page_state(session, page_id, snapshot)
    if not before.get("has_composer"):
        raise RuntimeError("page_not_ready: ChatGPT composer is not available")
    if before.get("generating"):
        raise RuntimeError("page_busy: an existing ChatGPT response is still generating")
    if not high_state_is_verified(before):
        if before.get("reasoning_controls"):
            raise RuntimeError(
                "reasoning_state_unverified: High is visible but its selected state "
                "is not confirmed"
            )
        raise RuntimeError(
            "reasoning_state_unverified: no visible High reasoning control was found"
        )

    initial_messages = await message_state(session, page_id)

    composer = find_composer(snapshot)
    if not composer:
        raise RuntimeError("page_state_unreadable: composer uid was not found in snapshot")
    draft = composer_text(composer)
    if draft and squashed(draft) != squashed(message):
        raise RuntimeError(
            "composer_not_empty: refusing to overwrite an existing unsent draft"
        )
    if not draft and user_message_matches(initial_messages, message):
        raise RuntimeError(
            "submission_already_present: the requested text is already the latest "
            "visible user message; inspect before retry"
        )
    # Normalize unconditionally: empty, identical, or whitespace-identical
    # drafts all end up editor-registered; a truly differing draft was refused.
    await insert_composer_text(session, page_id, message, composer.get("id"))

    send_button, latest, latest_messages = await wait_for_send_control_or_submission(
        session, page_id, initial_messages, message
    )
    submission_observed = submission_started(initial_messages, latest_messages, message)
    if send_button and not submission_observed:
        await require_selected_page(session, args, page_id)
        try:
            await call_tool(
                session,
                "click",
                {"pageId": page_id, "uid": send_button["id"], "includeSnapshot": False},
            )
        except Exception as exc:
            try:
                latest = await page_state(session, page_id)
                latest_messages = await message_state(session, page_id)
            except Exception as state_exc:
                raise RuntimeError(
                    "submission_unknown: click failed and post-click state could not be "
                    "read; inspect before retry"
                ) from state_exc
            if not submission_started(initial_messages, latest_messages, message):
                raise RuntimeError(
                    "submission_unknown: click outcome is not observable; inspect before retry"
                ) from exc
            submission_observed = True

    deadline = asyncio.get_running_loop().time() + args.timeout
    last_response_signature: tuple[Any, ...] | None = None
    stable_response_polls = 0
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        latest = await page_state(session, page_id)
        latest_messages = await message_state(session, page_id)
        submission_observed = submission_observed or submission_started(
            initial_messages, latest_messages, message
        )
        if response_is_new(initial_messages, latest_messages, message):
            signature = response_signature(latest_messages)
            if signature == last_response_signature:
                stable_response_polls += 1
            else:
                last_response_signature = signature
                stable_response_polls = 1
            if stable_response_polls >= RESPONSE_STABILITY_POLLS:
                result: dict[str, Any] = {
                    "operation": "send",
                    "conversation_url_before": actual_url,
                    "conversation_url_after": latest.get("url"),
                    "response": latest_messages["last_assistant_text"],
                    "response_key": latest_messages.get("last_assistant_key"),
                    "response_stable_polls": stable_response_polls,
                    "verified_user_message": True,
                    "page_after": latest,
                }
                if getattr(args, "auto_save_report", False) and auto_report_root is not None:
                    report_values = vars(args).copy()
                    report_values.update(
                        {
                            "conversation_url": latest.get("url") or actual_url,
                            "status": "valid",
                            "verified_before_submit": True,
                            "verified_after_response": True,
                        }
                    )
                    report_args = argparse.Namespace(**report_values)
                    try:
                        result["saved_report"] = save_report_document(
                            report_args,
                            latest_messages["last_assistant_text"],
                            result,
                            auto_report_root,
                        )
                    except Exception as exc:
                        # The send is already verified. Preserve its response and
                        # make a report-save failure explicit without inviting a
                        # blind resend.
                        result["report_save_error"] = error_message(exc)
                        result["operation_error"] = f"save_failure: {error_message(exc)}"
                return result
        else:
            last_response_signature = None
            stable_response_polls = 0

    if submission_observed:
        raise RuntimeError(
            "incomplete_response: submission was observed but no new completed assistant "
            "response arrived; inspect before retry"
        )
    raise RuntimeError(
        "submission_unknown: no verified new user message appeared before timeout; "
        "inspect before retry"
    )


def require_non_empty_message_file(path: Path | None) -> str:
    if path is None:
        raise ValueError("send requires --message-file")
    candidate = path.expanduser()
    if not candidate.is_file():
        raise FileNotFoundError(f"message file not found: {candidate}")
    message = candidate.read_text(encoding="utf-8")
    if not message.strip():
        raise ValueError("send requires a non-empty message file")
    return message


def is_landing_url(url: str | None) -> bool:
    return bool(url) and url.rstrip("/") in {
        "https://chatgpt.com",
        "https://chat.openai.com",
    }


def preflight_url_shape(args: argparse.Namespace) -> None:
    """Reject empty or off-site conversation URLs before the MCP session.

    A foreign URL can never match a ChatGPT tab, so failing here only moves
    the inevitable conversation_url_unavailable earlier (and keeps it exact).
    """
    if args.operation not in {
        "send",
        "upload",
        "status",
        "wait",
        "inspect",
        "list-conversations",
        "switch-conversation",
        "export-conversation",
        "download-attachments",
        "select-model",
        "close-duplicates",
        "doctor",
    }:
        return
    url = args.conversation_url
    if not url:
        return
    if not (is_conversation_url(url) or is_landing_url(url)):
        raise RuntimeError(
            "conversation_url_unavailable: expected an exact ChatGPT "
            f"conversation URL or landing page, observed {url!r}"
        )


def preflight_args(args: argparse.Namespace) -> None:
    """Fail fast on caller-side errors before opening the MCP session.

    Errors raised inside the session can be masked by anyio task-group
    teardown (a BrokenResourceError replaces the original), so every check
    that does not need the browser runs here with the same error codes the
    operations would raise. Timeouts are required positive here so a
    non-positive value can never report failure after side effects.
    """
    op = args.operation
    preflight_url_shape(args)
    if op == "send":
        if not args.confirm_send:
            raise PermissionError(
                "send_refused: use --confirm-send only after action-time user confirmation"
            )
        if not args.verified_high:
            raise PermissionError(
                "send_refused: visibly verify High first, then pass --verified-high"
            )
        if not args.conversation_url and not args.new_conversation:
            raise RuntimeError(
                "conversation_url_unavailable: pass the exact bound URL, or "
                "--new-conversation for the landing-page first bind"
            )
        require_non_empty_message_file(args.message_file)
        if args.timeout <= 0:
            raise ValueError(
                "send requires a positive --timeout; a non-positive timeout "
                "would report failure after side effects"
            )
        if args.auto_save_report:
            root = require_project_control_plane(args)
            report_path = report_output_path(args, root)
            if report_path.exists() and not args.overwrite:
                raise FileExistsError(
                    f"output_exists: {report_path} already exists; pass --overwrite only after confirming replacement"
                )
    elif op == "select-model":
        if not args.confirm_model:
            raise PermissionError(
                "model_selection_refused: use --confirm-model only after confirming the exact target page"
            )
        if args.conversation_url is None or not is_conversation_url(args.conversation_url):
            raise ValueError(
                "conversation_url_unavailable: select-model requires an exact ChatGPT conversation URL"
            )
        if compact_label(args.effort) not in {"high", "medium", "low", "instant", "高", "中", "低"}:
            raise ValueError(
                "reasoning_option_unavailable: supported effort names are High, Medium, Low, or Instant"
            )
    elif op == "batch-send":
        if not args.confirm_batch:
            raise PermissionError(
                "batch_refused: use --confirm-batch only after confirming every exact file and destination"
            )
        if not args.verified_high:
            raise PermissionError(
                "batch_refused: visibly verify High first, then pass --verified-high"
            )
        if args.new_conversation:
            raise ValueError("batch_invalid: batch-send does not allow --new-conversation")
        if args.batch_file is None:
            raise ValueError("batch-send requires --batch-file")
        for item in load_batch_items(args.batch_file):
            # Items without an explicit timeout inherit the parent --timeout,
            # so validate the effective value: nothing may act on <= 0.
            effective = item["timeout"] if item["timeout"] is not None else args.timeout
            if effective <= 0:
                raise ValueError(
                    f"batch_invalid: item {item['index']} needs a positive effective timeout"
                )
            require_non_empty_message_file(item["message_file"])
    elif op == "upload":
        if not args.confirm_upload:
            raise PermissionError(
                "upload_refused: use --confirm-upload only after confirming the exact file and destination"
            )
        if not args.verified_high:
            raise PermissionError(
                "upload_refused: visibly verify High first, then pass --verified-high"
            )
        if not args.conversation_url and not args.new_conversation:
            raise RuntimeError(
                "conversation_url_unavailable: pass the exact bound URL, or "
                "--new-conversation for the landing-page first bind"
            )
        normalize_upload_files(args.file)
    elif op == "close-duplicates":
        if not args.confirm_close:
            raise PermissionError(
                "close_refused: use --confirm-close only after confirming the exact conversation URL"
            )
        if args.conversation_url is None or not is_conversation_url(args.conversation_url):
            raise ValueError(
                "conversation_url_unavailable: close-duplicates requires an exact ChatGPT conversation URL"
            )
    elif op == "download-attachments":
        if not args.confirm_download:
            raise PermissionError(
                "download_refused: use --confirm-download only after confirming the exact page and target"
            )
        if args.conversation_url is None or not is_conversation_url(args.conversation_url):
            raise ValueError(
                "conversation_url_unavailable: download-attachments requires an exact ChatGPT conversation URL"
            )
        if args.timeout <= 0:
            raise ValueError("download-attachments requires a positive --timeout")
        require_project_control_plane(args)
    elif op == "new-page":
        if args.navigation_timeout <= 0:
            raise ValueError("new-page requires a positive --navigation-timeout")
        validate_new_page_url(args.conversation_url or "https://chatgpt.com/")
    elif op == "wait":
        if args.conversation_url is None:
            raise ValueError("wait requires --conversation-url")
        if args.timeout < 0:
            raise ValueError("wait requires a non-negative --timeout")


def cdp_reachable(cdp_url: str | None) -> tuple[bool, str]:
    base = (cdp_url or "").rstrip("/")
    try:
        with urllib.request.urlopen(base + "/json/version", timeout=5) as response:
            info = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return False, f"{base or '(empty CDP URL)'} unreachable: {exc}"
    return True, str(info.get("Browser", "reachable"))


def doctor_local_checks(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Interpreter, dependency, launcher, and CDP checks (no browser session)."""
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    record("python", sys.version_info >= (3, 10), sys.version.split()[0])
    try:
        record("mcp_sdk", True, importlib.metadata.version("mcp"))
    except importlib.metadata.PackageNotFoundError:
        record("mcp_sdk", False, 'missing: pip install "mcp==1.12.2" (tested)')
    node = shutil.which("node")
    record("node", node is not None, node or "node not in PATH")
    launcher = args.mcp_command if args.mcp_command != "npx" else "npx"
    launcher_detail = launcher
    if args.mcp_package_root:
        launcher_detail += " (package-root mode falls back to /usr/local/bin/node when node is absent from PATH)"
    record(
        "mcp_launcher",
        shutil.which(launcher) is not None or Path(launcher).is_file(),
        launcher_detail,
    )
    reachable, cdp_detail = cdp_reachable(args.cdp_url)
    record("cdp", reachable, cdp_detail)
    return checks


async def doctor_operation(
    session: ClientSession, args: argparse.Namespace
) -> dict[str, Any]:
    """Read-only environment checklist. Never sends, uploads, or closes."""
    checks: list[dict[str, Any]] = doctor_local_checks(args)

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        text, structured = await call_tool(session, "list_pages")
        pages = pages_from_result(text, structured)
    except Exception as exc:
        record("chatgpt_tab", False, f"list_pages failed: {exc}")
        pages = []
    chat_pages = [page for page in pages if is_chatgpt_url(page.get("url"))] if pages else []
    if not pages:
        record("chatgpt_tab", False, "list_pages returned no tabs")
    else:
        record(
            "chatgpt_tab",
            bool(chat_pages),
            f"{len(chat_pages)} ChatGPT tab(s) open"
            if chat_pages
            else "no ChatGPT tab open in the attached browser",
        )
    if args.conversation_url:
        matches = [
            page
            for page in chat_pages
            if canonical_conversation_url(page.get("url"))
            == canonical_conversation_url(args.conversation_url)
        ]
        record(
            "bound_page",
            len(matches) == 1,
            "exact bound page open" if len(matches) == 1 else "bound URL not open",
        )
        if len(matches) == 1 and isinstance(matches[0].get("id"), int):
            try:
                snapshot = await take_page_snapshot(session, matches[0]["id"])
                state = await page_state(session, matches[0]["id"], snapshot)
            except Exception as exc:
                record("composer", False, f"page state unreadable: {exc}")
                state = {}
            if state:
                record("composer", bool(state.get("has_composer")), "")
                record(
                    "high_control",
                    bool(state.get("reasoning_controls")),
                    "High control visible" if state.get("reasoning_controls") else "no reasoning control visible",
                )
    failed = sum(1 for item in checks if not item["ok"])
    return {
        "operation": "doctor",
        "checks": checks,
        "passed_count": len(checks) - failed,
        "failed_count": failed,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.operation == "save-report":
        return save_report_operation(args)
    if args.operation == "doctor" and not _MCP_SDK_AVAILABLE:
        checks = doctor_local_checks(args)
        failed = sum(1 for item in checks if not item["ok"])
        return {
            "operation": "doctor",
            "checks": checks,
            "passed_count": len(checks) - failed,
            "failed_count": failed,
            "mcp_session": "skipped: MCP SDK missing",
        }
    if not _MCP_SDK_AVAILABLE:
        raise RuntimeError(
            'mcp_sdk_missing: the MCP Python SDK is not installed; run pip install "mcp==1.12.2" '
            "with the interpreter launching bridge.py"
        )
    if not args.mcp_python.expanduser().is_file():
        raise FileNotFoundError(f"MCP Python executable not found: {args.mcp_python}")
    preflight_args(args)
    params = server_parameters(args)

    async def list_roots_callback(_context: Any) -> ListRootsResult:
        roots: list[Root] = []
        if args.operation == "upload" and args.file is not None:
            seen: set[Path] = set()
            for raw_file in (
                args.file if isinstance(args.file, list) else [args.file]
            ):
                selected_file = Path(raw_file).expanduser().resolve()
                if selected_file.parent in seen:
                    continue
                seen.add(selected_file.parent)
                roots.append(
                    Root(
                        uri=selected_file.parent.as_uri(),
                        name="selected-upload-directory",
                    )
                )
        return ListRootsResult(roots=roots)

    async with stdio_client(params, errlog=sys.stderr) as (read_stream, write_stream):
        async with ClientSession(
            read_stream, write_stream, list_roots_callback=list_roots_callback
        ) as session:
            try:
                await asyncio.wait_for(
                    session.initialize(), timeout=MCP_CALL_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"mcp_timeout: initialize exceeded {MCP_CALL_TIMEOUT_SECONDS}s"
                ) from exc
            if args.operation == "inspect":
                return await inspect_operation(session, args.conversation_url)
            if args.operation == "doctor":
                return await doctor_operation(session, args)
            if args.operation == "new-page":
                return await new_page_operation(
                    session, args.conversation_url, args.navigation_timeout
                )
            if args.operation == "status":
                return await status_operation(session, args)
            if args.operation == "wait":
                return await wait_operation(session, args)
            if args.operation == "close-duplicates":
                return await close_duplicate_pages_operation(session, args)
            if args.operation == "select-model":
                return await select_model_operation(session, args)
            if args.operation == "list-conversations":
                return await list_conversations_operation(session, args)
            if args.operation == "switch-conversation":
                return await switch_conversation_operation(session, args)
            if args.operation == "batch-send":
                return await batch_send_operation(session, args)
            if args.operation == "upload":
                return await upload_operation(session, args)
            if args.operation == "export-conversation":
                return await export_conversation_operation(session, args)
            if args.operation == "download-attachments":
                return await download_attachments_operation(session, args)
            if args.operation == "send":
                return await send_operation(session, args)
    raise RuntimeError(f"unsupported operation: {args.operation}")


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": error_message(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    operation_ok = not (
        state_count(result, "failed_count") > 0 or result.get("operation_error")
    )
    print(json.dumps({"ok": operation_ok, **result}, ensure_ascii=False, indent=2))
    return 0 if operation_ok else 2


_WRAPPER_MESSAGE_PATTERNS = (
    "unhandled errors in a TaskGroup",
    re.compile(r"\(\d+ sub-exceptions?\)"),
)


def _is_wrapper_message(message: str) -> bool:
    for pattern in _WRAPPER_MESSAGE_PATTERNS:
        if isinstance(pattern, str):
            if pattern in message:
                return True
        elif pattern.search(message):
            return True
    return False


def error_message(exc: Exception) -> str:
    """Keep MCP task-group failures readable without dumping nested traces.

    Walks nested groups plus __cause__/__context__ chains and returns the
    first meaningful message, skipping empty teardown noise (e.g. anyio
    BrokenResourceError that replaces the original error on session exit).
    """
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    fallback = ""
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        nested = getattr(current, "exceptions", None)
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        # LIFO order: nested children first, then cause, then context.
        if context is not None and context is not current and context is not cause:
            stack.append(context)
        if cause is not None and cause is not current:
            stack.append(cause)
        if nested:
            stack.extend(reversed(list(nested)))
        message = str(current)
        if not message:
            continue
        if _is_wrapper_message(message):
            if not fallback:
                fallback = message
            continue
        return message
    return fallback or str(exc)


if __name__ == "__main__":
    raise SystemExit(main())
