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
import struct
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


def _st_embedder(model_name: str) -> Embedder:
    """sentence-transformers 后端（可选依赖）。未安装 -> MemexError。"""
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception as exc:  # pragma: no cover - 环境相关
        raise MemexError(
            "unsupported",
            "sentence-transformers 未安装",
            {"model": model_name, "install": "uv pip install 'memex[default]'"},
        ) from exc
    try:
        model = SentenceTransformer(model_name)
    except Exception as exc:  # pragma: no cover
        raise MemexError(
            "internal", "加载本地嵌入模型失败", {"model": model_name, "reason": str(exc)}
        ) from exc
    dim = int(model.get_sentence_embedding_dimension() or ALL_MINILM_DIM)

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


def get_embedder(spec: str | None) -> Embedder:
    """按规格构造嵌入器。

    spec 形如 "hash:512" / "sentence-transformers:<model>" / "http:<url>"；
    为 None 或空时用默认真语义模型（D1）。
    """
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
