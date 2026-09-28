# syntax=docker/dockerfile:1.6
# ============================================================
# memex — 单容器远程 MCP 服务（serve-http）多阶段构建
# 规格来源：docs/deployment.md §5（容器形态）
# 与 Benchmark 项目 Dockerfile 同构：多阶段 / 分节中文注释 / 非 root / exec 直启
#
# 镜像只承载「机械活 + 知识库」：标准库 HTTP 服务 + git clone/bundle + 嵌入。
# 不装 analysis extra（tree-sitter 尚未接线），不引入任何 ASGI 依赖
# （server 恒用标准库 ThreadingHTTPServer，见 pyproject 注释里的 D6）。
#
# 缓存策略（需 BuildKit）：
# - deps 阶段用 pip cache mount 复用已下载 wheel
# - model 阶段用 HF cache mount 复用 470MB 模型下载；该阶段只依赖第三方依赖、
#   不依赖 src/，因此改代码不会触发模型重下
# ============================================================

# ---- base ----
FROM python:3.12-slim AS base
# git 是硬依赖：T1 clone / T4 bundle / T17 verify 都调 git 二进制
# tzdata 让镜像认得 TZ（报告时间戳按北京时间渲染，与 Benchmark 一致）
RUN apt-get update \
 && apt-get install -y --no-install-recommends git tzdata \
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
RUN --mount=type=cache,id=pip,target=/root/.cache/pip \
    python - <<'PY'
import subprocess, sys, tomllib

with open("pyproject.toml", "rb") as fh:
    cfg = tomllib.load(fh)
# 只取 default extra（numpy + sentence-transformers）；analysis extra 不进生产镜像
deps = cfg["project"]["optional-dependencies"]["default"]
subprocess.check_call([sys.executable, "-m", "pip", "install", *deps])
PY

# ---- model: 烘焙嵌入模型（不在请求路径上下载，见 deployment.md §5.2）----
FROM deps AS model
ENV HF_HOME=/opt/memex-models \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_OFFLINE=1
# 先下到 BuildKit cache mount（跨构建复用 470MB），再拷进镜像层。
# 运行时 HF_HOME 指向 /opt/memex-models，配合 OFFLINE=1 完全离线加载。
RUN --mount=type=cache,id=hf-model,target=/tmp/hf-cache \
    HF_HOME=/tmp/hf-cache python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')" \
 && mkdir -p /opt/memex-models \
 && cp -a /tmp/hf-cache/. /opt/memex-models/

# ---- runner: 运行时镜像（第三方依赖 + 模型 + 本项目源码）----
FROM model AS runner
# 装 memex 命令本身；第三方依赖/模型层已在下面，这里只补本项目（--no-deps）
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN --mount=type=cache,id=pip,target=/root/.cache/pip \
    python -m pip install --no-deps .

# 非 root：服务要 clone 任意仓、能读整个 home，有 root 等于全盘可读
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin memex \
 && mkdir -p /var/lib/memex \
 && chown -R memex:memex /var/lib/memex /opt/memex-models

USER memex
# 远程形态的 MEMEX_HOME（E17）；compose 具名卷挂到这里
ENV MEMEX_HOME=/var/lib/memex
VOLUME /var/lib/memex
EXPOSE 8931

# exec 形式（无 shell）→ PID 1 就是 memex，docker stop 信号直达进程。
# 容器内必须绑 0.0.0.0：绑回环等于外部访问不到（cli.py 默认 127.0.0.1 是本地形态）。
ENTRYPOINT ["memex"]
CMD ["serve-http", "--host", "0.0.0.0", "--port", "8931"]
