import json
import logging
import os
from datetime import date

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MCP_BASE = os.getenv("MCP_BASE_URL", "http://localhost:8000/mcp")
API_BASE = os.getenv("MCP_API_URL", "http://localhost:8000/api")
MCP_TOKEN = os.getenv("BUDGET_MCP_TOKEN", "")


def api_headers() -> dict:
    return {
        "Authorization": f"Bearer {MCP_TOKEN}",
        "Content-Type": "application/json",
    }


def call_tool(tool_name: str, arguments: dict):
    try:
        resp = httpx.post(
            API_BASE,
            headers=api_headers(),
            json={"tool": tool_name, "arguments": arguments},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "error" in data:
            st.error(f"API error: {data['error']}")
            return None
        return data
    except httpx.ConnectError:
        st.error("Cannot connect to MCP server at " + API_BASE + ". Is it running?")
        return None
    except Exception as e:
        st.error(f"Request failed: {e}")
        return None


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Budget Tracker",
    page_icon="💰",
    layout="wide",
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.title("💰 Budget Tracker")

today = date.today()
selected_month = st.sidebar.number_input("Month", min_value=1, max_value=12, value=today.month)
selected_year = st.sidebar.number_input("Year", min_value=2020, max_value=2099, value=today.year)

page = st.sidebar.radio(
    "View",
    ["Overview", "By Category", "Timeline", "Transactions"],
    index=0,
)

st.sidebar.markdown("---")
st.sidebar.caption(f"MCP: `{MCP_BASE}`")


# ── Data loading ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_expenses(month: int, year: int):
    data = call_tool("get_expenses", {"month": month, "year": year})
    return data or []


@st.cache_data(ttl=30)
def load_summary(month: int, year: int):
    return call_tool("get_monthly_summary", {"month": month, "year": year})


@st.cache_data(ttl=30)
def load_budget_status(month: int, year: int):
    data = call_tool("get_budget_status", {"month": month, "year": year})
    return data or []


month_label = date(selected_year, selected_month, 1).strftime("%B %Y")

# ── Overview ──────────────────────────────────────────────────────────────────
if page == "Overview":
    st.title(f"Overview — {month_label}")

    summary = load_summary(selected_month, selected_year)
    expenses = load_expenses(selected_month, selected_year)

    if not summary:
        st.info("No data for this period. Start logging expenses via the Telegram bot!")
        st.stop()

    total = summary.get("total", 0)
    by_cat = summary.get("by_category", {})
    top_cat = max(by_cat, key=by_cat.get) if by_cat else "—"

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Spend", f"R{total:,.2f}")
    col2.metric("Transactions", len(expenses))
    col3.metric("Top Category", f"{top_cat} (R{by_cat.get(top_cat, 0):,.2f})" if top_cat != "—" else "—")

    st.markdown("---")
    st.subheader("Spend by Category")
    if by_cat:
        cat_df = pd.DataFrame(
            [{"Category": k, "Amount": v} for k, v in sorted(by_cat.items(), key=lambda x: -x[1])]
        )
        fig = px.pie(cat_df, names="Category", values="Amount", hole=0.4)
        fig.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig, use_container_width=True)

# ── By Category ───────────────────────────────────────────────────────────────
elif page == "By Category":
    st.title(f"By Category — {month_label}")

    budget_status = load_budget_status(selected_month, selected_year)
    summary = load_summary(selected_month, selected_year)
    by_cat = summary.get("by_category", {}) if summary else {}

    # Merge: include categories with spend but no budget
    all_cats = set(by_cat.keys()) | {b["category"] for b in budget_status}

    rows = []
    budget_map = {b["category"]: b for b in budget_status}
    for cat in sorted(all_cats):
        spent = by_cat.get(cat, 0)
        bdata = budget_map.get(cat, {})
        limit = bdata.get("monthly_limit")
        rows.append({
            "Category": cat,
            "Spent": spent,
            "Budget": limit or 0,
            "Has Budget": limit is not None,
        })

    if not rows:
        st.info("No data for this period.")
        st.stop()

    df = pd.DataFrame(rows)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Spent",
        x=df["Spent"],
        y=df["Category"],
        orientation="h",
        marker_color="#EF553B",
    ))
    fig.add_trace(go.Bar(
        name="Budget",
        x=df["Budget"],
        y=df["Category"],
        orientation="h",
        marker_color="#636EFA",
        opacity=0.4,
    ))
    fig.update_layout(
        barmode="overlay",
        xaxis_title="Amount (R)",
        height=max(300, len(rows) * 40),
        margin=dict(l=10, r=10, t=10, b=30),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Budget Status Table")
    if budget_status:
        status_df = pd.DataFrame(budget_status)[
            ["category", "monthly_limit", "spent", "remaining", "percentage_used", "over_budget"]
        ]
        status_df.columns = ["Category", "Limit (R)", "Spent (R)", "Remaining (R)", "Used %", "Over Budget"]
        st.dataframe(status_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("Set Budget Limit")
    from models_ref import CATEGORIES_LIST
    with st.form("budget_form"):
        col1, col2 = st.columns(2)
        cat = col1.selectbox("Category", CATEGORIES_LIST)
        lim = col2.number_input("Monthly Limit (R)", min_value=1.0, step=50.0)
        submitted = st.form_submit_button("Save Budget")
        if submitted:
            result = call_tool("set_budget", {"category": cat, "monthly_limit": lim})
            if result:
                st.success(f"Budget for {cat} set to R{lim:,.2f}")
                st.cache_data.clear()

# ── Timeline ──────────────────────────────────────────────────────────────────
elif page == "Timeline":
    st.title(f"Daily Spend — {month_label}")

    expenses = load_expenses(selected_month, selected_year)
    if not expenses:
        st.info("No expenses found for this period.")
        st.stop()

    df = pd.DataFrame(expenses)
    df["date"] = pd.to_datetime(df["date"])
    daily = df.groupby("date")["amount"].sum().reset_index()
    daily = daily.sort_values("date")
    daily["cumulative"] = daily["amount"].cumsum()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=daily["date"],
        y=daily["cumulative"],
        mode="lines+markers",
        name="Cumulative Spend",
        line=dict(color="#636EFA", width=2),
        fill="tozeroy",
        fillcolor="rgba(99,110,250,0.1)",
    ))
    fig.add_trace(go.Bar(
        x=daily["date"],
        y=daily["amount"],
        name="Daily Spend",
        marker_color="#EF553B",
        opacity=0.6,
        yaxis="y2",
    ))
    fig.update_layout(
        xaxis_title="Date",
        yaxis_title="Cumulative (R)",
        yaxis2=dict(title="Daily (R)", overlaying="y", side="right"),
        legend=dict(x=0, y=1),
        height=450,
    )
    st.plotly_chart(fig, use_container_width=True)

# ── Transactions ──────────────────────────────────────────────────────────────
elif page == "Transactions":
    st.title(f"Transactions — {month_label}")

    expenses = load_expenses(selected_month, selected_year)
    if not expenses:
        st.info("No expenses found for this period.")
        st.stop()

    df = pd.DataFrame(expenses)

    col1, col2 = st.columns(2)
    cats = ["All"] + sorted(df["category"].unique().tolist())
    selected_cat = col1.selectbox("Filter by Category", cats)
    search = col2.text_input("Search merchant/description")

    filtered = df.copy()
    if selected_cat != "All":
        filtered = filtered[filtered["category"] == selected_cat]
    if search:
        mask = (
            filtered["merchant"].fillna("").str.contains(search, case=False)
            | filtered["description"].fillna("").str.contains(search, case=False)
        )
        filtered = filtered[mask]

    st.write(f"Showing {len(filtered)} of {len(df)} transactions")

    for _, row in filtered.sort_values("date", ascending=False).iterrows():
        with st.container():
            c1, c2, c3, c4, c5 = st.columns([2, 3, 3, 2, 1])
            c1.write(row["date"])
            c2.write(f"**{row['category']}**")
            c3.write(row.get("merchant") or row.get("description") or "—")
            c4.write(f"R{row['amount']:,.2f}")
            if c5.button("🗑", key=f"del_{row['id']}"):
                result = call_tool("delete_expense", {"expense_id": int(row["id"])})
                if result and not result.get("error"):
                    st.success(f"Deleted expense #{row['id']}")
                    st.cache_data.clear()
                    st.rerun()
