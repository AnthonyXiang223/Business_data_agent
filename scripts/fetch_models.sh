#!/usr/bin/env bash
# 下载 RAG 嵌入/精排模型到 rag/models/（约 6.5GB，不进 git）。
#
# 默认走 hf-mirror.com（HuggingFace 国内镜像）；能直连 HuggingFace 的环境
# 用 `HF_ENDPOINT=https://huggingface.co ./scripts/fetch_models.sh`。
# 克隆仓库后、跑 kb-init 之前必须执行一次。
set -euo pipefail

cd "$(dirname "$0")/.."   # 回到项目根目录

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

if ! command -v huggingface-cli >/dev/null 2>&1; then
  echo "未找到 huggingface-cli，安装 huggingface_hub ..."
  python -m pip install --quiet huggingface_hub
fi

mkdir -p rag/models

for repo in BAAI/bge-m3 BAAI/bge-reranker-v2-m3; do
  name=${repo##*/}
  echo "下载 $repo → rag/models/$name（走 $HF_ENDPOINT）"
  huggingface-cli download "$repo" --local-dir "rag/models/$name"
done

echo "完成。验证："
ls -d rag/models/bge-m3 rag/models/bge-reranker-v2-m3
