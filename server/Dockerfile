# ============================================================
# 服务层镜像：FastAPI + agent（11 工具）+ rag 检索管道
# 构建：docker build -f server/Dockerfile -t analyst-server .
# 构建上下文 = 项目根目录（要能读到 agent/ rag/ server/ pyproject.toml）
# ============================================================

# 基础镜像：slim 变体只含运行 Python 的最小系统（不含编译器/文档），
# 体积小、攻击面小。对比完整版 python:3.12 省 700MB+
FROM python:3.12-slim

# 环境变量：
#   PIP_INDEX_URL —— 本机直连 PyPI 会卡死，装依赖必须走清华镜像
#   PIP_DEFAULT_TIMEOUT —— pip 默认 15s 读超时，torch（554MB）等大 wheel
#     下载中网络一抖就 ReadTimeout 整装失败；放宽到 300s
#   PYTHONUNBUFFERED=1 —— 关掉 Python 输出缓冲，日志立刻出现在 docker logs，
#                         否则容器崩了最后一段日志可能还憋在缓冲区里
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=300 \
    PYTHONUNBUFFERED=1

# 容器内的工作目录（后续 COPY/RUN/CMD 都相对这里）
WORKDIR /app

# CPU 版 torch 先装（下载量与镜像体积双重优化）：
# PyPI/清华源给 Linux 的是 CUDA 版 torch（连带 nvidia 全家桶 ~2.5GB 下载、
# 镜像 13.5GB）；CPU 索引的 torch 同样满足 sentence-transformers 的
# torch>=2.2 依赖，后续主安装时已满足、不会再装 CUDA 版——下载省 ~2GB、
# 镜像省 ~8GB。本系统只做 CPU 推理（bge-m3 嵌入 / rerank）。
# 放在 COPY pyproject 之前：这层在依赖清单变化时仍命中缓存，不重下 torch
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# ---------- 依赖层（先装，后拷代码） ----------
# Docker 镜像按"层"缓存：每行指令一层。把"变更频繁的东西"放在文件
# 后面、把"变更不频繁的"放前面——依赖清单很少变，装依赖这层能长期
# 命中缓存，改一行 agent 代码重建时不用重装几百兆的包
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# ---------- 应用代码层（变更频繁，放后面） ----------
COPY agent/ ./agent/
COPY rag/ ./rag/
COPY server/ ./server/

# 模型（rag/models，7GB）与数据文件（data/）不进镜像：
# 运行时用 bind mount 从宿主机挂载（见 docker-compose.yml 的 volumes 段）。
# 进镜像的代价：镜像膨胀到 10GB+、每次重建都要重传、模型更新要重打镜像

# 声明容器监听端口（文档作用；真正的端口映射在 compose 的 ports 段）
EXPOSE 8000

# 启动命令：uvicorn 起 FastAPI。
#  --host 0.0.0.0 是必须的：容器默认 localhost 只接受容器内部的连接，
#  绑定 0.0.0.0 才能让容器外的浏览器/nginx 访问
CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
