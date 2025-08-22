## bundlpay_app.py
# Streamlit demo: BundlPay – Sync all bills into one bundle and pay with a single transaction
# - 50 demo users
# - 100 different billers
# - Generate realistic bill portfolios
# - BundlPay engine allocates a single payment by urgency (due soonest) and payoff logic
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
# BundlPay Allocation Engine
# -----------------------------

def allocate_bundlpay(bills: pd.DataFrame, payment_amount: float) -> pd.DataFrame:
    """
    Allocate a single payment across many bills (BundlPay logic).
    Priority rules (descending):
      1) Past Due first, then Due Soon, then Upcoming
      2) Sooner due date first
      3) Higher APR next (to reduce interest)
      4) Higher late fee next (to avoid penalties)
      5) Then by min_due coverage and remaining balance
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

    # Step 2: Snowball remaining by APR
    if remaining > 0:
        alloc_df = pd.DataFrame(allocations)
        paid_by_acct = alloc_df.groupby("account_id")["allocated"].sum().to_dict() if not alloc_df.empty else {}

        df_snow = df.copy()
        df_snow["paid_so_far"] = df_snow["account_id"].map(paid_by_acct).fillna(0.0)
        df_snow["remaining_after_min"] = (df_snow["balance"] - df_snow["paid_so_far"]).clip(lower=0)
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
    st.session_state.user_bills = {
        uid: generate_user_bills(uid, st.session_state.all_billers)
        for uid in st.session_state.users["user_id"].tolist()
    }

if "transactions" not in st.session_state:
    st.session_state.transactions = []


# -----------------------------
# UI Layout
# -----------------------------

st.set_page_config(page_title="BundlPay – Bundle & Pay All Bills", page_icon="💳", layout="wide")

st.sidebar.title("💳 BundlPay Demo")

user_map = {f"{row.name} ({row.email})": row.user_id for _, row in st.session_state.users.iterrows()}
user_label = st.sidebar.selectbox("Select demo user", options=list(user_map.keys()))
user_id = user_map[user_label]
user_row = st.session_state.users.set_index("user_id").loc[user_id]

st.sidebar.markdown(f"**User ID:** {user_id}")
st.sidebar.markdown(f"**Name:** {user_row['name']}")
st.sidebar.markdown(f"**Credit Score:** {int(user_row['credit_score'])}")
st.sidebar.markdown(f"**Annual Income:** ${user_row['annual_income']:,}")

bills_df = st.session_state.user_bills[user_id].copy()

total_balance = float(bills_df["balance"].sum())
min_due_total = float(bills_df["min_due"].sum())
past_due_total = float(bills_df.loc[bills_df["status"] == "Past Due", "balance"].sum())

st.title("BundlPay – Sync & Pay All Bills in One")

col_a, col_b, col_c, col_d = st.columns(4)
col_a.metric("Total Balance", f"${total_balance:,.2f}")
col_b.metric("Total Min Due", f"${min_due_total:,.2f}")
col_c.metric("Total Past Due", f"${past_due_total:,.2f}")
col_d.metric("Bills Linked", f"{len(bills_df)}")

with st.expander("Linked Billers (100 catalog)"):
    st.dataframe(st.session_state.all_billers)

st.subheader("Linked Bills")
st.dataframe(bills_df)

st.subheader("BundlPay: One Payment, Smartly Allocated")
default_pay = min(round(min_due_total * 1.2, 2), round(total_balance, 2))
payment_amount = st.number_input("Payment amount (USD)", min_value=0.0, value=float(default_pay), step=10.0)

alloc_preview = allocate_bundlpay(bills_df, payment_amount)

st.subheader("Proposed Allocation")
if alloc_preview.empty:
    st.info("Enter a positive payment amount to see the BundlPay allocation plan.")
else:
    st.dataframe(alloc_preview)

covered = int(alloc_preview["covers_min_due"].sum()) if not alloc_preview.empty else 0
st.metric("Bills with Min Due Covered", f"{covered}")
st.metric("Accounts Receiving Payment", f"{len(alloc_preview)}")
st.metric("Unallocated Remainder", f"${round(max(0.0, payment_amount - float(alloc_preview['allocated'].sum() if not alloc_preview.empty else 0.0)), 2):,.2f}")

confirm = st.button("✅ Confirm BundlPay")
if confirm:
    if payment_amount <= 0 or alloc_preview.empty:
        st.warning("Enter a positive payment and ensure there are balances to pay.")
    else:
        new_bills = bills_df.set_index("account_id").copy()
        for _, r in alloc_preview.iterrows():
            acct = r["account_id"]
            paid = float(r["allocated"])
            new_bills.loc[acct, "balance"] = round(max(0.0, float(new_bills.loc[acct, "balance"]) - paid), 2)
            if r["covers_min_due"]:
                new_bills.loc[acct, "due_date"] = new_bills.loc[acct, "due_date"] + timedelta(days=30)
        new_bills = new_bills.reset_index()
        new_bills["status"] = new_bills["due_date"].apply(lambda d: "Past Due" if d < TODAY else ("Due Soon" if (d - TODAY).days <= 7 else "Upcoming"))
        st.session_state.user_bills[user_id] = new_bills
        st.session_state.transactions.append({
            "user_id": user_id,
            "ts": datetime.now().isoformat(timespec='seconds'),
            "total_paid": round(float(alloc_preview["allocated"].sum()), 2),
            "rows": alloc_preview.to_dict(orient="records
