import streamlit as st
import pandas as pd

st.set_page_config(page_title="Loan Eligibility Chatbot", page_icon="🏦")

# ---------- Data Loading ----------
@st.cache_data
def load_employees():
    # Expecting columns: Name, Base Salary, Years of Service, (optional) Employment Type
    df = pd.read_excel("Employee Details PGT & YOC.xlsx")
    # Normalise column names
    df.columns = [c.strip() for c in df.columns]
    # Basic validation
    needed = {"Name", "Base Salary", "Years of Service"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"Employee sheet missing columns: {', '.join(missing)}")
    return df

EMP = load_employees()

# ---------- Policy Logic ----------
LOAN_TYPES_BY_TENURE = {
    "1-3": ["Medical"],
    "3-8": ["Medical", "Own wedding", "Home repair emergency", "Children Education"],
    "8+":  ["Medical", "Own wedding", "Children Education", "Home repair", "Home renovation"],
}
MULTIPLIER_BY_TENURE = {
    "1-3": 5,
    "3-8": 6,
    "8+":  8,
}

def tenure_band(years: float) -> str:
    if years < 1:
        return "<1"
    elif 1 <= years < 3:
        return "1-3"
    elif 3 <= years < 8:
        return "3-8"
    else:
        return "8+"

def allowed_types(years: float):
    band = tenure_band(years)
    return LOAN_TYPES_BY_TENURE.get(band, [])

def max_multiplier(years: float):
    band = tenure_band(years)
    return MULTIPLIER_BY_TENURE.get(band, 0)

def check_eligibility(name: str, reason: str):
    # Find employee (case-insensitive exact match on Name)
    row = EMP[EMP["Name"].str.strip().str.lower() == name.strip().lower()]
    if row.empty:
        return {"status": "not_found"}

    r = row.iloc[0]
    years = float(r["Years of Service"])
    base = float(r["Base Salary"])

    # Policy Step 1: full-time & 1+ year service
    # If you track employment type in the sheet (e.g., "Employment Type"), you can enforce full-time here:
    # if "Employment Type" in r and str(r["Employment Type"]).strip().lower() != "full-time":
    #     return {"status": "denied_fulltime"}

    if years < 1:
        return {"status": "denied_tenure", "years": years}

    allowed = allowed_types(years)
    if reason not in allowed:
        return {"status": "denied_reason", "allowed": allowed, "years": years}

    multiplier = max_multiplier(years)
    max_amount = multiplier * base
    monthly_repay = 0.30 * base  # 30% of basic salary

    return {
        "status": "approved",
        "years": years,
        "base": base,
        "allowed": allowed,
        "reason": reason,
        "multiplier": multiplier,
        "max_amount": max_amount,
        "monthly_repay": monthly_repay,
    }

# ---------- Lightweight NLP (no heavy regex) ----------
ALL_REASONS = [
    "Medical",
    "Own wedding",
    "Home repair emergency",
    "Children Education",
    "Home repair",
    "Home renovation",
]

REASON_SYNONYMS = {
    "medical": "Medical",
    "hospital": "Medical",
    "treatment": "Medical",
    "surgery": "Medical",
    "wedding": "Own wedding",
    "marriage": "Own wedding",
    "nikah": "Own wedding",
    "repair": "Home repair",
    "emergency repair": "Home repair emergency",
    "urgent repair": "Home repair emergency",
    "education": "Children Education",
    "school": "Children Education",
    "fees": "Children Education",
    "renovation": "Home renovation",
    "renovate": "Home renovation",
}

def canonical_reason(text: str):
    t = text.lower()
    # direct matches
    for r in ALL_REASONS:
        if r.lower() in t:
            return r
    # synonyms
    for k, v in REASON_SYNONYMS.items():
        if k in t:
            return v
    return None

def extract_name(text: str):
    """
    Simple name extraction:
    - Looks for phrases like "my name is <name>", "i am <name>", "this is <name>"
    - Otherwise, tries to match a known employee name appearing in the text.
    """
    t = " " + text.strip() + " "
    lowers = t.lower()

    cues = ["my name is", "i am", "i’m", "im ", "this is", "name:"]
    for cue in cues:
        if cue in lowers:
            # Take the part after cue, up to a punctuation or end
            after = t[lowers.find(cue) + len(cue):].strip()
            # Stop at common delimiters
            for delim in [",", ".", ";", "!", "?", "\n"]:
                if delim in after:
                    after = after.split(delim)[0].strip()
                    break
            # Return if it matches an employee by fuzzy contains
            # Prefer exact match by tokens to avoid over-matching
            candidate = after
            # Try to resolve to a real employee
            match = EMP[EMP["Name"].str.strip().str.lower() == candidate.lower()]
            if not match.empty:
                return match.iloc[0]["Name"]
            # If not exact, try contains match on first token(s)
            # Fall through to a broader search below
            possible = EMP[EMP["Name"].str.lower().str.contains(candidate.lower())]
            if len(possible) == 1:
                return possible.iloc[0]["Name"]

    # If no cue found, try scanning for any employee name contained in message
    # (only if it uniquely identifies one person)
    possibles = EMP[EMP["Name"].str.lower().apply(lambda n: n in text.lower())]
    if len(possibles) == 1:
        return possibles.iloc[0]["Name"]

    return None

# ---------- Chat State ----------
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "Hi! Tell me your name and why you need a loan (e.g., “My name is Ali Raza, I need a loan for my child’s school fees”)."}
    ]
if "pending_name" not in st.session_state:
    st.session_state.pending_name = None
if "pending_reason" not in st.session_state:
    st.session_state.pending_reason = None

st.title("🏦 Loan Eligibility Chatbot (Internal)")

# Optional Admin panel for HR to sanity-check the sheet
with st.expander("Admin • Data sanity"):
    st.write(f"Loaded employees: {len(EMP)}")
    sample = EMP.head(5)
    st.dataframe(sample)

# Render history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
user_text = st.chat_input("Type your message…")

def respond(text: str):
    # Try to extract name and reason from the incoming text or from pending state
    name = extract_name(text) or st.session_state.pending_name
    reason = canonical_reason(text) or st.session_state.pending_reason

    # Ask for what’s missing
    if not name and not reason:
        return "Got it. Please tell me your full name and the reason (e.g., medical, wedding, home repair, children education, home renovation)."
    if not name:
        return "Please tell me your full name as it appears in the employee records."
    if not reason:
        return "Thanks. What’s the loan reason (Medical, Own wedding, Home repair emergency, Children Education, Home repair, Home renovation)?"

    # We have both — check eligibility
    result = check_eligibility(name, reason)

    # Persist resolved fields to keep the conversation smooth
    st.session_state.pending_name = name
    st.session_state.pending_reason = reason

    if result["status"] == "not_found":
        return f"Sorry, I couldn’t find **{name}** in the employee records. Please check the spelling or ask HR to update the sheet."

    if result["status"] == "denied_tenure":
        return f"❌ Not eligible: requires at least 1 year of continuous service. Current service: **{result['years']:.1f}** years."

    if result["status"] == "denied_reason":
        options = ", ".join(result["allowed"]) if result["allowed"] else "None"
        band = tenure_band(result["years"])
        return (
            f"❌ Not eligible for **{reason}** with **{result['years']:.1f}** years of service.\n\n"
            f"Eligible reasons for your tenure band ({band} years): {options}."
        )

    if result["status"] == "approved":
        band = tenure_band(result["years"])
        return (
            "✅ **Eligible**\n\n"
            f"- Tenure: **{result['years']:.1f}** years (band: {band})\n"
            f"- Reason: **{result['reason']}**\n"
            f"- Maximum Loan Amount: **PKR {result['max_amount']:,.0f}** "
            f"({result['multiplier']}× basic salary)\n"
            f"- Monthly Repayment (salary deduction): **PKR {result['monthly_repay']:,.0f}** "
            f"(30% of basic salary)\n"
            "\n*Note: Eligibility also remains subject to documentation review and available loan budget.*"
        )

    # Fallback (shouldn't reach here)
    return "Would you like to speak to a human?"

if user_text:
    st.session_state.messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    reply = respond(user_text)
    st.session_state.messages.append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
