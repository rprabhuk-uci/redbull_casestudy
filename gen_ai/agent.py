"""
agent.py — main GenAI data agent script.

Takes a natural-language question, generates SQL (grounded via context.py
and golden_queries.sql as few-shot examples), validates it against
guardrails, executes it against Snowflake, prints the result, and
generates a simple bar chart visualization when the result shape suggests
one is useful (a small number of rows with one label + one numeric column).

Setup:
    pip install google-genai snowflake-connector-python python-dotenv pandas matplotlib
    Add GEMINI_API_KEY to your .env alongside the existing SNOWFLAKE_* vars.

Run interactively:
    python agent.py
Run with a single question:
    python agent.py "What percentage of listings in GBR serve Red Bull?"
"""
from __future__ import annotations

import logging
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")  # no display needed — saves chart to file
import matplotlib.pyplot as plt
import pandas as pd
import snowflake.connector
from dotenv import load_dotenv
from google import genai
from google.genai import types
from snowflake.connector import SnowflakeConnection

from context import FULL_CONTEXT

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("agent.log")],
)
logger = logging.getLogger(__name__)

ALLOWED_TABLE_PATTERN = re.compile(r"\bGOLD\.[A-Z_]+\b")
FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|MERGE|COPY)\b",
    re.IGNORECASE,
)

GOLDEN_QUERIES_PATH = os.path.join(os.path.dirname(__file__), "golden_queries.sql")


class AgentGuardrailError(Exception):
    """Raised when generated SQL fails validation — never executed."""


def load_golden_queries_as_examples(max_examples: int = 5) -> str:
    """Reads golden_queries.sql and formats a handful as few-shot examples
    for the prompt — real, correct query patterns for this exact schema,
    not just abstract schema description."""
    if not os.path.exists(GOLDEN_QUERIES_PATH):
        return ""

    with open(GOLDEN_QUERIES_PATH) as f:
        content = f.read()

    # Each golden query is preceded by a "-- Q: ..." comment describing it
    blocks = re.findall(r"-- Q: (.+?)\n(SELECT.+?;)", content, re.DOTALL)
    examples = blocks[:max_examples]
    formatted = "\n\n".join(f"Question: {q}\nSQL: {sql.strip()}" for q, sql in examples)
    return f"\n=== EXAMPLE QUESTIONS AND CORRECT SQL ===\n{formatted}\n" if formatted else ""


def get_snowflake_connection() -> SnowflakeConnection:
    required = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"Missing env var(s): {', '.join(missing)}")
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "RBNA_CASE_STUDY"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "GOLD"),
    )


def generate_sql(question: str) -> str:
    """Calls the Gemini API to translate a natural-language question into
    SQL, grounded via context.py's schema/business definitions plus
    golden-query few-shot examples."""
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("Missing GEMINI_API_KEY in environment/.env")

    system_prompt = FULL_CONTEXT + load_golden_queries_as_examples()

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=question,
        config=types.GenerateContentConfig(
            temperature=0,
            system_instruction=system_prompt,
        ),
    )
    raw_sql = response.text.strip()
    raw_sql = re.sub(r"^```sql\s*|\s*```$", "", raw_sql, flags=re.IGNORECASE).strip()
    logger.info("Generated SQL for question=%r: %s", question, raw_sql)
    return raw_sql


def validate_sql(sql: str) -> None:
    stripped = sql.strip().rstrip(";")
    if ";" in stripped:
        raise AgentGuardrailError("Multiple statements detected — only one SELECT is allowed.")
    if not re.match(r"^\s*SELECT\b", stripped, re.IGNORECASE):
        raise AgentGuardrailError(f"Generated SQL does not start with SELECT: {stripped[:80]}")
    if FORBIDDEN_KEYWORDS.search(stripped):
        raise AgentGuardrailError(f"Generated SQL contains a forbidden keyword: {stripped[:120]}")
    if re.search(r"\b(RAW|SILVER)\.[A-Z_]+\b", stripped, re.IGNORECASE):
        raise AgentGuardrailError("Generated SQL references RAW or SILVER schema — not allowed.")
    if not ALLOWED_TABLE_PATTERN.findall(stripped.upper()):
        raise AgentGuardrailError("Generated SQL does not reference any GOLD.* object.")


def summarize_result(question: str, df: pd.DataFrame) -> str:
    """Calls Gemini a second time to turn the raw result table into a
    short, plain-English answer — the actual final output a person reads,
    rather than a bare dataframe/number."""
    if df is None or df.empty:
        return "No data was returned for this question."

    # Cap how much raw data we send back to the model — keeps this fast
    # and cheap even if a query returns many rows.
    table_preview = df.head(20).to_string(index=False)

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=(
            f"Question: {question}\n\n"
            f"Query result:\n{table_preview}\n\n"
            f"Answer the question in 1-3 plain-English sentences based on "
            f"this data. Be specific with numbers. Do not mention SQL or "
            f"the table itself — just answer naturally, as if speaking to "
            f"a business stakeholder."
        ),
        config=types.GenerateContentConfig(temperature=0),
    )
    return response.text.strip()


def maybe_visualize(df: pd.DataFrame, question: str) -> str | None:
    """If the result looks chart-worthy (one label column, one numeric
    column, <= 20 rows), saves a bar chart PNG and returns its path."""
    if df is None or df.empty or len(df) > 20 or len(df.columns) < 2:
        return None

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    label_cols = [c for c in df.columns if c not in numeric_cols]
    if not numeric_cols or not label_cols:
        return None

    label_col, value_col = label_cols[0], numeric_cols[0]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(df[label_col].astype(str), df[value_col])
    ax.set_xlabel(label_col)
    ax.set_ylabel(value_col)
    ax.set_title(question[:60])
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()

    filename = "agent_chart.png"
    fig.savefig(filename)
    plt.close(fig)
    return filename


def run_agent(conn: SnowflakeConnection, question: str) -> dict:
    """End-to-end: generate SQL, validate it, execute if safe, return a
    structured result. Never raises on a guardrail/generation failure."""
    try:
        sql = generate_sql(question)
    except Exception as e:
        logger.error("SQL generation failed for question=%r: %s", question, e)
        return {"question": question, "sql": None, "error": f"Generation failed: {e}", "rows": None, "chart": None, "summary": None}

    try:
        validate_sql(sql)
    except AgentGuardrailError as e:
        logger.warning("Guardrail rejected SQL for question=%r: %s", question, e)
        return {"question": question, "sql": sql, "error": f"Guardrail rejected: {e}", "rows": None, "chart": None, "summary": None}

    try:
        df = pd.read_sql(sql, conn)
        chart_path = maybe_visualize(df, question)
        summary = summarize_result(question, df)
        return {"question": question, "sql": sql, "error": None, "rows": df,
                "chart": chart_path, "summary": summary}
    except Exception as e:
        logger.error("Execution failed for question=%r, sql=%r: %s", question, sql, e)
        return {"question": question, "sql": sql, "error": f"Execution failed: {e}",
                "rows": None, "chart": None, "summary": None}


def main():
    conn = get_snowflake_connection()
    try:
        if len(sys.argv) > 1:
            # Single-question mode: python agent.py "your question"
            question = " ".join(sys.argv[1:])
            result = run_agent(conn, question)
            _print_result(result)
        else:
            # Interactive loop
            while True:
                q = input("\nAsk a question (or 'quit'): ").strip()
                if q.lower() in ("quit", "exit"):
                    break
                result = run_agent(conn, q)
                _print_result(result)
    finally:
        conn.close()


def _print_result(result: dict) -> None:
    if result["error"]:
        print(f"\n❌ {result['error']}")
        return

    if result.get("summary"):
        print(f"\n💬 {result['summary']}")

    print(f"\n[SQL used: {result['sql']}]")
    if result["rows"] is not None:
        print(result["rows"].to_string(index=False))
        if result["chart"]:
            print(f"[Chart saved: {result['chart']}]")


if __name__ == "__main__":
    main()