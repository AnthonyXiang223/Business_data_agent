"""阶段 1：业务数据分析 Agent（deepagents 单 Agent 版）。

运行: python demo_agent/analyst.py "你的分析问题"
（不带参数则运行默认的整体分析问题）

后续阶段：
- 阶段 2: 增加 create_chart 工具 + 前端 ChartCard 渲染
- 阶段 3: query_data 换成 Doris（护栏代码复用）
- 阶段 4: 拆分为主管 + SQL/统计/洞察/图表子智能体
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
    model_name=os.environ.get("OPENAI_MODEL_NAME", "deepseek-v4-flash"),
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ["OPENAI_API_BASE"],
)

ANALYST_PROMPT = """你是一位严谨的业务数据分析师。

工作流程：理解数据 → 检查质量 → 探索分析 → 给出有数据支撑的业务结论。

可用工具：
- list_datasets: 查看有哪些数据集
- get_dataset_schema: 了解列结构与业务含义
- get_data_profile: 数据质量画像（分析任何问题前必须先调用）
- query_data: 用 SQL 查询数据（表名 = 数据集名，duckdb 方言，聚合查询比逐行查看高效）
- get_metric_definitions: 查询指标口径（销售额/退货率等如何计算的权威标准）

行为准则：
1. 先探后问：回答任何分析问题前，必须先 get_data_profile 了解数据，再决定怎么分析
2. 引用数据：每个结论都要引用工具返回的具体数值，禁止凭空编造数字
3. 质量优先：发现缺失值、重复、非法日期、负值等数据问题时，先向用户报告，
   说明影响和建议处理方式，不要擅自修改或忽略
4. 迭代分析：先探查 → 提出假设 → 用 query_data 验证 → 修正结论
5. 口径权威：计算业务指标前必须先 get_metric_definitions 查口径库，
   公式以返回的定义和参考 SQL 为准，禁止自行发明口径；
   口径库中没有的指标，反问用户确认后再算
6. 业务语言：输出面向业务人员的结论（趋势、对比、原因），
   而不是罗列原始统计数字；适当时给出下一步分析建议

复杂分析时，把中间结论写入 /workspace 笔记文件，防止上下文丢失。
"""

agent = create_deep_agent(
    model=model,
    tools=[list_datasets, get_dataset_schema, get_data_profile, get_metric_definitions, query_data],
    system_prompt=ANALYST_PROMPT,
)

if __name__ == "__main__":
    question = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "分析一下 ecommerce_sales 这份数据的整体情况，"
             "包括数据质量问题和基本业务概况"
    )
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    print(result["messages"][-1].content)
