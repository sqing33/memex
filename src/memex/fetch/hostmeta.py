"""宿主元数据客户端（G10 写侧 / P1-1 / P2-1）。

设计取舍：
- 只用 stdlib（urllib），与 embeddings.py:240 同一套路，不引第三方依赖。
- 复用 cfg.git_tokens 里的同一份凭据，私有仓也能取到身份。
- **任何失败都不抛**：返回 None + reason，调用方降级到 host#owner/name 并记 warning。
  但「取不到」与「确认不是 fork」必须可区分：_HostMeta.fork 只在 API 明确说了 fork:false 时置 False。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..core import Config, MemexError, RepoRef

# GitHub 之外还没实现的宿主；显式列出来，避免「没实现」被误读成「实现错了」。
SUPPORTED_META_HOSTS: tuple[str, ...] = ("github.com",)


@dataclass
class HostMeta:
    """一次元数据查询的结果。字段全为 None 表示「没查到」，不表示「没有」。"""

    numeric_id: int | None = None
    fork: bool | None = None
    fork_of: str | None = None
    stars: int | None = None
    license: str | None = None
    description: str | None = None
    default_branch: str | None = None
    reason: str | None = None

    def identity_key(self, ref: RepoRef) -> tuple[str, bool]:
        """返回 (identity_key, 是否为退化值)。退化即「身份未校验」。"""
        if self.numeric_id is not None:
            return f"{ref.host}#{self.numeric_id}", False
        return f"{ref.host}#{ref.owner}/{ref.name}", True


def _api_url(ref: RepoRef) -> str:
    if ref.host == "github.com":
        return f"https://api.github.com/repos/{ref.owner}/{ref.name}"
    raise MemexError(
        "unsupported",
        "该宿主尚未实现元数据 API",
        {"host": ref.host, "supported": list(SUPPORTED_META_HOSTS)},
    )


def _headers(cfg: Config, ref: RepoRef) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "memex",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = cfg.git_tokens.get(ref.host)
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_host_meta(cfg: Config, ref: RepoRef, *, timeout: float = 10.0) -> HostMeta:
    """查一次宿主元数据。**永不抛**：任何失败都落在 reason 上。"""
    if ref.host not in SUPPORTED_META_HOSTS:
        return HostMeta(reason=f"{ref.host} 未实现元数据 API，身份按 owner/name 判定")
    url = _api_url(ref)
    req = urllib.request.Request(url, headers=_headers(cfg, ref))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:  # noqa: S310 (宿主已过白名单)
            payload = json.loads(fh.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return HostMeta(reason="宿主 API 返回 404（仓库不存在或无凭据），身份未校验")
        return HostMeta(reason=f"宿主 API 返回 HTTP {exc.code}，身份未校验")
    except urllib.error.URLError as exc:
        return HostMeta(reason=f"宿主 API 不可达（{exc.reason}），身份未校验")
    except (TimeoutError, ValueError) as exc:
        return HostMeta(reason=f"宿主 API 响应不可解析（{exc}），身份未校验")
    if not isinstance(payload, dict):
        return HostMeta(reason="宿主 API 返回结构异常，身份未校验")
    return _parse_github(payload)


def _parse_github(payload: dict[str, Any]) -> HostMeta:
    meta = HostMeta()
    raw_id = payload.get("id")
    if isinstance(raw_id, int):
        meta.numeric_id = raw_id
    if isinstance(payload.get("fork"), bool):
        meta.fork = payload["fork"]
        if meta.fork:
            parent = payload.get("parent")
            if isinstance(parent, dict) and isinstance(parent.get("full_name"), str):
                meta.fork_of = parent["full_name"]
    stars = payload.get("stargazers_count")
    if isinstance(stars, int):
        meta.stars = stars
    lic = payload.get("license")
    if isinstance(lic, dict) and isinstance(lic.get("spdx_id"), str):
        # GitHub 对无许可证仓库返回 "NOASSERTION"，那是「没有」不是「名字叫这个」。
        spdx = lic["spdx_id"]
        meta.license = None if spdx in ("", "NOASSERTION") else spdx
    if isinstance(payload.get("description"), str) and payload["description"].strip():
        meta.description = payload["description"].strip()
    if isinstance(payload.get("default_branch"), str):
        meta.default_branch = payload["default_branch"]
    if meta.numeric_id is None:
        meta.reason = "宿主 API 未返回 id，身份未校验"
    return meta
