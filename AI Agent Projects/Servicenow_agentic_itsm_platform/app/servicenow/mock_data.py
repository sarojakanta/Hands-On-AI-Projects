"""Mock ServiceNow dataset used when SERVICENOW_MODE=mock (default, no credentials needed)."""

MOCK_RECORDS = {
    "incident": [
        {"sys_id": "mock-inc-001", "number": "INC0010025",
         "short_description": "Application server experiencing high CPU utilization",
         "description": "CPU is above 95%. Application response time has increased. Multiple users report slowness since 10:30 AM.",
         "priority": "1", "state": "In Progress", "impact": "1", "urgency": "1", "category": "software",
         "assignment_group": "Application Support"},
        {"sys_id": "mock-inc-002", "number": "INC0010026", "short_description": "Unable to access employee portal",
         "description": "Several employees receive intermittent HTTP 503 errors while accessing the employee portal.",
         "priority": "2", "state": "New", "impact": "2", "urgency": "2", "category": "software",
         "assignment_group": "Application Support"},
    ],
    "problem": [
        {"sys_id": "mock-prb-001", "number": "PRB0001001",
         "short_description": "Recurring employee portal availability incidents",
         "description": "Multiple incidents over the last two weeks report intermittent portal unavailability.",
         "priority": "2", "state": "Assess", "category": "availability", "assignment_group": "Problem Management"},
    ],
    "change": [
        {"sys_id": "mock-chg-001", "number": "CHG0002001", "short_description": "Upgrade production application servers",
         "description": "Planned production server upgrade. Requires impact assessment, implementation plan and rollback plan.",
         "priority": "3", "state": "Assess", "category": "infrastructure", "assignment_group": "Change Advisory"},
    ],
    "request": [
        {"sys_id": "mock-req-001", "number": "REQ0013001", "short_description": "New employee access and laptop request",
         "description": "Provision a laptop, standard productivity software and access to approved business applications.",
         "priority": "3", "state": "Requested", "category": "access", "assignment_group": "Service Desk"},
    ],
}

# Requested items (sc_req_item) that belong to a request, keyed by the parent sys_id.
MOCK_ITEMS = {
    "mock-req-001": [
        {"sys_id": "mock-ritm-001", "number": "RITM0020001", "short_description": "Standard laptop",
         "state": "Open", "request": "mock-req-001"},
        {"sys_id": "mock-ritm-002", "number": "RITM0020002", "short_description": "Productivity software bundle",
         "state": "Open", "request": "mock-req-001"},
    ],
}
