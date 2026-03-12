# feed_db.py — Populates pgvector with retail knowledge base
# Reads markdown files from /data/, chunks by paragraph,
# generates embeddings with fastembed, and inserts into pgvector.
#
# Run as a K8s Job via feed-db.sh

import os
import glob
import psycopg2
from pgvector.psycopg2 import register_vector
from fastembed import TextEmbedding

PG_CONN   = os.environ.get("PG_CONN", "postgresql://retailbot:retailbot_secret@pgvector:5432/retailbot")
DATA_DIR  = os.environ.get("DATA_DIR", "/data")
MODEL_NAME = "BAAI/bge-small-en-v1.5"  # 384-dim, ~130MB, ONNX — no PyTorch needed
MIN_CHUNK_LEN = 60  # skip very short paragraphs


def load_documents(data_dir: str) -> list[dict]:
    """Load all markdown files and split into paragraph chunks."""
    docs = []
    for path in sorted(glob.glob(f"{data_dir}/*.md")):
        source = os.path.basename(path).replace(".md", "")
        with open(path) as f:
            content = f.read()

        # Split on double newlines (paragraph boundaries)
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        for i, para in enumerate(paragraphs):
            if len(para) >= MIN_CHUNK_LEN:
                docs.append({"source": source, "chunk_index": i, "content": para})

    return docs


def setup_schema(conn):
    """Create the knowledge_base table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id            SERIAL PRIMARY KEY,
                source        TEXT,
                chunk_index   INTEGER,
                content       TEXT,
                embedding     VECTOR(384)
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS kb_embedding_idx
            ON knowledge_base USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 10);
        """)
        conn.commit()


def clear_existing(conn):
    """Remove all existing rows so the feed is idempotent."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE knowledge_base;")
        conn.commit()


def insert_documents(conn, docs: list[dict], embeddings):
    """Batch insert documents with their embeddings."""
    with conn.cursor() as cur:
        for doc, embedding in zip(docs, embeddings):
            cur.execute(
                """
                INSERT INTO knowledge_base (source, chunk_index, content, embedding)
                VALUES (%s, %s, %s, %s)
                """,
                (doc["source"], doc["chunk_index"], doc["content"], embedding.tolist()),
            )
        conn.commit()


def main():
    print(f"[1/4] Loading documents from {DATA_DIR}...")
    docs = load_documents(DATA_DIR)
    print(f"      {len(docs)} chunks loaded from {len(set(d['source'] for d in docs))} files")

    print(f"[2/4] Loading embedding model ({MODEL_NAME})...")
    model = TextEmbedding(MODEL_NAME)

    print("[3/4] Generating embeddings...")
    texts = [d["content"] for d in docs]
    embeddings = list(model.embed(texts))
    print(f"      {len(embeddings)} embeddings generated (dim={len(embeddings[0])})")

    print("[4/4] Inserting into pgvector...")
    conn = psycopg2.connect(PG_CONN)
    register_vector(conn)
    setup_schema(conn)
    clear_existing(conn)
    insert_documents(conn, docs, embeddings)
    conn.close()
    print(f"      Done — {len(docs)} chunks inserted into knowledge_base.")


if __name__ == "__main__":
    main()
