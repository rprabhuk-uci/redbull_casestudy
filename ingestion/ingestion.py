"""
Raw-layer ingestion script for the RBNA Data Engineer case study.

Loads outlet / portfolio / matching CSVs for USA, GBR, DEU into
all-VARCHAR RAW tables in Snowflake using write_pandas.

Setup:
    pip install "snowflake-connector-python[pandas]" pandas chardet python-dotenv

Configure connection via environment variables (don't hardcode secrets):
    export SNOWFLAKE_ACCOUNT=xxxxx.us-east-1
    export SNOWFLAKE_USER=your_user
    export SNOWFLAKE_PASSWORD=your_password
    export SNOWFLAKE_WAREHOUSE=COMPUTE_WH
    export SNOWFLAKE_DATABASE=RBNA_CASE_STUDY
    export SNOWFLAKE_SCHEMA=RAW
"""
from __future__ import annotations  # allows str | None style hints on Python < 3.10

import os
from dotenv import load_dotenv

load_dotenv()  # loads variables from a .env file in the current directory, if present

import glob
import csv
import chardet
import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas

# ---------------------------------------------------------------------------
# Config — adjust FILE_DIR and the market/file-type mapping to match your
# actual downloaded folder structure.
# ---------------------------------------------------------------------------
FILE_DIR = "/Users/rakshithaprabhukumar/Desktop/DE_case_study"  # folder where the raw CSVs live

MARKETS = ["USA", "GBR", "DEU"]
FILE_TYPES = ["outlet", "portfolio", "matching"]

# Default encodings to try, in order, per market. DEU is likely
# Windows-1252 / ISO-8859-1 given the special-character issue observed.
ENCODING_CANDIDATES = {
    "USA": ["utf-8"],
    "GBR": ["utf-8"],
    "DEU": ["utf-8", "windows-1252", "iso-8859-1"],
}


def detect_encoding(filepath: str) -> str:
    """Sniff a file's encoding from a sample of raw bytes."""
    with open(filepath, "rb") as f:
        raw = f.read(200_000)
    result = chardet.detect(raw)
    return result["encoding"] or "utf-8"


def read_csv_robust(filepath: str, market: str) -> pd.DataFrame:
    """Try candidate encodings for a market before falling back to detection.
    Uses the C engine with a hardcoded tab delimiter and standard CSV
    quoting (QUOTE_MINIMAL) — the source files use proper quoting to
    protect legitimate embedded newlines/tabs in free-text fields (e.g.
    multi-line item descriptions), so quotes must be respected, not
    disabled. Rare rows with a genuinely mismatched/stray quote can still
    cascade a column shift; those are caught downstream via post-load
    validation (see silver-layer quarantine logic) rather than here."""
    last_error = None
    for enc in ENCODING_CANDIDATES.get(market, ["utf-8"]):
        try:
            df = pd.read_csv(
                filepath,
                sep="\t",
                engine="c",
                encoding=enc,
                dtype=str,
                quoting=csv.QUOTE_MINIMAL,
                on_bad_lines="warn",
            )
            print(f"  loaded {filepath} with encoding={enc}")
            return df
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue

    # Fall back to sniffed encoding if none of the candidates worked
    detected = detect_encoding(filepath)
    print(f"  candidates failed, falling back to detected encoding={detected}")
    return pd.read_csv(
        filepath,
        sep="\t",
        engine="c",
        encoding=detected,
        dtype=str,
        quoting=csv.QUOTE_MINIMAL,
        on_bad_lines="warn",
    )


def find_files(market: str, file_type: str) -> list[str]:
    """Locate all CSVs for a market/file_type under the real folder layout:
    FILE_DIR/<file_type>/<YYYYMM>/<MARKET>/*.csv
    Handles multiple month folders (Q1 2024 = 202401/202402/202403) and
    multiple files per market (e.g. one per platform), if present."""
    pattern = os.path.join(FILE_DIR, file_type, "*", market, "*.csv")
    matches = sorted(glob.glob(pattern))
    if not matches:
        # Case-insensitive fallback in case folder casing differs (e.g. "usa" vs "USA")
        pattern_ci = os.path.join(FILE_DIR, file_type, "*", market.lower(), "*.csv")
        matches = sorted(glob.glob(pattern_ci))
    return matches


def get_snowflake_connection():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "RBNA_CASE_STUDY"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "RAW"),
    )


def load_file_type(conn, file_type: str):
    """Read every market's file for one file_type, tag with metadata,
    concat, and write to one raw table (e.g. RAW.OUTLET)."""
    frames = []
    for market in MARKETS:
        filepaths = find_files(market, file_type)
        if not filepaths:
            print(f"  [WARN] no {file_type} files found for market={market} — skipping "
                  f"(known gap: USA has no portfolio file, per the case study data)")
            continue

        for filepath in filepaths:
            df = read_csv_robust(filepath, market)

            # Load metadata — essential for later debugging / observability
            df["_source_market"] = market
            df["_source_file_name"] = os.path.basename(filepath)
            df["_source_month"] = os.path.basename(os.path.dirname(os.path.dirname(filepath)))
            df["_loaded_at"] = pd.Timestamp.utcnow()

            frames.append(df)

    if not frames:
        print(f"  [WARN] no files found at all for file_type={file_type}")
        return

    # Some markets may have slightly different columns — align on the union,
    # filling missing columns with NULL rather than dropping data.
    combined = pd.concat(frames, ignore_index=True, sort=False)

    # Uppercase all column names so Snowflake treats them as normal unquoted
    # identifiers — avoids case-sensitivity/quoting headaches in every query
    # that touches these tables later.
    combined.columns = [c.upper() for c in combined.columns]

    target_table = file_type.upper()
    print(f"  writing {len(combined)} rows to RAW.{target_table}")
    success, nchunks, nrows, _ = write_pandas(
        conn,
        combined,
        target_table,
        auto_create_table=True,
        overwrite=True,  # re-runnable for iteration during dev; switch to False + dedup logic for prod
    )
    print(f"  done: success={success}, rows_written={nrows}")


def main():
    conn = get_snowflake_connection()
    try:
        for file_type in FILE_TYPES:
            print(f"\n=== Loading {file_type} ===")
            load_file_type(conn, file_type)
    finally:
        conn.close()


if __name__ == "__main__":
    main()