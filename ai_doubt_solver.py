"""
ai/ai_doubt_solver.py
----------------------
AI-powered doubt solving engine for JEE-AI-PRO.

Responsibilities:
    - Accept a doubt as text and/or an image (handwritten/printed question).
    - Run OCR on images (via utils.ocr) to extract the question text.
    - Classify subject (Physics / Chemistry / Mathematics) and topic.
    - Generate a clear, step-by-step solution using Claude.
    - Attach related formulas/concepts and follow-up practice suggestions.
    - Persist the doubt + solution to the database for history/analytics.

This module is self-contained: it degrades gracefully if optional
dependencies (Anthropic SDK, OCR utils, DB layer) aren't configured yet,
so it can be dropped into the project early and wired up incrementally.
"""

from __future__ import annotations

import os
import json
import time
import logging
import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

logger = logging.getLogger("ai_doubt_solver")
logging.basicConfig(level=logging.INFO)

# --------------------------------------------------------------------------
# Optional integrations — imported defensively so this file works stand-alone
# --------------------------------------------------------------------------
try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic package not installed. Run: pip install anthropic")

try:
    from utils.ocr import extract_text_from_image  # project OCR helper
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False

try:
    from database import save_doubt_record, get_student_history  # project DB layer
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
MODEL_NAME = os.getenv("AI_DOUBT_SOLVER_MODEL", "claude-sonnet-4-5")
MAX_TOKENS = 2048
SUBJECTS = ["Physics", "Chemistry", "Mathematics"]

SYSTEM_PROMPT = """You are an expert JEE (Main + Advanced) tutor.
A student will send you a doubt — a question they are stuck on, in Physics,
Chemistry, or Mathematics.

Respond ONLY with a single JSON object (no markdown fences, no preamble) with
this exact schema:

{
  "subject": "Physics | Chemistry | Mathematics",
  "topic": "specific chapter/topic name",
  "difficulty": "Easy | Medium | Hard",
  "restated_question": "clean restatement of the question",
  "concepts_used": ["concept 1", "concept 2"],
  "key_formulas": ["formula 1", "formula 2"],
  "steps": [
    {"step_number": 1, "explanation": "...", "work": "..."},
    {"step_number": 2, "explanation": "...", "work": "..."}
  ],
  "final_answer": "the final answer, clearly stated",
  "common_mistakes": ["mistake students often make"],
  "similar_question_suggestion": "a short similar practice question"
}

Rules:
- Be rigorous and exam-accurate; use correct JEE-level notation.
- Keep each step's explanation short (1-2 sentences); put derivations/math in "work".
- If the question is ambiguous or the image OCR text is garbled, make your
  best reasonable interpretation and note the assumption inside
  "restated_question" rather than refusing.
- Never include text outside the JSON object.
"""


@dataclass
class DoubtSolution:
    subject: str
    topic: str
    difficulty: str
    restated_question: str
    concepts_used: List[str]
    key_formulas: List[str]
    steps: List[Dict[str, Any]]
    final_answer: str
    common_mistakes: List[str]
    similar_question_suggestion: str
    raw_question: str
    solved_at: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    source: str = "text"  # "text" or "image"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        """Render solution as Markdown for display in a Streamlit page."""
        lines = [
            f"### 📘 {self.subject} — {self.topic}  \n*Difficulty: {self.difficulty}*",
            f"\n**Question:** {self.restated_question}\n",
        ]
        if self.concepts_used:
            lines.append("**Concepts used:** " + ", ".join(self.concepts_used))
        if self.key_formulas:
            lines.append("\n**Key formulas:**")
            lines += [f"- `{f}`" for f in self.key_formulas]

        lines.append("\n**Step-by-step solution:**")
        for step in self.steps:
            lines.append(f"\n**Step {step.get('step_number')}:** {step.get('explanation')}")
            if step.get("work"):
                lines.append(f"```\n{step['work']}\n```")

        lines.append(f"\n✅ **Final Answer:** {self.final_answer}")

        if self.common_mistakes:
            lines.append("\n⚠️ **Common mistakes to avoid:**")
            lines += [f"- {m}" for m in self.common_mistakes]

        if self.similar_question_suggestion:
            lines.append(f"\n🎯 **Try this next:** {self.similar_question_suggestion}")

        return "\n".join(lines)


class AIDoubtSolverError(Exception):
    """Raised when the doubt solver cannot produce a valid solution."""


class AIDoubtSolver:
    """
    Main entry point for solving student doubts.

    Usage:
        solver = AIDoubtSolver(api_key=os.getenv("ANTHROPIC_API_KEY"))
        solution = solver.solve_text("A block of mass 2kg slides down a...")
        solution = solver.solve_image("/path/to/doubt_photo.jpg")
    """

    def __init__(self, api_key: Optional[str] = None, model: str = MODEL_NAME):
        self.model = model
        self.client = None
        if _ANTHROPIC_AVAILABLE:
            key = api_key or os.getenv("ANTHROPIC_API_KEY")
            if key:
                self.client = anthropic.Anthropic(api_key=key)
            else:
                logger.warning("No ANTHROPIC_API_KEY found; solver will run in mock mode.")
        else:
            logger.warning("anthropic SDK unavailable; solver will run in mock mode.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def solve_text(
        self,
        question: str,
        student_id: Optional[str] = None,
        subject_hint: Optional[str] = None,
    ) -> DoubtSolution:
        """Solve a doubt submitted as plain text."""
        if not question or not question.strip():
            raise AIDoubtSolverError("Question text is empty.")

        user_prompt = question.strip()
        if subject_hint:
            user_prompt = f"[Subject hint: {subject_hint}]\n{user_prompt}"

        solution = self._call_model(user_prompt, raw_question=question, source="text")
        self._persist(solution, student_id)
        return solution

    def solve_image(
        self,
        image_path: str,
        student_id: Optional[str] = None,
        subject_hint: Optional[str] = None,
    ) -> DoubtSolution:
        """
        Solve a doubt submitted as an image (e.g. photo of handwritten
        question). Runs OCR first, then routes to the same solving pipeline.
        """
        extracted_text = self._extract_text(image_path)
        if not extracted_text.strip():
            raise AIDoubtSolverError(
                "Could not extract any readable text from the image. "
                "Try a clearer photo or type the question manually."
            )

        user_prompt = extracted_text
        if subject_hint:
            user_prompt = f"[Subject hint: {subject_hint}]\n{user_prompt}"

        solution = self._call_model(user_prompt, raw_question=extracted_text, source="image")
        self._persist(solution, student_id)
        return solution

    def get_history(self, student_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch a student's past solved doubts, if the DB layer is wired up."""
        if not _DB_AVAILABLE:
            logger.info("Database layer not available; returning empty history.")
            return []
        return get_student_history(student_id, limit=limit)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _extract_text(self, image_path: str) -> str:
        if _OCR_AVAILABLE:
            return extract_text_from_image(image_path)
        raise AIDoubtSolverError(
            "OCR utility (utils/ocr.py) is not available yet. "
            "Implement `extract_text_from_image(path) -> str` to enable image doubts."
        )

    def _call_model(self, user_prompt: str, raw_question: str, source: str) -> DoubtSolution:
        if self.client is None:
            return self._mock_solution(raw_question, source)

        for attempt in range(1, 4):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                text = "".join(
                    block.text for block in response.content if block.type == "text"
                ).strip()
                data = self._safe_json_parse(text)
                return DoubtSolution(
                    subject=data.get("subject", "Unknown"),
                    topic=data.get("topic", "Unknown"),
                    difficulty=data.get("difficulty", "Medium"),
                    restated_question=data.get("restated_question", raw_question),
                    concepts_used=data.get("concepts_used", []),
                    key_formulas=data.get("key_formulas", []),
                    steps=data.get("steps", []),
                    final_answer=data.get("final_answer", ""),
                    common_mistakes=data.get("common_mistakes", []),
                    similar_question_suggestion=data.get("similar_question_suggestion", ""),
                    raw_question=raw_question,
                    source=source,
                )
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Attempt %d: failed to parse model output (%s). Retrying...", attempt, e)
                time.sleep(1)
            except Exception as e:  # network/API errors
                logger.error("Attempt %d: API call failed (%s). Retrying...", attempt, e)
                time.sleep(2 ** attempt)

        raise AIDoubtSolverError("AI solver failed after multiple attempts. Please try again.")

    @staticmethod
    def _safe_json_parse(text: str) -> Dict[str, Any]:
        """Strip accidental code fences and parse JSON robustly."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json", "", 1).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("No JSON object found in model response.")
        return json.loads(cleaned[start : end + 1])

    def _persist(self, solution: DoubtSolution, student_id: Optional[str]) -> None:
        if student_id and _DB_AVAILABLE:
            try:
                save_doubt_record(student_id, solution.to_dict())
            except Exception as e:
                logger.error("Failed to save doubt record: %s", e)

    @staticmethod
    def _mock_solution(raw_question: str, source: str) -> DoubtSolution:
        """Fallback used when no API key/SDK is configured (dev/demo mode)."""
        return DoubtSolution(
            subject="Physics",
            topic="Demo Mode — configure ANTHROPIC_API_KEY",
            difficulty="Medium",
            restated_question=raw_question,
            concepts_used=["(mock) Kinematics"],
            key_formulas=["v = u + at"],
            steps=[
                {
                    "step_number": 1,
                    "explanation": "This is placeholder output. Set ANTHROPIC_API_KEY to get real solutions.",
                    "work": "N/A",
                }
            ],
            final_answer="Configure your API key to see the real solution.",
            common_mistakes=[],
            similar_question_suggestion="",
            raw_question=raw_question,
            source=source,
        )


# --------------------------------------------------------------------------
# Quick manual test
# --------------------------------------------------------------------------
if __name__ == "__main__":
    solver = AIDoubtSolver()
    demo_question = (
        "A particle moves in a straight line with initial velocity 5 m/s and "
        "constant acceleration 2 m/s^2. Find its velocity and displacement after 4 seconds."
    )
    result = solver.solve_text(demo_question)
    print(result.to_markdown())
