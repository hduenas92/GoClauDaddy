"""Permission rule store and decision log (WP-AP J1).

Two tables back the "approvals that work" feature:

* ``permission_rules`` is what "Always allow this command here" writes. v1
  stores one exact Bash command verbatim plus the exact folder. It is
  deliberately not a pattern store: ``match_rule`` compares strings byte for
  byte and revoked rules never match.
* ``permission_log`` records every request and every decision. A row opens as
  ``pending`` and is resolved once, never rewritten, so an unanswered or broken
  request stays visible and can be abandoned (fail closed).

Storage only -- no HTTP and no websocket here.
"""

import dataclasses
import datetime
import json
import os
import uuid

from app.db.connection import get_connection
from app.logging_setup import get_logger
from app.services.redact import redact_secrets

log = get_logger("permission")


@dataclasses.dataclass(frozen=True)
class Rule:
    id: str
    tool_name: str
    pattern: str
    cwd: str
    created_at: str
    revoked_at: str | None


# v1 expresses exactly one kind of rule: a Bash command in one folder.
_BASH_TOOL = "Bash"
_BASH_INPUT_KEYS = frozenset({"command", "description", "timeout", "run_in_background"})
_MAX_PATTERN_CHARS = 4000

VALID_STATUSES = frozenset({"allow", "deny"})
VALID_SOURCES = frozenset({
    "rule",
    "user_once",
    "user_always",
    "user_deny",
    "timeout",
    "no_ui",
    "error",
    "stopped",
    "abandoned",
})

_ABANDON_REASON = "still pending when the queue was abandoned"


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def rule_pattern_for(tool_name, tool_input) -> str | None:
    """The exact string an "Always" rule stores, or None when it cannot be expressed.

    Only a Bash command can become a rule in v1. The command is returned
    verbatim -- never trimmed, globbed or de-escaped -- so a rule can only ever
    match the identical command.
    """
    if tool_name != _BASH_TOOL:
        return None
    if not isinstance(tool_input, dict):
        return None
    if not set(tool_input) <= _BASH_INPUT_KEYS:
        return None
    command = tool_input.get("command")
    if not isinstance(command, str):
        return None
    if not command.strip():
        return None
    if len(command) > _MAX_PATTERN_CHARS:
        return None
    if "\x00" in command:
        return None
    return command


def _norm_cwd(cwd: str) -> str:
    return os.path.normcase(os.path.normpath(cwd))


def _rule_from_row(row) -> Rule:
    return Rule(
        id=row["id"],
        tool_name=row["tool_name"],
        pattern=row["pattern"],
        cwd=row["cwd"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


def add_rule(tool_name, tool_input, cwd) -> Rule:
    """Store an exact rule, or return the live one that already matches.

    A revoked rule does not count as a match: re-allowing the same command
    creates a fresh rule rather than silently resurrecting the old one.
    """
    pattern = rule_pattern_for(tool_name, tool_input)
    if pattern is None:
        raise ValueError("tool input cannot be expressed as an exact rule")
    if not cwd:
        raise ValueError("a rule needs a working directory")
    target = _norm_cwd(cwd)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM permission_rules WHERE tool_name = ? AND pattern = ? AND revoked_at IS NULL",
            (tool_name, pattern),
        ).fetchall()
        for row in rows:
            if _norm_cwd(row["cwd"]) == target:
                return _rule_from_row(row)
        rule = Rule(
            id=str(uuid.uuid4()),
            tool_name=tool_name,
            pattern=pattern,
            cwd=cwd,
            created_at=_now(),
            revoked_at=None,
        )
        conn.execute(
            "INSERT INTO permission_rules (id, tool_name, pattern, cwd, created_at, revoked_at)"
            " VALUES (?, ?, ?, ?, ?, NULL)",
            (rule.id, rule.tool_name, rule.pattern, rule.cwd, rule.created_at),
        )
    return rule


def list_rules(include_revoked: bool = False) -> list[Rule]:
    sql = "SELECT * FROM permission_rules"
    if not include_revoked:
        sql += " WHERE revoked_at IS NULL"
    # created_at is the sort key; rowid breaks the tie for rules created inside
    # the same clock tick so "newest first" is stable.
    sql += " ORDER BY created_at DESC, rowid DESC"
    with get_connection() as conn:
        rows = conn.execute(sql).fetchall()
    return [_rule_from_row(row) for row in rows]


def revoke_rule(rule_id) -> bool:
    """Revoke once. True on the transition, False if already revoked or unknown."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE permission_rules SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (_now(), rule_id),
        )
        return cur.rowcount > 0


def match_rule(tool_name, tool_input, cwd) -> Rule | None:
    """A live rule whose command and folder match exactly, or None.

    The stored command is compared for string equality only; it is never read as
    a glob, regex or prefix.
    """
    pattern = rule_pattern_for(tool_name, tool_input)
    if pattern is None or not cwd:
        return None
    target = _norm_cwd(cwd)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM permission_rules WHERE tool_name = ? AND pattern = ? AND revoked_at IS NULL",
            (tool_name, pattern),
        ).fetchall()
    for row in rows:
        if _norm_cwd(row["cwd"]) == target:
            return _rule_from_row(row)
    return None


def _redact_recursive(value):
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {k: _redact_recursive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_recursive(v) for v in value]
    return value


def _input_json(tool_input) -> str:
    try:
        return json.dumps(_redact_recursive(tool_input))
    except (TypeError, ValueError):
        return json.dumps(redact_secrets(str(tool_input)))


def log_open(*, conversation_id, request_id, tool_use_id, tool_name, tool_input, cwd) -> int:
    """Record a request as pending. Returns the log id the decision references."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO permission_log
               (ts, conversation_id, request_id, tool_use_id, tool_name, input_json, cwd,
                status, source, rule_id, reason, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, NULL)""",
            (_now(), conversation_id, request_id, tool_use_id, tool_name, _input_json(tool_input), cwd),
        )
        log_id = cur.lastrowid
    log.info("permission request %s status=%s source=%s", request_id, "pending", None)
    return log_id


def log_resolve(log_id, *, status, source, rule_id=None, reason=None) -> None:
    """Resolve one pending request. A decision is written once and never rewritten."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid permission status: {status!r}")
    if source not in VALID_SOURCES:
        raise ValueError(f"invalid permission source: {source!r}")
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM permission_log WHERE id = ?", (log_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown permission log entry: {log_id!r}")
        if row["status"] != "pending":
            raise ValueError(f"permission log entry {log_id!r} is not pending")
        conn.execute(
            "UPDATE permission_log SET status = ?, source = ?, rule_id = ?, reason = ?, resolved_at = ?"
            " WHERE id = ? AND status = 'pending'",
            (status, source, rule_id, reason, _now(), log_id),
        )
        request_id = row["request_id"]
    log.info("permission decision %s status=%s source=%s", request_id, status, source)


def list_log(limit: int = 100) -> list[dict]:
    """Newest-first decisions, with ``input_json`` replaced by parsed ``input``."""
    if limit is None or limit <= 0:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM permission_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        raw = item.pop("input_json")
        if raw is None:
            item["input"] = None
        else:
            try:
                item["input"] = json.loads(raw)
            except (TypeError, ValueError):
                item["input"] = raw
        out.append(item)
    return out


def abandon_open_entries() -> int:
    """Deny every still-pending request (fail closed). Returns how many changed."""
    with get_connection() as conn:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM permission_log WHERE status = 'pending' ORDER BY id"
            ).fetchall()
        ]
    for log_id in ids:
        log_resolve(log_id, status="deny", source="abandoned", reason=_ABANDON_REASON)
    return len(ids)
