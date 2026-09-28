# syntax=docker/dockerfile:1.6
# ============================================================
# memex — 单容器远程 MCP 服务（serve-http）多阶段构建
# 规格来源：docs/deployment.md §5（容器形态）
#
# 镜像只承载「机械活 + 代码」：标准库 HTTP 服务 + git clone/bundle + 嵌入依赖（CPU-only）。
# **不烘焙嵌入模型**——模型由容器首启时的准备步骤下载到挂载卷内（见 §5.2）。
# 因此镜像小、构建快、换模型不用重建镜像；代价是首次启动要联网下载约 470MB。
#
# 不装 analysis extra（tree-sitter 尚未接线）；不引入任何 ASGI 依赖
# （server 恒用标准库 ThreadingHTTPServer）。
#
# 缓存策略（需 BuildKit）：deps 阶段用 pip cache mount 复用已下载 wheel。
# ============================================================

# ---- base ----
FROM python:3.12-slim AS base
# git 是硬依赖：T1 clone / T4 bundle / T17 verify 都调 git 二进制
# tzdata 让镜像认得 TZ（报告时间戳按北京时间渲染）
# ca-certificates：首启联网下载嵌入模型走 HTTPS 的前提
RUN apt-get update \
 && apt-get install -y --no-install-recommends git tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai
WORKDIR /app

# ---- deps: 只装第三方依赖（default extra），版本从 pyproject 读取，避免漂移 ----
FROM base AS deps
COPY pyproject.toml ./
RUN --mount=type=cache,id=pip,target=/root/.cache/pip \
    python -m pip install --upgrade pip
# 先装 **CPU-only torch**：PyPI 的 torch 会拖整套 CUDA（nvidia-* wheels），
# 单这一层就 3GB+；本服务不需 GPU，走 PyTorch CPU index 装可让镜像从 ~4GB 缩到 ~1GB。
# 放在 default extra 之前：pip 解析 sentence-transformers 时看到 torch 已满足，不会重装。
RUN --mount=type=cache,id=pip,target=/root/.cache/pip \
    python -m pip install --index-url https://download.pytorch.org/whl/cpu torch
RUN --mount=type=cache,id=pip,target=/root/.cache/pip \
    python - <<'PY'
import subprocess, sys, tomllib

with open("pyproject.toml", "rb") as fh:
    cfg = tomllib.load(fh)
# 只取 default extra（numpy + sentence-transformers）；analysis extra 不进生产镜像
deps = cfg["project"]["optional-dependencies"]["default"]
subprocess.check_call([sys.executable, "-m", "pip", "install", *deps])
PY

# ---- runner: 运行时镜像（第三方依赖 + 本项目源码）----
FROM deps AS runner
# 装 memex 命令本身；第三方依赖已在上一层，这里只补本项目（--no-deps）
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN --mount=type=cache,id=pip,target=/root/.cache/pip \
    python -m pip install --no-deps .

# 非 root：服务要 clone 任意仓、能读整个 home，有 root 等于全盘可读
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin memex \
 && mkdir -p /data/memex /data/models \
 && chown -R memex:memex /data/memex /data/models

USER memex
# 容器内数据根（E17 的 MEMEX_HOME）。用 /data/memex：
# 容器里的挂载点本无标准，是镜像作者定的；/data 是 docker 镜像里最常见的一派
# （本机 NAS 上其他应用也多用 /data、/config 这类短路径），比自己发明一个 /srv/... 好认。
# 这只是容器内路径，真正落盘位置由 compose 的卷决定。
ENV MEMEX_HOME=/data/memex
# 嵌入模型缓存**单独一个挂载点**（/data/models），与数据目录分开，便于各自挂卷：
# 模型是纯缓存、可再生，数据是要备份的真源。首启由 memex 联网下载到 HF_HOME（§5.2），
# 之后每次启动命中同一份缓存、不再联网。
# MEMEX_EMBEDDER_LOAD_TIMEOUT 放大到 300s：首启在容器内下载 470MB 比本机慢得多，
# 沿用默认 20s 会把「第一次下载」误判为加载失败。
ENV HF_HOME=/data/models \
    MEMEX_EMBEDDER_LOAD_TIMEOUT=300
VOLUME /data/memex /data/models
EXPOSE 8931

# exec 形式（无 shell）→ PID 1 就是 memex，docker stop 信号直达进程。
# 容器内必须绑 0.0.0.0：绑回环等于外部访问不到（cli.py 默认 127.0.0.1 是本地形态）。
ENTRYPOINT ["memex"]
CMD ["serve-http", "--host", "0.0.0.0", "--port", "8931"]
