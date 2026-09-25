# ============================================================
# 前端镜像：多阶段构建（构建环境与运行环境分离）
# 构建：docker build -t analyst-web frontend-analyst/
# ============================================================
# 为什么多阶段：
#   阶段 1 需要 Node + 全部依赖来编译出 dist/ 静态文件；
#   但"运行"只需要一个能吐静态文件的服务器。
#   多阶段构建 = 前半段用胖环境（node:22），后半段只带走产物
#   （几十 KB 的 dist），最终镜像里没有 Node、没有 node_modules，
#   体积从 ~1GB 缩到 ~25MB。这是 Docker 最实用的模式之一。

# ---------- 阶段 1：builder —— 编译静态产物 ----------
# 用别名 AS builder 给它命名，后面阶段用 COPY --from=builder 拿产物
FROM node:22-alpine AS builder
WORKDIR /build

# 先拷依赖清单再 npm ci：与 server 镜像同理——package 清单没变时
# 这层命中缓存，改前端源码重建不用重装依赖
COPY package.json package-lock.json ./
# npmmirror：npm 官方源在国内同样慢
RUN npm config set registry https://registry.npmmirror.com && npm ci

# 拷全部源码（.dockerignore 会排除 node_modules/.env 等）
COPY . .
# VITE_ 前缀的环境变量会被 Vite 在构建期"烘焙"进产物：
# 这里写死 /api（相对路径），配合 nginx 反代实现同源请求——
# 浏览器只跟 nginx 说话，CORS 问题整体消失
ENV VITE_ANALYST_API_URL=/api
# 构建时跑一遍类型检查 + 单测：门禁不过镜像根本打不出来
RUN npm run build && npm test -- --run

# ---------- 阶段 2：运行 —— nginx 只服务静态文件 ----------
FROM nginx:1.27-alpine
# 覆盖默认站点配置：/ 吐静态产物，/api 反代到 server 容器
COPY nginx.conf /etc/nginx/conf.d/default.conf
# 从 builder 阶段只拷贝编译产物（--from=builder 是跨阶段拷贝语法）
COPY --from=builder /build/dist /usr/share/nginx/html
EXPOSE 80
