-- ============================================================================
-- RBAC — market-based row access control
-- Market analysts see only their market's rows; HQ sees all markets.
-- Run after gold_tables.sql and gold_views.sql.
-- ============================================================================

USE DATABASE RBNA_CASE_STUDY;

-- ----------------------------------------------------------------------------
-- 1. ROLES
-- One role per market analyst persona, plus one HQ (sees-everything) role.
-- Mapping a user to their market happens via role grant, not a lookup table —
-- simple and sufficient for this exercise; a real production setup might
-- instead use a mapping table + a more dynamic policy (see design doc).
-- ----------------------------------------------------------------------------
CREATE ROLE IF NOT EXISTS ANALYST_USA;
CREATE ROLE IF NOT EXISTS ANALYST_GBR;
CREATE ROLE IF NOT EXISTS ANALYST_DEU;
CREATE ROLE IF NOT EXISTS HQ_ANALYST;

-- Grant these roles to your own user for demo purposes — replace <YOUR_USER>
-- with your actual Snowflake username so you can switch roles live to prove
-- the policy works.
-- GRANT ROLE ANALYST_USA TO USER <YOUR_USER>;
-- GRANT ROLE ANALYST_GBR TO USER <YOUR_USER>;
-- GRANT ROLE HQ_ANALYST  TO USER <YOUR_USER>;

-- ----------------------------------------------------------------------------
-- 2. BASE PRIVILEGES
-- All analyst roles need USAGE on the warehouse/database/schema and SELECT
-- on Gold objects — the row access policy (below) restricts WHICH ROWS they
-- see, not whether they can query the tables at all.
-- ----------------------------------------------------------------------------
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE ANALYST_USA;
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE ANALYST_GBR;
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE ANALYST_DEU;
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE HQ_ANALYST;

GRANT USAGE ON DATABASE RBNA_CASE_STUDY TO ROLE ANALYST_USA;
GRANT USAGE ON DATABASE RBNA_CASE_STUDY TO ROLE ANALYST_GBR;
GRANT USAGE ON DATABASE RBNA_CASE_STUDY TO ROLE ANALYST_DEU;
GRANT USAGE ON DATABASE RBNA_CASE_STUDY TO ROLE HQ_ANALYST;

GRANT USAGE ON SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_USA;
GRANT USAGE ON SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_GBR;
GRANT USAGE ON SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_DEU;
GRANT USAGE ON SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE HQ_ANALYST;

GRANT SELECT ON ALL TABLES IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_USA;
GRANT SELECT ON ALL TABLES IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_GBR;
GRANT SELECT ON ALL TABLES IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_DEU;
GRANT SELECT ON ALL TABLES IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE HQ_ANALYST;

GRANT SELECT ON ALL VIEWS IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_USA;
GRANT SELECT ON ALL VIEWS IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_GBR;
GRANT SELECT ON ALL VIEWS IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_DEU;
GRANT SELECT ON ALL VIEWS IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE HQ_ANALYST;

-- Ensure future tables/views in GOLD also get these grants automatically,
-- so the policy doesn't silently stop covering new objects.
GRANT SELECT ON FUTURE TABLES IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_USA;
GRANT SELECT ON FUTURE TABLES IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_GBR;
GRANT SELECT ON FUTURE TABLES IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_DEU;
GRANT SELECT ON FUTURE TABLES IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE HQ_ANALYST;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_USA;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_GBR;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE ANALYST_DEU;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA RBNA_CASE_STUDY.GOLD TO ROLE HQ_ANALYST;

-- ----------------------------------------------------------------------------
-- 3. ROW ACCESS POLICY
-- CURRENT_ROLE() drives the filter: HQ_ANALYST sees everything; each
-- ANALYST_<MARKET> role sees only rows matching their market. Any other
-- role (e.g. ACCOUNTADMIN used for admin tasks) is denied by default —
-- explicit allow-list, not an implicit fallback-allow.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE ROW ACCESS POLICY RBNA_CASE_STUDY.GOLD.MARKET_ACCESS_POLICY
  AS (market_col VARCHAR) RETURNS BOOLEAN ->
    CURRENT_ROLE() = 'HQ_ANALYST'
    OR (CURRENT_ROLE() = 'ANALYST_USA' AND market_col = 'USA')
    OR (CURRENT_ROLE() = 'ANALYST_GBR' AND market_col = 'GBR')
    OR (CURRENT_ROLE() = 'ANALYST_DEU' AND market_col = 'DEU');

-- ----------------------------------------------------------------------------
-- 4. APPLY POLICY to every Gold object that has a MARKET column.
-- Note: a row access policy applies to the base table; a view built on a
-- policy-protected table inherits the filtering automatically — so applying
-- it to DIM_LOCATION, DIM_LISTING, FACT_MENU_ITEM covers VW_RED_BULL_AVAILABILITY
-- and VW_COMPETITOR_LANDSCAPE too, without needing to apply it to the views
-- themselves.
-- ----------------------------------------------------------------------------
ALTER TABLE RBNA_CASE_STUDY.GOLD.DIM_LOCATION
  ADD ROW ACCESS POLICY RBNA_CASE_STUDY.GOLD.MARKET_ACCESS_POLICY ON (MARKET);

ALTER TABLE RBNA_CASE_STUDY.GOLD.DIM_LISTING
  ADD ROW ACCESS POLICY RBNA_CASE_STUDY.GOLD.MARKET_ACCESS_POLICY ON (MARKET);

ALTER TABLE RBNA_CASE_STUDY.GOLD.FACT_MENU_ITEM
  ADD ROW ACCESS POLICY RBNA_CASE_STUDY.GOLD.MARKET_ACCESS_POLICY ON (MARKET);

ALTER TABLE RBNA_CASE_STUDY.GOLD.DIM_MARKET
  ADD ROW ACCESS POLICY RBNA_CASE_STUDY.GOLD.MARKET_ACCESS_POLICY ON (MARKET);

-- ----------------------------------------------------------------------------
-- 5. DEMO / VERIFICATION — run these as different roles to prove it works.
-- In Snowsight: use the role-switcher (top right) or `USE ROLE <role>;`
-- ----------------------------------------------------------------------------
-- As ANALYST_GBR — should ONLY return GBR rows:
--   USE ROLE ANALYST_GBR;
--   SELECT DISTINCT MARKET FROM RBNA_CASE_STUDY.GOLD.DIM_LOCATION;
--
-- As HQ_ANALYST — should return all 3 markets:
--   USE ROLE HQ_ANALYST;
--   SELECT DISTINCT MARKET FROM RBNA_CASE_STUDY.GOLD.DIM_LOCATION;
--
-- As ANALYST_USA — should return zero rows from FACT_MENU_ITEM (since USA
-- has no portfolio data at all) but should still see USA rows in DIM_LOCATION
-- and DIM_LISTING — a good live-demo moment tying RBAC back to the USA data
-- gap you found earlier.
--   USE ROLE ANALYST_USA;
--   SELECT COUNT(*) FROM RBNA_CASE_STUDY.GOLD.FACT_MENU_ITEM;
--   SELECT COUNT(*) FROM RBNA_CASE_STUDY.GOLD.DIM_LISTING;