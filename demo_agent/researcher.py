import os
from typing import Literal
from tavily import TavilyClient
from langchain_openai import ChatOpenAI
from deepagents import create_deep_agent, FilesystemPermission
from dotenv import load_dotenv
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langgraph.store.memory import InMemoryStore

load_dotenv()

model = ChatOpenAI(
    model_name=os.environ.get("OPENAI_MODEL_NAME", "deepseek-v4-flash"),
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ["OPENAI_API_BASE"],
)

researcher_prompt = """你是一位专业的研究员。
你的工作是进行深入研究，然后撰写一份完整的研究报告。

你可以使用 internet_search 工具搜索互联网获取信息。
"""

tavily_client = TavilyClient(api_key=os.environ.get("TAVILY_API_KEY"))

# 定义搜索工具
def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "images"] = "general",
    include_raw_content: bool = False,
):
    """Run a web search for the given query and return the results.
    Args:
        query (str): The search query.
        max_results (int, optional): The maximum number of results to return.
        topic : The topic of the search. 
        include_raw_content (bool, optional): Whether to include the raw page content. 
    """
    return tavily_client.search(
        query=query,
        max_results=max_results,
        topic=topic,
        include_raw_content=include_raw_content,
    )

agent = create_deep_agent(
    model=model,
    tools=[internet_search],
    system_prompt=researcher_prompt,
    backend=CompositeBackend(
        default=StateBackend(),     # 默认：临时存储
        routes=[
            ("/user/*", StoreBackend(
                namespace=lambda rt: (
                    rt.server_info.user.identity if (rt.server_info and rt.server_info.user) else "fallback-user",   # namespace格式要求元组
                )
            )),
            # 助手全局共享知识库
            ("/knowledge/*", StoreBackend(
                namespace=lambda rt: (
                    rt.server_info.assistant_id if rt.server_info else "business‑analysis‑agent",   
            )
            ))  
        ]
    ),
    store=InMemoryStore(),
    permissions = [
    # 禁止Agent修改全局知识库，只能读取
    FilesystemPermission(
        operations=["write","edit"],
        paths=["/knowledge/**"],
        mode="deny"
    )
]
)


result =  agent.invoke(
    {"messages": [{"role": "user", "content": "什么是langgraph？"}]}
)

print(result["messages"][-1].content)