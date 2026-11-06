"""Dummy seed data for BizAssist.

Seeds TWO coaching centres, each fully isolated:
  * SGEN Computer Academy (software training)
  * Bright Career Institute (competitive-exam coaching)

The data is a list, so the seed script creates every institute here with its
own admin, courses, batches and knowledge documents. All data is fictional.
"""
from __future__ import annotations

# A fixed demo admin password. Overridable via the seed script / env for
# anything beyond local demoing. This is dummy data only.
DEMO_ADMIN_PASSWORD = "admin12345"

INSTITUTES: list[dict] = [
    {
        "name": "SGEN Computer Academy",
        "slug": "sgen-computer-academy",
        # Deterministic demo API key so the widget demo page can hard-wire it.
        "api_key": "demo-sgen-academy-key",
        "contact_email": "admissions@sgenacademy.example",
        "contact_phone": "+91-90000-00001",
        "address": "2nd Floor, Tech Plaza, MG Road, Bengaluru 560001",
        "description": (
            "SGEN Computer Academy offers industry-focused software training "
            "in programming, web development and data analytics."
        ),
        "admin": {
            "email": "admin@sgenacademy.example",
            "full_name": "SGEN Academy Admin",
            "role": "admin",
        },
        "courses": [
            {
                "name": "Python Programming",
                "slug": "python-programming",
                "description": (
                    "Beginner-friendly Python course covering syntax, data "
                    "structures, OOP, file handling and an intro to automation."
                ),
                "fee": 15000.0,
                "duration": "3 months",
                "eligibility": "Open to anyone with basic computer familiarity. No prior coding needed.",
                "mode": "offline",
                "batches": [
                    {
                        "name": "PY-Morning-A",
                        "timing": "Mon-Fri, 9:00-10:30 AM",
                        "seats_total": 30,
                        "seats_available": 8,
                        "start_offset_days": 14,
                        "duration_days": 90,
                    },
                    {
                        "name": "PY-Weekend-B",
                        "timing": "Sat-Sun, 11:00 AM-1:00 PM",
                        "seats_total": 25,
                        "seats_available": 15,
                        "start_offset_days": 21,
                        "duration_days": 120,
                    },
                ],
            },
            {
                "name": "Java Development",
                "slug": "java-development",
                "description": (
                    "Core and advanced Java with OOP, collections, JDBC and an "
                    "introduction to Spring Boot."
                ),
                "fee": 20000.0,
                "duration": "4 months",
                "eligibility": "Basic programming knowledge recommended. Graduates and students welcome.",
                "mode": "offline",
                "batches": [
                    {
                        "name": "JAVA-Evening-A",
                        "timing": "Mon-Fri, 6:30-8:00 PM",
                        "seats_total": 30,
                        "seats_available": 12,
                        "start_offset_days": 10,
                        "duration_days": 120,
                    },
                ],
            },
            {
                "name": "Full Stack Development",
                "slug": "full-stack-development",
                "description": (
                    "End-to-end web development: HTML, CSS, JavaScript, React, "
                    "Node.js, REST APIs and databases, with a capstone project."
                ),
                "fee": 35000.0,
                "duration": "6 months",
                "eligibility": "Suitable for freshers and working professionals. Laptop required.",
                "mode": "hybrid",
                "batches": [
                    {
                        "name": "FS-Weekend-A",
                        "timing": "Sat-Sun, 10:00 AM-1:00 PM",
                        "seats_total": 25,
                        "seats_available": 5,
                        "start_offset_days": 7,
                        "duration_days": 180,
                    },
                ],
            },
            {
                "name": "Data Analytics",
                "slug": "data-analytics",
                "description": (
                    "Practical data analytics with Excel, SQL, Python (pandas) "
                    "and visualisation using Power BI."
                ),
                "fee": 25000.0,
                "duration": "4 months",
                "eligibility": "Open to graduates from any stream. Comfort with numbers helps.",
                "mode": "online",
                "batches": [
                    {
                        "name": "DA-Online-A",
                        "timing": "Tue/Thu/Sat, 7:00-8:30 PM",
                        "seats_total": 40,
                        "seats_available": 22,
                        "start_offset_days": 12,
                        "duration_days": 120,
                    },
                ],
            },
        ],
    },
    {
        "name": "Bright Career Institute",
        "slug": "bright-career-institute",
        "api_key": "demo-bright-career-key",
        "contact_email": "admissions@brightcareer.example",
        "contact_phone": "+91-90000-00002",
        "address": "1st Floor, Vidya Towers, Station Road, Pune 411001",
        "description": (
            "Bright Career Institute coaches students for banking, SSC, "
            "railway and other government competitive examinations."
        ),
        "admin": {
            "email": "admin@brightcareer.example",
            "full_name": "Bright Career Admin",
            "role": "admin",
        },
        "courses": [
            {
                "name": "Banking Preparation",
                "slug": "banking-preparation",
                "description": (
                    "Complete preparation for IBPS and SBI PO/Clerk exams: "
                    "quantitative aptitude, reasoning, English and banking "
                    "awareness."
                ),
                "fee": 18000.0,
                "duration": "5 months",
                "eligibility": "Graduates in any discipline. Final-year students may apply.",
                "mode": "offline",
                "batches": [
                    {
                        "name": "BANK-Morning-A",
                        "timing": "Mon-Sat, 8:00-10:00 AM",
                        "seats_total": 40,
                        "seats_available": 18,
                        "start_offset_days": 10,
                        "duration_days": 150,
                    },
                ],
            },
            {
                "name": "SSC Preparation",
                "slug": "ssc-preparation",
                "description": (
                    "Coaching for SSC CGL and CHSL: quantitative aptitude, "
                    "reasoning, general awareness and English comprehension."
                ),
                "fee": 15000.0,
                "duration": "5 months",
                "eligibility": "Open to candidates who have passed 10+2 or graduation as per the post.",
                "mode": "offline",
                "batches": [
                    {
                        "name": "SSC-Evening-A",
                        "timing": "Mon-Fri, 5:30-7:30 PM",
                        "seats_total": 45,
                        "seats_available": 25,
                        "start_offset_days": 14,
                        "duration_days": 150,
                    },
                ],
            },
            {
                "name": "Railway Examination",
                "slug": "railway-examination",
                "description": (
                    "Targeted preparation for RRB NTPC and Group D exams, "
                    "including mock tests and previous-year analysis."
                ),
                "fee": 12000.0,
                "duration": "4 months",
                "eligibility": "Eligibility varies by post; 10th/12th pass or graduate as applicable.",
                "mode": "hybrid",
                "batches": [
                    {
                        "name": "RLY-Weekend-A",
                        "timing": "Sat-Sun, 10:00 AM-1:00 PM",
                        "seats_total": 50,
                        "seats_available": 30,
                        "start_offset_days": 9,
                        "duration_days": 120,
                    },
                ],
            },
            {
                "name": "Aptitude Training",
                "slug": "aptitude-training",
                "description": (
                    "Foundation course in quantitative aptitude and logical "
                    "reasoning, useful across banking, SSC and placement tests."
                ),
                "fee": 8000.0,
                "duration": "2 months",
                "eligibility": "Open to all students and job-seekers.",
                "mode": "online",
                "batches": [
                    {
                        "name": "APT-Online-A",
                        "timing": "Tue/Thu/Sat, 7:00-8:30 PM",
                        "seats_total": 60,
                        "seats_available": 40,
                        "start_offset_days": 7,
                        "duration_days": 60,
                    },
                ],
            },
        ],
    },
]
