"""Server info, all-chat stats, and live log stream for the Console panel."""
import asyncio
import logging
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.config import HOST, PORT
from app.db.connection import get_connection
from app.logging_setup import get_logger
from app.services.cost import compute_cost_usd

BUDGET_USD = 200.0

router = APIRouter(prefix="/api/server", tags=["server"])
log = get_logger("server")

_log_buffer: deque[str] = deque(maxlen=300)
_subscribers: set[asyncio.Queue] = set()


class _FrontendHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        _log_buffer.append(line)
        dead: set = set()
        for q in _subscribers:
            try:
                q.put_nowait(line)
            except Exception:
                dead.add(q)
        _subscribers.difference_update(dead)


_h = _FrontendHandler()
_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))
logging.getLogger("goclaudaddy").addHandler(_h)


@router.get("/info")
def server_info():
    return {"url": f"http://{HOST}:{PORT}", "host": HOST, "port": PORT}


@router.get("/stats")
def server_stats():
    first_of_month = datetime.now(timezone.utc).date().replace(day=1).isoformat()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              COUNT(DISTINCT conversation_id) AS chat_count,
              COUNT(*) AS message_count,
              COALESCE(SUM(input_tokens), 0) AS total_input,
              COALESCE(SUM(output_tokens), 0) AS total_output
            FROM messages
            WHERE role = 'assistant'
            """
        ).fetchone()
        monthly_rows = conn.execute(
            """
            -- Grouped by the MESSAGE's model, not the conversation's current
            -- one: `GROUP BY c.model` repriced a month of history whenever
            -- anyone switched a conversation's model. COALESCE covers rows
            -- written before migration 4 added messages.model.
            SELECT COALESCE(m.model, c.model) AS model,
                   COALESCE(SUM(m.input_tokens),            0) AS mo_input,
                   COALESCE(SUM(m.output_tokens),           0) AS mo_output,
                   COALESCE(SUM(m.cache_read_tokens),       0) AS mo_cache_read,
                   COALESCE(SUM(m.cache_creation_tokens),   0) AS mo_cache_creation
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.role = 'assistant'
              AND m.created_at >= ?
            GROUP BY COALESCE(m.model, c.model)
            """,
            (first_of_month,),
        ).fetchall()

    monthly_cost = 0.0
    monthly_input = 0
    monthly_output = 0
    for r in monthly_rows:
        monthly_cost += compute_cost_usd(
            r["model"] or "",
            r["mo_input"], r["mo_output"],
            r["mo_cache_read"], r["mo_cache_creation"],
        )
        monthly_input  += r["mo_input"]
        monthly_output += r["mo_output"]

    return {
        "chat_count":        row["chat_count"],
        "message_count":     row["message_count"],
        "total_input":       row["total_input"],
        "total_output":      row["total_output"],
        "monthly_cost_usd":  round(monthly_cost, 4),
        "monthly_input":     monthly_input,
        "monthly_output":    monthly_output,
        "budget_usd":        BUDGET_USD,
    }


@router.get("/logs")
async def stream_logs():
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
    _subscribers.add(q)

    async def _generate():
        for line in list(_log_buffer):
            yield f"data: {line}\n\n"
        try:
            while True:
                try:
                    line = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {line}\n\n"
                except asyncio.TimeoutError:
                    yield "data: \n\n"
        finally:
            _subscribers.discard(q)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
