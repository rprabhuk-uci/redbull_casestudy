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

import csv
import glob
import logging
import os
import sys
from dataclasses import dataclass, field

import chardet
import pandas as pd
import snowflake.connector
from dotenv import load_dotenv
from snowflake.connector import SnowflakeConnection
from snowflake.connector.pandas_tools import write_pandas

load_dotenv()  # loads variables from a .env file in the current directory, if present

# ---------------------------------------------------------------------------
# Logging — structured, timestamped, single source of truth for run history.
# Writes to both console and a log file so a run's output survives after the
# terminal session ends (useful for counting skipped rows, debugging later).
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("ingestion.log"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — adjust FILE_DIR and the market/file-type mapping to match your
# actual downloaded folder structure.
# ---------------------------------------------------------------------------
FILE_DIR = "/Users/rakshithaprabhukumar/Desktop/DE_case_study"  # folder where the raw CSVs live

MARKETS: list[str] = ["USA", "GBR", "DEU"]
FILE_TYPES: list[str] = ["outlet", "portfolio", "matching"]

# Known data gap, confirmed during exploration: USA has no portfolio file.
# Documented here (not just as a runtime warning) so it's visible to anyone
# reading the code, not just anyone who happens to read the logs.
KNOWN_MISSING_FILES: dict[tuple[str, str], str] = {
    ("USA", "portfolio"): "No portfolio (menu-level) data exists for USA in the "
                           "source dataset — confirmed via manual folder inspection, "
                           "not a pipeline bug.",
}

# Default encodings to try, in order, per market. DEU needed
# Windows-1252 / ISO-8859-1 fallback due to German special characters.
ENCODING_CANDIDATES: dict[str, list[str]] = {
    "USA": ["utf-8"],
    "GBR": ["utf-8"],
    "DEU": ["utf-8", "windows-1252", "iso-8859-1"],
}


@dataclass
class LoadStats:
    """Tracks per-file-type load results for a clean end-of-run summary."""
    file_type: str
    files_loaded: int = 0
    rows_written: int = 0
    markets_missing: list[str] = field(default_factory=list)


class IngestionError(Exception):
    """Raised for ingestion failures that shouldn't be silently swallowed —
    e.g. every encoding candidate failing, or a Snowflake write failing."""


def detect_encoding(filepath: str) -> str:
    """Sniff a file's encoding from a sample of raw bytes."""
    with open(filepath, "rb") as f:
        raw = f.read(200_000)
    result = chardet.detect(raw)
    detected = result["encoding"] or "utf-8"
    logger.debug("Detected encoding=%s for %s (confidence=%.2f)",
                 detected, filepath, result.get("confidence", 0.0))
    return detected


def read_csv_robust(filepath: str, market: str) -> pd.DataFrame:
    """Try candidate encodings for a market before falling back to detection.
    Uses the C engine with a hardcoded tab delimiter and standard CSV
    quoting (QUOTE_MINIMAL) — the source files use proper quoting to
    protect legitimate embedded newlines/tabs in free-text fields (e.g.
    multi-line item descriptions), so quotes must be respected, not
    disabled. Rare rows with a genuinely mismatched/stray quote can still
    cascade a column shift; those are caught downstream via post-load
    validation (see silver-layer quarantine logic) rather than here.

    Raises:
        IngestionError: if the file cannot be read under any encoding.
    """
    read_kwargs = dict(
        sep="\t", engine="c", dtype=str,
        quoting=csv.QUOTE_MINIMAL, on_bad_lines="warn",
    )

    for enc in ENCODING_CANDIDATES.get(market, ["utf-8"]):
        try:
            df = pd.read_csv(filepath, encoding=enc, **read_kwargs)
            logger.info("Loaded %s (encoding=%s, rows=%d)", filepath, enc, len(df))
            return df
        except (UnicodeDecodeError, UnicodeError) as e:
            logger.warning("Encoding %s failed for %s: %s", enc, filepath, e)
            continue

    # Fall back to sniffed encoding if none of the candidates worked
    try:
        detected = detect_encoding(filepath)
        df = pd.read_csv(filepath, encoding=detected, **read_kwargs)
        logger.info("Loaded %s via detected encoding=%s (rows=%d)", filepath, detected, len(df))
        return df
    except Exception as e:
        raise IngestionError(f"Could not read {filepath} under any encoding: {e}") from e


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


def get_snowflake_connection() -> SnowflakeConnection:
    """Opens a Snowflake connection from environment variables.

    Raises:
        IngestionError: if required env vars are missing, with a clear
        message rather than a raw KeyError.
    """
    required = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"]
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        raise IngestionError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            f"Set them in a .env file or your shell — see module docstring."
        )

    try:
        return snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            password=os.environ["SNOWFLAKE_PASSWORD"],
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
            database=os.environ.get("SNOWFLAKE_DATABASE", "RBNA_CASE_STUDY"),
            schema=os.environ.get("SNOWFLAKE_SCHEMA", "RAW"),
        )
    except snowflake.connector.errors.Error as e:
        raise IngestionError(f"Failed to connect to Snowflake: {e}") from e


def load_file_type(conn: SnowflakeConnection, file_type: str) -> LoadStats:
    """Read every market's file for one file_type, tag with metadata,
    concat, and write to one raw table (e.g. RAW.OUTLET).

    Raises:
        IngestionError: if the Snowflake write itself fails.
    """
    stats = LoadStats(file_type=file_type)
    frames: list[pd.DataFrame] = []

    for market in MARKETS:
        filepaths = find_files(market, file_type)
        if not filepaths:
            reason = KNOWN_MISSING_FILES.get(
                (market, file_type), "no files found under expected folder path"
            )
            logger.warning("No %s files for market=%s — skipping (%s)", file_type, market, reason)
            stats.markets_missing.append(market)
            continue

        for filepath in filepaths:
            try:
                df = read_csv_robust(filepath, market)
            except IngestionError:
                logger.error("Skipping unreadable file: %s", filepath, exc_info=True)
                continue

            # Load metadata — essential for later debugging / observability
            df["_source_market"] = market
            df["_source_file_name"] = os.path.basename(filepath)
            df["_source_month"] = os.path.basename(os.path.dirname(os.path.dirname(filepath)))
            df["_loaded_at"] = pd.Timestamp.utcnow()

            frames.append(df)
            stats.files_loaded += 1

    if not frames:
        logger.error("No files found at all for file_type=%s — nothing to load", file_type)
        return stats

    # Some markets may have slightly different columns — align on the union,
    # filling missing columns with NULL rather than dropping data.
    combined = pd.concat(frames, ignore_index=True, sort=False)

    # Uppercase all column names so Snowflake treats them as normal unquoted
    # identifiers — avoids case-sensitivity/quoting headaches in every query
    # that touches these tables later.
    combined.columns = [c.upper() for c in combined.columns]

    target_table = file_type.upper()
    logger.info("Writing %d rows to RAW.%s", len(combined), target_table)

    try:
        success, _nchunks, nrows, _ = write_pandas(
            conn,
            combined,
            target_table,
            auto_create_table=True,
            overwrite=True,  # re-runnable for iteration during dev; switch to False + dedup logic for prod
        )
    except Exception as e:
        raise IngestionError(f"write_pandas failed for RAW.{target_table}: {e}") from e

    if not success:
        raise IngestionError(f"write_pandas reported failure for RAW.{target_table}")

    stats.rows_written = nrows
    logger.info("Done: RAW.%s — %d rows written", target_table, nrows)
    return stats


def main() -> int:
    """Runs the full ingestion. Returns a process exit code (0 = success,
    1 = one or more file types failed) so this is CI/CD-friendly."""
    conn = get_snowflake_connection()
    all_stats: list[LoadStats] = []
    had_failure = False

    try:
        for file_type in FILE_TYPES:
            logger.info("=== Loading %s ===", file_type)
            try:
                stats = load_file_type(conn, file_type)
                all_stats.append(stats)
            except IngestionError:
                logger.error("Failed to load file_type=%s", file_type, exc_info=True)
                had_failure = True
    finally:
        conn.close()

    logger.info("=== Run summary ===")
    for s in all_stats:
        logger.info(
            "%-12s files_loaded=%-3d rows_written=%-10d missing_markets=%s",
            s.file_type, s.files_loaded, s.rows_written, s.markets_missing or "none",
        )

    return 1 if had_failure else 0


if __name__ == "__main__":
    sys.exit(main())