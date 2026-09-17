"""Seed built-in flow templates. Idempotent — safe to call on every startup."""

from app.db.connection import get_connection

_BUILTIN_TEMPLATES = [
    {
        "id": "builtin-1",
        "title": "Weekly Status Report",
        "description": "Structured weekly update for a project or team",
        "body": (
            "Write a weekly status report for {{Project Name}} covering the week of {{Week Date}}.\n\n"
            "Accomplishments this week:\n- \n\nPlanned for next week:\n- \n\nBlockers or risks:\n- "
        ),
        "category": "report",
        "sort_order": 1,
    },
    {
        "id": "builtin-2",
        "title": "Executive Summary",
        "description": "Concise one-page summary for leadership",
        "body": (
            "Write an executive summary for {{Document Title}}.\n\n"
            "Target audience: {{Target Audience}}\n\n"
            "Key points to highlight:\n- \n\nKeep it under one page and lead with the most important finding."
        ),
        "category": "report",
        "sort_order": 2,
    },
    {
        "id": "builtin-3",
        "title": "Follow-up Email",
        "description": "Professional follow-up after a meeting or call",
        "body": (
            "Write a professional follow-up email to {{Recipient Name}} about {{Meeting Topic}}.\n\n"
            "We met on {{Meeting Date}}. Recap the discussion, confirm next steps, and keep it under 150 words."
        ),
        "category": "email",
        "sort_order": 3,
    },
    {
        "id": "builtin-4",
        "title": "Proposal Email",
        "description": "Outreach email proposing a project or service",
        "body": (
            "Write a proposal email to {{Client Name}} for {{Project or Service}}.\n\n"
            "Include: a brief overview, the key benefit for them, and a clear call to action. Keep it concise."
        ),
        "category": "email",
        "sort_order": 4,
    },
    {
        "id": "builtin-5",
        "title": "Meeting Agenda",
        "description": "Structured agenda with goals and time blocks",
        "body": (
            "Create a meeting agenda for {{Meeting Title}}.\n\n"
            "Date and time: {{Meeting Date and Time}}\n"
            "Attendees: {{Attendees}}\n"
            "Duration: {{Duration}}\n\n"
            "Topics:\n1. \n2. \n3. \n\nGoal for this meeting: "
        ),
        "category": "document",
        "sort_order": 5,
    },
    {
        "id": "builtin-6",
        "title": "Code Review",
        "description": "Structured review covering correctness, performance, and security",
        "body": (
            "Review the following code for {{Project Name}}. Cover: correctness and edge cases, "
            "performance, readability, and security.\n\n"
            "Code:\n{{Code}}"
        ),
        "category": "analysis",
        "sort_order": 6,
    },
    {
        "id": "builtin-7",
        "title": "Data Analysis",
        "description": "Extract trends, outliers, and actionable insights from data",
        "body": (
            "Analyze the following data for {{Analysis Goal}}.\n\n"
            "Data:\n{{Data or Description}}\n\n"
            "Provide: key trends, anomalies, and 2-3 actionable insights."
        ),
        "category": "analysis",
        "sort_order": 7,
    },
    {
        "id": "builtin-8",
        "title": "Write Tests",
        "description": "Generate unit tests with edge cases and error handling",
        "body": (
            "Write comprehensive tests for the following {{Language or Framework}} code.\n\n"
            "Code:\n{{Code}}\n\n"
            "Cover: core logic, edge cases, and error handling."
        ),
        "category": "code",
        "sort_order": 8,
    },
]

_SEED_AT = "2026-01-01T00:00:00Z"


def seed_builtin_templates() -> None:
    with get_connection() as conn:
        for t in _BUILTIN_TEMPLATES:
            conn.execute(
                """INSERT OR IGNORE INTO flow_templates
                   (id, title, description, body, category, is_builtin, sort_order, created_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (t["id"], t["title"], t["description"], t["body"], t["category"], t["sort_order"], _SEED_AT),
            )
