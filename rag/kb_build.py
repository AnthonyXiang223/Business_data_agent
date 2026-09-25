"""知识库入库：chunk + 向量 -> pgvector"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

from chunk import parse_docx
from embedder import embed_texts

load_dotenv()  

POSTGRES_URL = os.environ["POSTGRES_URL"]

# 建表语句列表
DDL = [
    "CREATE EXTENSION IF NOT EXISTS vector;",
    # 文档元数据表：与chunk分离，删除重建文档只改kb_chunks
    # kb_documents记录每个文档的入库历史
    """
    CREATE TABLE IF NOT EXISTS kb_documents (
        doc_id TEXT PRIMARY KEY,    --文件名作为自然主键
        doc_title TEXT NOT NULL,    --文档大标题
        chunk_count INT NOT NULL,   --本次入库的chunk数
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # chunk表：知识库最小单元 + 向量
    """
    CREATE TABLE IF NOT EXISTS kb_chunks (
        chunk_id BIGSERIAL PRIMARY KEY,
        doc_id TEXT NOT NULL REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
        chunk_index INT NOT NULL,   --文档内序号（保持原文顺序）
        section_path TEXT[] NOT NULL DEFAULT '{}',  --章节路径数组，命中后溯源展示用
        content TEXT NOT NULL,      --chunk文本内容
        chunk_type TEXT NOT NULL,        --text或table
        embedding vector(1024)  --bge-m3输出1024维
    )
    """,
    # HNSW 近似最邻索引：近似最近邻 ANN
    # 图索引
    # """
    # CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx
    # ON kb_chunks USING hnsw (embedding vector_cosine_ops)
    # """,
]


def rebuild_doc(conn, doc_id, doc_title, chunks, vectors):
    """单文档原子重建：同事务内 upsert 父行 → 删旧子行 → 插新子行。

    两个关键点：
    1. 先父后子：kb_chunks.doc_id 有外键指向 kb_documents，
       首次入库时父行还不存在，先插子行会违反外键约束（本次报错的原因）
    2. 包在事务里：任何一步失败整体回滚，知识库不会出现"删了一半"的残废状态
    """
    with conn.transaction():
        # 先 upsert 父行（文档元数据）：不存在则插入，存在则刷新
        conn.execute(
            """
            INSERT INTO kb_documents (doc_id, doc_title, chunk_count)
            VALUES (%s, %s, %s)
            ON CONFLICT (doc_id) DO UPDATE
            SET doc_title = EXCLUDED.doc_title,
                chunk_count = EXCLUDED.chunk_count,
                updated_at = NOW()
            """,
            (doc_id, doc_title, len(chunks)),
        )
        # 再删旧子行（重新入库 = 原子替换）
        conn.execute("DELETE FROM kb_chunks WHERE doc_id = %s", (doc_id,))
        # 最后批量插入新子行：psycopg3 把整批打成一个管道发给PG，比循环单条快
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO kb_chunks (doc_id, chunk_index, section_path, content, chunk_type, embedding)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        doc_id,
                        i,
                        c.section_path,
                        c.content,
                        c.chunk_type,
                        vectors[i],
                    )
                    for i, c in enumerate(chunks)
                ],
            )

if __name__ == "__main__":
    here = Path(__file__).parent
    with psycopg.connect(POSTGRES_URL) as conn:
        # 先建扩展与表：register_vector 要求 vector 类型已存在于数据库，
        # 全新库上先注册会报 'vector type not found'（顺序 bug，主机上
        # 没暴露是因为扩展早已手工建过；容器化后暴露）
        for stmt in DDL:
            conn.execute(stmt)
        # 再注册 pgvector 适配器，让 psycopg 认识numpy数组跟vector类型的转换
        # 之后embedding参数直接传numpy数组
        register_vector(conn)
        
        for path in sorted(here.glob("*.docx")):
            chunks = parse_docx(path)
            vectors = embed_texts([c.content for c in chunks])
            rebuild_doc(conn, path.name, chunks[0].doc_title, chunks, vectors)
            print(f"已入库 {path.name}，共 {len(chunks)} 个 chunk")
        
        total = conn.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0]
        print(f"知识库总chunk数: {total}")