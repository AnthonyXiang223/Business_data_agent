"""服务层请求/响应模型。"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    thread_id: str | None = None  # 缺省 = 新建会话（服务端生成 thread_id 并回传）
