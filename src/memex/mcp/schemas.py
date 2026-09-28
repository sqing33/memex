"""17 个 MCP 工具的元数据：annotations + inputSchema + outputSchema（G1/G2）。

来源：docs/mcp-tools.md §2/§3。这张表是**机械来源**——MEMEX_TOOLS 的类别门禁、
help 的清单、list_tools 的返回都从这里导出，避免三处漂移。

outputSchema 用统一写法：{"oneOf": [ToolError, <payload ok:true 分支>]}。
下文 payload 只写"ok:true 分支"的核心结构（完整校验不依赖它，客户端是尽力而为）。
"""

from __future__ import annotations

from typing import Any

TOOL_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok", "error"],
    "properties": {
        "ok": {"const": False},
        "error": {
            "type": "object",
            "additionalProperties": False,
            "required": ["code", "message"],
            "properties": {
                "code": {"enum": [
                    "invalid_argument", "not_found", "invalid_report", "conflict", "stale_repo",
                    "fetch_failed", "unsupported", "rate_limited", "disabled", "internal",
                ]},
                "message": {"type": "string"},
                "details": {"type": "object"},
            },
        },
    },
}

_REPO_ID = {"type": "string", "pattern": r"^[a-z0-9.-]+__[A-Za-z0-9._-]+__[A-Za-z0-9._-]+$"}
_DETAIL = {"type": "string", "enum": ["brief", "normal", "full"], "default": "normal"}
_DEPTH = {"type": "string", "enum": ["fast", "standard", "deep"], "default": "standard"}


def _ann(title: str, ro: bool, destructive: bool, idem: bool, open_world: bool) -> dict[str, Any]:
    return {
        "title": title,
        "readOnlyHint": ro,
        "destructiveHint": destructive,
        "idempotentHint": idem,
        "openWorldHint": open_world,
    }


# 名称 -> (类别, annotations, inputSchema properties, required, payload_props, payload_required)
_TOOLS: dict[str, dict[str, Any]] = {
    "fetch_repo": {
        "category": "network",
        "annotations": _ann("Fetch repository", False, False, True, True),
        "input": {
            "repo_url": {"type": "string"},
            "ref": {"type": "string"},
            "subpath": {"type": "string"},
            "refresh": {"type": "boolean", "default": False},
        },
        "required": ["repo_url"],
        "payload": {
            "repo": {"type": "object"},
            "repo_path": {"type": "string"},
            "is_new": {"type": "boolean"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "next_step": {"type": "object"},
        },
        "payload_required": ["repo"],
    },
    "get_evidence_pack": {
        "category": "read",
        "annotations": _ann("Get evidence pack", True, False, True, False),
        "input": {"repo_id": _REPO_ID, "depth": _DEPTH, "detail": _DETAIL, "subpath": {"type": "string"}},
        "required": ["repo_id"],
        "payload": {
            "repo": {"type": "object"}, "commit_sha": {"type": "string"}, "tree": {"type": "object"},
            "entry_points": {"type": "array"}, "symbols": {"type": "array"}, "groups": {"type": "array"},
            "stats": {"type": "object"}, "warnings": {"type": "array"}, "next_step": {"type": "object"},
        },
        "payload_required": ["repo", "commit_sha", "tree", "entry_points", "symbols", "stats"],
    },
    "read_file_slice": {
        "category": "read",
        "annotations": _ann("Read file slice", True, False, True, False),
        "input": {
            "repo_id": _REPO_ID, "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1, "default": 1},
            "end_line": {"type": "integer", "minimum": 1},
            "max_lines": {"type": "integer", "default": 400, "maximum": 2000},
        },
        "required": ["repo_id", "path"],
        "payload": {
            "path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"},
            "total_lines": {"type": "integer"}, "text": {"type": "string"}, "file_sha": {"type": "string"},
            "truncated": {"type": "boolean"},
        },
        "payload_required": ["path", "start_line", "end_line", "text", "file_sha"],
    },
    "request_repo_bundle": {
        "category": "network",
        "annotations": _ann("Request repo bundle", False, False, True, False),
        "input": {"repo_id": _REPO_ID, "ref": {"type": "string"}},
        "required": ["repo_id"],
        "payload": {
            "url": {"type": "string"}, "sha256": {"type": "string"}, "bytes": {"type": "integer"},
            "commit_sha": {"type": "string"}, "expires_at": {"type": "string"}, "usage": {"type": "string"},
        },
        "payload_required": ["url", "sha256", "commit_sha"],
    },
    "begin_analysis": {
        "category": "write",
        "annotations": _ann("Begin analysis", False, False, False, False),
        "input": {
            "repo_id": _REPO_ID, "ref": {"type": "string"}, "depth": _DEPTH,
            "analyst": {"type": "string"}, "include_pack": {"type": "boolean", "default": True},
        },
        "required": ["repo_id"],
        "payload": {
            "session_id": {"type": "string"}, "state": {"type": "string"},
            "repo": {"type": "object"}, "commit_sha": {"type": "string"},
            "evidence_pack": {"type": "object"}, "contract": {"type": "object"},
            "checklist": {"type": "array"}, "expires_at": {"type": "string"},
            "analyst": {"type": "string"}, "next_step": {"type": "object"},
        },
        "payload_required": ["session_id", "state", "contract", "checklist", "expires_at", "next_step"],
    },
    "validate_report": {
        "category": "read",
        "annotations": _ann("Validate report", True, False, True, False),
        "input": {
            "report": {"type": "object"},
            "repo_id": _REPO_ID,
            "session_id": {
                "type": "string",
                "description": "可选；给了且 is_valid=true 时把会话推进到 validated，解锁 commit_report",
            },
        },
        "required": ["report"],
        "payload": {
            "is_valid": {"type": "boolean"}, "problems": {"type": "array"},
            "warnings": {"type": "array"}, "counts": {"type": "object"},
        },
        "payload_required": ["is_valid", "problems", "warnings", "counts"],
    },
    "commit_report": {
        "category": "write",
        "annotations": _ann("Commit report", False, False, True, False),
        "input": {
            "session_id": {"type": "string"}, "report": {"type": "object"},
            "analyst": {"type": "string"}, "force": {"type": "boolean", "default": False},
        },
        "required": ["session_id", "report"],
        "payload": {
            "is_committed": {"type": "boolean"}, "analysis_id": {"type": "string"},
            "counts": {"type": "object"}, "quality": {"type": "object"}, "next_step": {"type": "object"},
        },
        "payload_required": ["is_committed", "analysis_id"],
    },
    "search_implementations": {
        "category": "read",
        "annotations": _ann("Search implementations", True, False, True, False),
        "input": {
            "query": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 50},
            "repo_id": _REPO_ID, "language": {"type": "string"},
            "kind": {"type": "string", "enum": ["feature", "card", "pattern", "report_section"]},
            "detail": _DETAIL, "rerank": {"type": "boolean", "default": False},
        },
        "required": ["query"],
        "payload": {
            "query": {"type": "string"}, "detail": {"type": "string"},
            "channels": {"type": "object"}, "results": {"type": "array"},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "payload_required": ["query", "detail", "channels", "results"],
    },
    "get_card": {
        "category": "read",
        "annotations": _ann("Get card", True, False, True, False),
        "input": {"card_id": {"type": "string"}, "detail": _DETAIL},
        "required": ["card_id"],
        "payload": {"card": {"type": "object"}},
        "payload_required": ["card"],
    },
    "list_patterns": {
        "category": "read",
        "annotations": _ann("List patterns", True, False, True, False),
        "input": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
            "cursor": {"type": "string"},
        },
        "required": [],
        "payload": {
            "count": {"type": "integer"}, "total": {"type": "integer"},
            "items": {"type": "array"}, "next_cursor": {"type": "string"},
        },
        "payload_required": ["count", "items"],
    },
    "get_report": {
        "category": "read",
        "annotations": _ann("Get report", True, False, True, False),
        "input": {
            "repo_id": _REPO_ID, "commit_sha": {"type": "string"},
            "format": {"type": "string", "enum": ["json", "markdown", "both"], "default": "json"},
        },
        "required": ["repo_id"],
        "payload": {
            "repo": {"type": "object"}, "analysis": {"type": "object"},
            "report": {"type": "object"}, "report_md": {"type": "string"},
        },
        "payload_required": ["repo", "analysis", "report"],
    },
    "list_repos": {
        "category": "read",
        "annotations": _ann("List repos", True, False, True, False),
        "input": {
            "query": {"type": "string"}, "language": {"type": "string"},
            "is_stale_only": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
            "cursor": {"type": "string"},
        },
        "required": [],
        "payload": {
            "count": {"type": "integer"}, "total": {"type": "integer"},
            "items": {"type": "array"}, "next_cursor": {"type": "string"},
        },
        "payload_required": ["count", "items"],
    },
    "recall_stats": {
        "category": "read",
        "annotations": _ann("Recall stats", True, False, True, False),
        "input": {},
        "required": [],
        "payload": {
            "schema_version": {"type": "string"}, "contract_id": {"type": "string"},
            "embedder": {"type": "object"}, "counts": {"type": "object"},
            "quality": {"type": "object"}, "stale": {"type": "object"}, "disk": {"type": "object"},
        },
        "payload_required": ["schema_version", "contract_id", "embedder", "counts"],
    },
    "help": {
        "category": "read",
        "annotations": _ann("Help", True, False, True, False),
        "input": {"topic": {"type": "string", "enum": [
            "analyze-flow", "report-contract", "card-kinds", "search-usage", "patterns",
        ]}},
        "required": ["topic"],
        "payload": {
            "topic": {"type": "string"}, "title": {"type": "string"},
            "markdown": {"type": "string"}, "related_topics": {"type": "array"},
        },
        "payload_required": ["topic", "title", "markdown"],
    },
    "forget_analysis": {
        "category": "destructive",
        "annotations": _ann("Forget analysis", False, True, True, False),
        "input": {"analysis_id": {"type": "string"}, "confirm": {"const": True}},
        "required": ["analysis_id", "confirm"],
        "payload": {
            "deleted": {"const": "analysis"}, "counts": {"type": "object"},
            "reclustered": {"type": "boolean"},
        },
        "payload_required": ["deleted", "counts", "reclustered"],
    },
    "forget_repo": {
        "category": "destructive",
        "annotations": _ann("Forget repo", False, True, True, False),
        "input": {"repo_id": _REPO_ID, "confirm": {"const": True}},
        "required": ["repo_id", "confirm"],
        "payload": {
            "deleted": {"const": "repo"}, "summary": {"type": "object"},
            "reclustered": {"type": "boolean"},
        },
        "payload_required": ["deleted", "summary", "reclustered"],
    },
    "upload_repo_bundle": {
        "category": "write",
        "annotations": _ann("Upload repo bundle", False, False, True, False),
        "input": {
            # A2/D3：bundle_path 与 bundle_url 二选一，哪个都不能单独标必填；真正的
            # 「必须二选一」由 handler 判定（这里只描述形状）。
            "bundle_path": {"type": "string"}, "bundle_url": {"type": "string"},
            "repo_url": {"type": "string"},
            "ref": {"type": "string"}, "subpath": {"type": "string"}, "sha256": {"type": "string"},
        },
        "required": ["repo_url"],
        "payload": {
            "repo": {"type": "object"}, "commit_sha": {"type": "string"},
            "bytes": {"type": "integer"}, "repo_path": {"type": "string"},
            "is_new": {"type": "boolean"}, "warnings": {"type": "array"},
            "next_step": {"type": "object"},
        },
        "payload_required": ["repo", "commit_sha"],
    },
}


def _input_schema(spec: dict[str, Any]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": spec["input"],
    }
    if spec["required"]:
        schema["required"] = spec["required"]
    return schema


def _output_schema(spec: dict[str, Any]) -> dict[str, Any]:
    """工具的 outputSchema。

    MCP 官方 SDK 强制要求根为 ``{"type":"object"}``（否则客户端 zod 报
    ``expected "object"``），所以这里把 ``oneOf`` 放在一个 object 根之下：
    根级 ``properties`` 列出 ``ok``/``error`` 便于校验器推断，``oneOf`` 再精确区分
    「错误信封 / payload」两分支。
    """
    payload: dict[str, Any] = {
        "type": "object",
        "additionalProperties": True,
        "properties": {"ok": {"const": True}, **spec["payload"]},
    }
    if spec["payload_required"]:
        payload["required"] = ["ok", *spec["payload_required"]]
    return {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "error": {
                "type": "object",
                "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
                "required": ["code", "message"],
            },
        },
        "required": ["ok"],
        "additionalProperties": True,
        "oneOf": [TOOL_ERROR_SCHEMA, payload],
    }


def tool_definitions() -> list[dict[str, Any]]:
    """返回 MCP tools/list 的定义（按文档顺序）。"""
    out: list[dict[str, Any]] = []
    for name, spec in _TOOLS.items():
        out.append({
            "name": name,
            "title": spec["annotations"]["title"],
            "description": _DESCRIPTIONS.get(name, ""),
            "inputSchema": _input_schema(spec),
            "outputSchema": _output_schema(spec),
            "annotations": spec["annotations"],
        })
    return out


def tool_category(name: str) -> str | None:
    spec = _TOOLS.get(name)
    return spec["category"] if spec else None


def tool_names() -> list[str]:
    return list(_TOOLS.keys())


_DESCRIPTIONS: dict[str, str] = {
    "fetch_repo": "Clone a public repository, register it, and return its repo_id / commit / clone path.",
    "get_evidence_pack": "Static evidence pack: directory tree + entry points + symbol list (no code slices).",
    "read_file_slice": "Read an exact byte range from a file in a cloned repo (never dump whole files).",
    "request_repo_bundle": "Pack the clone into a single git bundle for remote download (remote form).",
    "begin_analysis": "Open an analysis session; returns session_id + evidence pack + contract + checklist.",
    "validate_report": "Idempotent report self-check (read-only vs the knowledge base; with session_id it also advances the session to validated so commit_report can run).",
    "commit_report": "The only write path: full validation, then persist + index (code_mismatch must be 0).",
    "search_implementations": "Cross-repo semantic recall by functional intent (3-channel RRF).",
    "get_card": "Fetch a single card with its real code slices and evidence chain.",
    "list_patterns": "Cross-repo patterns (clusters confirmed by >=2 independent repos).",
    "get_report": "Fetch a repo's committed analysis report (JSON / Markdown).",
    "list_repos": "Catalog of registered repos, with stale flags and quality summaries.",
    "recall_stats": "Knowledge base overview and quality metrics grouped by producer.",
    "help": "Structured help for five fixed topics, readable by any MCP client.",
    "forget_analysis": "DESTRUCTIVE (default off): hard-delete one analysis and its cascade.",
    "forget_repo": "DESTRUCTIVE (default off): hard-delete a repo and all its analyses.",
    "upload_repo_bundle": "Register a repo from an uploaded git bundle (offline / private).",
}
