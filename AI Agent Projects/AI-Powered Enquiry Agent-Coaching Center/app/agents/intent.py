"""Intent detection for the admission enquiry agent.

A deterministic, keyword-driven classifier. It is fast, free, testable, and
good enough to route to the right tool for the MVP. The spec intents are:

  course_info, fee_details, batch_schedule, eligibility,
  admission_procedure, counselling, enquiry_registration,
  followup, lead_status, knowledge, greeting, other

When a real LLM is configured it still handles the final wording; routing
stays deterministic so behaviour is predictable and safe.
"""
from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    GREETING = "greeting"
    COURSE_INFO = "course_info"
    FEE_DETAILS = "fee_details"
    BATCH_SCHEDULE = "batch_schedule"
    ELIGIBILITY = "eligibility"
    ADMISSION_PROCEDURE = "admission_procedure"
    COUNSELLING = "counselling"
    ENQUIRY_REGISTRATION = "enquiry_registration"
    FOLLOWUP = "followup"
    LEAD_STATUS = "lead_status"
    KNOWLEDGE = "knowledge"
    OTHER = "other"


# Ordered so more specific intents win over generic ones.
_PATTERNS: list[tuple[Intent, list[str]]] = [
    (Intent.GREETING, [r"\b(hi|hello|hey|good (morning|afternoon|evening))\b"]),
    (Intent.FEE_DETAILS, [r"\bfee", r"\bcost", r"\bprice", r"\bcharge", r"how much"]),
    (
        Intent.BATCH_SCHEDULE,
        [r"\bbatch", r"\bschedule", r"\btiming", r"\bwhen.*(start|begin)", r"\bslot"],
    ),
    (
        Intent.ELIGIBILITY,
        [r"\beligib", r"\bqualif", r"\bprerequisite", r"\bwho can (join|apply)", r"\brequire"],
    ),
    (
        Intent.COUNSELLING,
        [r"\bcounsel", r"\btalk to (a |someone|human)", r"\bhuman\b", r"\bagent\b", r"\bspeak to"],
    ),
    (
        Intent.ENQUIRY_REGISTRATION,
        [
            r"\bi want to (join|enroll|enrol|register|sign ?up|apply)",
            r"\bi (would like|wish) to (join|enroll|enrol|register|apply)",
            r"\bregister (me|my)\b",
            r"\bsign me up\b",
            r"\b(interested in (joining|enrolling))",
            r"\bmy (name|number|phone) is",
            r"\bname[:\-]\s*",
        ],
    ),
    (
        Intent.ADMISSION_PROCEDURE,
        [r"\badmission", r"\benrol", r"\bregister (for|in)\b", r"how.*(apply|join)", r"\bprocedure", r"\bprocess"],
    ),
    (Intent.LEAD_STATUS, [r"\b(status|update).*(enquiry|application)", r"\bmy enquiry"]),
    (Intent.FOLLOWUP, [r"\bfollow.?up", r"\bcall me", r"\bcontact me", r"\breach out"]),
    (
        Intent.COURSE_INFO,
        [r"\bcourse", r"\btraining", r"\bclass", r"\bsyllabus", r"\bwhat do you (offer|teach)", r"\bprogram"],
    ),
]


def detect_intent(message: str) -> Intent:
    """Classify the message into an Intent (deterministic)."""
    text = (message or "").lower().strip()
    if not text:
        return Intent.OTHER
    for intent, patterns in _PATTERNS:
        for pat in patterns:
            if re.search(pat, text):
                return intent
    # Default: treat as a knowledge question (RAG) rather than giving up.
    return Intent.KNOWLEDGE
