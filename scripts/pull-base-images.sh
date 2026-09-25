#!/usr/bin/env bash
# 拉取 docker-compose 所需的基础镜像（Docker Hub 直连不可达的环境用）。
#
# 原理：pull + tag 模式——从镜像源拉 dockerproxy.net/library/xxx，
# 再 tag 回规范名 xxx，compose 里的 image 字段不用改、换源不用改 compose。
# 镜像源会轮流抽风（限流/停服/大 blob 停摆），脚本按序尝试多个源，
# 成功即 tag 规范名并跳过后续源。
#
# 用法：./scripts/pull-base-images.sh
set -euo pipefail

MIRRORS=(
  "dockerproxy.net"
  "docker.1panel.live"
  "docker.m.daocloud.io"
)

# "源镜像路径 规范tag" 对
IMAGES=(
  "library/python:3.12-slim python:3.12-slim"
  "library/node:22-alpine node:22-alpine"
  "library/nginx:1.27-alpine nginx:1.27-alpine"
  "library/redis:7-alpine redis:7-alpine"
  "pgvector/pgvector:pg16 pgvector/pgvector:pg16"
)

for entry in "${IMAGES[@]}"; do
  src=${entry%% *}
  tag=${entry##* }
  if docker image inspect "$tag" >/dev/null 2>&1; then
    echo "✓ 已存在 $tag，跳过"
    continue
  fi
  ok=0
  for mirror in "${MIRRORS[@]}"; do
    echo "尝试 $mirror/$src ..."
    if docker pull "$mirror/$src" >/dev/null 2>&1; then
      docker tag "$mirror/$src" "$tag"
      echo "✓ $tag（来自 $mirror）"
      ok=1
      break
    fi
    echo "  × $mirror 失败，换下一个源"
  done
  if [ "$ok" != 1 ]; then
    echo "✗ $tag 所有镜像源均失败，请稍后重试或手动添加可用镜像源"
  fi
done

echo "完成。docker images 确认 5 个基础镜像均已就位。"
