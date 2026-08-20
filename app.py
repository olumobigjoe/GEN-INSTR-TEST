import json

# IMPORTANT: The 40-question bank and correct answers are stored only in
# Streamlit Secrets (QUESTION_BANK_JSON). Do NOT put the questions or
# answer key in this public source file.
# Students receive only question text/options; correct answers remain server-side.
import os
import random
import re
import threading
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="GLT 302 — GENERAL INSTRUMENTATION",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# GLT 302 CONFIGURATION
# ============================================================
COURSE_CODE = "GLT 302"
COURSE_TITLE = "GENERAL INSTRUMENTATION"
LEVEL = "HND 1"
DEPARTMENT = "BIOCHEMISTRY"
TEST_DATE = "2026-08-22"
LOCAL_TIMEZONE = ZoneInfo("Africa/Lagos")
TOTAL_STUDENTS = 156
TOTAL_QUESTION_BANK = 40
QUESTIONS_PER_STUDENT = 20
TEST_DURATION_SECONDS = 8 * 60
AUTO_REFRESH_SECONDS = 10
RESULTS_FILE = "glt302_results.csv"
PASS_MARK = None  # Set in code only if the institution defines one.

BATCHES = [
    {"name": "Batch 1", "start": "08:00", "end": "08:30", "size": 40},
    {"name": "Batch 2", "start": "08:31", "end": "09:00", "size": 40},
    {"name": "Batch 3", "start": "09:01", "end": "09:30", "size": 40},
    {"name": "Batch 4", "start": "09:31", "end": "10:00", "size": 36},
]

RESULT_COLUMNS = [
    "Timestamp", "Name", "Gender", "Matric Number", "Course",
    "Course Title", "Department", "Level", "Batch", "Score",
    "Total Questions", "Correct Answers", "Wrong Answers", "Percentage",
    "Time Used", "Time Seconds", "Status", "Session Type"
]

csv_lock = threading.Lock()

# ============================================================
# STYLING
# ============================================================
st.markdown(
    """
    <style>
    .block-container {max-width: 1200px; padding-top: 1.5rem; padding-bottom: 2rem;}
    .main-title {text-align:center; font-size:36px; font-weight:800; line-height:1.35; margin-top:6px; margin-bottom:2px; padding-top:4px;}
    .main-subtitle {text-align:center; font-size:16px; margin-bottom:20px;}
    .timer-box {text-align:center; font-size:30px; font-weight:800; padding:10px; margin-bottom:8px;}
    .login-info {padding:14px 16px; border-radius:8px; margin-bottom:18px; background:#eef6ff;}
    .footer-text {text-align:center; color:#6b7280; font-size:12px; margin-top:25px;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ============================================================
# SESSION STATE
# ============================================================
DEFAULTS = {
    "exam_started": False,
    "exam_submitted": False,
    "student_name": "",
    "student_gender": "",
    "student_matric": "",
    "student_batch": "",
    "exam_questions": [],
    "answers": {},
    "start_time": None,
    "result": None,
    "lecturer_authenticated": False,
    "show_lecturer_login": False,
}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ============================================================
# PRIVATE CONFIGURATION / QUESTION BANK
# ============================================================
def secret_text(name, default=None):
    """Read a Streamlit Secret using the exact configured key name."""
    try:
        if name in st.secrets:
            value = st.secrets[name]
            if value is None:
                return default
            return str(value).strip()
    except Exception:
        pass
    return default


def available_secret_keys():
    """Return the top-level Secret key names currently visible to the app.

    Only key *names* are returned (never values), so this is safe to show
    on-screen. This exists purely to diagnose configuration problems such
    as a typo'd key name or a key nested under a [section] header instead
    of being set at the top level.
    """
    try:
        return sorted(list(st.secrets.keys()))
    except Exception:
        return []


def lecturer_password_from_secrets():
    """Return the lecturer password from the canonical Secret key.

    Canonical Streamlit Secret name:
        LECTURER_PASSWORD

    A lowercase fallback is accepted only to make deployment tolerant of
    an existing `lecturer_password` Secret. The recommended/canonical
    configuration remains `LECTURER_PASSWORD`.
    """
    password = secret_text("LECTURER_PASSWORD")
    if password:
        return password

    # Backward-compatible fallback for an already-created lowercase secret.
    password = secret_text("lecturer_password")
    return password


def parse_json_secret(name):
    raw = secret_text(name)
    if not raw:
        return None, f"{name} is not configured in Streamlit Secrets."
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, f"{name} contains invalid JSON: {exc}"


def normalise_question_bank(raw):
    if isinstance(raw, dict) and "questions" in raw:
        raw = raw["questions"]
    if not isinstance(raw, list):
        raise ValueError("Question bank must be a JSON list or an object containing 'questions'.")
    if len(raw) < QUESTIONS_PER_STUDENT:
        raise ValueError(f"At least {QUESTIONS_PER_STUDENT} valid questions are required.")

    cleaned = []
    for idx, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Question {idx} is not an object.")
        text = item.get("question") or item.get("text")
        options = item.get("options")
        answer = item.get("answer", item.get("correct_answer"))
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Question {idx} has no valid question text.")
        if isinstance(options, dict):
            options_list = [options[k] for k in ("A", "B", "C") if k in options]
        elif isinstance(options, list):
            options_list = options
        else:
            raise ValueError(f"Question {idx} has invalid options.")
        if len(options_list) != 3 or any(not isinstance(x, str) for x in options_list):
            raise ValueError(f"Question {idx} must contain exactly 3 text options.")

        # Accept either the full correct option text or A/B/C.
        if isinstance(answer, str) and answer.strip().upper() in {"A", "B", "C"}:
            answer_text = options_list["ABC".index(answer.strip().upper())]
        else:
            answer_text = str(answer).strip() if answer is not None else ""
        if answer_text not in options_list:
            raise ValueError(f"Question {idx} has a correct answer that does not match its options.")

        cleaned.append({"question": text.strip(), "options": options_list, "answer": answer_text})
    return cleaned


def load_question_bank():
    raw, error = parse_json_secret("QUESTION_BANK_JSON")
    if error:
        return None, error
    try:
        bank = normalise_question_bank(raw)
        if len(bank) != TOTAL_QUESTION_BANK:
            raise ValueError(f"Exactly {TOTAL_QUESTION_BANK} questions are required; {len(bank)} were found.")
        return bank, None
    except Exception as exc:
        return None, str(exc)


QUESTION_BANK, QUESTION_BANK_ERROR = load_question_bank()

# ============================================================
# AUTHORIZED STUDENTS / BATCH ASSIGNMENT
# ============================================================
def normalise_matric(value):
    return str(value).strip().upper()


def build_default_authorized_students():
    """Build the stated GLT 302 roster.

    The supplied allocation is 0001–0155 plus 0301 as the final student.
    That produces 156 students, matching the requested 40+40+40+36 batches.
    Batch 4 therefore contains 0121–0155 plus 0301.
    """
    roster = [f"FPA/BC/25/3-{i:04d}" for i in range(1, 156)]
    roster.append("FPA/BC/25/3-0301")
    mapping = {}
    cursor = 0
    for batch in BATCHES:
        for matric in roster[cursor:cursor + batch["size"]]:
            mapping[matric] = batch["name"]
        cursor += batch["size"]
    return mapping


def load_authorized_students():
    """Load an optional explicit roster from Secrets, otherwise use the supplied roster."""
    raw, error = parse_json_secret("AUTHORIZED_STUDENTS_JSON")
    if error:
        return build_default_authorized_students(), None

    mapping = {}
    try:
        if isinstance(raw, dict):
            for matric, batch in raw.items():
                mapping[normalise_matric(matric)] = str(batch).strip()
        elif isinstance(raw, list):
            if all(isinstance(x, str) for x in raw):
                if len(raw) != TOTAL_STUDENTS:
                    raise ValueError(f"Exactly {TOTAL_STUDENTS} authorized students are required; {len(raw)} were found.")
                cursor = 0
                for batch in BATCHES:
                    for matric in raw[cursor:cursor + batch["size"]]:
                        mapping[normalise_matric(matric)] = batch["name"]
                    cursor += batch["size"]
            else:
                for item in raw:
                    matric = normalise_matric(item.get("matric", item.get("Matric Number", "")))
                    batch = str(item.get("batch", item.get("Batch", ""))).strip()
                    if matric and batch:
                        mapping[matric] = batch
        else:
            raise ValueError("AUTHORIZED_STUDENTS_JSON must be an object or list.")

        valid_names = {b["name"] for b in BATCHES}
        if len(mapping) != TOTAL_STUDENTS:
            raise ValueError(f"Exactly {TOTAL_STUDENTS} authorized students are required; {len(mapping)} were found.")
        if any(batch not in valid_names for batch in mapping.values()):
            raise ValueError("Every student must be assigned to Batch 1, Batch 2, Batch 3, or Batch 4.")
        if len(set(mapping)) != TOTAL_STUDENTS:
            raise ValueError("Duplicate matriculation numbers detected.")
        return mapping, None
    except Exception as exc:
        return {}, str(exc)


AUTHORIZED_STUDENTS, AUTH_ERROR = load_authorized_students()

# ============================================================
# TIME / BATCH HELPERS
# ============================================================
def now_lagos():
    return datetime.now(LOCAL_TIMEZONE)


def parse_hhmm(value):
    h, m = [int(x) for x in value.split(":")]
    return h * 60 + m


def batch_by_name(name):
    return next((b for b in BATCHES if b["name"] == name), None)


def batch_access_status(batch_name, now=None):
    now = now or now_lagos()
    if now.strftime("%Y-%m-%d") != TEST_DATE:
        return False, "The GLT 302 test is not scheduled for today."
    batch = batch_by_name(batch_name)
    if not batch:
        return False, "Your batch assignment is invalid."
    current = now.hour * 60 + now.minute
    start = parse_hhmm(batch["start"])
    end = parse_hhmm(batch["end"])
    if current < start:
        return False, f"Your {batch_name} access window opens at {batch['start']} Nigeria time."
    if current > end:
        return False, f"Your {batch_name} access window has closed."
    return True, ""


def validate_matric(matric):
    matric = normalise_matric(matric)
    # FPA/BC/25/3-0001 is 16 characters.
    return bool(re.fullmatch(r"FPA/BC/25/3-\d{4}", matric)) and len(matric) == 16

# ============================================================
# RESULTS / DUPLICATE SAFETY
# ============================================================
def initialise_results_file():
    if not os.path.exists(RESULTS_FILE):
        pd.DataFrame(columns=RESULT_COLUMNS).to_csv(RESULTS_FILE, index=False)


def load_results_safe():
    try:
        initialise_results_file()
        if os.path.getsize(RESULTS_FILE) == 0:
            return pd.DataFrame(columns=RESULT_COLUMNS), None
        df = pd.read_csv(RESULTS_FILE, dtype=str)
        for col in RESULT_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        return df, None
    except Exception as exc:
        return pd.DataFrame(columns=RESULT_COLUMNS), str(exc)


def matric_has_attempted(matric):
    df, _ = load_results_safe()
    if df.empty or "Matric Number" not in df.columns:
        return False
    return df["Matric Number"].astype(str).str.strip().str.upper().eq(normalise_matric(matric)).any()


def time_to_seconds(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [int(float(p)) for p in parts]
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        if len(nums) == 3:
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
    except Exception:
        return None
    return None


def ensure_time_seconds(df):
    """Permanently prevents the Time Seconds KeyError."""
    if "Time Seconds" not in df.columns:
        df["Time Seconds"] = pd.NA
    if "Time Used" in df.columns:
        missing = df["Time Seconds"].isna() | (df["Time Seconds"].astype(str).str.strip() == "")
        df.loc[missing, "Time Seconds"] = df.loc[missing, "Time Used"].apply(time_to_seconds)
    df["Time Seconds"] = pd.to_numeric(df["Time Seconds"], errors="coerce")
    return df


def save_result(result):
    initialise_results_file()
    matric = normalise_matric(result["Matric Number"])
    with csv_lock:
        try:
            existing, _ = load_results_safe()
            if not existing.empty and matric_has_attempted(matric):
                return False, "This matriculation number has already submitted an attempt."
            row = {k: result.get(k, "") for k in RESULT_COLUMNS}
            if not row["Timestamp"]:
                row["Timestamp"] = now_lagos().strftime("%Y-%m-%d %H:%M:%S")
            row["Time Seconds"] = int(row.get("Time Seconds") or 0)
            new_df = pd.DataFrame([row], columns=RESULT_COLUMNS)
            updated = pd.concat([existing[RESULT_COLUMNS], new_df], ignore_index=True)
            updated.to_csv(RESULTS_FILE, index=False)
            return True, "Saved"
        except Exception as exc:
            return False, str(exc)

# ============================================================
# TEST CREATION / SCORING
# ============================================================
def create_student_test():
    selected = random.sample(QUESTION_BANK, QUESTIONS_PER_STUDENT)
    result = []
    for q in selected:
        opts = list(q["options"])
        random.shuffle(opts)
        result.append({"question": q["question"], "options": opts, "answer": q["answer"]})
    random.shuffle(result)
    return result


def score_current_test():
    correct = 0
    for i, q in enumerate(st.session_state.exam_questions):
        if st.session_state.answers.get(i) == q["answer"]:
            correct += 1
    wrong = QUESTIONS_PER_STUDENT - correct
    percentage = (correct / QUESTIONS_PER_STUDENT) * 100
    elapsed = min(int(time.time() - st.session_state.start_time), TEST_DURATION_SECONDS)
    return correct, wrong, percentage, elapsed


def format_seconds(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def submit_current_test(status="Submitted"):
    correct, wrong, percentage, elapsed = score_current_test()
    result = {
        "Timestamp": now_lagos().strftime("%Y-%m-%d %H:%M:%S"),
        "Name": st.session_state.student_name,
        "Gender": st.session_state.student_gender,
        "Matric Number": st.session_state.student_matric,
        "Course": COURSE_CODE,
        "Course Title": COURSE_TITLE,
        "Department": DEPARTMENT,
        "Level": LEVEL,
        "Batch": st.session_state.student_batch,
        "Score": round(percentage, 2),
        "Total Questions": QUESTIONS_PER_STUDENT,
        "Correct Answers": correct,
        "Wrong Answers": wrong,
        "Percentage": round(percentage, 2),
        "Time Used": format_seconds(elapsed),
        "Time Seconds": elapsed,
        "Status": status,
        "Session Type": "Regular",
    }
    saved, message = save_result(result)
    if saved:
        st.session_state.result = {"correct": correct, "wrong": wrong, "score": percentage, "time_used": format_seconds(elapsed), "status": status}
        st.session_state.exam_submitted = True
        st.session_state.exam_started = False
        return True, message
    return False, message

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown(f"### 📝 {COURSE_CODE}")
    st.caption(COURSE_TITLE)
    st.markdown("---")
    st.markdown("**Test Information**")
    st.write(f"Date: **22 August 2026**")
    st.write("Nigeria time: **Africa/Lagos**")
    st.write(f"Question Bank: **{TOTAL_QUESTION_BANK}**")
    st.write(f"Questions per Student: **{QUESTIONS_PER_STUDENT}**")
    st.write("Duration: **8 minutes per student**")
    st.write(f"Students: **{TOTAL_STUDENTS}**")
    st.markdown("---")
    st.markdown("**Batch Schedule**")
    for b in BATCHES:
        st.markdown(f"**{b['name']}** — {b['start']}–{b['end']}")
    st.caption("All times are Nigeria/Lagos time.")
    st.markdown("---")
    if st.button("👨‍🏫 Lecturer / Admin Access", use_container_width=True):
        st.session_state.show_lecturer_login = True
        st.rerun()

# ============================================================
# LECTURER LOGIN
# ============================================================
if st.session_state.show_lecturer_login and not st.session_state.lecturer_authenticated:
    st.markdown('<div class="main-title">👨‍🏫 Lecturer / Admin Access</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="main-subtitle">{COURSE_CODE} — {COURSE_TITLE}</div>', unsafe_allow_html=True)
    password = st.text_input("Lecturer Password", type="password")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔐 Login", use_container_width=True):
            correct_password = lecturer_password_from_secrets()
            if correct_password and password == correct_password:
                st.session_state.lecturer_authenticated = True
                st.session_state.show_lecturer_login = False
                st.rerun()
            elif not correct_password:
                st.error(
                    "Lecturer password is not configured in Streamlit Secrets. "
                    "Add the exact key: LECTURER_PASSWORD"
                )
                visible_keys = available_secret_keys()
                if visible_keys:
                    st.warning(
                        "Debug info — Secret **key names** this app can currently see "
                        f"(no values shown): {', '.join(f'`{k}`' for k in visible_keys)}.\n\n"
                        "If `LECTURER_PASSWORD` is not in that list exactly as written, "
                        "it either wasn't saved to **this** app's Secrets, or it's nested "
                        "under a `[section]` header in the TOML instead of being at the top level."
                    )
                else:
                    st.warning(
                        "Debug info — Streamlit Cloud is not exposing **any** Secrets to this app. "
                        "This usually means: (1) you edited Secrets on a different app/deployment, "
                        "(2) the app hasn't been rebooted since you saved the Secrets, or "
                        "(3) the Secrets panel was saved with a syntax error (check for a red "
                        "error banner in the Secrets editor)."
                    )
            else:
                st.error("Incorrect lecturer password.")
    with c2:
        if st.button("← Back to Student Portal", use_container_width=True):
            st.session_state.show_lecturer_login = False
            st.rerun()
    st.stop()

# ============================================================
# LECTURER DASHBOARD
# ============================================================
if st.session_state.lecturer_authenticated:
    st.markdown(f'<div class="main-title">📊 {COURSE_CODE} — {COURSE_TITLE}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="main-subtitle">{LEVEL} • {DEPARTMENT} • Lecturer / Admin Dashboard</div>', unsafe_allow_html=True)

    df, load_error = load_results_safe()
    df = ensure_time_seconds(df)
    tabs = st.tabs([
        "Overview", "Descriptive Analysis", "Visualizations", "Batch Analysis",
        "Makeup/Retake Analysis", "Gender Analysis", "Time Analysis", "Student Results",
        "Reports", "Question Bank Verification", "Session Control"
    ])

    with tabs[0]:
        attempted = len(df)
        st.metric("Students Attempted", attempted)
        st.metric("Students Not Attempted", max(TOTAL_STUDENTS - attempted, 0))
        if df.empty:
            st.info("No student results are available yet.")
        else:
            scores = pd.to_numeric(df["Score"], errors="coerce").dropna()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Mean Score", f"{scores.mean():.2f}" if not scores.empty else "—")
            c2.metric("Median Score", f"{scores.median():.2f}" if not scores.empty else "—")
            c3.metric("Minimum", f"{scores.min():.2f}" if not scores.empty else "—")
            c4.metric("Maximum", f"{scores.max():.2f}" if not scores.empty else "—")

    with tabs[1]:
        if df.empty:
            st.info("No data available for descriptive analysis.")
        else:
            scores = pd.to_numeric(df["Score"], errors="coerce").dropna()
            metrics = {
                "Students Attempted": len(df),
                "Students Not Attempted": max(TOTAL_STUDENTS - len(df), 0),
                "Mean Score": scores.mean() if not scores.empty else None,
                "Median Score": scores.median() if not scores.empty else None,
                "Standard Deviation": scores.std(ddof=1) if len(scores) > 1 else None,
                "Minimum Score": scores.min() if not scores.empty else None,
                "Maximum Score": scores.max() if not scores.empty else None,
                "Score Range": (scores.max() - scores.min()) if not scores.empty else None,
                "Completion Rate %": (len(df) / TOTAL_STUDENTS) * 100,
            }
            st.dataframe(pd.DataFrame([metrics]), use_container_width=True, hide_index=True)
            if PASS_MARK is not None:
                passed = (scores >= PASS_MARK).sum()
                failed = (scores < PASS_MARK).sum()
                st.write({"Pass": int(passed), "Fail": int(failed)})

    with tabs[2]:
        if df.empty:
            st.info("No data available for visualizations.")
        else:
            scores = pd.to_numeric(df["Score"], errors="coerce").dropna()
            st.subheader("Score Distribution")
            st.bar_chart(scores.value_counts().sort_index())
            st.subheader("Batch Comparison")
            batch_scores = df.assign(ScoreNum=pd.to_numeric(df["Score"], errors="coerce")).groupby("Batch")["ScoreNum"].mean()
            st.bar_chart(batch_scores)
            st.subheader("Gender Distribution")
            st.bar_chart(df["Gender"].value_counts())
            st.subheader("Time Used")
            time_data = ensure_time_seconds(df.copy())["Time Seconds"].dropna()
            if not time_data.empty:
                st.bar_chart(time_data.reset_index(drop=True))

    with tabs[3]:
        if df.empty:
            st.info("No batch data available.")
        else:
            work = df.copy()
            work["ScoreNum"] = pd.to_numeric(work["Score"], errors="coerce")
            rows = []
            for b in BATCHES:
                x = work[work["Batch"] == b["name"]]
                s = x["ScoreNum"].dropna()
                rows.append({
                    "Batch": b["name"], "Students": len(x), "Attempted": len(x),
                    "Mean": s.mean() if not s.empty else None,
                    "Median": s.median() if not s.empty else None,
                    "Minimum": s.min() if not s.empty else None,
                    "Maximum": s.max() if not s.empty else None,
                    "Std Dev": s.std(ddof=1) if len(s) > 1 else None,
                    "Completion Rate %": (len(x) / max(1, sum(1 for v in AUTHORIZED_STUDENTS.values() if v == b["name"]))) * 100 if AUTHORIZED_STUDENTS else None,
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tabs[4]:
        if df.empty or "Session Type" not in df.columns:
            st.info("No makeup/retake data available.")
        else:
            st.dataframe(df[df["Session Type"].astype(str).str.contains("Makeup|Retake", case=False, na=False)], use_container_width=True, hide_index=True)

    with tabs[5]:
        if df.empty:
            st.info("No gender data available.")
        else:
            work = df.copy()
            work["ScoreNum"] = pd.to_numeric(work["Score"], errors="coerce")
            st.dataframe(work.groupby("Gender").agg(Students=("Matric Number", "count"), Mean_Score=("ScoreNum", "mean"), Median_Score=("ScoreNum", "median")).reset_index(), use_container_width=True, hide_index=True)

    with tabs[6]:
        if df.empty:
            st.info("No time data available.")
        else:
            work = ensure_time_seconds(df.copy())
            valid = work.dropna(subset=["Time Seconds"]).copy()
            if valid.empty:
                st.warning("No valid Time Used values are available.")
            else:
                st.dataframe(valid.sort_values("Time Seconds", ascending=False), use_container_width=True, hide_index=True)

    with tabs[7]:
        if df.empty:
            st.info("No student results have been submitted yet.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)

    with tabs[8]:
        if df.empty:
            st.info("No report data available.")
        else:
            report = df.copy()
            report = ensure_time_seconds(report)
            st.download_button("⬇️ Download Results CSV", report.to_csv(index=False).encode("utf-8"), "glt302_results.csv", "text/csv", use_container_width=True)

    with tabs[9]:
        if QUESTION_BANK_ERROR:
            st.error(f"Question bank verification failed: {QUESTION_BANK_ERROR}")
        else:
            st.success("QUESTION BANK VERIFIED")
            c1, c2, c3 = st.columns(3)
            c1.metric("Questions in Bank", len(QUESTION_BANK))
            c2.metric("Questions per Student", QUESTIONS_PER_STUDENT)
            c3.metric("Options per Question", 3)
            st.info("Question text and correct answers are not displayed in the public/student interface.")

    with tabs[10]:
        st.subheader("Session Control")
        session_rows = [
            ("Course", COURSE_CODE), ("Course Title", COURSE_TITLE), ("Level", LEVEL),
            ("Department", DEPARTMENT), ("Test Date", "Saturday, 22 August 2026"),
            ("Overall Access", "8:00 AM – 10:00 AM"), ("Batch 1", "8:00 AM – 8:30 AM"),
            ("Batch 2", "8:31 AM – 9:00 AM"), ("Batch 3", "9:01 AM – 9:30 AM"),
            ("Batch 4", "9:31 AM – 10:00 AM"),
            ("Student Test Duration", "8 minutes"), ("Question Bank", "40 questions"),
            ("Questions per Student", "20 questions"), ("Automatic Refresh", "10 seconds"),
            ("Total Students", str(TOTAL_STUDENTS)),
        ]
        st.table(pd.DataFrame(session_rows, columns=["Setting", "Value"]))
        if AUTH_ERROR:
            st.warning(f"Authorized student allocation: {AUTH_ERROR}")
        else:
            st.success(f"Authorized student allocation loaded: {len(AUTHORIZED_STUDENTS)} students.")

    if load_error:
        st.warning(f"Results file warning: {load_error}")
    if st.button("🚪 Logout Lecturer", use_container_width=True):
        st.session_state.lecturer_authenticated = False
        st.rerun()
    st.stop()

# ============================================================
# RESULT PAGE
# ============================================================
if st.session_state.exam_submitted:
    st.markdown(f'<div class="main-title">🎉 {COURSE_CODE}</div>', unsafe_allow_html=True)
    st.success("Your test has been submitted successfully.")
    result = st.session_state.result
    c1, c2, c3 = st.columns(3)
    c1.metric("Score", f"{result['score']:.2f} / 100")
    c2.metric("Correct", f"{result['correct']} / {QUESTIONS_PER_STUDENT}")
    c3.metric("Time Used", result["time_used"])
    st.write(f"**Name:** {st.session_state.student_name}")
    st.write(f"**Matric Number:** {st.session_state.student_matric}")
    st.write(f"**Batch:** {st.session_state.student_batch}")
    st.info("Your result has been recorded. Only one attempt is permitted for each matriculation number.")
    st.stop()

# ============================================================
# STUDENT LOGIN
# ============================================================
if not st.session_state.exam_started:
    st.markdown(f'<div class="main-title">{COURSE_CODE} — {COURSE_TITLE}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="main-subtitle">{LEVEL} • {DEPARTMENT} • Online Test</div>', unsafe_allow_html=True)
    st.markdown('<div class="login-info">Enter your details below. Your batch is determined automatically from the authorized matriculation-number allocation.</div>', unsafe_allow_html=True)

    name = st.text_input("Student Name", placeholder="Enter your full name")
    gender = st.selectbox("Gender", ["Select Gender", "Male", "Female"])
    matric = st.text_input("Matric Number", placeholder="FPA/BC/25/3-0001", max_chars=16)
    st.caption("MATRIC NUMBER: exactly 16 characters • Format: FPA/BC/25/3-0001")

    if st.button("🚀 Start Test", use_container_width=True):
        name = name.strip()
        matric = normalise_matric(matric)
        if not name:
            st.error("Please enter your name.")
            st.stop()
        if gender not in {"Male", "Female"}:
            st.error("Please select your gender.")
            st.stop()
        if not validate_matric(matric):
            st.error("Invalid matriculation number. Use the complete 16-character format FPA/BC/25/3-0001.")
            st.stop()
        if AUTH_ERROR:
            st.error("The authorized student allocation is not correctly configured. Please contact the lecturer.")
            st.stop()
        batch_name = AUTHORIZED_STUDENTS.get(matric)
        if not batch_name:
            st.error("This matriculation number is not on the authorized GLT 302 student list.")
            st.stop()
        if matric_has_attempted(matric):
            st.error("This matriculation number has already submitted the GLT 302 test.")
            st.stop()
        if QUESTION_BANK_ERROR or QUESTION_BANK is None:
            st.error(f"The test cannot start because the private question bank is unavailable: {QUESTION_BANK_ERROR}")
            st.stop()
        allowed, message = batch_access_status(batch_name)
        if not allowed:
            st.error(message)
            st.info(f"Your assigned batch: {batch_name}. Access: {batch_by_name(batch_name)['start']}–{batch_by_name(batch_name)['end']} Nigeria time.")
            st.stop()

        st.session_state.student_name = name
        st.session_state.student_gender = gender
        st.session_state.student_matric = matric
        st.session_state.student_batch = batch_name
        # Generate exactly once; 10-second reruns reuse this session-state copy.
        st.session_state.exam_questions = create_student_test()
        st.session_state.answers = {}
        st.session_state.start_time = time.time()
        st.session_state.exam_started = True
        st.session_state.exam_submitted = False
        st.rerun()

    st.markdown('<div class="footer-text">GLT 302 • GENERAL INSTRUMENTATION • HND 1 BIOCHEMISTRY</div>', unsafe_allow_html=True)
    st.stop()

# ============================================================
# ACTIVE TEST — 10 SECOND FRAGMENT RERUN
# ============================================================
@st.fragment(run_every=f"{AUTO_REFRESH_SECONDS}s")
def render_active_test():
    if not st.session_state.exam_started:
        return

    elapsed = time.time() - st.session_state.start_time
    remaining = TEST_DURATION_SECONDS - int(elapsed)

    if remaining <= 0:
        ok, msg = submit_current_test("Time Expired")
        if ok:
            st.rerun()
        st.error(f"Time expired, but the result could not be saved: {msg}")
        return

    st.markdown(f'<div class="timer-box">⏱️ Time Remaining: {remaining // 60:02d}:{remaining % 60:02d}</div>', unsafe_allow_html=True)
    st.progress(min(elapsed / TEST_DURATION_SECONDS, 1.0))
    st.info(
        f"**Name:** {st.session_state.student_name}  \n"
        f"**Gender:** {st.session_state.student_gender}  \n"
        f"**Matric Number:** {st.session_state.student_matric}  \n"
        f"**Batch:** {st.session_state.student_batch}"
    )
    st.markdown("---")
    st.subheader(f"Answer all {QUESTIONS_PER_STUDENT} questions")

    for i, q in enumerate(st.session_state.exam_questions):
        st.markdown(f"### Question {i + 1} of {QUESTIONS_PER_STUDENT}")
        st.write(f"**{q['question']}**")
        current = st.session_state.answers.get(i)
        options = q["options"]
        default_index = options.index(current) if current in options else None
        selected = st.radio("Select one answer:", options, key=f"question_{i}", index=default_index)
        st.session_state.answers[i] = selected
        st.markdown("---")

    st.warning("⚠️ Once you submit the test, you cannot change your answers or attempt the test again.")
    if st.button("📤 SUBMIT TEST", use_container_width=True):
        ok, msg = submit_current_test("Submitted")
        if ok:
            st.rerun()
        else:
            st.error(f"Submission failed: {msg}")

render_active_test()
