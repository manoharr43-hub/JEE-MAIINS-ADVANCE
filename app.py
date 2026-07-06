"""
app.py
-------
Main entry point for JEE-AI-PRO.

Responsibilities:
    - One-time app setup (page config, DB init, session state defaults)
    - Authentication gate (login/signup screen if not logged in)
    - Sidebar: branding, student snapshot, navigation, logout
    - Routes to all pages under pages/ via st.navigation / st.Page

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import logging
import datetime
from typing import Optional, Dict, Any

import streamlit as st

logger = logging.getLogger("app")
logging.basicConfig(level=logging.INFO)

# --------------------------------------------------------------------------
# Project imports — defensive so the app still boots while pieces are WIP
# --------------------------------------------------------------------------
try:
    from database import init_db
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False

try:
    from auth import (
        login_student,
        register_student,
        logout as auth_logout,
        AuthError,
    )
    _AUTH_AVAILABLE = True
except ImportError:
    _AUTH_AVAILABLE = False

try:
    from config import APP_NAME, APP_ICON, SUPPORT_EMAIL
except ImportError:
    APP_NAME, APP_ICON, SUPPORT_EMAIL = "JEE-AI-PRO", "🎯", "support@jeeaipro.app"


# ==========================================================================
# One-time setup
# ==========================================================================
def configure_page() -> None:
    st.set_page_config(
        page_title=APP_NAME,
        page_icon=APP_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )


def init_session_state() -> None:
    defaults = {
        "authenticated": False,
        "student_id": None,
        "student_name": None,
        "student_email": None,
        "auth_mode": "login",  # "login" | "signup"
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def bootstrap_database() -> None:
    if _DB_AVAILABLE:
        try:
            init_db()
        except Exception as e:
            logger.error("Database init failed: %s", e)
            st.error("Could not initialize the database. Check logs / config.py.")
    else:
        st.warning("database.py not found yet — running without persistence.", icon="⚠️")


# ==========================================================================
# Auth screens
# ==========================================================================
def render_login_form() -> None:
    st.subheader("Log in")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", use_container_width=True)

    if submitted:
        if not _AUTH_AVAILABLE:
            st.error("auth.py isn't implemented yet — cannot log in.")
            return
        try:
            student = login_student(email, password)
            _set_authenticated_session(student)
            st.rerun()
        except AuthError as e:
            st.error(str(e))
        except Exception as e:
            logger.error("Login failed: %s", e)
            st.error("Something went wrong logging in. Please try again.")

    st.caption("Don't have an account?")
    if st.button("Create one →", use_container_width=True):
        st.session_state["auth_mode"] = "signup"
        st.rerun()


def render_signup_form() -> None:
    st.subheader("Create your account")
    with st.form("signup_form"):
        name = st.text_input("Full name")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        confirm = st.text_input("Confirm password", type="password")
        target_exam = st.selectbox("Target exam", ["JEE Main", "JEE Advanced", "JEE Main + Advanced"])
        class_level = st.selectbox("Class", ["11th", "12th", "Dropper"])
        submitted = st.form_submit_button("Sign up", use_container_width=True)

    if submitted:
        if not name or not email or not password:
            st.error("Please fill in all fields.")
        elif password != confirm:
            st.error("Passwords don't match.")
        elif not _AUTH_AVAILABLE:
            st.error("auth.py isn't implemented yet — cannot create an account.")
        else:
            try:
                student = register_student(
                    name=name, email=email, password=password,
                    target_exam=target_exam, class_level=class_level,
                )
                _set_authenticated_session(student)
                st.rerun()
            except AuthError as e:
                st.error(str(e))
            except Exception as e:
                logger.error("Signup failed: %s", e)
                st.error("Something went wrong creating your account. Please try again.")

    st.caption("Already have an account?")
    if st.button("Log in instead →", use_container_width=True):
        st.session_state["auth_mode"] = "login"
        st.rerun()


def _set_authenticated_session(student: Dict[str, Any]) -> None:
    st.session_state["authenticated"] = True
    st.session_state["student_id"] = student.get("id")
    st.session_state["student_name"] = student.get("name")
    st.session_state["student_email"] = student.get("email")


def render_auth_gate() -> None:
    st.markdown(
        f"<h1 style='text-align:center'>{APP_ICON} {APP_NAME}</h1>"
        "<p style='text-align:center;color:gray'>Your AI-powered JEE prep companion</p>",
        unsafe_allow_html=True,
    )
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        if st.session_state["auth_mode"] == "signup":
            render_signup_form()
        else:
            render_login_form()


# ==========================================================================
# Sidebar
# ==========================================================================
def render_sidebar() -> None:
    with st.sidebar:
        st.markdown(f"### {APP_ICON} {APP_NAME}")
        st.divider()
        st.markdown(f"**{st.session_state['student_name']}**")
        st.caption(st.session_state["student_email"])
        st.caption(f"Logged in · {datetime.date.today().strftime('%b %d, %Y')}")
        st.divider()

        if st.button("🚪 Log out", use_container_width=True):
            if _AUTH_AVAILABLE:
                try:
                    auth_logout(st.session_state["student_id"])
                except Exception as e:
                    logger.warning("Logout cleanup failed: %s", e)
            for key in ("authenticated", "student_id", "student_name", "student_email"):
                st.session_state[key] = None
            st.session_state["authenticated"] = False
            st.session_state["auth_mode"] = "login"
            st.rerun()

        st.divider()
        st.caption(f"Need help? {SUPPORT_EMAIL}")


# ==========================================================================
# Navigation / routing
# ==========================================================================
def build_navigation() -> st.navigation:
    """
    Groups pages the way a student actually thinks about the app.
    Requires each pages/*.py file to expose the page content when run
    (they already call main() at import time, matching the pattern used
    in pages/dashboard.py).
    """
    dashboard = st.Page("pages/dashboard.py", title="Dashboard", icon="🏠", default=True)

    study_pages = [
        st.Page("pages/physics.py", title="Physics", icon="⚛️"),
        st.Page("pages/chemistry.py", title="Chemistry", icon="🧪"),
        st.Page("pages/mathematics.py", title="Mathematics", icon="📐"),
        st.Page("pages/notes.py", title="Notes", icon="📝"),
        st.Page("pages/formulas.py", title="Formula Sheet", icon="📖"),
    ]

    practice_pages = [
        st.Page("pages/question_bank.py", title="Question Bank", icon="📚"),
        st.Page("pages/pyq.py", title="Previous Year Qs", icon="🗂️"),
        st.Page("pages/mock_tests.py", title="Mock Tests", icon="📝"),
        st.Page("pages/revision.py", title="Revision", icon="🔁"),
    ]

    ai_pages = [
        st.Page("pages/ai_teacher.py", title="AI Teacher", icon="🧠"),
    ]

    progress_pages = [
        st.Page("pages/analytics.py", title="Analytics", icon="📊"),
        st.Page("pages/study_planner.py", title="Study Planner", icon="📅"),
        st.Page("pages/leaderboard.py", title="Leaderboard", icon="🏆"),
    ]

    account_pages = [
        st.Page("pages/profile.py", title="Profile", icon="👤"),
        st.Page("pages/parent_dashboard.py", title="Parent Dashboard", icon="👪"),
        st.Page("pages/settings.py", title="Settings", icon="⚙️"),
    ]

    if st.session_state.get("student_email") and _is_admin(st.session_state["student_email"]):
        account_pages.append(st.Page("pages/admin.py", title="Admin", icon="🛠️"))

    return st.navigation(
        {
            "": [dashboard],
            "Study": study_pages,
            "Practice": practice_pages,
            "AI Tools": ai_pages,
            "Progress": progress_pages,
            "Account": account_pages,
        }
    )


def _is_admin(email: str) -> bool:
    try:
        from config import ADMIN_EMAILS
        return email in ADMIN_EMAILS
    except ImportError:
        return False


# ==========================================================================
# Main
# ==========================================================================
def main() -> None:
    configure_page()
    init_session_state()
    bootstrap_database()

    if not st.session_state["authenticated"]:
        render_auth_gate()
        return

    render_sidebar()
    nav = build_navigation()
    nav.run()


if __name__ == "__main__":
    main()
