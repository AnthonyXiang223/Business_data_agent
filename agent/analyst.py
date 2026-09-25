"""业务数据分析 Agent（deepagents 单 Agent 版，阶段 1+2：取数 + 图表）。

运行（在 demo_agent 目录）:
    ../.venv/bin/python analyst.py "你的分析问题"
不带参数时会交互式提示输入问题（也可 echo "问题" | analyst.py 管道传入）。

同时由 langgraph.json 以 graph id "analyst" 挂载给 langgraph dev（前端连 127.0.0.1:2024）。
后续阶段路线见 CLAUDE.md。
"""

import os
import sys
from pathlib import Path
import atexit
import uuid

# langgraph dev 对 path 形式 graph 用 spec_from_file_location 加载，不会把本目录加进
# sys.path，裸 `from analyst_tools import ...` 会 ImportError；垫片同时兼容 CLI 运行
sys.path.insert(0, str(Path(__file__).resolve().parent))
# rag/ 工具目录（rag_tools 与 embedder 同目录，模型从 rag/models 加载）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rag"))

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.summarization import SummarizationMiddleware
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row


from analyst_tools import (
    create_chart,
    get_data_profile,
    get_dataset_schema,
    get_metric_definitions,
    list_datasets,
    query_data,
)
from stats_tools import (
    analyze_trend,
    compare_distribution,
    correlation_analysis,
    drill_down,
)
from rag_tools import search_knowledge

load_dotenv()

backend = FilesystemBackend(root_dir=str(Path(__file__).resolve().parent / "workspace"))

model = ChatOpenAI(
    model_name=os.environ.get("OPENAI_MODEL_NAME", "Qwen/Qwen3-8B"),
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ["OPENAI_API_BASE"],
)

ANALYST_PROMPT = """你是一位严谨的业务数据分析师。

可用工具：
- list_datasets: 查看有哪些数据集
- get_dataset_schema: 了解列结构与业务含义
- get_data_profile: 数据质量画像（分析任何问题前必须先调用）
- query_data: 用 SQL 查询数据（表名 = 数据集名，duckdb 方言；分桶写法见工具说明）
- analyze_trend: 时序趋势（按日/周/月聚合指标，可选环比/同比）
- drill_down: 多维下钻（1-2 个维度分组聚合 + 结构化过滤 + Top N）
- compare_distribution: 分布对比（分组统计量：四分位/均值/标准差并排比较）
- correlation_analysis: 相关性分析（数值列两两 Pearson 相关矩阵）
- get_metric_definitions: 查询指标口径（销售额/GMV、客单价等如何计算的权威标准）
- search_knowledge: 检索业务知识库（指标口径文档/字段说明文档）的权威原文片段
- create_chart: 生成标准图表规格 JSON 供前端渲染（bar/line/pie/table）

趋势/下钻/分布/相关性分析优先用专用统计工具（参数结构化、防注入），
只有统计工具覆盖不了的复杂自定义查询才用 query_data 写 SQL。

行为准则：
1. 歧义优先：问题有歧义（指标口径/统计范围/评价标准/分桶区间）先反问澄清，
   禁止替用户假设直接给结论；问题明确时直接执行，不要过度反问
2. 先探后问：先 get_data_profile 了解数据质量（缺失/重复/非法日期要报告），
   再按"探查 → 假设 → 验证 → 修正结论"推进分析
3. 口径权威：算指标先查 get_metric_definitions，公式以口径库为准，禁止自创；
   把用户原话作为 keyword 传入，工具负责匹配；计数/分桶/过滤类问题不查
4. 引用数据：结论必须引用工具返回的具体数值，面向业务人员给结论，不罗列原始数字
5. 图表规范：仅在用户明确要求画图/图表时才调用 create_chart，用户没要求时只输出文字分析；
   需要画图时，先调用 create_chart 生成图表，再输出文字总结；
   禁止在回答中输出图片链接、图表代码（DSL/mermaid/ASCII 图）——图表只能通过 create_chart 生成；
   规格严格符合契约 v1；校验失败按工具返回的错误信息修正后重试；
   数据未确认（口径/数值未核实）前不要生成图表
6. 知识库佐证：需要口径定义/计算逻辑/字段含义等权威原文时调用 search_knowledge；
   先把用户口语经 get_metric_definitions 归一成标准指标名再检索（不要直接传用户原话）；
   检索片段只作口径依据，数值必须用 query_data 现查；
   文档与口径库（get_metric_definitions）不一致时提示冲突并向用户确认；
   引用文档原文时在结论中标注来源（文档名 + 章节）

复杂分析时，把中间结论写入 /workspace 笔记文件，防止上下文丢失。
"""

# ---------- 畸形工具调用修复 + 空轮重试护栏 ----------
# 实现已抽到 model_guard.py：生产（本文件）与评估（eval_runner 的 single
# agent）共用同一套护栏；eval import 本文件会连带拉起 PG/agent 构建副作用，
# import model_guard 则零副作用（铁律 2：护栏藏在内部，LLM 不可见不可绕）
from model_guard import _EmptyTurnRetryMiddleware
from stop_guard import CancellationMiddleware


def _list_threads():
    """列出所有历史会话：thread_id + 首问标题。"""
    with checkpointer.conn.connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (thread_id) thread_id, metadata->>'title' AS title "
            "FROM checkpoints ORDER BY thread_id, checkpoint_id ASC"
        ).fetchall()
    return [(r["thread_id"], r["title"] or "(无标题)") for r in rows]

        

# langgraph dev（LangGraph 平台）自带持久化，带自定义 checkpointer 的 graph
# 会被拒绝加载（GraphLoadError）；CLI 与 server/ 服务层需要自己的 PG
# checkpointer（历史会话）。平台上下文由 langgraph dev 注入
# LANGSMITH_LANGGRAPH_API_VARIANT=local_dev 区分
if os.environ.get("LANGSMITH_LANGGRAPH_API_VARIANT") == "local_dev":
    checkpointer = None  # 平台持久化（local_dev = 内存，重启即失）
else:
    checkpointer = PostgresSaver(
        ConnectionPool(
            conninfo=os.environ["POSTGRES_URL"],
            open=True,
            min_size=1,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
    )
    # 全新库上建 checkpoints 表（幂等）。主机库历史上早已建过所以此前
    # 没暴露；容器化的全新 PG 上缺失会报 relation "checkpoints" does not exist
    checkpointer.setup()
    # 池有后台连接线程，退出前显式关闭
    atexit.register(checkpointer.conn.close)

agent = create_deep_agent(
    model=model,
    middleware=[_EmptyTurnRetryMiddleware(),
        SummarizationMiddleware(model=model,backend=backend,trigger=("tokens", 32_000),keep=("messages", 10),),
        # 放最后 = 最内层（langchain 列表首位是最外层）：model_guard 的 nudge
        # 重试也会经过取消检查。停止标志由 server/ 的 /stop 端点置位
        CancellationMiddleware(),
        ],
    tools=[
        list_datasets,
        get_dataset_schema,
        get_data_profile,
        get_metric_definitions,
        query_data,
        analyze_trend,
        drill_down,
        compare_distribution,
        correlation_analysis,
        create_chart,
        search_knowledge,
    ],
    system_prompt=ANALYST_PROMPT,
    checkpointer=checkpointer,

)

if __name__ == "__main__":
    thread_id = str(uuid.uuid4())   # 每次启动 = 新会话；历史留在 Postgres
    print("连续输入问题即可追问，空行退出。")
    print("会话命令：:new 新建会话 | :list 列出历史 | :goto <id前缀> 切换会话")
    while True:
        raw = input("你: ").strip()
        if not raw:
            break
        if raw == ":new":
            thread_id = str(uuid.uuid4())
            print(f"已新建会话: {thread_id[:8]}")
            continue
        if raw == ":list":
            for tid, title in _list_threads():
                mark = " ← 当前" if tid == thread_id else ""
                print(f"  {tid[:8]} | {title}{mark}")
            continue
        if raw.startswith(":goto "):
            target = raw.split(None, 1)[1].strip()
            match = next((tid for tid, _ in _list_threads() if tid.startswith(target)), None)
            if not match:
                print(f"未找到会话 {target}（:list 查看全部）")
                continue
            thread_id = match
            print(f"已切换到会话: {thread_id[:8]}")
            continue
        config = {
            "configurable": {"thread_id": thread_id},
            "metadata": {"title": raw[:30]},   # 首问进 metadata，:list 用作标题
            "run_name": raw[:30],              # LangSmith trace 名，UI 列表直接可读
        }
        result = agent.invoke({"messages": [{"role": "user", "content": raw}]}, config=config)
        print("分析员:", result["messages"][-1].content)
