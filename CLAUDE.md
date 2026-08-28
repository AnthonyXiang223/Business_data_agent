# 业务数据分析多智能体系统（research_deepagent）

## 项目定位

用 deepagents 构建生产级业务数据分析师系统。目标效果：**给一份数据 → 完成数据分析 → 可视化图表**。
用户学习路线：Agent 工程师（简历含金量驱动）。核心工程哲学：**自建校验/编排逻辑，用现成基础设施**（不造轮子、不自建沙箱/向量库/SQL 解析器）。

## 已确认的架构决策（用户拍板，不要推翻）

- 只用 deepagents 框架开发
- 查询方式：duckdb + SQL（`query_data` 工具）+ sqlglot 白名单护栏；阶段 3 换 Apache Doris 时护栏与 Agent SQL 技能零成本迁移（只换 pymysql 连接）
- 模型：DeepSeek（OpenAI 兼容网关，.env 的 OPENAI_MODEL_NAME/API_KEY/API_BASE）
- 数仓：Doris 自建 SQL 执行器（只读账号 + 审计表）；语义层：YAML 起步 → 阶段 5 升级结构化口径库 + Milvus 只做模糊检索；Superset 仅作展示终端（Agent 推结果，查询仍走 Agent-Doris 链路）
- 统计能力：预定义工具封装，不执行任意 Python 代码
- 可视化：Agent 产出标准 chart spec → 前端渲染（不依赖 Superset）

## 阶段路线图

1. ✅ 单 Agent + 文件数据（demo_agent/）
2. ⬜ 前端图表渲染（create_chart 工具 + ChartCard）← 当前可推进
3. ⬜ Doris + 护栏迁移（sqlglot 方言换 mysql，工具逻辑不动）
4. ⬜ 拆分多智能体（主管 + SQL/统计/洞察/图表子智能体）
5. ⬜ 生产加固（checkpoint Postgres 持久化、HITL interrupt、审计 API 用 FastAPI、LangSmith、用户隔离）
6. ⬜ Superset 集成（可选，最后做）

## 现有代码结构

| 文件 | 职责 |
|---|---|
| demo_agent/analyst.py | 生产版单 Agent（5 工具 + 6 行为准则 ANALYST_PROMPT） |
| demo_agent/analyst_tools.py | 工具层：list_datasets / get_dataset_schema / get_data_profile / get_metric_definitions / query_data + sqlglot 护栏 |
| demo_agent/eval_retrieval.py | 取数评估集 21 题（运行：cd demo_agent && .venv/bin/python eval_retrieval.py --runs 3 [--only id]） |
| demo_agent/generate_sample_data.py | 造样本数据（5000 行电商订单，含脏数据埋点：缺失/非法日期/负数量/重复行） |
| demo_agent/analyst_toolset.py | **用户手写练习版，不完整，不要动**（用户自己练手用） |
| data/ecommerce_sales.csv | 样本数据（列：order_id/order_date/region/category/product/channel/customer_age_group/quantity/unit_price/amount/is_returned） |
| data/datasets.yaml | 数据集元数据：列级口径 + metrics 段（5 个指标：销售额_GMV/净销售额/退货率/客单价/销售件数，每个含定义 + 参考SQL） |
| src/research_deepagent/agent.py | langgraph.json 挂载的 research graph（AgentSeek 模板，与本项目主线独立） |
| frontend/ | React + @langchain/react useStream，连 langgraph dev（127.0.0.1:2024），已有 ToolCallCard/ThinkingBlock |
| fast1.py | 用户学 FastAPI 的练习文件，与主线无关 |

## 关键设计铁律

1. **护栏白名单**：只放行单条 SELECT——`sqlglot.parse()` + 过滤 None + `len==1` + `isinstance(stmt, exp.Select)`；表名必须等于数据集名（大小写不敏感但拒绝带引号——已讨论，用户暂缓修改，当前是精确比较）；自动注入 LIMIT 10000。执行的是**重生成的 SQL** 而非原始输入
2. **工具内外之分**：外部 = 注册进 tools 的函数（docstring 是 LLM 的说明书，必须写清参数）；内部 = `_` 前缀辅助函数（护栏必须藏在入口内部，LLM 不可见不可绕）
3. **口径权威**：算指标先 `get_metric_definitions`，公式以口径库为准，禁止 LLM 自创；库没有 → 反问用户
4. 工具返回紧凑（截断 20 行预览 + 总行数）；源数据只读；SQL 护栏管不到自定义工具，安全逻辑在工具内部
5. LangChain 工具函数必须有 docstring（否则 ValueError）；YAML 读取必须 yaml.safe_load

## 评估集现状与已知问题

- 21 题 = 16 数值题（规则判分：golden 值集合 ⊆ Agent SQL 结果）+ 3 歧义题 + 2 护栏负样本
- 数值题结果（deepseek-v4-flash）：基础 11 题 13/13；难度题 4/5——dirty_date 掉过一次**非法日期陷阱**（字符串区间比较把 '2025-13-01' 算进 2025 年，正解 TRY_CAST）
- **行为题判分有天花板**：关键词规则无限打地鼠（"需要确认"→"需要跟您确认"→"最新月"→"理解为"…）。已和用户讨论，正解是 LLM-judge（约 30 行）。用户尚未拍板三选项：LLM-judge / 人工复核 / 冻结先推进阶段 2。**行为题当前分数不可全信，看失败明细**
- 已知模型行为基线：护栏拒绝 100%；澄清反问 2/3 稳定（amb_poor）；小空间歧义枚举口径是合格行为（amb_sales，用户已确认标准：歧义空间小→枚举/声明假设=过，空间大→必须反问）
- 判分器三个坑（已修，重犯即回归）：① 结果集完全等价误伤"多查几列但答案对"→ 改值集合包含；② numpy int64 不是 Python int 子类（_cell_key 必须覆盖 np.integer/np.floating）；③ 无 SQL 调用但答案对（数据画像推导）→ 回退答案数值判定

## 环境与运行

- Python 3.12+，uv + .venv；**装包必须清华镜像**：`uv pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple <pkg>`（直连卡死）
- 运行脚本必须 `cd demo_agent`（模块间相对导入）
- LLM 评估全量 = 21 题 × N 次调用、耗时几分钟 → 必须后台跑（Bash run_in_background）；**用户在意 token 消耗**，跑前说明调用次数
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
