from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.db.connection import get_connection
from app.services.cost import compute_cost_usd

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("/{conversation_id}/stats")
def get_conversation_stats(conversation_id: str):
    with get_connection() as conn:
        conv = conn.execute(
            "SELECT model, status, started_at, completed_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if not conv:
            raise HTTPException(404, "Conversation not found")

        row = conn.execute(
            """SELECT
                COALESCE(SUM(input_tokens), 0)          AS ti,
                COALESCE(SUM(output_tokens), 0)         AS tot_out,
                COALESCE(SUM(cache_read_tokens), 0)     AS tcr,
                COALESCE(SUM(cache_creation_tokens), 0) AS tcc,
                COUNT(*)                                AS step_count,
                COALESCE(SUM(
                    CASE WHEN tool_calls IS NOT NULL AND tool_calls != '[]'
                    THEN json_array_length(tool_calls) ELSE 0 END
                ), 0) AS tool_call_count
               FROM messages
               WHERE conversation_id = ? AND role = 'assistant'""",
            (conversation_id,),
        ).fetchone()

    elapsed_s = None
    if conv["started_at"]:
        end_str = conv["completed_at"] or datetime.now(timezone.utc).isoformat()
        try:
            start = datetime.fromisoformat(conv["started_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            elapsed_s = round((end - start).total_seconds(), 1)
        except ValueError:
            pass

    return {
        "model": conv["model"],
        "status": conv["status"],
        "tokens_in": row["ti"],
        "tokens_out": row["tot_out"],
        "cost_usd": compute_cost_usd(conv["model"], row["ti"], row["tot_out"], row["tcr"], row["tcc"]),
        "elapsed_s": elapsed_s,
        "step_count": row["step_count"],
        "tool_call_count": row["tool_call_count"],
    }
