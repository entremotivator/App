import streamlit as st
import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta, date

# ------------------------------
# Setup Users and Billers
# ------------------------------

# Today's date
TODAY = date.today()

# Billers categories and names
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
        ALL_BILLERS.append({
            "biller_id": f"{cat[:3].upper()}-{n[:6].upper()}-{abs(hash(n))%10000}",
            "name": n,
            "category": cat
        })

# If < 100, fabricate additional generic billers
counter = 1
while len(ALL_BILLERS) < 100:
    cat = random.choice(list(BILLER_CATEGORIES.keys()))
    name = f"{cat.split()[0]} Co. {counter}"
    ALL_BILLERS.append({
        "biller_id": f"{cat[:3].upper()}-GEN-{counter:03d}",
        "name": name,
        "category": cat
    })
    counter += 1

ALL_BILLERS_DF = pd.DataFrame(ALL_BILLERS).drop_duplicates("biller_id").reset_index(drop=True)

# User names pool
USER_FIRST_NAMES = [
    "Alex","Jordan","Taylor","Morgan","Riley","Casey","Jamie","Drew","Avery","Quinn",
    "Parker","Rowan","Emerson","Reese","Hayden","Skyler","Logan","Charlie","Blake","Dakota",
    "Cameron","Sage","Phoenix","Jules","Harper","Sawyer","Ellis","Finley","Remy","Kai",
    "Eden","Indigo","Jesse","Milan","Oakley","Paris","River","Sasha","Tatum","Val",
    "Winter","Zen","Echo","Arden","Briar","Bowie","Lux","Noa","Joss","Skye",
]

# Generate 100 users
def generate_demo_users(n=100):
    users = []
    for i in range(n):
        first = USER_FIRST_NAMES[i % len(USER_FIRST_NAMES)]
        last = f"User{i+1}"
        users.append({
            "user_id": f"U{i+1:03d}",
            "name": f"{first} {last}",
            "email": f"{first.lower()}{i+1}@demo.com"
        })
    return pd.DataFrame(users)

users_df = generate_demo_users(100)

# ------------------------------
# Generate Bills per User
# ------------------------------

def random_due_date():
    return TODAY + timedelta(days=random.randint(-10, 30))

def generate_user_bills(user_id, billers_df, min_bills=3, max_bills=8):
    bills = []
    n = random.randint(min_bills, max_bills)
    sampled_billers = billers_df.sample(n, replace=False)
    for _, b in sampled_billers.iterrows():
        due = random_due_date()
        amount = round(random.uniform(20, 500), 2)
        bills.append({
            "user_id": user_id,
            "biller_id": b.biller_id,
            "biller_name": b.name,
            "category": b.category,
            "due_date": due,
            "amount_due": amount,
            "status": "PAST DUE" if due < TODAY else "DUE"
        })
    return bills

all_bills = []
for uid in users_df.user_id:
    all_bills.extend(generate_user_bills(uid, ALL_BILLERS_DF))

bills_df = pd.DataFrame(all_bills)

# ------------------------------
# Streamlit UI
# ------------------------------

st.set_page_config(page_title="BundlPay – One Payment for All Bills", layout="wide")
st.title("💳 BundlPay – One Payment for All Bills")

selected_user = st.selectbox("Select Customer", users_df["name"])
user_id = users_df.loc[users_df["name"] == selected_user, "user_id"].values[0]
user_bills = bills_df[bills_df.user_id == user_id].copy()

st.subheader("Your Bills")
st.dataframe(user_bills[["biller_name", "category", "due_date", "amount_due", "status"]])

total_due = user_bills.amount_due.sum()
total_past_due = user_bills[user_bills.status == "PAST DUE"].amount_due.sum()

t1, t2 = st.columns(2)
with t1:
    st.metric("Total Outstanding", f"${total_due}")
with t2:
    st.metric("Past Due", f"${total_past_due}")

# ------------------------------
# BundlPay Allocation
# ------------------------------

st.subheader("OnePay – Bundle Your Payment")
payment_amount = st.number_input("Enter Total Payment Amount", min_value=0.0, value=float(total_due/2))

if payment_amount > 0:
    bills_sorted = user_bills.sort_values(by=["status", "due_date"], ascending=[True, True])
    alloc = []
    remaining = payment_amount
    for _, row in bills_sorted.iterrows():
        if remaining <= 0:
            alloc.append(0)
            continue
        pay = min(remaining, row.amount_due)
        alloc.append(pay)
        remaining -= pay

    user_bills["allocated"] = alloc

    st.subheader("Allocation Preview")
    st.dataframe(user_bills[["biller_name", "amount_due", "allocated", "due_date", "status"]])
    st.write(f"**Total Allocated:** ${user_bills['allocated'].sum():.2f}")
    st.write(f"**Remaining Balance After Allocation:** ${remaining:.2f}")

    if st.button("Confirm Payment"):
        bills_df.loc[user_bills.index, "amount_due"] -= user_bills["allocated"]
        bills_df.loc[bills_df.amount_due <= 0, "status"] = "PAID"
        st.success("Payment applied successfully!")

# ------------------------------
# Payment History
# ------------------------------
if "payment_history" not in st.session_state:
    st.session_state["payment_history"] = []

if st.button("Save Payment History"):
    st.session_state["payment_history"].append({
        "user_id": user_id,
        "total_paid": payment_amount,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    st.success("Payment history updated.")

if st.session_state["payment_history"]:
    st.subheader("Payment History")
    st.table(pd.DataFrame(st.session_state["payment_history"]))
