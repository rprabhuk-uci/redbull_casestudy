import streamlit as st
import pandas as pd
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="RBNA Insights", layout="wide")

session = get_active_session()

st.title("Red Bull North America — Delivery Platform Insights")

tab1, tab2 = st.tabs(["🥤 Red Bull Availability", "⚔️ Competitor Landscape"])

# ============================================================================
# TAB 1 — Red Bull availability by market, drillable by state
# ============================================================================
with tab1:
    st.caption("Delivery-platform listing coverage across USA, GBR, DEU — drillable by state/region")

    market_df = session.sql(
        "SELECT * FROM GOLD.VW_RED_BULL_AVAILABILITY_BY_MARKET ORDER BY PCT_SERVING_RED_BULL DESC"
    ).to_pandas()

    col1, col2, col3 = st.columns(3)
    for col, (_, row) in zip([col1, col2, col3], market_df.iterrows()):
        col.metric(
            label=row["MARKET"],
            value=f'{row["PCT_SERVING_RED_BULL"]}%',
            help=f'{row["LISTINGS_SERVING_RED_BULL"]:,} of {row["TOTAL_LISTINGS"]:,} listings',
        )

    st.bar_chart(market_df.set_index("MARKET")["PCT_SERVING_RED_BULL"])

    st.divider()
    st.subheader("Drill down by state / region")

    selected_market = st.selectbox("Market", market_df["MARKET"].tolist(), key="avail_market")

    state_df = session.sql(f"""
        SELECT STATE, TOTAL_LISTINGS, LISTINGS_SERVING_RED_BULL, PCT_SERVING_RED_BULL
        FROM GOLD.VW_RED_BULL_AVAILABILITY
        WHERE MARKET = '{selected_market}'
          AND TOTAL_LISTINGS >= 10
        ORDER BY PCT_SERVING_RED_BULL DESC
    """).to_pandas()

    st.bar_chart(state_df.set_index("STATE")["PCT_SERVING_RED_BULL"])

    st.dataframe(
        state_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "PCT_SERVING_RED_BULL": st.column_config.ProgressColumn(
                "% Serving Red Bull", min_value=0, max_value=100, format="%.1f%%"
            )
        },
    )

    st.caption(
        "Note: 'state/region' is derived from ADDRESS_LOCALITY in source data — "
        "reliably a US state for USA; a broader region (e.g. England) for GBR."
    )

# ============================================================================
# TAB 2 — Competitor landscape: Red Bull vs. other brands, by category/market
# ============================================================================
with tab2:
    st.caption(
        "Brand share within each drink category — Red Bull vs. competitors. "
        "Note: USA has no menu-item (portfolio) data in this dataset, so USA "
        "is not available here — see data quality notes."
    )

    categories = session.sql(
        "SELECT DISTINCT ITEM_DRINK_CATEGORY_1 FROM GOLD.VW_COMPETITOR_LANDSCAPE "
        "WHERE ITEM_DRINK_CATEGORY_1 IS NOT NULL ORDER BY 1"
    ).to_pandas()["ITEM_DRINK_CATEGORY_1"].tolist()

    markets = session.sql(
        "SELECT DISTINCT MARKET FROM GOLD.VW_COMPETITOR_LANDSCAPE ORDER BY 1"
    ).to_pandas()["MARKET"].tolist()

    colA, colB = st.columns(2)
    selected_category = colA.selectbox("Drink category", categories)
    selected_comp_market = colB.selectbox("Market", markets, key="comp_market")

    share_df = session.sql(f"""
        SELECT ITEM_BRAND, IS_RED_BULL_PRODUCT, NUM_LISTINGS_CARRYING_BRAND, PCT_SHARE_OF_CATEGORY
        FROM GOLD.VW_BRAND_SHARE_BY_CATEGORY
        WHERE ITEM_DRINK_CATEGORY_1 = '{selected_category}'
          AND MARKET = '{selected_comp_market}'
        ORDER BY PCT_SHARE_OF_CATEGORY DESC
        LIMIT 15
    """).to_pandas()

    if share_df.empty:
        st.info("No data for this category/market combination.")
    else:
        st.bar_chart(share_df.set_index("ITEM_BRAND")["PCT_SHARE_OF_CATEGORY"])

        rb_row = share_df[share_df["IS_RED_BULL_PRODUCT"] == True]
        if not rb_row.empty:
            rb_rank = share_df.reset_index(drop=True).index[
                share_df["IS_RED_BULL_PRODUCT"] == True
            ].tolist()[0] + 1
            st.metric(
                "Red Bull's share of this category",
                f'{rb_row.iloc[0]["PCT_SHARE_OF_CATEGORY"]}%',
                help=f"Ranked #{rb_rank} of {len(share_df)} brands shown",
            )
        else:
            st.info("Red Bull has no presence in this category/market.")

        st.dataframe(
            share_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "PCT_SHARE_OF_CATEGORY": st.column_config.ProgressColumn(
                    "% Share of Category", min_value=0, max_value=100, format="%.1f%%"
                )
            },
        )