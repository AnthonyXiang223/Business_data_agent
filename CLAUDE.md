# 业务数据分析多智能体系统（research_deepagent）

## 项目定位

用 deepagents 构建生产级业务数据分析师系统。目标效果：**给一份数据 → 完成数据分析 → 可视化图表**。
用户学习路线：Agent 工程师（简历含金量驱动）。核心工程哲学：**自建校验/编排逻辑，用现成基础设施**（不造轮子、不自建沙箱/向量库/SQL 解析器）。

## 已确认的架构决策（用户拍板，不要推翻）

- 只用 deepagents 框架开发
- 查询方式：duckdb + SQL（`query_data` 工具）+ sqlglot 白名单护栏；阶段 3 换 Apache Doris 时护栏与 Agent SQL 技能零成本迁移（只换 pymysql 连接）
- 模型：deepseek-v4-flash（DeepSeek 官方 OpenAI 兼容网关 api.deepseek.com/v1，.env 的 OPENAI_MODEL_NAME/API_KEY/API_BASE）——曾用 Qwen3-8B@硅基流动，已迁移
- 数仓：Doris 自建 SQL 执行器（只读账号 + 审计表）；语义层：YAML 起步 → 阶段 5 升级结构化口径库 + Milvus 只做模糊检索；Superset 仅作展示终端（Agent 推结果，查询仍走 Agent-Doris 链路）
- 统计能力：预定义工具封装，不执行任意 Python 代码
- 可视化：Agent 产出标准 chart spec → 前端渲染（不依赖 Superset）

## 阶段路线图

1. ✅ 单 Agent + 文件数据（demo_agent/）
2. ✅ 前端图表渲染（create_chart 工具 + ChartCard 手写 SVG，nature-figure 视觉规范 + dataviz 调色板校验）
3. ⬜ Doris + 护栏迁移（sqlglot 方言换 mysql，工具逻辑不动）
4. ⬜ 拆分多智能体（主管 + SQL/统计/洞察/图表子智能体）
5. ⬜ 生产加固（checkpoint Postgres 持久化、HITL interrupt、审计 API 用 FastAPI、LangSmith、用户隔离）
6. ⬜ Superset 集成（可选，最后做）

## 现有代码结构

| 文件 | 职责 |
|---|---|
| demo_agent/analyst.py | 生产版单 Agent（6 工具 + 6 行为准则 ANALYST_PROMPT + 空轮护栏 `_retry_on_empty_turn`） |
| demo_agent/analyst_tools.py | 工具层：list_datasets / get_dataset_schema / get_data_profile / get_metric_definitions / query_data + create_chart（契约 v1 校验，返回规范化 JSON）+ sqlglot 护栏 |
| demo_agent/eval_retrieval.py | 评估集 CLI 入口（16 题；运行：cd demo_agent && ../.venv/bin/python eval_retrieval.py --runs 3 [--only id]） |
| demo_agent/eval_dataset.py | 评估题目加载与校验（run_sql 执行器 + load_cases，golden 加载时真实执行校验） |
| demo_agent/eval_scoring.py | 评估判分：值包含直通 → LLM judge → 答案兜底；judge 计数器（thread-local） |
| demo_agent/eval_runner.py | 评估运行层：Agent 构建（import 时）+ SQL 捕获（collect_runs）+ 尝试/聚合 |
| demo_agent/eval_state.py | 断点状态：hash 命名（题集+模式+judge 模型）+ 原子写（tmp+fsync+replace）+ 坏 JSON 容错 |
| demo_agent/eval_report.py | 评估报告打印（summary 表 / 失败明细） |
| demo_agent/generate_sample_data.py | 造样本数据（5000 行电商订单，含脏数据埋点：缺失/非法日期/负数量/重复行） |
| demo_agent/analyst_toolset.py | **用户手写练习版，不完整，不要动**（用户自己练手用） |
| data/ | 样本数据：ecommerce_data（15000 行）+ 京东/天猫/拼多多/淘宝/苏宁易购/阿里1688（各 1000 行，中文 12 列 schema） |
| data/datasets.yaml | 数据集元数据：列级口径 + metrics 段（指标定义 + 参考SQL） |
| src/research_deepagent/agent.py | langgraph.json 挂载的 research graph（AgentSeek 模板，与本项目主线独立） |
| langgraph.json | 双 graph：research + analyst（demo_agent/analyst.py:agent） |
| frontend/ | AgentSeek 模板调试 UI（工具卡/TodoList/plan 卡）——仅作模板调试，生产不用 |
| frontend-analyst/ | **生产前端**（独立工程，端口 5175）：论文风业务界面——工具过程全隐、Figure 编号图卡 + 左侧图表看板、数据详情可展开、仅浅色白纸风格（nature-figure 视觉规范）；npm test 45 用例 |
| fast1.py | 用户学 FastAPI 的练习文件，与主线无关 |

## 关键设计铁律

1. **护栏白名单**：只放行单条 SELECT——`sqlglot.parse()` + 过滤 None + `len==1` + `isinstance(stmt, exp.Select)`；表名必须等于数据集名（大小写不敏感但拒绝带引号——已讨论，用户暂缓修改，当前是精确比较）；自动注入 LIMIT 10000。执行的是**重生成的 SQL** 而非原始输入
2. **工具内外之分**：外部 = 注册进 tools 的函数（docstring 是 LLM 的说明书，必须写清参数）；内部 = `_` 前缀辅助函数（护栏必须藏在入口内部，LLM 不可见不可绕）
3. **口径权威**：算指标先 `get_metric_definitions`，公式以口径库为准，禁止 LLM 自创；库没有 → 反问用户
4. 工具返回紧凑（截断 20 行预览 + 总行数）；源数据只读；SQL 护栏管不到自定义工具，安全逻辑在工具内部
5. LangChain 工具函数必须有 docstring（否则 ValueError）；YAML 读取必须 yaml.safe_load
6. **模型轮次护栏**：`_retry_on_empty_turn`（wrap_model_call 中间件）——Qwen3-8B 经网关偶发"finish_reason=tool_calls 但 tool_calls 空/畸形"（长 JSON 工具调用被截断），agent 循环会静默结束。处理顺序：invalid_tool_calls 先 json-repair 本地修复（零 LLM 调用）→ 失败 nudge 重试一次 → 再失败原样返回
7. **图表按需**：用户没要求画图不调 create_chart（行为准则 5 + 工具 docstring 双写）；chart spec 契约 v1 校验在工具内部，成功返回规范化 JSON（丢未知键）

## 评估集现状与已知问题

- 16 题 = 13 sql 题（规则判分：golden 值集合 ⊆ Agent SQL 结果）+ 1 歧义题（clarify）+ 2 护栏负样本（其中 age_bucket 用淘宝数据集）
- 数值题结果（deepseek-v4-flash）：基础 11 题 13/13；难度题 4/5——dirty_date 掉过一次**非法日期陷阱**（字符串区间比较把 '2025-13-01' 算进 2025 年，正解 TRY_CAST）
- **行为题判分有天花板**：关键词规则无限打地鼠（"需要确认"→"需要跟您确认"→"最新月"→"理解为"…）。LLM-judge 已实现并接入（demo_agent/eval_judge.py：judge_execution/judge_behavior + JSON 解析兜底 + 关键词兜底）。**行为题分数仍建议看失败明细**
- 已知模型行为基线：护栏拒绝 100%；澄清反问 2/3 稳定（amb_poor，该题已移出当前题集，历史基线）；小空间歧义枚举口径是合格行为（amb_sales，用户已确认标准：歧义空间小→枚举/声明假设=过，空间大→必须反问）
- 判分器三个坑（已修，重犯即回归）：① 结果集完全等价误伤"多查几列但答案对"→ 改值集合包含；② numpy int64 不是 Python int 子类（_cell_key 必须覆盖 np.integer/np.floating）；③ 无 SQL 调用但答案对（数据画像推导）→ 回退答案数值判定
- 图表行为题（"用户没要求画图不得调 create_chart"）尚未进评估集：行为 judge 目前只看 final_text，要判"没调某工具"需给 judge 喂工具轨迹，待扩展后加入

## 环境与运行

- Python 3.12+，uv + .venv；**装包必须清华镜像**：`uv pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple <pkg>`（直连卡死）
- 运行脚本必须 `cd demo_agent` 后用**项目根目录的 .venv**：`../.venv/bin/python xxx.py`（venv 不在 demo_agent 里；不要用系统/conda 的 python——缺依赖；依赖清单在根 pyproject.toml）
- **langgraph dev 启动必须**：`UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple uv run --no-sync langgraph dev --port 2024 --no-browser`——`uv run` 会在 pyproject 变更时同步环境，默认走 PyPI 直连卡死；--no-sync 跳过（依赖用 uv pip 手动装）。**改 .env 后必须重启 langgraph dev**（进程环境是启动时快照，热重载不重读）
- 前端：生产界面 `cd frontend-analyst && npm run dev`（5175）；模板调试界面 `cd frontend && npm run dev`（5174）
- LLM 评估全量 = 16 题 × N 次调用、耗时几分钟 → 必须后台跑（Bash run_in_background）；**用户在意 token 消耗**，跑前说明调用次数
- 外部下载受限（GitHub/HuggingFace 慢）；BIRD dev 数据可用阿里云北京 OSS 直链（bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip）

## 用户工作风格

- 边学边做，持续追问"为什么"（如：工具为何内外之分、YAML 为什么、护栏为何单语句、要不要 Redis）——回答必须讲清原理和权衡
- 节奏偏好：先讨论需求 → 出设计 → 用户审题/审方案 → 再实施
- 对 AI 反馈持怀疑态度，要求实测验证（豆包说 parse_one 只解析首句的案例）——关键结论必须给本机实测证据
- 决策快但省成本；对"为用而用技术"反感（已明确拒绝：FastAPI 套 Agent 服务层、Redis、BIRD 裸 SQL 评测、DAB/BIRD-INTERACT 现阶段接入）

## 外部基准定位（已讨论定案）

- BIRD-SQL（单次生成 SQL）：测模型裸能力，对本系统无增量价值，**不接入**
- DAB（DataAgentBench）/ BIRD-INTERACT（agent 交互评测）：阶段 4/5 的进阶考，现在不接入；但**题目类型可蒸馏**进自建评估集（值对齐/歧义注入/非法日期已蒸馏 8 题）
- 自建评估集是唯一常设回归基准：改 prompt/工具/模型都跑它
