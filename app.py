# app.py
# Streamlit demo: Sync all bills + OnePay (single payment split across many bills)
# - 50 demo users
# - 100 different billers
# - Generate realistic bill portfolios
# - OnePay engine allocates a single payment by urgency (due soonest) and payoff logic
# - In-memory state using st.session_state (portable for Streamlit Cloud)
# - Download/upload demo data for persistence

import random
import math
from datetime import datetime, timedelta, date
from typing import List, Dict

import pandas as pd
import numpy as np
import streamlit as st

# -----------------------------
# Constants & Helpers
# -----------------------------

random.seed(42)
np.random.seed(42)
TODAY = date.today()

BILLER_CATEGORIES = {
    "Utilities": ["City Water", "GreenGas Energy", "BrightGrid Electric", "EcoWaste Services", "Metro Water", "RiverPower"],
    "Telecom": ["SkyNet Mobile", "FiberLink Internet", "Horizon Wireless", "Orbit Phone", "Velox Internet"],
    "Streaming": ["FlixZone", "TuneBox Music", "DocuStream", "Sportify Live", "CineClub"],
    "Credit Card": ["Apex Card", "Nova Rewards", "Pioneer Platinum", "Metro Cashback", "Zenith Card"],
    "Loans": ["Unity Student Loan", "HomeFirst Mortgage", "AutoFlex Finance", "DriveNow Auto Loan", "MicroLend Personal"],
    "Insurance": ["Shield Auto Ins", "Haven Home Ins", "WellPath Health", "SecureLife", "PetCare Ins"],
    "Rent & HOA": ["Maple Grove Apts", "Oakwood HOA", "Pinecrest Rentals", "City Center Lofts"],
    "Subscriptions": ["ProCloud Storage", "DevSuite Pro", "NewsDaily+", "MealPlan Weekly", "GymPlus"],
}

# Build a catalog of ~100 billers
ALL_BILLERS = []
for cat, names in BILLER_CATEGORIES.items():
    for n in names:
        ALL_BILLERS.append({"biller_id": f"{cat[:3].upper()}-{n[:6].upper()}-{abs(hash(n))%10000}", "name": n, "category": cat})

# If < 100, fabricate additional generic billers
counter = 1
while len(ALL_BILLERS) < 100:
    cat = random.choice(list(BILLER_CATEGORIES.keys()))
    name = f"{cat.split()[0]} Co. {counter}"
    ALL_BILLERS.append({"biller_id": f"{cat[:3].upper()}-GEN-{counter:03d}", "name": name, "category": cat})
    counter += 1

ALL_BILLERS_DF = pd.DataFrame(ALL_BILLERS).drop_duplicates("biller_id").reset_index(drop=True)

USER_FIRST_NAMES = [
    "Alex","Jordan","Taylor","Morgan","Riley","Casey","Jamie","Drew","Avery","Quinn",
    "Parker","Rowan","Emerson","Reese","Hayden","Skyler","Logan","Charlie","Blake","Dakota",
    "Cameron","Sage","Phoenix","Jules","Harper","Sawyer","Ellis","Finley","Remy","Kai",
    "Eden","Indigo","Jesse","Milan","Oakley","Paris","River","Sasha","Tatum","Val",
    "Winter","Zen","Echo","Arden","Briar","Bowie","Lux","Noa","Joss","Skye",
]

# -----------------------------
# Demo Data Generation
# -----------------------------

def generate_demo_users(num_users: int = 50) -> pd.DataFrame:
    users = []
    for i in range(num_users):
        name = f"{USER_FIRST_NAMES[i % len(USER_FIRST_NAMES)]} {chr(65 + (i % 26))}."  # e.g., Alex A.
        income = int(np.random.lognormal(mean=10.5, sigma=0.35))  # skewed gross annual
        credit_score = int(np.clip(np.random.normal(680, 60), 500, 820))
        users.append({
            "user_id": f"U{i+1:03d}",
            "name": name,
            "email": f"user{i+1}@demo.local",
            "annual_income": income,
            "credit_score": credit_score,
        })
    return pd.DataFrame(users)


def random_due_date() -> date:
    # Due dates within +/- 20 days from today (some past due)
    offset = random.randint(-10, 20)
    return TODAY + timedelta(days=offset)


def generate_user_bills(user_id: str, billers_df: pd.DataFrame) -> pd.DataFrame:
    # Each user will have 8-20 bills across categories
    num_bills = random.randint(8, 20)
    billers_sample = billers_df.sample(num_bills, replace=False, random_state=random.randint(0, 10_000))

    rows = []
    for _, b in billers_sample.iterrows():
        cat = b["category"]
        # Amount heuristics by category
        if cat == "Credit Card":
            balance = round(np.random.uniform(200, 4500), 2)
            min_due = round(max(25, 0.03 * balance), 2)
            apr = round(np.random.uniform(14.9, 29.99), 2)
            late_fee = 35
        elif cat == "Loans":
            balance = round(np.random.uniform(1500, 25000), 2)
            min_due = round(max(50, 0.015 * balance), 2)
            apr = round(np.random.uniform(4.5, 18.9), 2)
            late_fee = 25
        elif cat in ("Rent & HOA", "Insurance"):
            balance = round(np.random.uniform(80, 2400), 2)
            min_due = round(np.random.uniform(40, 300), 2)
            apr = 0.0
            late_fee = 50 if cat == "Rent & HOA" else 15
        else:  # Utilities / Telecom / Streaming / Subscriptions
            balance = round(np.random.uniform(10, 300), 2)
            min_due = round(np.random.uniform(10, min(150, balance)), 2)
            apr = 0.0
            late_fee = 10

        due = random_due_date()
        status = "Past Due" if due < TODAY else ("Due Soon" if (due - TODAY).days <= 7 else "Upcoming")
        rows.append({
            "user_id": user_id,
            "account_id": f"{user_id}-{b.biller_id}",
            "biller_id": b.biller_id,
            "biller_name": b.name,
            "category": cat,
            "due_date": due,
            "status": status,
            "balance": balance,
            "min_due": min_due,
            "apr": apr,
            "late_fee": late_fee,
            "autopay": random.choice([True, False, False]),
        })
    return pd.DataFrame(rows)


# -----------------------------
# OnePay Allocation Engine
# -----------------------------

def allocate_onepay(bills: pd.DataFrame, payment_amount: float) -> pd.DataFrame:
    """
    Allocate a single payment across many bills.
    Priority rules (descending):
      1) Past Due first, then Due Soon, then Upcoming
      2) Sooner due date first
      3) Higher APR next (to reduce interest)
      4) Higher late fee next (to avoid penalties)
      5) Then by min_due coverage and remaining balance
    Strategy: cover min_due for as many urgent bills as possible, then snowball remainder by APR.
    Returns a DataFrame of allocations with per-account payment.
    """
    if payment_amount <= 0:
        return pd.DataFrame(columns=["account_id","biller_name","category","due_date","status","apr","late_fee","allocated","covers_min_due","remaining_balance"])

    df = bills.copy()
    df = df[df["balance"] > 0].copy()
    if df.empty:
        return pd.DataFrame(columns=["account_id","biller_name","category","due_date","status","apr","late_fee","allocated","covers_min_due","remaining_balance"])

    # Urgency rank
    status_rank = {"Past Due": 0, "Due Soon": 1, "Upcoming": 2}
    df["status_rank"] = df["status"].map(status_rank)

    # Sort by rules
    df = df.sort_values(
        by=["status_rank", "due_date", "apr", "late_fee", "min_due", "balance"],
        ascending=[True, True, False, False, False, False]
    )

    allocations = []
    remaining = float(payment_amount)

    # Step 1: Cover minimum dues across sorted list
    for _, row in df.iterrows():
        if remaining <= 0:
            break
        need = float(min(row["min_due"], row["balance"]))
        pay = min(need, remaining)
        if pay > 0:
            remaining -= pay
            allocations.append({
                "account_id": row["account_id"],
                "biller_name": row["biller_name"],
                "category": row["category"],
                "due_date": row["due_date"],
                "status": row["status"],
                "apr": row["apr"],
                "late_fee": row["late_fee"],
                "allocated": round(pay, 2),
                "covers_min_due": pay + 1e-9 >= need,
                "remaining_balance": round(row["balance"] - pay, 2),
            })

    # Step 2: Use leftover to snowball by APR among unpaid/partially paid
    if remaining > 0:
        # Merge current allocations to know what's been paid so far
        alloc_df = pd.DataFrame(allocations)
        paid_by_acct = alloc_df.groupby("account_id")["allocated"].sum().to_dict() if not alloc_df.empty else {}

        df_snow = df.copy()
        df_snow["paid_so_far"] = df_snow["account_id"].map(paid_by_acct).fillna(0.0)
        df_snow["remaining_after_min"] = (df_snow["balance"] - df_snow["paid_so_far"]).clip(lower=0)
        # Prefer high APR, then urgency
        df_snow = df_snow[df_snow["remaining_after_min"] > 0].sort_values(
            by=["apr", "status_rank", "due_date", "late_fee", "remaining_after_min"],
            ascending=[False, True, True, False, False]
        )
        for _, row in df_snow.iterrows():
            if remaining <= 0:
                break
            need = float(row["remaining_after_min"])
            pay = min(need, remaining)
            if pay > 0:
                remaining -= pay
                # Append or update existing allocation row
                idx = next((i for i, a in enumerate(allocations) if a["account_id"] == row["account_id"]), None)
                if idx is None:
                    allocations.append({
                        "account_id": row["account_id"],
                        "biller_name": row["biller_name"],
                        "category": row["category"],
                        "due_date": row["due_date"],
                        "status": row["status"],
                        "apr": row["apr"],
                        "late_fee": row["late_fee"],
                        "allocated": round(pay, 2),
                        "covers_min_due": pay >= row["min_due"],
                        "remaining_balance": round(row["balance"] - pay, 2),
                    })
                else:
                    allocations[idx]["allocated"] = round(allocations[idx]["allocated"] + pay, 2)
                    allocations[idx]["remaining_balance"] = round(row["balance"] - allocations[idx]["allocated"], 2)

    return pd.DataFrame(allocations)


# -----------------------------
# UI State & Initialization
# -----------------------------

if "users" not in st.session_state:
    st.session_state.users = generate_demo_users(50)

if "all_billers" not in st.session_state:
    st.session_state.all_billers = ALL_BILLERS_DF.copy()

if "user_bills" not in st.session_state:
    # Dict[user_id] -> DataFrame of bills
    st.session_state.user_bills = {
        uid: generate_user_bills(uid, st.session_state.all_billers)
        for uid in st.session_state.users["user_id"].tolist()
    }

if "transactions" not in st.session_state:
    st.session_state.transactions = []  # each: {user_id, ts, total_paid, rows: [allocations]}


# -----------------------------
# UI Layout
# -----------------------------

st.set_page_config(page_title="OnePay Bills Demo", page_icon="💳", layout="wide")

st.sidebar.title("💳 OnePay Demo")

# User select
user_map = {f"{row.name} ({row.email})": row.user_id for _, row in st.session_state.users.iterrows()}
user_label = st.sidebar.selectbox("Select demo user", options=list(user_map.keys()))
user_id = user_map[user_label]
user_row = st.session_state.users.set_index("user_id").loc[user_id]

st.sidebar.markdown(f"**User ID:** {user_id}")
st.sidebar.markdown(f"**Name:** {user_row['name']}")
st.sidebar.markdown(f"**Credit Score:** {int(user_row['credit_score'])}")
st.sidebar.markdown(f"**Annual Income:** ${user_row['annual_income']:,}")

# Payment input
bills_df = st.session_state.user_bills[user_id].copy()

total_balance = float(bills_df["balance"].sum())
min_due_total = float(bills_df["min_due"].sum())
past_due_total = float(bills_df.loc[bills_df["status"] == "Past Due", "balance"].sum())

def money(x):
    return f"${x:,.2f}"

# Header
st.title("Sync & Pay All Bills with One Payment")
col_a, col_b, col_c, col_d = st.columns(4)
col_a.metric("Total Balance", money(total_balance))
col_b.metric("Total Min Due", money(min_due_total))
col_c.metric("Past Due", money(past_due_total))
col_d.metric("Bills Linked", f"{len(bills_df)}")

with st.expander("Linked Billers (100 catalog)"):
    st.dataframe(st.session_state.all_billers)

# Filters
with st.container():
    f1, f2, f3, f4 = st.columns(4)
    status_filter = f1.multiselect("Status", ["Past Due","Due Soon","Upcoming"], default=["Past Due","Due Soon","Upcoming"])
    cat_filter = f2.multiselect("Category", sorted(bills_df["category"].unique()), default=list(sorted(bills_df["category"].unique())))
    autopay_filter = f3.selectbox("Autopay", ["All","On","Off"], index=0)
    sort_by = f4.selectbox("Sort By", ["due_date","status","category","apr","balance","min_due"], index=0)

    df_view = bills_df.copy()
    df_view = df_view[df_view["status"].isin(status_filter) & df_view["category"].isin(cat_filter)]
    if autopay_filter != "All":
        df_view = df_view[df_view["autopay"] == (autopay_filter == "On")]
    df_view = df_view.sort_values(sort_by)

st.subheader("Linked Bills")
st.dataframe(df_view)

# OnePay input & preview
st.subheader("OnePay: Split a Single Payment Across All Bills")
default_pay = min(round(min_due_total * 1.2, 2), round(total_balance, 2))
payment_amount = st.number_input("Payment amount (USD)", min_value=0.0, value=float(default_pay), step=10.0, help="We'll smartly allocate this across your bills.")

alloc_preview = allocate_onepay(bills_df, payment_amount)

col1, col2 = st.columns([2,1])
with col1:
    st.markdown("**Proposed Allocation** (based on urgency, due date, APR, fees)")
    if alloc_preview.empty:
        st.info("Enter a positive payment amount to see the allocation plan.")
    else:
        st.dataframe(alloc_preview)

with col2:
    covered = int(alloc_preview["covers_min_due"].sum()) if not alloc_preview.empty else 0
    st.metric("Bills with Min Due Covered", f"{covered}")
    st.metric("Accounts Receiving Payment", f"{len(alloc_preview)}")
    st.metric("Unallocated Remainder", money(round(max(0.0, payment_amount - float(alloc_preview["allocated"].sum() if not alloc_preview.empty else 0.0)), 2)))

# Confirm button
confirm = st.button("✅ Confirm OnePay")
if confirm:
    if payment_amount <= 0 or alloc_preview.empty:
        st.warning("Enter a positive payment and ensure there are balances to pay.")
    else:
        # Apply allocation to balances
        new_bills = bills_df.set_index("account_id").copy()
        for _, r in alloc_preview.iterrows():
            acct = r["account_id"]
            paid = float(r["allocated"])
            new_bills.loc[acct, "balance"] = round(max(0.0, float(new_bills.loc[acct, "balance"]) - paid), 2)
            # If fully covered min due, push due date forward ~30 days (simulate next cycle)
            if r["covers_min_due"]:
                new_bills.loc[acct, "due_date"] = new_bills.loc[acct, "due_date"] + timedelta(days=30)

        # Recompute status
        new_bills = new_bills.reset_index()
        new_bills["status"] = new_bills["due_date"].apply(lambda d: "Past Due" if d < TODAY else ("Due Soon" if (d - TODAY).days <= 7 else "Upcoming"))

        st.session_state.user_bills[user_id] = new_bills
        st.session_state.transactions.append({
            "user_id": user_id,
            "ts": datetime.now().isoformat(timespec='seconds'),
            "total_paid": round(float(alloc_preview["allocated"].sum()), 2),
            "rows": alloc_preview.to_dict(orient="records"),
        })
        st.success(f"OnePay completed: {money(float(alloc_preview['allocated'].sum()))} across {len(alloc_preview)} accounts.")
        st.experimental_rerun()

# Transactions history
st.subheader("Payment History")
hist = [t for t in st.session_state.transactions if t["user_id"] == user_id]
if not hist:
    st.info("No payments yet for this user.")
else:
    # Flatten for display
    flat = []
    for t in hist:
        for r in t["rows"]:
            flat.append({
                "timestamp": t["ts"],
                "user_id": user_id,
                **{k: r[k] for k in ["account_id","biller_name","category","allocated"]},
                "total_paid": t["total_paid"],
            })
    st.dataframe(pd.DataFrame(flat).sort_values("timestamp", ascending=False))

# Data management
st.subheader("Data Management")
colx, coly, colz = st.columns(3)
with colx:
    if st.button("🔄 Re-generate demo data (all users)"):
        st.session_state.users = generate_demo_users(50)
        st.session_state.user_bills = {
            uid: generate_user_bills(uid, st.session_state.all_billers)
            for uid in st.session_state.users["user_id"].tolist()
        }
        st.session_state.transactions = []
        st.success("Demo data regenerated.")
        st.experimental_rerun()

with coly:
    # Export current selected user's bills
    csv = st.session_state.user_bills[user_id].to_csv(index=False).encode()
    st.download_button("⬇️ Download current user's bills (CSV)", data=csv, file_name=f"{user_id}_bills.csv", mime="text/csv")

with colz:
    uploaded = st.file_uploader("Upload bills CSV for this user (same columns)")
    if uploaded is not None:
        try:
            df_up = pd.read_csv(uploaded, parse_dates=["due_date"]).copy()
            # Ensure types
            needed_cols = {"user_id","account_id","biller_id","biller_name","category","due_date","status","balance","min_due","apr","late_fee","autopay"}
            missing = needed_cols - set(df_up.columns)
            if missing:
                st.error(f"Missing columns: {missing}")
            else:
                # Coerce date to date
                df_up["due_date"] = pd.to_datetime(df_up["due_date"]).dt.date
                st.session_state.user_bills[user_id] = df_up
                st.success("Bills uploaded and replaced for this user.")
                st.experimental_rerun()
        except Exception as e:
            st.error(f"Upload failed: {e}")

# Footer
st.caption("Demo app for showcasing a one-payment bill allocation flow with 50 demo users and 100 billers. Not connected to real payment rails.")
