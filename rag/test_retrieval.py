
from pathlib import Path
import hashlib
import json

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

ROOT = Path(__file__).resolve().parents[1]
RAG_DIR = ROOT / "rag"

KNOWLEDGE_PATH = RAG_DIR / "knowledge_chunks.jsonl"
MANIFEST_PATH = RAG_DIR / "knowledge_manifest.json"


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads(
        MANIFEST_PATH.read_text(encoding="utf-8")
    )

    if sha256_file(KNOWLEDGE_PATH) != manifest["knowledge_sha256"]:
        raise ValueError("Knowledge base SHA-256 mismatch.")

    chunks = [
        json.loads(line)
        for line in KNOWLEDGE_PATH.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    if len(chunks) != manifest["chunk_count"]:
        raise ValueError("Knowledge chunk count mismatch.")

    ids = [chunk["chunk_id"] for chunk in chunks]

    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate chunk IDs detected.")

    for source in manifest["sources"]:
        raw_file = ROOT / source["raw_file"]

        if sha256_file(raw_file) != source["raw_sha256"]:
            raise ValueError(
                f"Raw source checksum failed: {source['source_id']}"
            )

    texts = [chunk["text"] for chunk in chunks]

    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        sublinear_tf=True
    )

    matrix = vectorizer.fit_transform(texts)

    questions = [
        "How can workers prevent heat illness and acclimatize?",
        "What should employers do about heat stress and rest breaks?",
        "What is heat index and how does humidity affect it?"
    ]

    print("=" * 60)
    print("HEATSAFE RAG RETRIEVAL TEST")
    print("=" * 60)

    print("Knowledge integrity: PASS")
    print("Official sources:", manifest["source_count"])
    print("Total chunks:", len(chunks))
    print("TF-IDF index shape:", matrix.shape)

    for question in questions:
        query_vector = vectorizer.transform([question])

        scores = cosine_similarity(
            query_vector,
            matrix
        )[0]

        top_indices = scores.argsort()[::-1][:3]

        print("\n" + "-" * 60)
        print("QUESTION:", question)

        for rank, index in enumerate(top_indices, start=1):
            chunk = chunks[index]

            print(f"\nRESULT {rank}")
            print("Source:", chunk["publisher"])
            print("Chunk:", chunk["chunk_id"])
            print("Similarity:", round(float(scores[index]), 4))
            print("URL:", chunk["source_url"])
            print("Text preview:", chunk["text"][:250])

    print("\n" + "=" * 60)
    print("RETRIEVAL TEST EXECUTED")
    print("Review relevance before approving RAG retrieval.")
    print("=" * 60)


if __name__ == "__main__":
    main()
