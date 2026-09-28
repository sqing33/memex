"""嵌入器抽象（D1 / D2 / G22）。

三种后端，按 MEMEX_EMBEDDER 选择：
- "sentence-transformers:<model>"（默认）：本地真语义模型，多语言 MiniLM；
- "http:<url>"：调远端嵌入 HTTP 接口（POST {"texts":[...]} -> {"vectors":[[...]]}）；
- "hash:512"：**仅冒烟**用的哈希伪向量，必须在响应里带 degraded=true（G22）。

铁律：真嵌入器不可用时，启动检查直接 REFUSE（3 条出路），绝不静默降级为 hash
（除非调用方显式指定 hash:512）。hash 向量只保证「同文本同向量」，无任何语义。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import threading
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .core import MemexError

DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
HASH_DIM = 512
ALL_MINILM_DIM = 384


@dataclass
class Embedder:
    """嵌入器句柄。model 是稳定标识（写入 chunk_vectors.embedder）。"""

    model: str
    dim: int
    degraded: bool
    _fn: Callable[[list[str]], list[list[float]]]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self._fn(texts)
        for v in vecs:
            if len(v) != self.dim:
                raise MemexError(
                    "internal",
                    "嵌入维度不一致",
                    {"expected": self.dim, "actual": len(v), "model": self.model},
                )
        return vecs

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


def _hash_vec(text: str, dim: int = HASH_DIM) -> list[float]:
    """确定性伪向量：sha256 派生种子 -> 分桶计数 -> L2 归一。无语义，仅冒烟。"""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    buckets = [0.0] * dim
    # 用哈希字节铺满桶，稳定且分布均匀
    for i in range(dim):
        b = h[(i * 7) % len(h)]
        buckets[i] = (b / 255.0) - 0.5
    norm = math.sqrt(sum(x * x for x in buckets)) or 1.0
    return [x / norm for x in buckets]


def hash_embedder(dim: int = HASH_DIM) -> Embedder:
    return Embedder(
        model=f"hash:{dim}",
        dim=dim,
        degraded=True,
        _fn=lambda texts: [_hash_vec(t, dim) for t in texts],
    )


def _sanitize_no_proxy_env() -> None:
    """清理 no_proxy/NO_PROXY 中 httpx 无法解析的条目（如带方括号的 IPv6 `[::1]`）。

    背景：huggingface_hub 用 httpx 建客户端时会解析 no_proxy；`[::1]` 这种带方括号的
    IPv6 会让 httpx 抛 InvalidURL("Invalid port: ':1]'")，进而导致本地嵌入模型加载失败。
    这里只剔除无法解析的条目，保留 127.0.0.1/localhost 等有效项。
    """
    for key in ("no_proxy", "NO_PROXY"):
        raw = os.environ.get(key)
        if not raw:
            continue
        kept = [
            t
            for t in (item.strip() for item in raw.split(","))
            if t and "[" not in t and "]" not in t
        ]
        os.environ[key] = ",".join(kept)


def _load_timeout_seconds() -> float:
    """加载真模型的墙钟上限（秒）；可用 MEMEX_EMBEDDER_LOAD_TIMEOUT 覆盖。

    默认 300s：**镜像不烘焙模型**（deployment.md §5.2），容器首启要联网下载
    约 470MB 到卷内 HF 缓存，比本机命中缓存慢得多——沿用早先的 20s 会把
    「第一次下载」误判成加载失败。上限只决定「一次加载最多等多久」，
    不改变失败语义：超时仍**显式报错**，绝不静默降级（D1/G22）。
    """
    raw = os.environ.get("MEMEX_EMBEDDER_LOAD_TIMEOUT")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return 300.0


def _run_with_deadline(fn: Callable[[], Any], seconds: float, label: str) -> Any:
    """在后台线程里执行 fn，超过 seconds 未返回即抛 MemexError（不静默降级）。

    网络不可达时 huggingface_hub 会带退避重试，单次调用可能挂几分钟；此护栏保证
    失败能**显式**返回而不是无限挂起（守 D1/G22）。超时后工作线程为 daemon，随进程退出。
    """
    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["v"] = fn()
        except BaseException as exc:  # noqa: BLE001 - 原样回传
            box["e"] = exc

    th = threading.Thread(target=_worker, name="memex-embedder-load", daemon=True)
    th.start()
    th.join(seconds)
    if th.is_alive():
        raise MemexError(
            "internal",
            "嵌入模型加载超时（>%ds）：%s" % (int(seconds), label),
            {
                "reason": "load timeout",
                "outs": [
                    "确保首启能出网（模型会自动下到卷内 HF 缓存），或预置模型文件",
                    "MEMEX_EMBEDDER=http:<url> 指向远端嵌入接口",
                    "仅调试可显式 MEMEX_EMBEDDER=hash:512（无语义，degraded:true）",
                ],
            },
        )
    if "e" in box:
        exc = box["e"]
        if isinstance(exc, MemexError):
            raise exc
        raise MemexError("internal", "加载嵌入模型失败：" + str(exc), {"reason": str(exc)}) from exc
    return box.get("v")


def _st_load_online(model_name: str) -> Any:
    """联网拉取一次模型（本地无缓存时）。

    给 huggingface_hub 的元数据/下载设置**有界超时**：默认值在坏代理下会触发
    多轮 http_backoff，把一次调用拖到几分钟；有界后失败也能在数十秒内返回，
    从而保证「显式报错」而不是无限挂起。
    """
    import os

    keys = ("HF_HUB_ETAG_TIMEOUT", "HF_HUB_DOWNLOAD_TIMEOUT")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        if os.environ.get(k) is None:
            os.environ[k] = "15"
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        return SentenceTransformer(model_name)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _st_embedder(model_name: str) -> Embedder:
    """sentence-transformers 后端（可选依赖）。未安装 -> MemexError。"""
    _sanitize_no_proxy_env()
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception as exc:  # pragma: no cover - 环境相关
        raise MemexError(
            "unsupported",
            "sentence-transformers 未安装",
            {"model": model_name, "install": "uv pip install 'memex[default]'"},
        ) from exc
    try:
        # 离线优先：命中本地 HF 缓存即不联网；仅当本地没有才联网下载一次。
        # （联网回源会被代理拖住并触发 http_backoff 重试，是 initialize 超时的元凶。）
        try:
            model = SentenceTransformer(model_name, local_files_only=True)
        except Exception:  # noqa: BLE001 - 本地无缓存 -> 联网拉取一次（有界）
            model = _run_with_deadline(
                lambda: _st_load_online(model_name),
                _load_timeout_seconds(),
                "联网下载 " + model_name,
            )
    except Exception as exc:
        raise MemexError(
            "internal",
            "嵌入模型不可用：" + str(exc),
            {
                "model": model_name,
                "reason": str(exc),
                "outs": [
                    "预置模型文件到 HF 缓存（离线可用）",
                    "MEMEX_EMBEDDER=http:<url> 指向远端嵌入接口",
                    "仅调试可显式 MEMEX_EMBEDDER=hash:512（无语义，degraded:true）",
                ],
            },
        ) from exc
    _dim_fn = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    dim = int(_dim_fn() or ALL_MINILM_DIM)

    def fn(texts: list[str]) -> list[list[float]]:
        arr = model.encode(texts, normalize_embeddings=True)
        return [list(map(float, row)) for row in arr]

    return Embedder(model=f"sentence-transformers:{model_name}", dim=dim, degraded=False, _fn=fn)


def _http_embedder(url: str) -> Embedder:
    """HTTP 嵌入后端：POST {texts} -> {vectors, dim, model?}。"""
    probe = _http_call(url, ["__dim_probe__"])
    dim = int(probe.get("dim") or len(probe.get("vectors", [[]])[0]) or 0)
    if dim <= 0:
        raise MemexError("internal", "HTTP 嵌入接口未返回维度", {"url": url})
    model = str(probe.get("model") or f"http:{url}")

    def fn(texts: list[str]) -> list[list[float]]:
        resp = _http_call(url, texts)
        return [[float(x) for x in v] for v in resp["vectors"]]

    return Embedder(model=model, dim=dim, degraded=False, _fn=fn)


def _http_call(url: str, texts: list[str]) -> dict[str, Any]:
    body = json.dumps({"texts": texts}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as fh:  # noqa: S310 (受信 URL)
            return json.loads(fh.read().decode("utf-8"))  # type: ignore[no-any-return]
    except Exception as exc:
        raise MemexError("internal", "调用 HTTP 嵌入接口失败", {"url": url, "reason": str(exc)}) from exc


def local_model_cached(spec: str) -> bool:
    """轻量探测：给定 ST 模型名是否已在本地 HF 缓存（不 import torch / 不联网）。

    仅用 huggingface_hub 的 try_to_load_from_cache 查 config.json 是否落盘，
    用于启动横幅提前给出「未缓存」提示；探测失败一律当作未缓存（保守）。
    """
    name = (spec or "").strip()
    if name.startswith("sentence-transformers:"):
        name = name.split(":", 1)[1] or DEFAULT_MODEL
    if "/" not in name:
        name = "sentence-transformers/" + name
    try:
        from huggingface_hub import try_to_load_from_cache  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return False
    try:
        res = try_to_load_from_cache(name, "config.json")
        return isinstance(res, str)
    except Exception:  # noqa: BLE001
        return False


_EMBEDDER_CACHE: dict[str, Embedder] = {}
_EMBEDDER_ERRORS: dict[str, MemexError] = {}
_EMBEDDER_LOCK = threading.Lock()


def _cache_key(spec: str | None) -> str:
    """把等价规格归一到同一缓存键（None / 裸名 / sentence-transformers:名 同键）。"""
    s = (spec or "").strip()
    if not s:
        return DEFAULT_MODEL
    if s.startswith("sentence-transformers:"):
        return s.split(":", 1)[1] or DEFAULT_MODEL
    return s


def _build_embedder(spec: str | None) -> Embedder:
    if not spec:
        return _st_embedder(DEFAULT_MODEL)
    low = spec.strip()
    if low.startswith("hash:"):
        try:
            dim = int(low.split(":", 1)[1])
        except ValueError:
            dim = HASH_DIM
        return hash_embedder(dim)
    if low.startswith("http:"):
        return _http_embedder(low.split(":", 1)[1] and low[5:])
    if low.startswith("sentence-transformers:"):
        return _st_embedder(low.split(":", 1)[1] or DEFAULT_MODEL)
    # 裸模型名等价于 sentence-transformers:<name>
    return _st_embedder(low)


def get_embedder(spec: str | None) -> Embedder:
    """按规格构造嵌入器（进程内按规格缓存）。

    spec 形如 "hash:512" / "sentence-transformers:<model>" / "http:<url>"；
    为 None 或空时用默认真语义模型（D1）。

    真模型加载昂贵（import torch + 权重，冷启动十几秒），因此**进程内按 spec 缓存**：
    后台预热与首个工具调用共享同一句柄，只加载一次。
    """
    key = _cache_key(spec)
    cached = _EMBEDDER_CACHE.get(key)
    if cached is not None:
        return cached
    err = _EMBEDDER_ERRORS.get(key)
    if err is not None:
        raise err
    with _EMBEDDER_LOCK:
        cached = _EMBEDDER_CACHE.get(key)
        if cached is not None:
            return cached
        err = _EMBEDDER_ERRORS.get(key)
        if err is not None:
            raise err
        try:
            cached = _build_embedder(spec)
        except MemexError as exc:
            _EMBEDDER_ERRORS[key] = exc
            raise
        except Exception as exc:  # noqa: BLE001 - 归一为 MemexError 并缓存
            wrapped = MemexError(
                "internal", "构建嵌入器失败：" + str(exc), {"spec": key, "reason": str(exc)}
            )
            _EMBEDDER_ERRORS[key] = wrapped
            raise wrapped from exc
        _EMBEDDER_CACHE[key] = cached
        return cached


# —— 暴力余弦（app 层回退；sqlite-vec 不可用时使用）——

def pack_vector(vec: list[float]) -> bytes:
    """float32 小端序 BLOB（chunk_vectors.vec）。"""
    return struct.pack("<%df" % len(vec), *vec)


def unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack("<%df" % n, blob))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
