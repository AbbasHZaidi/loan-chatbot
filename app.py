#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import textwrap
from typing import Dict, Any, List, Optional, Tuple

import streamlit as st
import pandas as pd
from PyPDF2 import PdfReader

POLICY_PDF = "Final Loan Policy.pdf"
EMPLOYEE_XLSX = "EmployeeList -YOC.xlsx"

HUMAN_ESCALATION = "Would you like to speak to a human?"

# -------------------------
# Helpers: PDF + policy parse
# -------------------------
def read_pdf_text(path: str) -> str:
    try:
        reader = PdfReader(path)
        pages = [p.extract_text() or "" for p in reader.pages]
        return "\\n".join(pages)
    except Exception:
        return ""

def extract_policy_checklist_and_rules(policy_text: str) -> Dict[str, Any]:
    """
    Heuristic parser:
    - Finds a "Checklist" or "Eligibility" section and pulls bullet points as required checks.
    - Looks for numeric rules such as min tenure, max amount, and salary multiples.
    STRICT MODE: We only use rules that are unambiguous; otherwise we won't answer.
    """
    policy = {
        "checklist": [],
        "rules": {
            "min_tenure_months": None,
            "min_tenure_years": None,
            "max_loan_amount": None,
            "max_salary_multiple": None,       # numeric value, only if context == "months"
            "multiple_context": None,          # only "months" is accepted for safety
            "max_percent_of_salary": None,
            "min_base_salary": None,
        }
    }

    text = policy_text or ""
    if not text.strip():
        return policy

    norm = re.sub(r"[ \\t]+", " ", text)
    norm = norm.replace("\\u2022", "- ").replace("•", "- ").replace("▪", "- ").replace("–", "- ")
    lines = [l.strip() for l in norm.splitlines() if l is not None]

    # 1) Extract checklist bullets under "Checklist" / "Eligibility" / "Criteria" headings
    headings = []
    for i, l in enumerate(lines):
        if re.match(r"^[A-Z][A-Za-z ]{0,60}$", l) and len(l.split()) <= 7:
            headings.append((i, l.lower()))

    def capture_bullets(start_idx: int) -> List[str]:
        items = []
        for j in range(start_idx + 1, min(len(lines), start_idx + 120)):
            lj = lines[j]
            if re.match(r"^[A-Z][A-Za-z ]{0,60}$", lj) and len(lj.split()) <= 7:
                break  # next heading
            if re.match(r"^(-|\\*|\\d+[\\.\\)]|□|▪|–)\\s", lj):
                bullet = lj
                k = j + 1
                while k < len(lines) and (not re.match(r"^(-|\\*|\\d+[\\.\\)]|□|▪|–)\\s", lines[k])) and not re.match(r"^[A-Z][A-Za-z ]{0,60}$", lines[k]):
                    if lines[k]:
                        bullet += " " + lines[k]
                    k += 1
                items.append(re.sub(r"^(-|\\*|\\d+[\\.\\)]|□|▪|–)\\s", "", bullet).strip())
        # Deduplicate + keep reasonable length
        return [c for c in dict.fromkeys(items) if 3 <= len(c) <= 300]

    for idx, lower_h in headings:
        if any(key in lower_h for key in ["checklist", "eligibility", "qualifications", "criteria"]):
            policy["checklist"].extend(capture_bullets(idx))

    # 2) Numeric rules extraction
    joined = " ".join(lines)

    # tenure (years / months)
    m_years = re.search(r"(?:min(?:imum)?\\s+tenure|at\\s+least)\\s+(\\d+(?:\\.\\d+)?)\\s*(?:years|yrs?)", joined, re.I)
    if m_years: policy["rules"]["min_tenure_years"] = float(m_years.group(1))

    m_months = re.search(r"(?:min(?:imum)?\\s+tenure|at\\s+least)\\s+(\\d+)\\s*(?:months?|mths?)", joined, re.I)
    if m_months: policy["rules"]["min_tenure_months"] = int(m_months.group(1))

    # salary multiple ONLY if explicitly expressed as "months of salary"
    for mm in re.finditer(r"(?:up to|maximum of)?\\s*(\\d+(?:\\.\\d+)?)\\s*(months?[’']?|months?\\s+of)\\s+(?:base\\s+)?salary", joined, re.I):
        val = float(mm.group(1))
        policy["rules"]["max_salary_multiple"] = val
        policy["rules"]["multiple_context"] = "months"

    # percentage of salary (e.g., “max 50% of salary”)
    m_pct = re.search(r"(?:max(?:imum)?\\s*)?(\\d+(?:\\.\\d+)?)\\s*%\\s*(?:of\\s+)?(?:base\\s+)?salary", joined, re.I)
    if m_pct: policy["rules"]["max_percent_of_salary"] = float(m_pct.group(1))

    # absolute cap (currency like 500,000 or 500000)
    m_abs = re.search(r"(?:max(?:imum)?\\s*loan\\s*amount|cap)\\s*[:\\-]?\\s*\\$?\\s*([0-9][0-9,\\.]+)", joined, re.I)
    if m_abs:
        try:
            amt = float(m_abs.group(1).replace(",", ""))
            policy["rules"]["max_loan_amount"] = amt
        except Exception:
            pass

    # minimum base salary threshold
    m_min_sal = re.search(r"(?:min(?:imum)?\\s*base\\s*salary)\\s*[:\\-]?\\s*\\$?\\s*([0-9][0-9,\\.]+)", joined, re.I)
    if m_min_sal:
        try:
            policy["rules"]["min_base_salary"] = float(m_min_sal.group(1).replace(",", ""))
        except Exception:
            pass

    return policy

# -------------------------
# Employee data
# -------------------------
def load_employees(path: str) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_excel(path)
        cols = {c.lower().strip(): c for c in df.columns}

        name_col = next((cols[k] for k in cols if k in ["name", "employee", "employee name"]), None)
        salary_col = next((cols[k] for k in cols if ("salary" in k and "base" in k) or k in ["salary", "base salary", "current base salary"]), None)
        tenure_col = next((cols[k] for k in cols if "tenure" in k or "years" in k or "months" in k), None)

        if not (name_col and salary_col and tenure_col):
            return None

        df = df[[name_col, salary_col, tenure_col]].copy()
        df.columns = ["name", "base_salary", "tenure_raw"]

        def tenure_to_months(v):
            if pd.isna(v): return None
            s = str(v).strip().lower()
            y = re.search(r"(\\d+(?:\\.\\d+)?)\\s*(?:year|yr|y)(?:s)?", s)
            m = re.search(r"(\\d+)\\s*(?:month|mth|mo)(?:s)?", s)
            if y and m: return round(float(y.group(1))*12 + int(m.group(1)))
            if y: return round(float(y.group(1))*12)
            if m: return int(m.group(1))
            try:
                val = float(s)
                if 0 < val <= 10 and abs(val - int(val)) > 1e-9:
                    return round(val * 12)
                return int(val)
            except:
                return None

        df["tenure_months"] = df["tenure_raw"].apply(tenure_to_months)

        def to_money(x):
            if pd.isna(x): return None
            s = str(x).replace(",", "")
            m = re.search(r"([0-9]+(?:\\.[0-9]+)?)", s)
            return float(m.group(1)) if m else None

        df["base_salary"] = df["base_salary"].apply(to_money)
        df["name_key"] = df["name"].astype(str).str.strip().str.lower()

        # Drop rows missing critical info
        df = df.dropna(subset=["name", "base_salary", "tenure_months"])
        return df
    except Exception:
        return None

# -------------------------
# Decision logic
# -------------------------
def compute_amount_cap(base_salary: float, rules: Dict[str, Any]) -> Optional[float]:
    caps = []

    if rules.get("max_loan_amount") is not None:
        caps.append(rules["max_loan_amount"])

    # Only apply multiple if context is explicitly months (policy stated "months of salary")
    if rules.get("multiple_context") == "months" and rules.get("max_salary_multiple") is not None and base_salary is not None:
        caps.append(rules["max_salary_multiple"] * base_salary)

    if rules.get("max_percent_of_salary") is not None and base_salary is not None:
        caps.append(base_salary * (rules["max_percent_of_salary"] / 100.0))

    if not caps:
        return None
    return min(caps)

def check_eligibility(
    emp_row: pd.Series,
    requested_amount: Optional[float],
    answers: Dict[str, bool],
    policy: Dict[str, Any]
) -> Tuple[str, Dict[str, Any]]:
    if emp_row is None or pd.isna(emp_row["base_salary"]) or pd.isna(emp_row["tenure_months"]):
        return HUMAN_ESCALATION, {}

    base_salary = float(emp_row["base_salary"])
    tenure_months = int(emp_row["tenure_months"])

    rules = policy.get("rules", {})

    # tenure
    if rules.get("min_tenure_months") is not None and tenure_months < rules["min_tenure_months"]:
        return "Not eligible (tenure below minimum as per policy).", {"reason": "tenure"}
    if rules.get("min_tenure_years") is not None and tenure_months < int(round(float(rules["min_tenure_years"])*12)):
        return "Not eligible (tenure below minimum as per policy).", {"reason": "tenure"}

    if rules.get("min_base_salary") is not None and base_salary < rules["min_base_salary"]:
        return "Not eligible (base salary below minimum as per policy).", {"reason": "base_salary"}

    # checklist strict
    checklist = policy.get("checklist", [])
    if checklist:
        for item in checklist:
            if item not in answers:
                return HUMAN_ESCALATION, {}
            if answers[item] is not True:
                return "Not eligible (failed policy checklist).", {"reason": "checklist", "failed_item": item}

    # amount
    if requested_amount is not None:
        cap = compute_amount_cap(base_salary, rules)
        if cap is None:
            return HUMAN_ESCALATION, {}
        if requested_amount > cap:
            return f"Conditionally eligible up to {cap:,.2f} (requested exceeds policy cap).", {"cap": cap}

    return "Eligible per current policy and records.", {}

# -------------------------
# UI
# -------------------------
st.set_page_config(page_title="Loan Policy Chatbot", page_icon="💬", layout="centered")

st.title("💬 Loan Policy Chatbot (Internal)")
st.caption("I only answer using: ‘Final Loan Policy.pdf’ and ‘EmployeeList - YOC.xlsx’. Anything else → “Would you like to speak to a human?”")

# Load documents
policy_text = read_pdf_text(POLICY_PDF) if os.path.exists(POLICY_PDF) else ""
policy = extract_policy_checklist_and_rules(policy_text) if policy_text else {"checklist": [], "rules": {}}
employees = load_employees(EMPLOYEE_XLSX) if os.path.exists(EMPLOYEE_XLSX) else None

with st.sidebar:
    st.header("Documents")
    st.write(f"**Policy PDF:** `{POLICY_PDF}` — {'✅ found' if policy_text else '❌ missing'}")
    st.write(f"**Employee Sheet:** `{EMPLOYEE_XLSX}` — {'✅ found' if employees is not None else '❌ missing or unreadable'}")

    if policy_text:
        with st.expander("Extracted Policy Rules"):
            r = policy.get("rules", {})
            st.write({
                "min_tenure_months": r.get("min_tenure_months"),
                "min_tenure_years": r.get("min_tenure_years"),
                "max_loan_amount": r.get("max_loan_amount"),
                "max_salary_multiple": r.get("max_salary_multiple"),
                "multiple_context": r.get("multiple_context"),
                "max_percent_of_salary": r.get("max_percent_of_salary"),
                "min_base_salary": r.get("min_base_salary"),
                "checklist_items_found": len(policy.get("checklist", [])),
            })
        if policy.get("checklist"):
            with st.expander("Policy Checklist Items"):
                for i, item in enumerate(policy["checklist"], 1):
                    st.markdown(f"{i}. {item}")

# Guard: if docs aren’t present or we couldn’t extract anything meaningful, escalate and stop
has_any_policy = bool(policy.get("checklist")) or any(v is not None for v in policy.get("rules", {}).values()) if policy else False
if not (policy_text and employees is not None and has_any_policy):
    st.error(HUMAN_ESCALATION)
    st.stop()

st.divider()
st.subheader("Eligibility Check (Strictly per Policy + Employee Sheet)")

# Employee picker
names = employees["name"].tolist()
name = st.selectbox("Select your name (exactly as on the employee list)", names)

emp_row = employees.loc[employees["name"] == name].iloc[0] if name else None
if emp_row is not None:
    col1, col2 = st.columns(2)
    col1.metric("Base Salary (from sheet)", f"{emp_row['base_salary']:,.2f}")
    col2.metric("Tenure (months)", int(emp_row["tenure_months"]))

# Requested amount (optional)
requested_amount = st.number_input("Requested loan amount (optional)", min_value=0.0, step=100.0, format="%.2f")

# Checklist answers
answers = {}
checklist = policy.get("checklist", [])
if checklist:
    st.markdown("**Policy Checklist — all must be true:**")
    for item in checklist:
        # Use a stable key derived from the item text
        key = "chk_" + str(abs(hash(item)) % (10**9))
        answers[item] = st.checkbox(item, value=False, key=key)

# Submit
if st.button("Check Eligibility"):
    verdict, extra = check_eligibility(emp_row, requested_amount if requested_amount > 0 else None, answers, policy)
    if verdict == HUMAN_ESCALATION:
        st.warning(HUMAN_ESCALATION)
    elif verdict.startswith("Not eligible"):
        st.error(verdict)
    elif verdict.startswith("Conditionally eligible"):
        st.info(verdict)
    else:
        st.success(verdict)

st.divider()
st.subheader("Chat (strictly limited)")

if "chat" not in st.session_state:
    st.session_state.chat = []

for role, content in st.session_state.chat:
    with st.chat_message(role):
        st.write(content)

msg = st.chat_input("Ask a question (only policy & employee sheet supported)")
if msg:
    st.session_state.chat.append(("user", msg))
    with st.chat_message("user"):
        st.write(msg)

    reply = None
    if re.search(r"\\b(check|eligible|eligibility|loan|apply|application)\\b", msg, re.I):
        reply = "Please use the Eligibility Check form above. I can only assess using the policy checklist and the employee sheet."
    else:
        reply = HUMAN_ESCALATION

    st.session_state.chat.append(("assistant", reply))
    with st.chat_message("assistant"):
        st.write(reply)
