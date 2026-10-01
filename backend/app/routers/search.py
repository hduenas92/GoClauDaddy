"""Full-text search over message content, backed by the messages_fts FTS5 index
(migration v18 in app/db/migrations.py).

FTS5 MATCH takes a *query language*, not a literal string. Raw user input
reaching MATCH lets unbalanced quotes 500 the request, and operators like
NEAR/AND/OR/NOT/*/^/column: silently change what gets matched. Parameterizing
the query does not help here -- the whole bound string is still parsed as an
FTS5 expression by SQLite.

Fix: treat the user's text as literal words. Split on whitespace, escape any
`"` in each token by doubling it (FTS5's own escape for a quote inside a
quoted string), and wrap every token in quotes. A quoted token is always a
literal phrase to FTS5, regardless of what punctuation it contains.

AND vs phrase for multi-word queries: quoted terms separated by a space is an
implicit AND in FTS5, so "foo" "bar" matches rows containing both words
anywhere, in any order. That is what an inexperienced user typing multiple
words expects (like a normal search box) -- not "match this exact phrase in
this exact order", which would surprise anyone who orders their words
differently from the message.
"""

from fastapi import APIRouter, HTTPException, Query

from app.db.connection import get_connection

router = APIRouter(prefix="/api/search", tags=["search"])

_MAX_Q_LEN = 1000  # bound pathological input before tokenizing


def _build_fts_query(q: str) -> str:
    """Turn free text into a literal, operator-free FTS5 MATCH expression.

    Raises ValueError if there are no usable tokens (e.g. q was only
    whitespace, or truncation left nothing).
    """
    tokens = q[:_MAX_Q_LEN].split()
    if not tokens:
        raise ValueError("no search terms")
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    return " ".join(quoted)


@router.get("")
def search_messages(
    q: str = Query(..., description="Search text"),
    conversation_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    if not q.strip():
        raise HTTPException(400, "q must not be empty")

    try:
        fts_query = _build_fts_query(q)
    except ValueError:
        raise HTTPException(400, "Search needs at least one word or number.")

    params: list = [fts_query]
    scope_sql = ""
    if conversation_id is not None:
        scope_sql = " AND m.conversation_id = ?"
        params.append(conversation_id)
    params.append(limit + 1)  # fetch one extra row to detect truncation

    sql = f"""
        SELECT
            m.id AS message_id,
            m.conversation_id AS conversation_id,
            c.name AS conversation_name,
            m.role AS role,
            m.created_at AS created_at,
            snippet(messages_fts, 0, '**', '**', '…', 8) AS snippet
        FROM messages_fts
        JOIN messages m ON m.rowid = messages_fts.rowid
        JOIN conversations c ON c.id = m.conversation_id
        WHERE messages_fts MATCH ?
          AND m.superseded_by IS NULL
          {scope_sql}
        ORDER BY messages_fts.rank
        LIMIT ?
    """

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    truncated = len(rows) > limit
    rows = rows[:limit]

    return {
        "results": [
            {
                "conversation_id": r["conversation_id"],
                "conversation_name": r["conversation_name"],
                "message_id": r["message_id"],
                "role": r["role"],
                "snippet": r["snippet"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "truncated": truncated,
    }
