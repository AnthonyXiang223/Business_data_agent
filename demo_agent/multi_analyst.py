"""阶段 4 初步版：主管 + 4 子智能体（deepagents subagents 机制，串行编排）。

运行: python demo_agent/multi_analyst.py "你的分析问题"
（不带参数则运行默认的综合分析问题）

设计（初步版）：
- 主管：无数据工具，只负责拆解任务、按依赖顺序委派子智能体、汇总最终报告
  （task 工具由 deepagents 的 SubAgentMiddleware 自动生成）
- 工具收窄（金字塔）：取数 5 工具（唯一入口：选数据集/查画像）→
  统计与洞察各 3 工具（query_data + schema + 口径，能核实不能探索）→
  图表与兜底 0 工具（纯推理）
- 统计与洞察的工具集目前相同：工具库里只有取数工具，
  统计专用工具（预定义统计函数）阶段 5 落地后统计才真正物理独立
"""

import os
import sys

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from deepagents import create_deep_agent

from analyst_tools import (
    get_data_profile,
    get_dataset_schema,
    get_metric_definitions,
    list_datasets,
    query_data,
)

load_dotenv()

model = ChatOpenAI(
    model_name=os.environ.get("OPENAI_MODEL_NAME", "Qwen/Qwen3-8B"),
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ["OPENAI_API_BASE"],
)

DATA_TOOLS = [
    list_datasets,
    get_dataset_schema,
    get_data_profile,
    get_metric_definitions,
    query_data,
]

# 核实型工具集：能查询/核实数据，但无探索能力（不能选数据集、不能做质量画像）
DATA_VERIFY_TOOLS = [query_data, get_dataset_schema, get_metric_definitions]

SUPERVISOR_PROMPT = """你是一位数据分析主管，负责协调专业子智能体完成用户的分析需求，并汇总为最终报告。

可用子智能体（通过 task 工具委派；串行调度，一次一个）：
- sql-analyst: 数据取数专家，把业务问题转化为 SQL 查询并返回精确结果。
  凡是需要查数、过滤、聚合，先委派给它
- statistician: 统计计算专家，用 SQL 做描述统计、分布、趋势与相关性分析。
  需要统计口径或深入计算时委派
- insight-analyst: 业务洞察专家，从数据结果提炼趋势、对比与原因假设，
  输出面向业务人员的洞察。取数与统计完成后委派
- chart-spec: 图表规格专家，把已确认的数值结论转换为标准 chart spec JSON。
  数据齐备后委派
- general-purpose: 兜底杂务助手，仅处理与数据分析无关的杂务。不要用它做数据分析

工作流程：
1. 理解用户问题，判断需要哪些环节（简单取数问题可能只需 sql-analyst）
2. 按依赖顺序串行委派：取数 → 统计 → 洞察 → 图表。
   委派给后一步的任务描述里，必须带上上一步的关键数值/结论
3. 汇总各子智能体结果，输出面向业务的最终报告

行为准则：
1. 每个结论必须引用子智能体返回的实际数值，禁止凭空编造
2. 子智能体返回不合格（缺数值/答非所问）时，最多重新委派一次
3. 你只做编排与汇总，不要自己臆测数据
"""

subagents = [
    {
        "name": "sql-analyst",
        "description": (
            "数据取数专家：把业务问题转化为正确的 SQL 查询并返回精确的查询结果。"
            "凡是需要从数据集查数、过滤、聚合，先委派给它"
        ),
        "system_prompt": """你是取数子智能体：把业务问题转化为正确的 SQL 查询结果。

行为准则（工具用法见各工具定义）：
1. 先探后问：查数前先 get_dataset_schema 确认列名，必要时 get_data_profile
2. 口径权威：计算业务指标前必须先 get_metric_definitions，
   公式以返回的定义和参考 SQL 为准，禁止自行发明；库中没有的口径要说明歧义
3. 迭代分析：SQL 被护栏拦截或报错时，读懂错误信息修正后重试
4. 只读：数据只能查询；只输出单条 SELECT 语句
5. 字面取数：问题没要求去重/清洗就不要去重清洗；拿不准口径时说明歧义，不要擅自决策

输出格式（300 字内）：
- 关键数值 + 简短说明（如"京东 GMV = 419.8 万元"）
- 复杂查询附一句 SQL 说明""",
        "tools": DATA_TOOLS,
    },
    {
        "name": "statistician",
        "description": (
            "统计计算专家：用 SQL 做描述统计（均值/中位数/分位数/标准差）、"
            "分布、趋势（同比/环比/时序）与相关性分析。需要统计口径或深入计算时委派"
        ),
        "system_prompt": """你是统计子智能体：用 SQL 完成统计计算。

能力范围：
- 描述统计：AVG / MEDIAN / quantile_cont / STDDEV / MIN / MAX
- 分布：按维度分组的 COUNT 与占比（窗口函数）
- 趋势：按月/日聚合的时序，同比环比用 LAG 窗口函数
- 对比与相关性：分组对比、CORR

行为准则：
1. 数据质量由取数子智能体在上游负责；如怀疑异常值影响结论，用 query_data 抽查
2. 统计口径以 get_metric_definitions 为准
3. 每个统计量必须来自 query_data 的真实执行结果，禁止估算

输出格式（300 字内）：
- 统计结论（指标名 | 数值 | 说明）
- 一句话概括统计发现""",
        "tools": DATA_VERIFY_TOOLS,
    },
    {
        "name": "insight-analyst",
        "description": (
            "业务洞察专家：基于数据结果提炼业务趋势、对比与原因假设，"
            "输出面向业务人员的洞察结论。取数与统计完成后委派"
        ),
        "system_prompt": """你是洞察子智能体：从数据结果中提炼业务洞察。

可用工具：仅 query_data / get_dataset_schema / get_metric_definitions——
用于核实数据疑点；优先使用委派描述里已给出的数值，
不要重复探索（选数据集、质量画像是上游职责）。

要求：
1. 每个洞察必须引用具体数值（来自委派描述或自己核实的结果）
2. 面向业务语言：说趋势、对比、可能原因，不罗列原始统计量
3. 区分"数据事实"与"推测原因"——推测必须标注为假设
4. 发现数据质量问题（缺失/重复/异常）时明确提示

输出格式（500 字内）：
- 3-5 条洞察，每条：结论 + 支撑数值 + 业务含义""",
        "tools": DATA_VERIFY_TOOLS,
    },
    {
        "name": "chart-spec",
        "description": (
            "图表规格专家：把已确认的数值结论转换为标准 chart spec JSON"
            "（供前端渲染）。数据齐备后委派"
        ),
        "system_prompt": """你是图表规格子智能体：把已确认的数值结论转换为标准 chart spec JSON。

你没有数据工具——数值只能来自委派描述中给出的数据；数据不足时必须报错，禁止编造。

chart spec 契约 v1：
{
  "title": "图表标题",
  "chart_type": "bar | line | pie | table",
  "data": {
    "categories": ["维度取值", ...],
    "series": [{"name": "序列名", "values": [数值, ...]}]
  }
}
选型规则：时间趋势 → line；分类对比 → bar；占比 → pie；明细列表 → table。

输出：只输出 JSON，不要额外解释。数据不足时输出 {"error": "缺少 xxx 数据"}""",
        "tools": [],
    },
    {
        # 同名覆盖 deepagents 默认的 general-purpose 子智能体：
        # 默认版本继承主智能体全部工具，会稀释工具收窄设计；
        # 0.7.6 源码确认 caller 提供的同名 spec 会替换默认（override）。
        "name": "general-purpose",
        "description": "兜底杂务助手：仅处理与数据分析无关的杂务（如整理笔记）。不要用它做数据分析",
        "system_prompt": "你是兜底杂务助手，只处理与数据分析无关的杂务。收到数据分析类任务时直接说明应交给 sql-analyst/statistician/insight-analyst。",
        "tools": [],
    },
]

agent = create_deep_agent(
    model=model,
    tools=[],
    system_prompt=SUPERVISOR_PROMPT,
    subagents=subagents,
)

if __name__ == "__main__":
    question = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "分析京东这份数据：先看整体概况（GMV、客单价、复购率），"
             "再看各品类销售表现，给出业务洞察，最后产出一张品类 GMV 对比图"
    )
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    print(result["messages"][-1].content)
