"""core —— 配置、路径、slugify、repo URL 解析、信息单元计数。

零第三方依赖（标准库 only）。见 docs/tech-design.md §3.1 / §3.3、
docs/operations.md §1（环境变量总表）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .constants import (
    BLANK_PLACEHOLDERS,
    DEFAULT_TOOLS,
    MIN_INTENT_UNITS,
    MIN_PRINCIPLE_UNITS,
    MIN_SUMMARY_UNITS,
    REGEX_CJK,
    REGEX_WORD,
    TOOL_CATEGORIES,
)

DEFAULT_HOSTS = ("github.com", "gitlab.com", "gitee.com")


class MemexError(Exception):
    """业务失败。一律走 {"ok": false, "error": {code, message, details}} 正常返回（G2）。

    绝不用于协议错误（协议错误只表示「调用方把工具用错了」）。
    """

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_error(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            err["details"] = self.details
        return err


# ——————————————————————————————— 路径 ———————————————————————————————
def default_home() -> Path:
    return Path(os.environ.get("MEMEX_HOME") or (Path.home() / ".memex"))


@dataclass(frozen=True)
class Paths:
    """$MEMEX_HOME 下的目录布局（tech-design.md §2.1）。"""

    home: Path

    def __post_init__(self) -> None:
        # 容忍传入 str（测试/调用方便利）；内部一律以 Path 表示
        object.__setattr__(self, "home", Path(self.home))

    @classmethod
    def default(cls) -> "Paths":
        return cls(default_home())

    @property
    def db(self) -> Path:
        return self.home / "memex.db"

    @property
    def index_db(self) -> Path:
        """远程形态的索引分库（E17）；本地形态与 memex.db 合一。"""
        return self.home / "index.db"

    @property
    def repos(self) -> Path:
        return self.home / "repos"

    @property
    def repos_dir(self) -> Path:
        """`repos` 的别名（部分模块以 dir 结尾更好读）。"""
        return self.home / "repos"

    @property
    def site(self) -> Path:
        return self.home / "site"

    @property
    def credentials(self) -> Path:
        return self.home / "credentials.json"

    def repo_dir(self, repo_id: str) -> Path:
        return self.repos / repo_id


# ——————————————————————————————— slugify / 命名 ———————————————————————————————
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """转成 kebab-case（features.key 用）。非 ASCII 一律降级为连字符。"""
    s = _SLUG_STRIP.sub("-", text.strip().lower()).strip("-")
    return s or "unnamed"


def repo_id_for(host: str, owner: str, name: str) -> str:
    """<host>__<owner>__<name>；host 全小写、owner/name 原样（mcp-tools.md §1.1）。"""
    return f"{host.lower()}__{owner}__{name}"


_REPO_ID_RE = re.compile(r"^[a-z0-9.-]+__[A-Za-z0-9._-]+__[A-Za-z0-9._-]+$")


def is_repo_id(value: str) -> bool:
    return bool(_REPO_ID_RE.match(value))


# ——————————————————————————————— repo URL 解析 ———————————————————————————————
@dataclass(frozen=True)
class RepoRef:
    host: str
    owner: str
    name: str
    url: str  # 规范化 https URL

    @property
    def repo_id(self) -> str:
        return repo_id_for(self.host, self.owner, self.name)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_GIT_SSH_RE = re.compile(r"^(?:[\w.-]+@)?([\w.-]+):([^/]+)/(.+?)(?:\.git)?$")


def parse_repo_url(raw: str, *, allow_hosts: tuple[str, ...] = DEFAULT_HOSTS) -> RepoRef:
    """解析公开仓地址。只接受 https/https 形与 scp 形 git 地址。

    G6：协议只允许 https；宿主必须在白名单内；local: 前缀不在此处理（由 fetch 层按形态决定）。
    """
    url = (raw or "").strip()
    if not url:
        raise MemexError("invalid_argument", "repo_url 为空")

    if url.startswith("local:") or url.startswith("/") or url.startswith("./"):
        # 本地路径形态由 fetch 层按 MEMEX_ALLOW_LOCAL_PATHS 决定；core 只给出原始值
        raise MemexError(
            "invalid_argument",
            "core.parse_repo_url 不处理本地路径；请走 fetch 层的 local 分支",
        )

    # scp 形：git@github.com:owner/name.git
    ssh = _GIT_SSH_RE.match(url) if not _URL_SCHEME_RE.match(url) else None
    if ssh:
        host, owner, name = ssh.group(1), ssh.group(2), ssh.group(3)
        if host not in allow_hosts:
            raise MemexError("unsupported", f"宿主不在白名单：{host}", {"host": host, "allow": list(allow_hosts)})
        return RepoRef(host.lower(), owner, _strip_git(name), f"https://{host.lower()}/{owner}/{_strip_git(name)}")

    m = re.match(r"^(https?)://([^/]+)/([^/]+)/([^/?#]+?)(?:\.git)?/?$", url)
    if not m:
        raise MemexError("invalid_argument", f"无法解析的仓库地址：{raw}")
    scheme, host, owner, name = m.group(1), m.group(2).lower(), m.group(3), _strip_git(m.group(4))
    if scheme != "https":
        raise MemexError("unsupported", f"只允许 https，收到 {scheme}://", {"scheme": scheme})
    if host not in allow_hosts:
        raise MemexError("unsupported", f"宿主不在白名单：{host}", {"host": host, "allow": list(allow_hosts)})
    if not owner or not name:
        raise MemexError("invalid_argument", f"无法解析 owner/name：{raw}")
    return RepoRef(host, owner, name, f"https://{host}/{owner}/{name}")


def _strip_git(name: str) -> str:
    return name[:-4] if name.endswith(".git") else name


# ——————————————————————————————— 信息单元 / 占位符 ———————————————————————————————
def count_units(text: str) -> int:
    """units = CJK/仮名/ハングル 字符数 + 拉丁/数字 连续串数（report-contract.md §3 / G13）。"""
    if not text:
        return 0
    cjk = len(REGEX_CJK.findall(text))
    words = len(REGEX_WORD.findall(text))
    return cjk + words


def is_blank(text: str) -> bool:
    """占位符（N/A / 无 / 未知 / 待补充 / TODO …）与纯空白都视为空（report-contract.md §3）。"""
    if text is None:
        return True
    s = text.strip()
    if not s:
        return True
    return s.lower() in BLANK_PLACEHOLDERS


def unit_thresholds() -> dict[str, int]:
    return {
        "principle": MIN_PRINCIPLE_UNITS,
        "summary": MIN_SUMMARY_UNITS,
        "intent": MIN_INTENT_UNITS,
    }


# ——————————————————————————————— 配置 ———————————————————————————————
def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise MemexError("invalid_argument", f"环境变量 {name} 不是整数：{raw!r}") from None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    """环境变量快照（docs/operations.md §1）。远程形态由 systemd 注入。"""

    home: Path
    # 基础
    tools: tuple[str, ...] = ("read", "write", "network")
    log_level: str = "info"
    # 抓取与宿主（G4 / G6）
    hosts: tuple[str, ...] = DEFAULT_HOSTS
    git_tokens: dict[str, str] = field(default_factory=dict)
    git_mirror: str = ""
    allow_local_paths: bool = True
    # 抓取时是否打宿主元数据 API（G10 写侧：identity_key / fork_of / stars / license）
    host_meta: bool = True
    clone_timeout: int = 300
    clone_concurrency: int = 3
    # 护栏（G5 / G7 / G16）
    max_file_bytes: int = 1_048_576
    max_repo_bytes: int = 2_147_483_648
    max_bundle_bytes: int = 536_870_912
    http_qps_per_token: int = 30
    embed_concurrency: int = 1
    pattern_chunk_max_units: int = 60
    # 嵌入（G11）
    embedder: str = ""
    rerank: str = "off"
    # 会话（G18）
    session_ttl_seconds: int = 7200
    session_retention_seconds: int = 604_800
    # 远程形态
    token: str = ""
    is_http: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        e = dict(os.environ if env is None else env)
        home = Path(e.get("MEMEX_HOME") or (Path.home() / ".memex"))
        is_http = bool(e.get("MEMEX_IS_HTTP"))
        tools = tuple(t.strip() for t in e.get("MEMEX_TOOLS", DEFAULT_TOOLS).split(",") if t.strip())
        for t in tools:
            if t not in TOOL_CATEGORIES:
                raise MemexError("invalid_argument", f"未知工具类别：{t}", {"categories": list(TOOL_CATEGORIES)})
        hosts = tuple(h.strip().lower() for h in e.get("MEMEX_GIT_HOSTS", ",".join(DEFAULT_HOSTS)).split(",") if h.strip())
        # host 里的 `.` 换成 `_`（operations.md §1.2）：先按白名单精确匹配，
        # 再兜底接受任意 `MEMEX_GIT_TOKEN__*`（把 `_` 还原为 `.`）。
        tokens: dict[str, str] = {}
        for h in hosts:
            key = "MEMEX_GIT_TOKEN__" + h.replace(".", "_")
            if e.get(key):
                tokens[h] = e[key]
        for k, v in e.items():
            if k.startswith("MEMEX_GIT_TOKEN__") and v:
                tokens.setdefault(k[len("MEMEX_GIT_TOKEN__") :].replace("_", "."), v)
        try:
            creds_path = home / "credentials.json"
            if creds_path.exists():
                import json as _json

                loaded = _json.loads(creds_path.read_text(encoding="utf-8"))
                for h, tok in (loaded.get("git_tokens") or {}).items():
                    if tok:
                        tokens.setdefault(h, tok)
        except (OSError, ValueError):
            pass
        return cls(
            home=home,
            tools=tools,
            log_level=e.get("MEMEX_LOG_LEVEL", "info"),
            hosts=hosts,
            git_tokens=tokens,
            git_mirror=e.get("MEMEX_GIT_MIRROR", ""),
            allow_local_paths=_env_bool("MEMEX_ALLOW_LOCAL_PATHS", not is_http),
            host_meta=_env_bool("MEMEX_HOST_META", True),
            clone_timeout=_env_int("MEMEX_CLONE_TIMEOUT", 300),
            clone_concurrency=_env_int("MEMEX_CLONE_CONCURRENCY", 3),
            max_file_bytes=_env_int("MEMEX_MAX_FILE_BYTES", 1_048_576),
            max_repo_bytes=_env_int("MEMEX_MAX_REPO_BYTES", 2_147_483_648),
            max_bundle_bytes=_env_int("MEMEX_MAX_BUNDLE_BYTES", 536_870_912),
            http_qps_per_token=_env_int("MEMEX_HTTP_QPS_PER_TOKEN", 30),
            embed_concurrency=_env_int("MEMEX_EMBED_CONCURRENCY", 1),
            pattern_chunk_max_units=_env_int("MEMEX_PATTERN_CHUNK_MAX_UNITS", 60),
            embedder=e.get("MEMEX_EMBEDDER", ""),
            rerank=e.get("MEMEX_RERANK", "off"),
            session_ttl_seconds=_env_int("MEMEX_SESSION_TTL_SECONDS", 7200),
            session_retention_seconds=_env_int("MEMEX_SESSION_RETENTION_SECONDS", 604_800),
            token=e.get("MEMEX_TOKEN", ""),
            is_http=is_http,
        )

    @property
    def paths(self) -> Paths:
        return Paths(self.home)

    def tool_enabled(self, category: str) -> bool:
        return category in self.tools


def json_pointer_escape(token: str) -> str:
    """RFC 6901 JSON Pointer 转义：~ -> ~0，/ -> ~1。"""
    return token.replace("~", "~0").replace("/", "~1")

