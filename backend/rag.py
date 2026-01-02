import os
from pathlib import Path

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from .embeddings import embed_text

KB_PATH = Path(__file__).resolve().parent / "knowledge_base"

documents = []
embeddings = []

# Load KB at startup
if KB_PATH.exists() and KB_PATH.is_dir():
    for filename in os.listdir(KB_PATH):
        file_path = KB_PATH / filename
        if not file_path.is_file():
            continue
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
            documents.append({
                "id": filename,
                "text": text,
            })
            embeddings.append(embed_text(text))

embeddings = np.array(embeddings) if embeddings else np.array([])

def retrieve(query: str, top_k: int = 2):
    if embeddings.size == 0:
        return []

    query_emb = embed_text(query).reshape(1, -1)
    scores = cosine_similarity(query_emb, embeddings)[0]
    top_indices = scores.argsort()[-top_k:][::-1]

    results = []
    for idx in top_indices:
        if scores[idx] > 0.2:  # similarity threshold
            results.append(documents[idx])

    return results
