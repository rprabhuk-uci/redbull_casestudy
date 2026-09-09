-- ============================================================================
-- GOLD LAYER — dimensional model (star schema)
-- Run in RBNA_CASE_STUDY database, after SILVER layer is built.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS RBNA_CASE_STUDY.GOLD;
USE SCHEMA RBNA_CASE_STUDY.GOLD;

-- ----------------------------------------------------------------------------
-- DIM_MARKET — grain: MARKET
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE GOLD.DIM_MARKET AS
SELECT
    _SOURCE_MARKET AS MARKET,  -- reliable, folder-derived tag; raw MARKET field is
                                 -- inconsistent (US/USA, DE/DEU, UK/null for GBR)
    ANY_VALUE(LOCAL_CURRENCY) AS LOCAL_CURRENCY
FROM SILVER.OUTLET
GROUP BY _SOURCE_MARKET;

-- ----------------------------------------------------------------------------
-- DIM_LOCATION — grain: ID_OUTLET (physical kitchen/address)
-- Derived NUM_LISTINGS surfaces the ghost-kitchen hub pattern directly
-- on the dimension, so it's queryable without recomputing the join.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE GOLD.DIM_LOCATION AS
SELECT
    o.ID_OUTLET,
    o.STREET_ADDRESS,
    o.POSTAL_CODE,
    o.ADDRESS_LOCALITY,
    o.ADDRESS_COUNTRY,
    o.LATITUDE,
    o.LONGITUDE,
    o.MARKET,  -- reliable, folder-derived (_SOURCE_MARKET); raw MARKET field kept separately below
    listings.NUM_LISTINGS,
    listings.NUM_DISTINCT_BRANDS,
    CASE WHEN listings.NUM_DISTINCT_BRANDS > 1 THEN TRUE ELSE FALSE END AS IS_MULTI_BRAND_HUB
FROM (
    SELECT ID_OUTLET, ANY_VALUE(STREET_ADDRESS) AS STREET_ADDRESS,
           ANY_VALUE(POSTAL_CODE) AS POSTAL_CODE, ANY_VALUE(ADDRESS_LOCALITY) AS ADDRESS_LOCALITY,
           ANY_VALUE(ADDRESS_COUNTRY) AS ADDRESS_COUNTRY, ANY_VALUE(LATITUDE) AS LATITUDE,
           ANY_VALUE(LONGITUDE) AS LONGITUDE, ANY_VALUE(_SOURCE_MARKET) AS MARKET
    FROM SILVER.OUTLET
    GROUP BY ID_OUTLET
) o
JOIN (
    -- NUM_LISTINGS: total platform listings at this location (cross-platform presence).
    -- NUM_DISTINCT_BRANDS: resolves each listing to a real-world business entity using
    -- MATCHING's place_id, trusted when EITHER similarity score is high-confidence
    -- (>= 0.95) — not both. Requiring both was too strict: e.g. two listings sharing
    -- the same place_id with a perfect 1.0 name match but 0.88 address match (address
    -- string formatting differences) were wrongly split into separate entities under
    -- an AND requirement. A single strong signal, combined with place_id agreement,
    -- is sufficient confidence. Below threshold on both, the listing is treated as
    -- its own distinct entity rather than risking a false merge.
    SELECT o.ID_OUTLET,
           COUNT(DISTINCT o.ID_EXT_LINK) AS NUM_LISTINGS,
           COUNT(DISTINCT
               CASE WHEN (m.SIMILARITY_SCORE_NAME >= 0.95
                       OR m.SIMILARITY_SCORE_ADDRESS >= 0.95)
                     AND m.PLACE_ID IS NOT NULL
                    THEN m.PLACE_ID
                    ELSE 'UNRESOLVED_' || o.ID_EXT_LINK::VARCHAR
               END
           ) AS NUM_DISTINCT_BRANDS
    FROM SILVER.OUTLET o
    LEFT JOIN SILVER.MATCHING m ON o.ID_EXT_LINK = m.ID_EXT_LINK
    GROUP BY o.ID_OUTLET
) listings ON o.ID_OUTLET = listings.ID_OUTLET;

-- ----------------------------------------------------------------------------
-- DIM_LISTING — grain: ID_EXT_LINK (the actual storefront/virtual brand)
-- Joins in chain-resolution attributes from MATCHING (1:1 on ID_EXT_LINK —
-- no bridge table needed, since matching's grain matches listing's grain).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE GOLD.DIM_LISTING AS
SELECT
    o.ID_EXT_LINK,
    o.ID_OUTLET,
    o.ID_PLATFORM,
    o.PLATFORM_NAME,
    o.NAME AS LISTING_NAME,
    o.CATEGORY,
    o.CUISINE,
    o._SOURCE_MARKET AS MARKET,  -- reliable, folder-derived (raw MARKET field is inconsistent)
    o.NUM_RATINGS,
    o.AVERAGE_RATING,
    o.AVERAGE_COST,
    o.MIN_ORDER_AMOUNT,
    o.SEGMENT_TYPE,
    m.MERGED_CHAIN_NAME,
    m.IS_CHAIN,
    m.SIMILARITY_SCORE_NAME,
    m.SIMILARITY_SCORE_ADDRESS,
    m.SERVES_RED_BULL,
    m.SUGAR_FREE_AVAILABLE,
    m.ORGANICS_AVAILABLE,
    m.EDITIONS_AVAILABLE
FROM SILVER.OUTLET o
LEFT JOIN SILVER.MATCHING m ON o.ID_EXT_LINK = m.ID_EXT_LINK;

-- ----------------------------------------------------------------------------
-- DIM_PRODUCT — grain: ID_DRINK
-- One row per distinct product; picks first-seen attribute values where
-- the same ID_DRINK has minor variation across listings.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE GOLD.DIM_PRODUCT AS
SELECT
    ID_DRINK,
    ANY_VALUE(ITEM_MANUFACTURER) AS ITEM_MANUFACTURER,
    ANY_VALUE(ITEM_BRAND) AS ITEM_BRAND,
    ANY_VALUE(ITEM_SUBBRAND) AS ITEM_SUBBRAND,
    ANY_VALUE(ITEM_DRINK_CATEGORY_1) AS ITEM_DRINK_CATEGORY_1,
    ANY_VALUE(ITEM_DRINK_CATEGORY_2) AS ITEM_DRINK_CATEGORY_2,
    CASE WHEN ANY_VALUE(ITEM_BRAND) ILIKE '%red bull%'
           OR ANY_VALUE(ITEM_MANUFACTURER) ILIKE '%red bull%'
         THEN TRUE ELSE FALSE END AS IS_RED_BULL_PRODUCT
FROM SILVER.PORTFOLIO
WHERE ID_DRINK IS NOT NULL
GROUP BY ID_DRINK;

-- ----------------------------------------------------------------------------
-- FACT_MENU_ITEM — grain: (ID_EXT_LINK, ID_BEVERAGE)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE GOLD.FACT_MENU_ITEM AS
SELECT
    p.ID_BEVERAGE,
    p.ID_EXT_LINK,
    p.ID_DRINK,
    p.ITEM_POSITION,
    p.ITEM_NAME,
    p.ITEM_VOLUME,
    p.ITEM_PRICE,
    pr.IS_RED_BULL_PRODUCT,
    l.ID_OUTLET,
    l.MARKET  -- via DIM_LISTING -> DIM_LOCATION chain, denormalized here for query convenience
FROM SILVER.PORTFOLIO p
LEFT JOIN GOLD.DIM_PRODUCT pr ON p.ID_DRINK = pr.ID_DRINK
LEFT JOIN GOLD.DIM_LISTING l ON p.ID_EXT_LINK = l.ID_EXT_LINK;

-- ----------------------------------------------------------------------------
-- Sanity checks
-- ----------------------------------------------------------------------------
SELECT 'DIM_MARKET' AS tbl, COUNT(*) FROM GOLD.DIM_MARKET
UNION ALL SELECT 'DIM_LOCATION', COUNT(*) FROM GOLD.DIM_LOCATION
UNION ALL SELECT 'DIM_LISTING', COUNT(*) FROM GOLD.DIM_LISTING
UNION ALL SELECT 'DIM_PRODUCT', COUNT(*) FROM GOLD.DIM_PRODUCT
UNION ALL SELECT 'FACT_MENU_ITEM', COUNT(*) FROM GOLD.FACT_MENU_ITEM;

-- orphan check — every fact row should resolve to a real listing
SELECT COUNT(*) AS fact_rows_missing_listing
FROM GOLD.FACT_MENU_ITEM f
LEFT JOIN GOLD.DIM_LISTING l ON f.ID_EXT_LINK = l.ID_EXT_LINK
WHERE l.ID_EXT_LINK IS NULL;