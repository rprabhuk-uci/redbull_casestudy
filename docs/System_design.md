 # RBNA Data Engineer Case Study — System Design Document

## 1. Overview

This project builds a Snowflake-based analytics platform for Red Bull's online food-delivery presence across three markets (USA, GBR,
DEU) and multiple delivery platforms per market. It covers ingestion,
data modeling, quality validation, role-based access control, and a
GenAI-powered natural-language query agent.

**Repo structure**: `ingestion/`, `sql/` (numbered, run-in-order scripts),
`streamlit_app/`, `genai_poc/`, `docs/`.

## 2. Architecture & Data Flow

```
Local CSVs (outlet/portfolio/matching × 3 markets × multiple platform files)
        │
        │  write_pandas (Python, encoding-aware, malformed-row tolerant)
        ▼
RAW schema — all-VARCHAR landing tables, one per file type, tagged with
             _source_market / _source_file_name / _loaded_at metadata
        │
        │  SQL: validation, quarantine, type casting, deduplication
        ▼
SILVER schema — cleaned, typed, quarantine tables hold rejected rows
        │
        │  SQL: dimensional modeling
        ▼
GOLD schema — star schema (facts + dimensions) + business-question views
        │
        ├──► Streamlit-in-Snowflake dashboards (2 tabs: availability, competitor landscape)
        └──► GenAI data agent (external script: NL question → SQL → result → plain-English summary)
```

**Why a medallion (raw/silver/gold) structure**: each layer has a single
responsibility: raw preserves fidelity to source (nothing is lost or
transformed, so any bug downstream can be traced back to real source
data), silver enforces correctness (validated, typed, deduplicated),
gold optimizes for consumption (denormalized where it aids query
simplicity, pre-computed business logic). This also cleanly separates
*where* a bug could be — a wrong number in a dashboard is either a
silver validation gap or a gold modeling choice, never ambiguous.

**Ingestion approach — local files vs. a production source**: for this
exercise, source files were read directly from local disk, which was
the most straightforward approach given a fixed, one-time dataset. In
production, these files would more realistically originate from a
system like S3 (or a live source database).
Ingestion logic would be written as a reusable, parameterized template
— source location (an S3 URL prefix, a database connection config, etc.)
and market/file-type as inputs — rather than hardcoded paths, so the
same ingestion code could be scheduled and reused across markets, file
types, and future data sources without rewriting it each time.

## 3. Data Modeling

**Star schema in GOLD**:
- `DIM_MARKET`, `DIM_LOCATION` (physical kitchen, grain `id_outlet`),
  `DIM_LISTING` (platform storefront, grain `id_ext_link`), `DIM_PRODUCT`
  (grain `id_drink`), `FACT_MENU_ITEM` (grain `id_ext_link, id_beverage`).

**Key discovery — the "ghost kitchen" pattern**: the initial assumption
was that `id_outlet` + `id_platform` formed the outlet's grain. Verifying
against real data showed `id_ext_link` is the true grain — the same
`id_outlet` can host many distinct listings, reflecting virtual restaurant brands sharing one commercial kitchen. This reshaped the model into two separate dimensions (location vs. listing)
rather than one.

**Entity resolution for brand counting**: naive name-string matching
(`LOWER(TRIM(NAME))`) produced both false positives (same brand across
two platforms counted as two brands) and false negatives (same brand
with a location suffix in the name counted as two brands). The final
approach resolves listings to a shared `place_id` (from `MATCHING`,
itself derived from Google Maps) when confidence is high (similarity
score ≥ 0.95 on name or address), falling back to treating a listing as
its own distinct entity when unresolved — a conservative choice that
avoids false merges at the cost of a known upper-bound imprecision on
brand counts.

## 4. Data Quality — Findings and Handling

All findings below were confirmed against real data:

| Finding | Root cause | Handling |
|---|---|---|
| DEU file encoding garbled | Files encoded Windows-1252/ISO-8859-1, not UTF-8 | Per-market encoding fallback chain in ingestion |
| Malformed quoting caused rows to split into two broken pieces | Source data used backslash-style internal quote escaping (e.g. `\"Southern fried\"`) instead of standard CSV doubled-quote escaping (`""`); standard parsing reads the first internal quote as the field's real closing quote, splitting one row into a truncated "front half" (passes ID validation, lands silently in the clean table missing trailing content) and an orphaned "back half" fragment (no valid ID, correctly quarantined) | Root-caused via manual raw-file inspection; **fixed** by adding `escapechar="\\"` to the CSV read configuration — quarantine count dropped from 185,253 to 0, with previously-split rows now parsing as complete, correct single records |
| Raw `MARKET` field inconsistent (US/USA, DE/DEU, UK/null) | Inconsistent source export conventions | Used `_source_market`, tagged reliably from folder structure at ingestion time — critical since RBAC filters on this field |
| No `portfolio` file for USA | Genuine data gap, not a pipeline bug | Documented; `FACT_MENU_ITEM`/competitor views correctly have zero USA rows |
| Ambiguous/undocumented fields (`LEADING_ID_EXT_LINK`, `sd_coke`, `ed`, etc.) | No documentation available | Kept as raw pass-through |

**Validation philosophy**: quarantine, don't discard. Every quarantined
row is preserved and countable, so data completeness (`clean + quarantine
= raw`) is provable.

## Observability — How You Know Something Broke

**The core idea**: don't wait for a stakeholder to notice bad numbers —
define expectations about each table up front, check them automatically
after every load, and alert when they're violated.

**In production, this maps onto tooling that already exists **: dbt's testing framework and
Dagster's asset checks (the `dagster-dbt` integration surfaces dbt
tests as Dagster asset checks directly) already provide exactly the
self-serve model described below — a developer adding a new table
declares its tests alongside the model definition, and checks run
automatically as part of every pipeline run, with results visible
per-asset in Dagster's UI.

**Common, reusable test types** (dbt's built-in generic tests cover
most of these directly):
- **Row count sanity** — a table shouldn't unexpectedly go to zero rows
  (signals a broken upstream source or a failed load) or spike far
  beyond its normal range (signals a duplicate load or a join fan-out bug).
- **Null rate checks** — a column that's normally populated shouldn't
  suddenly turn mostly-null.
- **Referential integrity** — every foreign key should resolve to a real
  row in its parent table (this is exactly what the quarantine logic
  built for this project already checks manually; in production it
  becomes a standing test, not a one-time investigation).
- **Freshness/staleness** — a table that's supposed to load daily
  shouldn't silently stop updating; check `_loaded_at` age.
- **Custom, table-specific tests** — for this project specifically:
  - **Quarantine-rate threshold**: if `SILVER.*_QUARANTINE` row count
    exceeds a defined threshold (e.g. >100 rows, or >2% of the load) in
    a single run, that's a signal the source data quality has genuinely
    shifted.
  - **Zero-row load check**: if `RAW.OUTLET`/`PORTFOLIO`/`MATCHING` comes
    back empty after a run that should have loaded data, that's almost
    certainly a pipeline failure (a bad file path, an auth failure, a
    silently-empty source), not a legitimate business state, and should
    alert immediately rather than flow downstream unnoticed.

**Ownership model**: the developer who owns a table defines its tests
alongside the model, rather than a central team maintaining tests for
tables they don't have context on.

**Alerting**: test/check failures route to Slack for day-to-day
visibility, with PagerDuty escalation for on-call-worthy failures (e.g.
a zero-row load on a table that feeds a live dashboard).

## 5. RBAC

Snowflake row access policy (`MARKET_ACCESS_POLICY`) filters on the
reliable `_source_market`-derived `MARKET` column, applied to base
tables (`DIM_LOCATION`, `DIM_LISTING`, `FACT_MENU_ITEM`, `DIM_MARKET`).
Four roles: `ANALYST_USA`/`GBR`/`DEU` (single-market visibility),
`HQ_ANALYST` (all markets). Default-deny: any role not explicitly
listed sees zero rows.

Applying the policy to base tables means every view built on top
(including both Streamlit dashboard tabs) inherits the same filtering
automatically.

**Known limitation**: Streamlit-in-Snowflake apps execute with
owner's-rights (like a stored procedure), so `CURRENT_ROLE()` inside an
app always resolves to the app's owner, not the viewer — switching your
own session role has no effect on what the app displays. Per-viewer
RBAC inside Streamlit specifically requires `CURRENT_USER()`-based
policies plus a `READ SESSION` grant. For this exercise, RBAC is
demonstrated directly via SQL session role-switching in a worksheet.

## 6. GenAI Data Agent

**Use case**: natural-language question → SQL → execution → plain-English
summary, scoped entirely to the GOLD schema.

**Architecture**: `context.py` (schema, join keys, business definitions,
known data gaps — the agent's entire grounding) + `golden_queries.sql`
(few-shot examples, also used as ground truth for benchmarking) →
`agent.py` (generation, validation, execution, summarization) →
`benchmark.py` (accuracy measurement against known-correct answers).

**Cost management**: two LLM calls per question (SQL generation +
result summarization) — a deliberate UX-vs-cost trade-off. In
production, I'd cache repeated/similar questions, and consider
skipping the summarization call for questions where the raw table is
already self-explanatory.

**Determinism**: `temperature=0` on both calls. Even at temperature 0,
most hosted LLMs aren't perfectly deterministic run-to-run.

**Guardrails**: generated SQL is validated before execution — must be a
single `SELECT` statement, must reference only `GOLD.*` objects, no
DDL/DML keywords, no cross-statement chaining. An adversarial test case
("delete all rows...") is included in the benchmark specifically to
prove the guardrail rejects unsafe requests rather than executing them.

**Human-in-the-loop**: for this POC, every generated SQL statement is
shown to the user alongside the answer . The benchmarks and guardrails themselves are human-defined — a person decides what "correct" looks like and what's safe to allow. Once opened up to end users, I'd add a feedback mechanism (thumbs up/down, or a short comment) on each question's SQL and answer. That feedback, combined with observed LLM performance over time, would feed back into refining the context — adding missing business definitions, clarifying ambiguous terms, or adding new golden-query examples.

**Evaluation**: `benchmark.py` runs a fixed set of questions with
known-correct answers (derived from manually-verified SQL), including a
"trap" question (USA has no menu data — a grounded agent should return
zero, not a fabricated number) and the adversarial guardrail test.

**Why Gemini instead of Snowflake Cortex**: `SNOWFLAKE.CORTEX.COMPLETE`
is not available on this trial account. The agent is built as a
standalone script rather than inside Snowflake (avoiding the need for
an External Access Integration for outbound API calls on a trial
account) — swapping in Cortex in production would be a contained change
to `generate_sql()`/`summarize_result()`.

## 7. CI/CD (Description)

Not built for this exercise, but the intended design:
- GitHub Actions workflow triggered on PR to `main`
- Lint/type-check Python (`ruff`/`mypy`)
- Validate SQL syntax (`sqlfluff` or a dry-run against a scratch schema
- On merge to `main`: deploy SQL changes to production schemas (via a
  migration tool or ordered script execution), redeploy the Streamlit
  app

## 8. Code Quality

`ingestion.py` uses structured `logging` (console + file), full type
hints throughout, a custom `IngestionError` for clear failure messages,
a `LoadStats` dataclass for structured run summaries, and an exit code
reflecting success/failure (CI-friendly). Per-file error isolation
means one unreadable file doesn't abort the whole run.

## 9. Scaling Considerations (Production vs. This POC)

- **Orchestration**: this pipeline is manually run scripts; production
  would use Dagster (or Airflow) — each SQL layer becomes an
  asset with declared dependencies, replacing manual run-ordering.
- **Incremental loading**: this was a one-time full load; production
  would use Snowflake streams + tasks (or dbt incremental models) so
  only new/changed source rows are reprocessed each run, not a full
  reload every time.
- **dbt**: the Silver/Gold SQL here is hand-written, sequential scripts.
  In production this maps directly onto dbt models — `ref()`-based
  dependency inference, built-in testing (the quarantine logic here
  could become dbt tests), and materialization strategy per model
  (table vs. incremental vs. view).
- **Compute sizing**: this ran entirely on a trial account's default
  warehouse; production would separate warehouses by workload (ETL vs.
  BI vs. ad hoc) so a heavy transformation run never competes with a
  dashboard user for compute.
