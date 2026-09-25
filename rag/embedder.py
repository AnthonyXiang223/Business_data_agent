"""chunk 嵌入（bge-m3）：把文本 chunk 变成 1024 维语义向量。
向量之间的距离 = 语义相似度
依赖 chunk.py，同目录运行
"""

import json
import sys
from pathlib import Path

import numpy as np
from langsmith import traceable

# agent/redis_cache.py 是项目根下的兄弟包（kb_search CLI 直接 import 本文件时
# 项目根不在 sys.path），垫一次再导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import redis_cache  # noqa: E402

MODEL_DIR = Path(__file__).parent / "models" / "bge-m3"

EMB_MODEL = "bge-m3"  # 模型名进缓存 key：换嵌入模型后旧向量缓存自然失效

# 模块级单例缓存：初始为None，第一次调用get_model()时才真正加载
_model = None

def get_model():
    """懒加载单例：第一次调用才加载模型，之后全局复用同一份"""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer   # 基于BERT，能输出整句的固定长度向量
        _model = SentenceTransformer(str(MODEL_DIR))    # 在内存中实例化一个embedding模型
    return _model


def embed_texts(texts: list[str]) -> np.ndarray:
    """把一批文本编码成向量矩阵，返回 shape=(n, 1024) 的 float32 数组。
    归一化：每个向量除以其模长，变成单位向量（模长=1）。
    归一化后，两个向量的点积就是余弦相似度（范围 -1~1，越接近 1 越相似）。
    入库后 pgvector 用内积算子（<#>）算相似度即可，比余弦更快。
    """
    if not texts:
        # 要返回形状正确的空矩阵，否则模型 encode 空列表会报错
        return np.zeros((0, 1024), dtype=np.float32)
    return get_model().encode(
        texts,
        normalize_embeddings=True,   # 归一化成单位向量
        show_progress_bar=False,
    )


@traceable(run_type="embedding", name="embed_query",
           process_outputs=lambda v: {"vector_dims": int(v.shape[0])})
def embed_query(text: str) -> np.ndarray:
    """单条查询文本编码（向量检索时的问题侧）
    查询侧与chunk侧要用同一个模型同样归一化确保在同一个语义空间中
    trace：输入=query 文本，输出只记维度（1024 维向量本身不进 trace，防膨胀）
    Redis 嵌入缓存：同一问句直接返回缓存的向量，跳过模型推理。
    与 search_knowledge 的结果缓存互补——结果缓存覆盖生产路径，
    本缓存覆盖绕开结果缓存的路径（kb_search CLI 调参调试、评估 _search_raw）
    """
    key = f"emb:{EMB_MODEL}:{redis_cache.digest(text)}"
    cached = redis_cache.get(key)
    if cached is not None:
        return np.array(json.loads(cached), dtype=np.float32)
    vec = embed_texts([text])[0]
    redis_cache.set(key, json.dumps([float(v) for v in vec.tolist()]), redis_cache.EMB_TTL)
    return vec


def top_k_similar(query_vec: np.ndarray, vectors: np.ndarray, k: int) -> list[int]:
    """在候选向量矩阵中找与查询向量最相似的k个，返回下标列表"""
    scores = vectors @ query_vec   # 内积，等价于余弦相似度
    return list(np.argsort(-scores)[:k])   # 原分数降序排序，取前k个下标


if __name__ == "__main__":
    # 把chunk.py的产出嵌入，先不接数据库，先验证语义检索
    from chunk import parse_docx
    here = Path(__file__).parent
    chunks = []
    for path in sorted(here.glob("*.docx")):
        chunks.extend(parse_docx(path))
    print(f"共解析 {len(chunks)} 个 chunk")

    # 批量嵌入：一次encode调用处理全部chunk
    vectors = embed_texts([c.content for c in chunks])
    print(f"向量矩阵： {vectors.shape}, dtype={vectors.dtype}")

    q = embed_query("平均一张订单花多少钱，这个指标叫什么")
    print("\n查询: 平均一张订单花多少钱，这个指标叫什么")
    for idx in top_k_similar(q, vectors, k=3):
        c = chunks[idx]
        score = float(q @ vectors[idx])     # 归一化后点积 = 余弦相似度
        loc = " / ".join(c.section_path) or "（无章节）"
        print(f"\n[{score:.3f}] {c.doc_title} | {loc}\n  {c.content[:120]}...")