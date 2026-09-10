-- ============================================================================
-- GOLDEN QUERIES — pre-validated, known-correct SQL against the Gold layer.
--
-- Purpose (two uses):
-- 1. Few-shot examples given to the LLM in agent.py, so it sees real,
--    correct query patterns for this exact schema rather than generalizing
--    from generic SQL knowledge alone.
-- 2. Manually verified ground truth — the actual result of each query here
--    is what benchmark.py checks the agent's answers against.
-- ============================================================================

-- Q: What percentage of listings in each market serve Red Bull?
SELECT MARKET, PCT_SERVING_RED_BULL
FROM GOLD.VW_RED_BULL_AVAILABILITY_BY_MARKET
ORDER BY PCT_SERVING_RED_BULL DESC;

-- Q: Which state in the USA has the highest Red Bull availability?
SELECT STATE, PCT_SERVING_RED_BULL, TOTAL_LISTINGS
FROM GOLD.VW_RED_BULL_AVAILABILITY
WHERE MARKET = 'USA' AND TOTAL_LISTINGS >= 10
ORDER BY PCT_SERVING_RED_BULL DESC
LIMIT 1;

-- Q: How many locations are multi-brand ghost kitchen hubs, by market?
SELECT MARKET, COUNT(*) AS num_hub_locations
FROM GOLD.DIM_LOCATION
WHERE IS_MULTI_BRAND_HUB = TRUE
GROUP BY MARKET;

-- Q: What is Red Bull's share of the Soft Drink category in GBR?
SELECT ITEM_BRAND, PCT_SHARE_OF_CATEGORY
FROM GOLD.VW_BRAND_SHARE_BY_CATEGORY
WHERE MARKET = 'GBR' AND ITEM_DRINK_CATEGORY_1 = 'Soft Drink'
  AND IS_RED_BULL_PRODUCT = TRUE;

-- Q: How many menu items exist for the USA market? (should be 0 — known gap)
SELECT COUNT(*) AS num_items
FROM GOLD.FACT_MENU_ITEM
WHERE MARKET = 'USA';

-- Q: What's the average menu item price in DEU?
SELECT ROUND(AVG(ITEM_PRICE), 2) AS avg_price
FROM GOLD.FACT_MENU_ITEM
WHERE MARKET = 'DEU';

-- Q: Top 5 brands by number of listings carrying them, in the Soft Drink
-- category, across all markets combined.
SELECT ITEM_BRAND, SUM(NUM_LISTINGS_CARRYING_BRAND) AS total_listings
FROM GOLD.VW_COMPETITOR_LANDSCAPE
WHERE ITEM_DRINK_CATEGORY_1 = 'Soft Drink'
GROUP BY ITEM_BRAND
ORDER BY total_listings DESC
LIMIT 5;

-- Q: How many distinct platforms are represented in each market?
SELECT MARKET, COUNT(DISTINCT PLATFORM_NAME) AS num_platforms
FROM GOLD.DIM_LISTING
GROUP BY MARKET;