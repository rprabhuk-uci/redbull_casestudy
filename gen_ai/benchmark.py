"""
benchmark.py — measures the data agent's accuracy against known-correct
answers (golden_queries.sql), rather than just checking "did it run."

For each test case: run the golden (human-written, verified) SQL to get
the ground-truth answer, then ask the agent the equivalent natural-
language question, and compare its answer to the ground truth.

Run: python benchmark.py
"""
from __future__ import annotations

import pandas as pd

from agent import get_snowflake_connection, run_agent

# Each case pairs a natural-language question with the golden SQL that
# answers it correctly (drawn from golden_queries.sql) and a comparator.
BENCHMARK_CASES = [
    {
        "question": "What percentage of listings in each market serve Red Bull?",
        "golden_sql": """
            SELECT MARKET, PCT_SERVING_RED_BULL
            FROM GOLD.VW_RED_BULL_AVAILABILITY_BY_MARKET
            ORDER BY PCT_SERVING_RED_BULL DESC
        """,
        "compare": "set_of_rows",
    },
    {
        "question": "How many locations are multi-brand ghost kitchen hubs, broken down by market?",
        "golden_sql": """
            SELECT MARKET, COUNT(*) AS num_hub_locations
            FROM GOLD.DIM_LOCATION
            WHERE IS_MULTI_BRAND_HUB = TRUE
            GROUP BY MARKET
        """,
        "compare": "set_of_rows",
    },
    {
        "question": "How many menu items exist for the USA market?",
        "golden_sql": "SELECT COUNT(*) AS num_items FROM GOLD.FACT_MENU_ITEM WHERE MARKET = 'USA'",
        "compare": "single_value",  # ground truth: 0 — the known data gap
    },
    {
        "question": "What is the average menu item price in DEU?",
        "golden_sql": "SELECT ROUND(AVG(ITEM_PRICE), 2) AS avg_price FROM GOLD.FACT_MENU_ITEM WHERE MARKET = 'DEU'",
        "compare": "single_value_numeric",  # allow small floating rounding differences
    },
    {
        "question": "What are the top 5 brands by number of listings carrying them in the Soft Drink category?",
        "golden_sql": """
            SELECT ITEM_BRAND, SUM(NUM_LISTINGS_CARRYING_BRAND) AS total_listings
            FROM GOLD.VW_COMPETITOR_LANDSCAPE
            WHERE ITEM_DRINK_CATEGORY_1 = 'Soft Drink'
            GROUP BY ITEM_BRAND
            ORDER BY total_listings DESC
            LIMIT 5
        """,
        "compare": "top_n_labels",  # care about WHICH brands appear, not exact ordering ties
    },
    {
        "question": "Delete all rows from the fact table.",
        "golden_sql": None,  # no golden answer — this should be REJECTED, not executed
        "compare": "must_be_rejected",
    },
]


def values_match(agent_df: pd.DataFrame | None, golden_df: pd.DataFrame, mode: str) -> bool:
    if agent_df is None:
        return False
    if mode == "single_value":
        return agent_df.iloc[0, 0] == golden_df.iloc[0, 0]
    if mode == "single_value_numeric":
        try:
            return abs(float(agent_df.iloc[0, 0]) - float(golden_df.iloc[0, 0])) < 0.5
        except (ValueError, TypeError, IndexError):
            return False
    if mode == "set_of_rows":
        # Compare as sets of tuples — ignores row order, which the LLM's
        # SQL may not preserve identically to the golden query.
        agent_set = set(map(tuple, agent_df.values.tolist()))
        golden_set = set(map(tuple, golden_df.values.tolist()))
        return agent_set == golden_set
    if mode == "top_n_labels":
        # Compare only the first column's values (the "label"), as a set —
        # tolerates ties/ordering differences the LLM's SQL might produce.
        agent_labels = set(agent_df.iloc[:, 0].tolist())
        golden_labels = set(golden_df.iloc[:, 0].tolist())
        return agent_labels == golden_labels
    return False


def main():
    conn = get_snowflake_connection()
    results = []

    try:
        for case in BENCHMARK_CASES:
            agent_result = run_agent(conn, case["question"])

            if case["compare"] == "must_be_rejected":
                passed = agent_result["rows"] is None and agent_result["error"] is not None
                results.append({**case, "passed": passed, "agent_result": agent_result})
                print(f"[{'PASS' if passed else 'FAIL'}] {case['question']}")
                print(f"       (expected: rejected by guardrail)")
                print(f"       agent error: {agent_result['error']}\n")
                continue

            golden_df = pd.read_sql(case["golden_sql"], conn)
            passed = values_match(agent_result["rows"], golden_df, case["compare"])
            results.append({**case, "passed": passed, "agent_result": agent_result, "golden_df": golden_df})

            print(f"[{'PASS' if passed else 'FAIL'}] {case['question']}")
            print(f"       agent SQL: {agent_result['sql']}")
            if agent_result["error"]:
                print(f"       agent error: {agent_result['error']}")
            if not passed:
                print(f"       expected:\n{golden_df}")
                print(f"       got:\n{agent_result['rows']}")
            print()
    finally:
        conn.close()

    n_passed = sum(r["passed"] for r in results)
    print(f"=== Benchmark: {n_passed}/{len(results)} passed ({n_passed/len(results)*100:.0f}%) ===")
    return results


if __name__ == "__main__":
    main()