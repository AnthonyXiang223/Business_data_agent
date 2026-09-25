"""知识库检索：多路召回（向量 + BM25）-> RRF融合 -> rerank 精排
向量路（语义）：能抓到零字符重叠的同义改写
BM25(字面)：按词频/逆文档频率统计打分
RRF融合：比排名，平滑常数防止单路第一垄断所有高分
rerank精排：cross-encoder把（问题，候选）拼成一堆输入模型精算相关性，只对粗筛后的少数候选用"""

import os
from pathlib import Path

import psycopg
import numpy as np
from dotenv import load_dotenv
from jieba import lcut  # 中文分词：BM25需要词边界
from pgvector.psycopg import register_vector
from rank_bm25 import BM25Okapi

from embedder import embed_query

load_dotenv()

POSTGRES_URL = os.environ["POSTGRES_URL"]
RERANK_MODEL_DIR = Path(__file__).parent / "models" / "bge-reranker-v2-m3"
RRF_K = 60  # 平滑常数

_reranker = None


def get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(str(RERANK_MODEL_DIR))
    return _reranker


# 路1：向量检索（SQL内完成）
def vector_search(conn, query_vec, top_k=20):
    """余弦距离升序取top_k,返回[{chunk_id, 相似度}]
    用余弦距离算子计算
    """
    rows = conn.execute(
        """SELECT chunk_id, 1 - (embedding <=> %s) score
        FROM kb_chunks
        ORDER BY embedding <=> %s
        LIMIT %s""", 
        (query_vec, query_vec, top_k)
    ).fetchall()
    return [(r[0], float(r[1])) for r in rows]

# 路2：BM25关键字检索
def bm25_search(conn, query, top_k=20):
    """BM25打分，返回[{chunk_id, score}]
    本质是几行词频统计，放应用内存无压力，无需放在数据库
    """
    rows = conn.execute("SELECT chunk_id, content FROM kb_chunks").fetchall()
    corpus = [lcut(r[1]) for r in rows]  # jieba分词:每个chunk变成一个此列表
    bm25 = BM25Okapi(corpus)  # 构建：统计全库词频 + 倒排索引(词语-文档)
    scores = bm25.get_scores(lcut(query))  # 打分：查询分词后对所有chunk同时计算
    order = np.argsort(-np.array(scores))[:top_k]  # 降序取前top_k
    return [(rows[i][0], float(scores[i])) for i in order]  

# RRF融合
def rrf_fuse(ranked_lists, k=10):
    """RRF（倒数排名融合）：每个候选的融合分 = Σ 1/(RRF_K + 该路排名)
    输入是多条"已按名次排序的 (chunk_id, 分数) 列表"，输出 [(chunk_id, 融合分)]
    多路共识加分
    """
    fused: dict[int, float] = {}
    for lst in ranked_lists:
        for rank, (cid, _score) in enumerate(lst):
            fused[cid] = fused.get(cid, 0) + 1 / (RRF_K + rank + 1)
    return sorted(fused.items(), key=lambda x: -x[1])[:k]  


# rerank精排
def rerank(query, candidates, top_n=3):
    """cross-encoder精排，返回[((chunk_id, 内容), 相关性分)]
    candidates 是RRF粗筛后的少数候选
    predict 一次处理全部对，输出每对的相关性分(越高越相关)
    """
    scores = get_reranker().predict([(query, c[1]) for c in candidates])
    order = np.argsort(-np.array(scores))[:top_n]
    return [(candidates[i], float(scores[i])) for i in order]


if __name__ == "__main__":
    with psycopg.connect(POSTGRES_URL) as conn:
        register_vector(conn)
        # 全库映射：chunk_id → (content, section_path, doc_id)，打印溯源用
        id2row = {
            r[0]: r for r in conn.execute(
                "SELECT chunk_id, content, section_path, doc_id FROM kb_chunks")
        }
        query = "每个买家平均花了多少钱，计算公式是什么"   
        qv = embed_query(query)

        v_res = vector_search(conn, qv, top_k=20)     # 路 A
        b_res = bm25_search(conn, query, top_k=20)    # 路 B
        fused = rrf_fuse([v_res, b_res], k=10)

        print(f"查询: {query}")
        print(f"\n向量路 top3: {[f'#{cid}({s:.3f})' for cid, s in v_res[:3]]}")
        print(f"BM25路 top3: {[f'#{cid}({s:.1f})' for cid, s in b_res[:3]]}")
        print(f"\nRRF 融合 top-{len(fused)}:")
        for cid, s in fused:
            loc = " / ".join(id2row[cid][2]) or "（无章节）"
            print(f"  [{s:.4f}] {id2row[cid][3]} | {loc}")

        if RERANK_MODEL_DIR.exists():
            print("\nrerank 精排 top-3:")
            for (cid, _content), s in rerank(query, [(cid, id2row[cid][1]) for cid, _ in fused]):
                loc = " / ".join(id2row[cid][2]) or "（无章节）"
                print(f"  [{s:.3f}] {id2row[cid][3]} | {loc}")
                print(f"    {id2row[cid][1][:100]}...")
        else:
            print("\n(rerank 模型尚未下载)")