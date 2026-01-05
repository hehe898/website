import streamlit as st
import sqlite3
import datetime
import os
import smtplib
from email.mime.text import MIMEText

# ---------------- CONFIG ----------------
DB = "licenses.db"
UPLOAD_DIR = "storage"
client = OpenAI()

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ---------------- DB ----------------
conn = sqlite3.connect(DB, check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS agreements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    country TEXT,
    brand TEXT,
    licenser TEXT,
    status TEXT,
    start_date TEXT,
    end_date TEXT,
    indefinite INTEGER,
    summary TEXT,
    parent_id INTEGER,
    obsolete INTEGER DEFAULT 0
)
""")

conn.commit()

# ---------------- UTILS ----------------
def extract_text(file):
    if file.name.endswith(".pdf"):
        reader = PdfReader(file)
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    if file.name.endswith(".docx"):
        doc = Document(file)
        return "\n".join(p.text for p in doc.paragraphs)
    return ""

def ai_summary(text):
    prompt = f"""
Summarize this license agreement.
Extract:
- What rights are granted
- Territory
- Brand
- Licenser
- Start date
- End date or say 'Indefinite'
Return plain English.
"""
    r = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role":"user","content": prompt + text}]
    )
    return r.choices[0].message.content

def ai_compare(old, new):
    prompt = f"""
Compare ORIGINAL vs AMENDMENT.
List only what changed.
ORIGINAL:
{old}

AMENDMENT:
{new}
"""
    r = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role":"user","content": prompt}]
    )
    return r.choices[0].message.content

def send_email(to, subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = os.getenv("EMAIL_SENDER")
    msg["To"] = to

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.getenv("EMAIL_SENDER"), os.getenv("EMAIL_PASSWORD"))
        server.send_message(msg)

# ---------------- AUTH ----------------
st.title("AI License Management MVP")

if "user" not in st.session_state:
    st.subheader("Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    if st.button("Login"):
        c.execute("SELECT * FROM users WHERE username=? AND password=?", (u,p))
        if c.fetchone():
            st.session_state.user = u
            st.rerun()
        else:
            st.error("Invalid login")
    st.stop()

# ---------------- MAIN ----------------
menu = st.sidebar.selectbox("Menu", [
    "Upload Agreement",
    "Upload Amendment",
    "View Agreements"
])

# ---------------- UPLOAD AGREEMENT ----------------
if menu == "Upload Agreement":
    st.header("Upload License Agreement")

    title = st.text_input("Agreement Title")
    country = st.text_input("Country")
    brand = st.text_input("Brand")
    licenser = st.text_input("Licenser")
    file = st.file_uploader("Upload PDF/DOCX")

    if st.button("Upload & Scan") and file:
        text = extract_text(file)
        summary = ai_summary(text)

        st.session_state.summary = summary
        st.session_state.text = text

    if "summary" in st.session_state:
        summary = st.text_area("AI Summary (Editable)", st.session_state.summary, height=300)

        start = st.date_input("Start Date", datetime.date.today())
        end = st.date_input("End Date", datetime.date.today())
        indefinite = st.checkbox("Indefinite")

        if st.button("Save Agreement"):
            c.execute("""
            INSERT INTO agreements
            (title,country,brand,licenser,status,start_date,end_date,indefinite,summary)
            VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                title,country,brand,licenser,
                "Active",
                start.isoformat(),
                None if indefinite else end.isoformat(),
                1 if indefinite else 0,
                summary
            ))
            conn.commit()
            st.success("Agreement saved")

# ---------------- AMENDMENT ----------------
if menu == "Upload Amendment":
    st.header("Upload Amendment")

    agreements = c.execute("SELECT id,title,summary FROM agreements WHERE obsolete=0").fetchall()
    aid = st.selectbox("Base Agreement", agreements, format_func=lambda x: x[1])

    file = st.file_uploader("Upload Amendment")
    if st.button("Scan Amendment") and file:
        text = extract_text(file)
        diff = ai_compare(aid[2], text)
        st.session_state.diff = diff

    if "diff" in st.session_state:
        diff = st.text_area("Changes (Editable)", st.session_state.diff, height=300)

        if st.button("Save Amendment"):
            c.execute("""
            INSERT INTO agreements
            (title,status,summary,parent_id)
            VALUES (?,?,?,?)
            """, (
                "Amendment",
                "Active",
                diff,
                aid[0]
            ))
            conn.commit()
            st.success("Amendment saved")

# ---------------- VIEW ----------------
if menu == "View Agreements":
    st.header("Agreements")

    rows = c.execute("""
    SELECT id,title,country,brand,licenser,status,start_date,end_date,indefinite
    FROM agreements
    """).fetchall()

    for r in rows:
        with st.expander(f"{r[1]} ({r[5]})"):
            status = st.selectbox(
                "Status",
                ["Active","Expired","Terminated","Replaced"],
                index=["Active","Expired","Terminated","Replaced"].index(r[5])
            )
            if st.button("Update Status", key=r[0]):
                c.execute("UPDATE agreements SET status=? WHERE id=?", (status,r[0]))
                conn.commit()
                st.success("Updated")

# ---------------- REMINDER JOB ----------------
def reminder_job():
    today = datetime.date.today()
    rows = c.execute("""
    SELECT title,end_date FROM agreements
    WHERE end_date IS NOT NULL AND status='Active'
    """).fetchall()

    for title,end in rows:
        end = datetime.date.fromisoformat(end)
        if (end - today).days == 180:
            send_email(
                st.session_state.user,
                "License Expiry Reminder",
                f"{title} expires in 6 months"
            )