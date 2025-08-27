import streamlit as st
import pandas as pd

# Load employee data
@st.cache_data
def load_data():
    df = pd.read_excel("Employee Details PGT & YOC.xlsx")
    return df

employee_df = load_data()

# Loan eligibility logic based on policy
def check_eligibility(name, loan_reason):
    # Find employee record
    emp = employee_df[employee_df["Name"].str.lower() == name.lower()]
    if emp.empty:
        return "Employee not found in records."

    emp = emp.iloc[0]
    basic_salary = emp["Base Salary"]
    years = emp["Years of Service"]

    # Step 1 – Full-time check (assuming all in sheet are full-time)
    if years < 1:
        return "❌ Not eligible – less than 1 year of service."

    # Step 2 – Loan type & max amount
    if 1 <= years < 3:
        allowed_types = ["Medical"]
        max_amount = 5 * basic_salary
    elif 3 <= years < 8:
        allowed_types = ["Medical", "Own wedding", "Home repair emergency", "Children Education"]
        max_amount = 6 * basic_salary
    else:  # 8+ years
        allowed_types = ["Medical", "Own wedding", "Children Education", "Home repair", "Home renovation"]
        max_amount = 8 * basic_salary

    if loan_reason not in allowed_types:
        return f"❌ Not eligible for {loan_reason}. Eligible reasons: {', '.join(allowed_types)}"

    repayment = 0.3 * basic_salary
    return (f"✅ Eligible!\n\n"
            f"- Maximum Loan Amount: PKR {max_amount:,.0f}\n"
            f"- Monthly Repayment: PKR {repayment:,.0f} (30% of basic salary)\n"
            f"- Eligible Loan Types: {', '.join(allowed_types)}")

# Streamlit UI
st.title("🏦 Employee Loan Eligibility Checker")

st.write("Check your loan eligibility according to company policy.")

employee_name = st.text_input("Enter your full name:")
loan_type = st.selectbox(
    "Select loan reason:",
    ["Medical", "Own wedding", "Home repair emergency", "Children Education", "Home repair", "Home renovation"]
)

if st.button("Check Eligibility"):
    if employee_name.strip() == "":
        st.warning("Please enter your name.")
    else:
        result = check_eligibility(employee_name, loan_type)
        st.success(result) if "✅" in result else st.error(result)
