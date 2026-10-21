
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse
import hashlib
import json
import re

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]

RAG_DIR = ROOT / "rag"
REGISTRY_PATH = RAG_DIR / "source_registry.json"
MANUAL_DIR = RAG_DIR / "manual"
RAW_DIR = RAG_DIR / "raw"

KNOWLEDGE_PATH = RAG_DIR / "knowledge_chunks.jsonl"
MANIFEST_PATH = RAG_DIR / "knowledge_manifest.json"


START_HEADINGS = {
    "NIOSH_001": "Workplace Recommendations",
    "HSE_001": "7. Heat stress",
    "NOAA_001": "Heat Index"
}

STOP_HEADINGS = {
    "NIOSH_001": {"What the research shows"},
    "HSE_001": {"Related content"},
    "NOAA_001": {"HeatRisk"}
}

REQUIRED_TERMS = {
    "NIOSH_001": [
        "acclimatization",
        "rest",
        "water"
    ],
    "HSE_001": [
        "heat stress",
        "rest breaks",
        "dehydration"
    ],
    "NOAA_001": [
        "heat index",
        "wbgt"
    ]
}


def sha256_bytes(content):
    return hashlib.sha256(content).hexdigest()


def clean_text(value):
    return re.sub(r"\s+", " ", value).strip()


def load_registry():
    if not REGISTRY_PATH.is_file():
        raise FileNotFoundError(
            f"Source registry missing: {REGISTRY_PATH}"
        )

    registry = json.loads(
        REGISTRY_PATH.read_text(encoding="utf-8")
    )

    sources = registry["sources"]
    source_ids = [source["source_id"] for source in sources]

    if len(sources) != 3:
        raise ValueError("Expected exactly three sources.")

    if len(set(source_ids)) != 3:
        raise ValueError("Duplicate source IDs detected.")

    if set(source_ids) != set(START_HEADINGS):
        raise ValueError("Unexpected source IDs.")

    return sources


def collect_source(session, source):
    source_id = source["source_id"]
    url = source["url"]

    manual_path = MANUAL_DIR / f"{source_id}.html"

    if manual_path.is_file():
        html_bytes = manual_path.read_bytes()

        if not html_bytes:
            raise ValueError(
                f"{source_id}: manually saved file is empty."
            )

        result = {
            "content": html_bytes,
            "url": url,
            "http_status": None,
            "retrieval_method": "manual_browser_save",
            "manual_file": str(
                manual_path.relative_to(ROOT)
            )
        }

        print("Using manually saved official webpage.")
        print("Local file:", manual_path)

        return result

    response = session.get(
        url,
        timeout=45
    )

    if response.status_code == 403:
        raise RuntimeError(
            f"{source_id}: website rejected automated access "
            f"(HTTP 403). Save the official webpage as "
            f"rag/manual/{source_id}.html and rerun."
        )

    response.raise_for_status()

    requested_host = urlparse(url).hostname
    returned_host = urlparse(response.url).hostname

    if requested_host != returned_host:
        raise ValueError(
            f"{source_id}: unexpected website redirect."
        )

    if not response.content:
        raise ValueError(
            f"{source_id}: downloaded file is empty."
        )

    print("Downloaded directly from official website.")

    return {
        "content": response.content,
        "url": response.url,
        "http_status": response.status_code,
        "retrieval_method": "direct_https",
        "manual_file": None
    }


def extract_guidance(html_bytes, source_id):
    soup = BeautifulSoup(
        html_bytes,
        "html.parser"
    )

    for tag in soup([
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "footer",
        "header",
        "aside",
        "form"
    ]):
        tag.decompose()

    start_heading = START_HEADINGS[source_id].casefold()

    stop_headings = {
        heading.casefold()
        for heading in STOP_HEADINGS[source_id]
    }

    blocks = []
    started = False

    for tag in soup.find_all([
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "li"
    ]):
        if tag.find_parent("li") is not None:
            continue

        value = clean_text(
            tag.get_text(" ", strip=True)
        )

        if not value:
            continue

        is_heading = tag.name.startswith("h")

        if not started:
            if is_heading and value.casefold() == start_heading:
                started = True
            else:
                continue

        if is_heading and value.casefold() in stop_headings:
            break

        if not blocks or blocks[-1] != value:
            blocks.append(value)

    if not started:
        raise ValueError(
            f"{source_id}: expected article heading not found."
        )

    article_text = "\n".join(blocks)

    word_count = len(article_text.split())

    if word_count < 80:
        raise ValueError(
            f"{source_id}: insufficient article content."
        )

    if word_count > 5000:
        raise ValueError(
            f"{source_id}: unusually large extraction. "
            "Check for navigation or unrelated content."
        )

    text_lower = article_text.casefold()

    for term in REQUIRED_TERMS[source_id]:
        if term.casefold() not in text_lower:
            raise ValueError(
                f"{source_id}: required topic missing: {term}"
            )

    return article_text


def create_chunks(text, max_words=160, overlap=25):
    if overlap >= max_words:
        raise ValueError(
            "Overlap must be smaller than chunk size."
        )

    words = text.split()
    chunks = []

    start = 0

    while start < len(words):
        end = min(
            start + max_words,
            len(words)
        )

        chunk = " ".join(
            words[start:end]
        )

        if chunk:
            chunks.append(chunk)

        if end == len(words):
            break

        start = end - overlap

    return chunks


def main():
    print("=" * 60)
    print("HEATSAFE OFFICIAL KNOWLEDGE COLLECTION")
    print("=" * 60)

    sources = load_registry()

    RAW_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    stamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    all_chunks = []
    source_records = []

    session = requests.Session()

    session.headers.update({
        "User-Agent": "HeatSafePakistan-Research/1.0"
    })

    for source in sources:
        source_id = source["source_id"]

        print("\n" + "-" * 55)
        print("Collecting:", source_id)
        print("Publisher:", source["publisher"])
        print("-" * 55)

        collected = collect_source(
            session,
            source
        )

        html_bytes = collected["content"]
        source_url = collected["url"]

        checksum = sha256_bytes(html_bytes)

        article_text = extract_guidance(
            html_bytes,
            source_id
        )

        parts = create_chunks(
            article_text
        )

        if not parts:
            raise ValueError(
                f"{source_id}: no knowledge chunks generated."
            )

        raw_path = RAW_DIR / f"{source_id}_{stamp}.html"

        raw_path.write_bytes(
            html_bytes
        )

        saved_checksum = sha256_bytes(
            raw_path.read_bytes()
        )

        if saved_checksum != checksum:
            raise AssertionError(
                f"{source_id}: raw file SHA-256 mismatch."
            )

        indexed_at = datetime.now(
            timezone.utc
        ).isoformat()

        for number, part in enumerate(parts, start=1):
            chunk_record = {
                "chunk_id": (
                    f"{source_id}_CHUNK_{number:03d}"
                ),
                "source_id": source_id,
                "publisher": source["publisher"],
                "title": source["title"],
                "source_url": source_url,
                "indexed_at_utc": indexed_at,
                "retrieval_method": collected[
                    "retrieval_method"
                ],
                "raw_sha256": checksum,
                "license_note": source["license_note"],
                "license_evidence_url": source.get(
                    "license_evidence_url"
                ),
                "text": part
            }

            all_chunks.append(
                chunk_record
            )

        source_record = {
            "source_id": source_id,
            "publisher": source["publisher"],
            "source_url": source_url,
            "indexed_at_utc": indexed_at,
            "retrieval_method": collected[
                "retrieval_method"
            ],
            "http_status": collected["http_status"],
            "manual_file": collected["manual_file"],
            "raw_file": str(
                raw_path.relative_to(ROOT)
            ),
            "raw_sha256": checksum,
            "extracted_words": len(
                article_text.split()
            ),
            "chunks": len(parts),
            "status": "PASS"
        }

        source_records.append(
            source_record
        )

        print("Source validation: PASS")
        print(
            "Collection method:",
            collected["retrieval_method"]
        )
        print(
            "Extracted words:",
            len(article_text.split())
        )
        print(
            "Knowledge chunks:",
            len(parts)
        )
        print("Raw SHA-256:", checksum)

    session.close()

    if len(source_records) != 3:
        raise AssertionError(
            "Knowledge collection is incomplete."
        )

    if not all_chunks:
        raise AssertionError(
            "Knowledge base contains zero chunks."
        )

    output = "\n".join(
        json.dumps(
            chunk,
            ensure_ascii=False
        )
        for chunk in all_chunks
    ) + "\n"

    KNOWLEDGE_PATH.write_text(
        output,
        encoding="utf-8"
    )

    knowledge_checksum = sha256_bytes(
        KNOWLEDGE_PATH.read_bytes()
    )

    manifest = {
        "project": "HeatSafe Pakistan",
        "created_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source_count": len(source_records),
        "chunk_count": len(all_chunks),
        "knowledge_file": str(
            KNOWLEDGE_PATH.relative_to(ROOT)
        ),
        "knowledge_sha256": knowledge_checksum,
        "sources": source_records
    }

    MANIFEST_PATH.write_text(
        json.dumps(
            manifest,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    saved_lines = [
        json.loads(line)
        for line in KNOWLEDGE_PATH.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    if len(saved_lines) != len(all_chunks):
        raise AssertionError(
            "Saved knowledge chunk count mismatch."
        )

    print("\n" + "=" * 60)
    print("HEATSAFE RAG KNOWLEDGE BASE COMPLETE")
    print("=" * 60)

    print("Official sources:", len(source_records))
    print("Total knowledge chunks:", len(all_chunks))
    print("Knowledge file:", KNOWLEDGE_PATH)
    print("Manifest:", MANIFEST_PATH)
    print("Knowledge SHA-256:", knowledge_checksum)

    print("\nSOURCE SUMMARY")

    for record in source_records:
        print(
            record["source_id"],
            "|",
            record["retrieval_method"],
            "|",
            record["chunks"],
            "chunks"
        )

    print("\nFINAL STATUS: PASS")


if __name__ == "__main__":
    main()
