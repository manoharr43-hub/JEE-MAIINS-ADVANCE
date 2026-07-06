"""
pages/dashboard.py
-------------------
Main student dashboard for JEE-AI-PRO.

Shows, at a glance:
    - Welcome header + streak/study-time summary
    - Overall progress across Physics / Chemistry / Mathematics
    - Today's study plan (from ai_study_planner)
    - Recent activity (mock tests, doubts solved, notes read)
    - Quick-action shortcuts to other pages
    - Performance snapshot chart + weak-topic alerts

This file is written for a Streamlit multipage app (`app.py` as the entry
point, pages under `pages/`). It degrades gracefully with demo data if the
database/model layers aren't wired up yet, so it can be dropped in early.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

import streamlit as st

logger = logging.getLogger("dashboard")

# --------------------------------------------------------------------------
# Optional project integrations — imported defensively
# --------------------------------------------------------------------------
try:
    from models.student import get_current_student, Student
    _STUDENT_MODEL_AVAILABLE = True
except ImportError:
    _STUDENT_MODEL_AVAILABLE = False

try:
    from models.progress import get_subject_progress, get_recent_activity
    _PROGRESS_MODEL_AVAILABLE = True
except ImportError:
    _PROGRESS_MODEL_AVAILABLE = False

try:
    from utils.charts import (
        render_progress_donut,
        render_performance_trend,
        render_subject_radar,
    )
    _CHARTS_AVAILABLE = True
except ImportError:
    _CHARTS_AVAILABLE = False

try:
    from ai.ai_study_planner import get_today_plan
    _PLANNER_AVAILABLE = True
except ImportError:
    _PLANNER_AVAILABLE = False

try:
    from ai.ai_recommendation import get_weak_topics
    _RECOMMENDATION_AVAILABLE = True
except ImportError:
    _RECOMMENDATION_AVAILABLE = False


PAGE_TITLE = "Dashboard — JEE-AI-PRO"
SUBJECTS = ["Physics", "Chemistry", "Mathematics"]


# --------------------------------------------------------------------------
# Demo/fallback data (used only when the real layers aren't available yet)
# --------------------------------------------------------------------------
def _demo_student() -> Dict[str, Any]:
    return {
        "name": "Student",
        "target_exam": "JEE Main 2027",
        "streak_days": 12,
        "study_minutes_today": 95,
        "rank_estimate": 18450,
    }


def _demo_progress() -> Dict[str, float]:
    return {"Physics": 62.0, "Chemistry": 48.0, "Mathematics": 71.0}


def _demo_activity() -> List[Dict[str, Any]]:
    now = datetime.datetime.now()
    return [
        {"type": "Mock Test", "detail": "JEE Main Full Test #14", "score": "168/300",
         "time": now - datetime.timedelta(hours=3)},
        {"type": "Doubt Solved", "detail": "Rotational Mechanics — torque question",
         "time": now - datetime.timedelta(hours=6)},
        {"type": "Notes Read", "detail": "Chemical Bonding — Chapter 4",
         "time": now - datetime.timedelta(days=1)},
    ]


def _demo_plan() -> List[Dict[str, str]]:
    return [
        {"time": "6:00 PM", "task": "Physics — Rotational Mechanics (revision)"},
        {"time": "7:15 PM", "task": "30 Chemistry PYQ questions"},
        {"time": "8:30 PM", "task": "Mathematics — Integration practice set"},
    ]


def _demo_weak_topics() -> List[Dict[str, Any]]:
    return [
        {"subject": "Chemistry", "topic": "Electrochemistry", "accuracy": 41},
        {"subject": "Physics", "topic": "Rotational Mechanics", "accuracy": 53},
        {"subject": "Mathematics", "topic": "Probability", "accuracy": 58},
    ]


# --------------------------------------------------------------------------
# Data loading (real layer if available, demo fallback otherwise)
# --------------------------------------------------------------------------
def load_dashboard_data() -> Dict[str, Any]:
    data: Dict[str, Any] = {}

    if _STUDENT_MODEL_AVAILABLE:
        try:
            student = get_current_student()
            data["student"] = student.to_dict() if hasattr(student, "to_dict") else student
        except Exception as e:
            logger.warning("get_current_student() failed (%s); using demo data.", e)
            data["student"] = _demo_student()
    else:
        data["student"] = _demo_student()

    if _PROGRESS_MODEL_AVAILABLE:
        try:
            data["progress"] = get_subject_progress(data["student"].get("id", "demo"))
            data["activity"] = get_recent_activity(data["student"].get("id", "demo"), limit=5)
        except Exception as e:
            logger.warning("Progress model failed (%s); using demo data.", e)
            data["progress"] = _demo_progress()
            data["activity"] = _demo_activity()
    else:
        data["progress"] = _demo_progress()
        data["activity"] = _demo_activity()

    if _PLANNER_AVAILABLE:
        try:
            data["plan"] = get_today_plan(data["student"].get("id", "demo"))
        except Exception as e:
            logger.warning("Study planner failed (%s); using demo data.", e)
            data["plan"] = _demo_plan()
    else:
        data["plan"] = _demo_plan()

    if _RECOMMENDATION_AVAILABLE:
        try:
            data["weak_topics"] = get_weak_topics(data["student"].get("id", "demo"))
        except Exception as e:
            logger.warning("Recommendation engine failed (%s); using demo data.", e)
            data["weak_topics"] = _demo_weak_topics()
    else:
        data["weak_topics"] = _demo_weak_topics()

    return data


# --------------------------------------------------------------------------
# UI sections
# --------------------------------------------------------------------------
def render_header(student: Dict[str, Any]) -> None:
    col1, col2 = st.columns([3, 1])
    with col1:
        hour = datetime.datetime.now().hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        st.title(f"{greeting}, {student.get('name', 'Student')} 👋")
        st.caption(f"Target: {student.get('target_exam', 'JEE')}")
    with col2:
        st.metric("🔥 Streak", f"{student.get('streak_days', 0)} days")


def render_summary_metrics(student: Dict[str, Any], progress: Dict[str, float]) -> None:
    col1, col2, col3, col4 = st.columns(4)
    overall = sum(progress.values()) / len(progress) if progress else 0
    col1.metric("Study time today", f"{student.get('study_minutes_today', 0)} min")
    col2.metric("Overall syllabus", f"{overall:.0f}%")
    col3.metric("Estimated rank", f"~{student.get('rank_estimate', 0):,}")
    weakest = min(progress, key=progress.get) if progress else "—"
    col4.metric("Focus area", weakest)


def render_progress_section(progress: Dict[str, float]) -> None:
    st.subheader("📊 Subject Progress")
    if _CHARTS_AVAILABLE:
        render_subject_radar(progress)
    else:
        cols = st.columns(len(progress))
        for col, (subject, pct) in zip(cols, progress.items()):
            with col:
                st.write(f"**{subject}**")
                st.progress(min(int(pct), 100) / 100)
                st.caption(f"{pct:.0f}% complete")


def render_today_plan(plan: List[Dict[str, str]]) -> None:
    st.subheader("📅 Today's Study Plan")
    if not plan:
        st.info("No plan generated yet. Visit Study Planner to create one.")
        return
    for item in plan:
        st.checkbox(f"**{item['time']}** — {item['task']}", key=f"plan_{item['time']}_{item['task']}")
    if st.button("Open full Study Planner →"):
        st.switch_page("pages/study_planner.py")


def render_weak_topics(weak_topics: List[Dict[str, Any]]) -> None:
    st.subheader("⚠️ Topics Needing Attention")
    if not weak_topics:
        st.success("No weak topics flagged right now — keep it up!")
        return
    for wt in weak_topics:
        c1, c2, c3 = st.columns([2, 3, 2])
        c1.write(f"**{wt['subject']}**")
        c2.write(wt["topic"])
        c3.write(f"{wt['accuracy']}% accuracy")


def render_recent_activity(activity: List[Dict[str, Any]]) -> None:
    st.subheader("🕘 Recent Activity")
    if not activity:
        st.caption("No recent activity yet.")
        return
    for item in activity:
        time_str = item["time"].strftime("%b %d, %I:%M %p") if isinstance(item["time"], datetime.datetime) else str(item["time"])
        line = f"**{item['type']}** — {item['detail']}"
        if item.get("score"):
            line += f"  ·  Score: {item['score']}"
        st.write(line)
        st.caption(time_str)
        st.divider()


def render_quick_actions() -> None:
    st.subheader("⚡ Quick Actions")
    actions = [
        ("🧠 Ask AI Teacher", "pages/ai_teacher.py"),
        ("📝 Take a Mock Test", "pages/mock_tests.py"),
        ("📚 Question Bank", "pages/question_bank.py"),
        ("📄 Previous Year Qs", "pages/pyq.py"),
        ("📖 Formula Sheet", "pages/formulas.py"),
        ("🔁 Revision", "pages/revision.py"),
    ]
    cols = st.columns(3)
    for i, (label, target) in enumerate(actions):
        with cols[i % 3]:
            if st.button(label, use_container_width=True):
                st.switch_page(target)


# --------------------------------------------------------------------------
# Page entry point
# --------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title=PAGE_TITLE, page_icon="🎯", layout="wide")

    data = load_dashboard_data()

    render_header(data["student"])
    st.divider()
    render_summary_metrics(data["student"], data["progress"])
    st.divider()

    left, right = st.columns([2, 1])
    with left:
        render_progress_section(data["progress"])
        st.divider()
        render_recent_activity(data["activity"])
    with right:
        render_today_plan(data["plan"])
        st.divider()
        render_weak_topics(data["weak_topics"])

    st.divider()
    render_quick_actions()


if __name__ == "__main__":
    main()
else:
    # Streamlit multipage apps import/execute the page module directly
    main()
