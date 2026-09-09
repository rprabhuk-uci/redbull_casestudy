-- ============================================================================
-- GOLD LAYER — VIEWS
-- Run after gold_tables.sql. Presentable business-question layers on top
-- of the Gold dimensional model.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- VW_RED_BULL_AVAILABILITY — Red Bull presence by market, drillable to state
-- (ADDRESS_LOCALITY in this dataset holds state/region, e.g. "Florida",
-- confirmed from source sample data — not city, despite the field name overlap
-- with CITY elsewhere in outlet).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW GOLD.VW_RED_BULL_AVAILABILITY AS
SELECT
    loc.MARKET,
    loc.ADDRESS_LOCALITY AS STATE,
    COUNT(DISTINCT lst.ID_EXT_LINK) AS TOTAL_LISTINGS,
    COUNT(DISTINCT CASE WHEN lst.SERVES_RED_BULL = TRUE THEN lst.ID_EXT_LINK END) AS LISTINGS_SERVING_RED_BULL,
    ROUND(
        COUNT(DISTINCT CASE WHEN lst.SERVES_RED_BULL = TRUE THEN lst.ID_EXT_LINK END)
        / NULLIF(COUNT(DISTINCT lst.ID_EXT_LINK), 0) * 100, 1
    ) AS PCT_SERVING_RED_BULL
FROM GOLD.DIM_LISTING lst
JOIN GOLD.DIM_LOCATION loc ON lst.ID_OUTLET = loc.ID_OUTLET
GROUP BY loc.MARKET, loc.ADDRESS_LOCALITY;

-- Market-level rollup (no state drill-down) for the top-line summary number
CREATE OR REPLACE VIEW GOLD.VW_RED_BULL_AVAILABILITY_BY_MARKET AS
SELECT
    MARKET,
    SUM(TOTAL_LISTINGS) AS TOTAL_LISTINGS,
    SUM(LISTINGS_SERVING_RED_BULL) AS LISTINGS_SERVING_RED_BULL,
    ROUND(SUM(LISTINGS_SERVING_RED_BULL) / NULLIF(SUM(TOTAL_LISTINGS), 0) * 100, 1) AS PCT_SERVING_RED_BULL
FROM GOLD.VW_RED_BULL_AVAILABILITY
GROUP BY MARKET;

-- ----------------------------------------------------------------------------
-- VW_COMPETITOR_LANDSCAPE — Red Bull vs. other brands, by category and market
-- Grain: (MARKET, ITEM_DRINK_CATEGORY_1, ITEM_BRAND) — one row per brand's
-- footprint within a drink category in a market.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW GOLD.VW_COMPETITOR_LANDSCAPE AS
SELECT
    f.MARKET,
    p.ITEM_DRINK_CATEGORY_1,
    p.ITEM_DRINK_CATEGORY_2,
    p.ITEM_BRAND,
    p.ITEM_MANUFACTURER,
    p.IS_RED_BULL_PRODUCT,
    COUNT(DISTINCT f.ID_EXT_LINK) AS NUM_LISTINGS_CARRYING_BRAND,
    COUNT(*) AS NUM_MENU_ITEMS,
    ROUND(AVG(f.ITEM_PRICE), 2) AS AVG_PRICE
FROM GOLD.FACT_MENU_ITEM f
JOIN GOLD.DIM_PRODUCT p ON f.ID_DRINK = p.ID_DRINK
WHERE p.ITEM_DRINK_CATEGORY_1 IS NOT NULL  -- exclude items with no clean category
GROUP BY f.MARKET, p.ITEM_DRINK_CATEGORY_1, p.ITEM_DRINK_CATEGORY_2, p.ITEM_BRAND,
         p.ITEM_MANUFACTURER, p.IS_RED_BULL_PRODUCT;

-- Brand share within a category/market — % of that category's listings carrying each brand
CREATE OR REPLACE VIEW GOLD.VW_BRAND_SHARE_BY_CATEGORY AS
SELECT
    MARKET,
    ITEM_DRINK_CATEGORY_1,
    ITEM_BRAND,
    IS_RED_BULL_PRODUCT,
    NUM_LISTINGS_CARRYING_BRAND,
    ROUND(
        NUM_LISTINGS_CARRYING_BRAND
        / NULLIF(SUM(NUM_LISTINGS_CARRYING_BRAND) OVER (PARTITION BY MARKET, ITEM_DRINK_CATEGORY_1), 0)
        * 100, 1
    ) AS PCT_SHARE_OF_CATEGORY
FROM GOLD.VW_COMPETITOR_LANDSCAPE;