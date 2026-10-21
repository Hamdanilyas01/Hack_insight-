
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import re

import duckdb
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


ROOT = Path(__file__).resolve().parents[1]
RAG_DIR = ROOT / "rag"
KNOWLEDGE = RAG_DIR / "knowledge_chunks.jsonl"
MANIFEST = RAG_DIR / "knowledge_manifest.json"
DATABASE = ROOT / "warehouse" / "heatsafe_live.duckdb"

UNKNOWN = "I don't know based on the available verified evidence."

DOMAIN_TERMS = {
    "heat", "humidity", "temperature", "worker", "workers",
    "employer", "workplace", "construction", "weather",
    "acclimatize", "acclimatization", "hydration",
    "water", "rest", "shade", "illness", "stress",
    "wbgt", "index", "sun", "precautions", "prevent"
}

UNSUPPORTED_TOPICS = (
    "aqi", "air quality right now", "hospital admissions",
    "hospitalizations", "deaths", "death count",
    "injury count", "medical records"
)

CURRENT_TERMS = (
    "current", "latest", "right now", "today",
    "tomorrow", "forecast", "next 24", "predicted"
)


def load_knowledge():
    if not KNOWLEDGE.is_file() or not MANIFEST.is_file():
        raise FileNotFoundError("RAG knowledge base is missing.")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data = KNOWLEDGE.read_bytes()

    if hashlib.sha256(data).hexdigest() != manifest["knowledge_sha256"]:
        raise ValueError("Knowledge SHA-256 verification failed.")

    chunks = [
        json.loads(line)
        for line in data.decode("utf-8").splitlines()
        if line.strip()
    ]

    if len(chunks) != manifest["chunk_count"]:
        raise ValueError("Knowledge chunk count mismatch.")


    for source in manifest["sources"]:
        raw_path = ROOT / source["raw_file"]

        if raw_path.is_file():
            actual_hash = hashlib.sha256(
                raw_path.read_bytes()
            ).hexdigest()

            if actual_hash != source["raw_sha256"]:
                raise ValueError(
                    f"Source checksum mismatch: {source['source_id']}"
                )

        else:
            print(
                f"NOTICE: Original HTML snapshot not installed "
                f"for {source['source_id']}. "
                "Using the integrity-checked knowledge index."
            )


    return chunks


def evidence_excerpt(text, question):
    sentences = re.split(r"(?<=[.!?])\s+", text)

    query_terms = set(re.findall(r"[a-z]{3,}", question.lower()))
    query_terms -= {"what", "does", "about", "should", "when", "where", "which"}

    candidates = [
        sentence.strip()
        for sentence in sentences
        if len(sentence.split()) >= 6
        and not re.search(r"\d", sentence)
    ]

    if not candidates:
        return None

    best = max(
        candidates,
        key=lambda sentence: len(
            query_terms.intersection(
                set(re.findall(r"[a-z]{3,}", sentence.lower()))
            )
        )
    )

    # Limit the quoted excerpt. The URL carries the full citation.
    return " ".join(best.split()[:22])


class HeatSafeInsightAgent:

    def __init__(self):
        self.chunks = load_knowledge()

        self.vectorizer = TfidfVectorizer(
            stop_words="english",
            ngram_range=(1, 2),
            sublinear_tf=True
        )

        self.matrix = self.vectorizer.fit_transform(
            [chunk["text"] for chunk in self.chunks]
        )


    def guidance_answer(self, question):
        q = question.lower().strip()

        words = set(re.findall(r"[a-z]+", q))

        heat_terms = {
            "heat", "hot", "temperature", "humidity",
            "humid", "worker", "workers", "workplace",
            "construction", "water", "rest", "shade",
            "acclimatization", "acclimatize",
            "hydration", "illness", "stress",
            "wbgt", "index", "safety", "safe",
            "protect", "prevent", "precautions"
        }

        if not words.intersection(heat_terms):
            return UNKNOWN

        def find_evidence(source_id, search_text, keywords):
            query_vector = self.vectorizer.transform(
                [search_text]
            )

            scores = cosine_similarity(
                query_vector,
                self.matrix
            )[0]

            candidates = []

            for index, chunk in enumerate(self.chunks):
                if chunk["source_id"] != source_id:
                    continue

                content = chunk["text"].lower()

                if not any(
                    term in content
                    for term in keywords
                ):
                    continue

                candidates.append(index)

            if not candidates:
                return None

            best_index = max(
                candidates,
                key=lambda index: float(scores[index])
            )

            return self.chunks[best_index]

        def cite(chunk):
            return (
                f"[{chunk['chunk_id']}] "
                f"{chunk['source_url']}"
            )

        # Question about heat index and humidity
        if (
            "heat index" in q
            or "humidity" in q
            or "feels like" in q
        ):
            evidence = find_evidence(
                "NOAA_001",
                "heat index air temperature relative humidity "
                "how hot it feels",
                ["relative humidity"]
            )

            if evidence is None:
                return UNKNOWN

            return (
                "Heat index combines air temperature and "
                "relative humidity to describe how hot "
                "the weather feels.\n\n"
                "Higher humidity can make hot weather "
                "feel hotter because sweat evaporates "
                "less effectively.\n\n"
                "Official evidence:\n"
                + cite(evidence)
            )

        # Workplace heat-safety guidance
        recommendations = [
            (
                "Drink water regularly while working in the heat.",
                "NIOSH_001",
                "workers drink water hydrate fluid intake",
                ["water", "fluid intake", "hydrate"]
            ),
            (
                "Take rest breaks during hot working conditions.",
                "NIOSH_001",
                "rest breaks heat exposure workers",
                ["rest breaks", "rest periods"]
            ),
            (
                "Increase time working in the heat gradually "
                "when getting acclimatized.",
                "NIOSH_001",
                "acclimatization gradually increase heat exposure",
                ["acclimatization", "acclimatize"]
            ),
            (
                "Recognize symptoms of heat-related illness "
                "and follow workplace emergency procedures.",
                "NIOSH_001",
                "heat illness symptoms training emergency response",
                ["symptoms", "emergency response"]
            )
        ]

        advice = []
        citations = []

        for message, source_id, search_text, keywords in recommendations:
            evidence = find_evidence(
                source_id,
                search_text,
                keywords
            )

            if evidence is None:
                continue

            advice.append(
                f"- {message} [{evidence['chunk_id']}]"
            )

            reference = cite(evidence)

            if reference not in citations:
                citations.append(reference)

        if not advice:
            return UNKNOWN

        temperature_question = (
            "temperature" in words
            or bool(re.search(r"\b30\b", q))
        )

        if temperature_question:
            introduction = (
                "If you mean an air temperature above 30°C, "
                "take precautions against heat illness.\n\n"
                "30°C alone is not a universal safe/unsafe "
                "limit. Humidity, sunlight and the type of "
                "physical work also affect heat stress.\n\n"
                "Recommended precautions:\n"
            )
        else:
            introduction = (
                "To help stay safe while working in hot "
                "conditions, follow these precautions:\n"
            )

        return (
            introduction
            + "\n".join(advice)
            + "\n\nOfficial evidence:\n"
            + "\n".join(citations)
            + "\n\nThese are general precautions, not "
            "a substitute for a workplace heat-risk assessment."
        )


    def latest_forecast_answer(self):
        if not DATABASE.is_file():
            return UNKNOWN + "\nThe live DuckDB database is unavailable."

        conn = duckdb.connect(str(DATABASE), read_only=True)

        try:
            rows = conn.execute("""
                SELECT
                    snapshot_id,
                    model_prediction_c,
                    model_risk_category,
                    interval_lower_c,
                    interval_upper_c,
                    target_time_utc,
                    collected_at_utc,
                    dq_status,
                    matches_latest_ingestion
                FROM gold.latest_live_ml
            """).df()
        except duckdb.Error:
            return UNKNOWN + "\nThe required gold evidence is unavailable."
        finally:
            conn.close()

        if len(rows) != 1:
            return UNKNOWN + "\nNo unique latest prediction exists."

        row = rows.iloc[0]

        if (
            row["dq_status"] != "PASS"
            or not bool(row["matches_latest_ingestion"])
        ):
            return UNKNOWN + "\nThe latest prediction failed evidence checks."

        collected = pd.Timestamp(row["collected_at_utc"])

        if collected.tzinfo is None:
            return UNKNOWN + "\nThe collection timezone is missing."

        age = pd.Timestamp.now(tz="UTC") - collected.tz_convert("UTC")

        if age < pd.Timedelta(0) or age > pd.Timedelta(hours=3):
            return (
                UNKNOWN
                + "\nThe latest stored forecast is stale; "
                "wait for a successful pipeline update."
            )

        numbers = [
            row["model_prediction_c"],
            row["interval_lower_c"],
            row["interval_upper_c"]
        ]

        if not np.isfinite(np.asarray(numbers, dtype=float)).all():
            return UNKNOWN

        target_pk = pd.Timestamp(
            row["target_time_utc"]
        ).tz_convert("Asia/Karachi")

        return (
            "Latest available HeatSafe ML prediction:\n"
            f"Heat index: {row['model_prediction_c']:.2f} °C\n"
            f"Category: {row['model_risk_category']}\n"
            f"Historical interval: "
            f"{row['interval_lower_c']:.2f}–"
            f"{row['interval_upper_c']:.2f} °C\n"
            f"Target: {target_pk}\n\n"
            "Data citation: gold.latest_live_ml\n"
            f"Source snapshot: {row['snapshot_id']}\n\n"
            "This interval was calibrated historically and is "
            "not a guarantee of workplace safety."
        )


    def answer(self, question):
        q = re.sub(r"\s+", " ", question.casefold()).strip()

        if not q:
            return UNKNOWN

        # 1. Refuse facts not available in our verified gold data.
        unsupported_patterns = [
            r"\b(?:hospital\w*|patient\w*|admission\w*|admitt\w*|death\w*|died|fatalit\w*|injur\w*|casualt\w*)\b",
            r"\b(?:aqi|air quality|pm2\.?5|pm10|pollution)\b",
            r"\b(?:how many|number of|count of|total number)\b"
        ]

        for pattern in unsupported_patterns:
            if re.search(pattern, q):
                return (
                    UNKNOWN
                    + "\nThis information is not supported "
                    "by our current verified gold data."
                )

        # 2. Answer supported live heat-index forecast requests.
        forecast_intent = re.search(
            r"\b(?:latest|current|forecast|predicted|prediction|today|tomorrow)\b",
            q
        )

        heat_forecast_topic = any(
            phrase in q
            for phrase in (
                "heat index",
                "heat forecast",
                "heat risk"
            )
        )

        if forecast_intent and heat_forecast_topic:
            return self.latest_forecast_answer()

        # 3. Do not confuse air-temperature forecasts
        # with our modeled heat-index forecast.
        if (
            forecast_intent
            and re.search(r"\b(?:temperature|weather)\b", q)
            and not re.search(
                r"\b(?:safe|safety|do|protect|prevent|precaution)\b",
                q
            )
        ):
            return (
                UNKNOWN
                + "\nThis agent currently provides a modeled "
                "heat-index forecast, not the requested "
                "air-temperature or general weather observation."
            )

        # 4. Check whether the question genuinely concerns
        # heat safety or an explanation covered by our sources.
        guidance_topic = re.search(
            r"\b(?:safe|safety|protect\w*|prevent\w*|"
            r"precaution\w*|acclimati[sz]\w*|water|rest|"
            r"shade|hydrat\w*|heat\s+(?:index|stress|illness)|"
            r"humidity|wbgt)\b",
            q
        )

        action_question = (
            bool(re.search(
                r"\b(?:what to do|what should|how to|how can)\b",
                q
            ))
            and bool(re.search(
                r"\b(?:heat|hot|temperature|worker|workplace)\b",
                q
            ))
        )

        if not guidance_topic and not action_question:
            return UNKNOWN

        # 5. Retrieve official safety guidance.
        return self.guidance_answer(question)



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", type=str)
    args = parser.parse_args()

    agent = HeatSafeInsightAgent()

    if args.question:
        print(agent.answer(args.question))
    else:
        print("HeatSafe Insight Agent ready.")
        print("Type exit to stop.")

        while True:
            question = input("\nQuestion: ").strip()

            if question.lower() in {"exit", "quit"}:
                break

            print("\n" + agent.answer(question))


if __name__ == "__main__":
    main()
