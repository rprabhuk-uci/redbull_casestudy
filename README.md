# RBNA Data Engineer Case Study

A Snowflake-based analytics platform for Red Bull North America's online
food-delivery presence across USA, GBR, and DEU — covering ingestion,
dimensional modeling, data quality validation, role-based access control,
interactive dashboards, and a GenAI natural-language query agent.

See [`docs/System_design.md`](docs/System_design.md) for the full
architecture writeup, data quality findings, and design decisions.

## Repo Structure

```
├── ingestion/
│   └── ingestion.py            # raw CSVs -> Snowflake RAW layer
├── transformations/
│   ├── silver_layer.sql        # validation, quarantine, cleaning
│   └── gold_layer.sql          # dimensional model (fact/dim)
├── views/
│   └── views.sql               # business-question views on the Gold model
├── rbac.sql                     # roles, row access policy, grants
├── streamlit/
│   └── app.py                   # Streamlit-in-Snowflake dashboard (2 tabs)
├── gen_ai/
│   ├── context.py               # schema/business-logic grounding for the agent
│   ├── golden_queries.sql       # reference queries — few-shot examples + eval ground truth
│   ├── agent.py                  # main script: NL question -> SQL -> result -> summary
│   └── benchmark.py             # accuracy measurement against known-correct answers
└── docs/
    └── System_design.md         # architecture, findings, design decisions
```

## Setup

### 1. Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root (see `.env.example`):

```
SNOWFLAKE_ACCOUNT=your_account_identifier
SNOWFLAKE_USER=your_username
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=RBNA_CASE_STUDY
SNOWFLAKE_SCHEMA=RAW
GEMINI_API_KEY=your_gemini_key
```

### 2. Snowflake objects

```sql
CREATE DATABASE IF NOT EXISTS RBNA_CASE_STUDY;
CREATE SCHEMA IF NOT EXISTS RBNA_CASE_STUDY.RAW;
```

### 3. Run the pipeline, in order

```bash
python ingestion/ingestion.py          # loads RAW.OUTLET / PORTFOLIO / MATCHING
```
Then, in a Snowsight worksheet (or via SnowSQL), run in order:
```
transformations/silver_layer.sql
transformations/gold_layer.sql
views/views.sql
rbac.sql
```

### 4. Dashboards

In Snowsight: **Projects → Streamlit → + Streamlit App**, paste in
`streamlit/app.py`.

### 5. GenAI agent

```bash
pip install google-genai matplotlib
python gen_ai/agent.py "What percentage of listings in GBR serve Red Bull?"
# or interactively:
python gen_ai/agent.py

# run the accuracy benchmark:
python gen_ai/benchmark.py
```


## RBAC

Four roles (`ANALYST_USA`, `ANALYST_GBR`, `ANALYST_DEU`, `HQ_ANALYST`)
with a Snowflake row access policy filtering Gold-layer tables by
market. Demonstrated via SQL role-switching in a worksheet — see
`rbac.sql` for the verification queries.

## GenAI Agent

A grounded, guardrailed text-to-SQL agent scoped entirely to the Gold
schema — see `docs/System_design.md` §6 for the full design (cost
management, determinism, human-in-the-loop, evaluation methodology).