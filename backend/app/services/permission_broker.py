"""Permission broker (WP-AP J2).

One in-memory broker owns the permission prompt flow the claude CLI drives
through ``POST /mcp/permission/{token}``:

* a turn is opened with a random URL-safe token and one async ``notify``
  callable that delivers JSON frames to the UI (or ``None`` when there is no
  UI to ask);
* every tool request is logged first, then matched against an existing exact
  rule, otherwise put in front of the user;
* every outcome is written once through ``app.services.permissions.log_resolve``
  and anything unanswered, broken or unknown DENIES (fail closed).

Storage stays in ``app.services.permissions``; this module only brokers the
decisions and keeps the per-request deadlines.
"""

import asyncio
import secrets
import threading
import time
import uuid

import app.services.permissions as permissions
from app.logging_setup import get_logger

log = get_logger("permission_broker")

APPROVAL_TIMEOUT_SECONDS = 60.0
APPROVAL_MAX_SECONDS = 300.0
EXTEND_SECONDS = 60.0

_VALID_DECISIONS = frozenset({"allow_once", "allow_always", "deny"})

_MSG_RECORD_FAILED = "The permission request could not be recorded, so the tool was denied."
_MSG_UNKNOWN_TOKEN = "The permission token is unknown, so the tool was denied."
_MSG_MALFORMED = "The permission request was malformed, so the tool was denied."
_MSG_NO_UI = "No approval prompt is available, so the tool was denied."
_MSG_TIMEOUT = "No answer was received in time, so the tool was denied."
_MSG_USER_DENIED = "The user denied this tool call."
_MSG_STOPPED = "The permission request was stopped before a decision, so the tool was denied."
_MSG_BROKER_ERROR = "The permission check failed, so the tool was denied."


def _monotonic_now() -> float:
    """One-line indirection so tests can install a fake clock if they need one."""
    return time.monotonic()


def _deny(message: str) -> dict:
    return {"behavior": "deny", "message": message}


def _allow(tool_input) -> dict:
    return {"behavior": "allow", "updatedInput": tool_input}


async def _send_frame(notify, frame) -> None:
    """Deliver one UI frame with a hard deadline.

    The timeout is read from this module at call time so tests can
    monkeypatch ``APPROVAL_TIMEOUT_SECONDS`` (for example to 0.4 s) and prove
    a notify callable that never returns cannot block a decision.
    """
    await asyncio.wait_for(notify(frame), timeout=APPROVAL_TIMEOUT_SECONDS)


class _Turn:
    __slots__ = ("conversation_id", "cwd", "notify")

    def __init__(self, conversation_id, cwd, notify):
        self.conversation_id = conversation_id
        self.cwd = cwd
        self.notify = notify


class _PendingRequest:
    __slots__ = (
        "token",
        "conversation_id",
        "request_id",
        "tool_name",
        "tool_input",
        "tool_use_id",
        "cwd",
        "log_id",
        "notify",
        "loop",
        "wake",
        "started_at",
        "deadline",
        "decision",
        "reason",
        "controls",
        "log_done",
    )

    def __init__(self, *, token, conversation_id, request_id, tool_name, tool_input, tool_use_id, cwd, log_id, notify):
        self.token = token
        self.conversation_id = conversation_id
        self.request_id = request_id
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.tool_use_id = tool_use_id
        self.cwd = cwd
        self.log_id = log_id
        self.notify = notify
        self.loop = None
        self.wake = asyncio.Event()
        self.started_at = 0.0
        self.deadline = 0.0
        self.decision = None
        self.reason = None
        self.controls = []
        self.log_done = False


class PermissionBroker:
    """Owns open turns and pending requests, thread-safe for ``resolve``."""

    def __init__(self):
        self._lock = threading.Lock()
        self._turns: dict[str, _Turn] = {}
        self._requests: dict[str, _PendingRequest] = {}

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------
    def open_turn(self, conversation_id, cwd, notify) -> str:
        """Open one permission turn and return its random token.

        The token is the only secret that lets the CLI reach this turn; it is
        long, URL-safe and never reused while the turn is open.
        """
        while True:
            token = secrets.token_urlsafe(32)
            with self._lock:
                if token not in self._turns:
                    self._turns[token] = _Turn(conversation_id, cwd, notify)
                    return token

    def close_turn(self, token: str) -> None:
        """Forget ``token`` and decide every still-pending request ``stopped``."""
        with self._lock:
            turn = self._turns.pop(token, None)
            if turn is None:
                return
            stopped = [req for req in self._requests.values() if req.token == token and req.decision is None]
            for req in stopped:
                req.decision = "stopped"
                req.reason = "turn closed before a decision"
                self._schedule_wake(req)

        for req in stopped:
            # _log_resolve_once claims the write; decide wakes on its own loop
            # and simply serves the deny decision when it sees log_done.
            self._log_resolve_once(req, status="deny", source="stopped", reason=req.reason)

    def has_pending(self, conversation_id) -> bool:
        with self._lock:
            return any(
                req.conversation_id == conversation_id and req.decision is None
                for req in self._requests.values()
            )

    # ------------------------------------------------------------------
    # User decisions and deadline extension (safe from other threads)
    # ------------------------------------------------------------------
    def resolve(self, conversation_id, request_id, decision) -> bool:
        if decision not in _VALID_DECISIONS:
            return False
        with self._lock:
            req = self._requests.get(request_id)
            if req is None or req.decision is not None or req.conversation_id != conversation_id:
                return False
            req.decision = decision
            req.reason = None
            self._schedule_wake(req)
        return True

    def extend(self, conversation_id, request_id=None):
        """Push one pending deadline out by EXTEND_SECONDS, capped at MAX.

        Returns the remaining seconds for the selected request, or ``None``
        when the conversation has no pending request (or ``request_id`` does
        not name one in that conversation).
        """
        now = _monotonic_now()
        with self._lock:
            req = self._find_pending(conversation_id, request_id)
            if req is None:
                return None
            cap = req.started_at + APPROVAL_MAX_SECONDS
            req.deadline = min(req.deadline + EXTEND_SECONDS, cap)
            remaining = max(0.0, req.deadline - now)
            req.controls.append(("extend", req.request_id, remaining))
            self._schedule_wake(req)
        return remaining

    # ------------------------------------------------------------------
    # The CLI's decide path
    # ------------------------------------------------------------------
    async def decide(self, token, tool_name, tool_input, tool_use_id) -> dict:
        """Return the CLI decision dict for one tool permission request."""
        request_id = str(uuid.uuid4())
        log_id = None
        req = None
        try:
            with self._lock:
                turn = self._turns.get(token)
            conversation_id = turn.conversation_id if turn is not None else None
            cwd = turn.cwd if turn is not None else None

            # The request is recorded before anything else, so even a broken or
            # unknown request leaves a trace. If the record itself fails, there
            # is no log id to resolve: deny without raising.
            try:
                log_id = permissions.log_open(
                    conversation_id=conversation_id,
                    request_id=request_id,
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    cwd=cwd,
                )
            except Exception:
                log.warning("permission log_open failed for %s", request_id, exc_info=True)
                return _deny(_MSG_RECORD_FAILED)

            if turn is None:
                self._resolve_log_id(log_id, status="deny", source="error", reason="unknown permission token")
                return _deny(_MSG_UNKNOWN_TOKEN)

            if not self._valid_request(tool_name, tool_input):
                self._resolve_log_id(log_id, status="deny", source="error", reason="malformed tool_name or tool_input")
                return _deny(_MSG_MALFORMED)

            # A stored exact rule means yes without asking.
            try:
                rule = permissions.match_rule(tool_name, tool_input, cwd)
            except Exception:
                log.warning("permission rule lookup failed for %s", request_id, exc_info=True)
                self._resolve_log_id(log_id, status="deny", source="error", reason="rule lookup failed")
                return _deny(_MSG_BROKER_ERROR)

            if rule is not None:
                frame = {
                    "type": "permission_auto_allowed",
                    "request_id": request_id,
                    "tool": tool_name,
                    "rule_id": rule.id,
                    "pattern": rule.pattern,
                }
                if turn.notify is not None:
                    try:
                        await _send_frame(turn.notify, frame)
                    except Exception:
                        log.warning("permission notify failed for %s", request_id, exc_info=True)
                        self._resolve_log_id(log_id, status="deny", source="error", reason="failed to deliver permission frame")
                        return _deny(_MSG_BROKER_ERROR)
                if not self._resolve_log_id(
                    log_id, status="allow", source="rule", rule_id=rule.id, reason="matched an existing allow rule"
                ):
                    return _deny(_MSG_BROKER_ERROR)
                return _allow(tool_input)

            if turn.notify is None:
                self._resolve_log_id(log_id, status="deny", source="no_ui", reason="no approval UI is available")
                return _deny(_MSG_NO_UI)

            pattern = permissions.rule_pattern_for(tool_name, tool_input)
            action = tool_name
            if tool_name == "Bash" and isinstance(tool_input.get("command"), str):
                action = tool_input["command"]

            req = _PendingRequest(
                token=token,
                conversation_id=conversation_id,
                request_id=request_id,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_use_id=tool_use_id,
                cwd=cwd,
                log_id=log_id,
                notify=turn.notify,
            )
            req.loop = asyncio.get_running_loop()
            req.started_at = _monotonic_now()
            req.deadline = req.started_at + APPROVAL_TIMEOUT_SECONDS
            with self._lock:
                self._requests[request_id] = req

            timeout = APPROVAL_TIMEOUT_SECONDS
            frame = {
                "type": "approval_needed",
                "request_id": request_id,
                "tool": tool_name,
                "action": action,
                "input": tool_input,
                "tool_use_id": tool_use_id,
                "cwd": cwd,
                "can_always": pattern is not None,
                "always_pattern": pattern,
                "timeout": timeout,
                "remaining": timeout,
            }
            try:
                await _send_frame(turn.notify, frame)
            except Exception:
                with self._lock:
                    self._requests.pop(request_id, None)
                log.warning("permission notify failed for %s", request_id, exc_info=True)
                self._resolve_log_id(log_id, status="deny", source="error", reason="failed to deliver approval prompt")
                return _deny(_MSG_BROKER_ERROR)

            await self._wait_for_decision(req)
            return await self._finish(req)
        except Exception:
            log.exception("permission decision failed for %s", request_id)
            if req is not None:
                with self._lock:
                    self._requests.pop(request_id, None)
                self._log_resolve_once(req, status="deny", source="error", reason="permission broker error")
            elif log_id is not None:
                self._resolve_log_id(log_id, status="deny", source="error", reason="permission broker error")
            return _deny(_MSG_BROKER_ERROR)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _valid_request(tool_name, tool_input) -> bool:
        return isinstance(tool_name, str) and bool(tool_name.strip()) and isinstance(tool_input, dict)

    def _find_pending(self, conversation_id, request_id):
        """Return a pending request for extend; caller holds ``_lock``."""
        if request_id is None:
            candidates = [
                req
                for req in self._requests.values()
                if req.conversation_id == conversation_id and req.decision is None
            ]
            return min(candidates, key=lambda req: req.started_at) if candidates else None
        req = self._requests.get(request_id)
        if req is None or req.conversation_id != conversation_id or req.decision is not None:
            return None
        return req

    @staticmethod
    def _schedule_wake(req: _PendingRequest) -> None:
        loop = req.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(req.wake.set)
        except RuntimeError:
            pass

    async def _wait_for_decision(self, req: _PendingRequest) -> None:
        while True:
            with self._lock:
                decision = req.decision
                deadline = req.deadline
                controls = list(req.controls)
                req.controls.clear()

            for kind, request_id, remaining in controls:
                if kind == "extend":
                    try:
                        await _send_frame(req.notify, {
                            "type": "approval_extended",
                            "request_id": request_id,
                            "remaining": remaining,
                        })
                    except Exception:
                        log.warning("approval_extended notify failed for %s", request_id, exc_info=True)
                        with self._lock:
                            if req.decision is None:
                                req.decision = "error"
                                req.reason = "failed to deliver approval_extended frame"
                        break

            with self._lock:
                decision = req.decision
            if decision is not None:
                return

            remaining = deadline - _monotonic_now()
            if remaining <= 0:
                with self._lock:
                    if req.decision is None:
                        req.decision = "timeout"
                        req.reason = "no answer in time"
                return

            req.wake.clear()
            try:
                await asyncio.wait_for(req.wake.wait(), timeout=remaining)
            except TimeoutError:
                with self._lock:
                    if req.decision is None:
                        req.decision = "timeout"
                        req.reason = "no answer in time"
                return

    async def _finish(self, req: _PendingRequest) -> dict:
        with self._lock:
            self._requests.pop(req.request_id, None)

        decision = req.decision
        if decision == "allow_once":
            return await self._user_outcome(req, status="allow", source="user_once", decision="allow")
        if decision == "allow_always":
            return await self._finish_allow_always(req)
        if decision == "deny":
            return await self._user_outcome(req, status="deny", source="user_deny", decision="deny")
        if decision == "timeout":
            return await self._user_outcome(
                req, status="deny", source="timeout", decision="deny", reason="no answer in time"
            )
        if decision == "stopped":
            self._log_resolve_once(req, status="deny", source="stopped", reason=req.reason or "turn closed before a decision")
            return _deny(_MSG_STOPPED)
        if decision == "error":
            self._log_resolve_once(req, status="deny", source="error", reason=req.reason or "permission broker error")
            return _deny(_MSG_BROKER_ERROR)
        # Should not be reached, but fail closed.
        self._log_resolve_once(req, status="deny", source="error", reason="unknown broker outcome")
        return _deny(_MSG_BROKER_ERROR)

    async def _finish_allow_always(self, req: _PendingRequest) -> dict:
        try:
            rule = permissions.add_rule(req.tool_name, req.tool_input, req.cwd)
        except ValueError:
            return await self._user_outcome(
                req,
                status="allow",
                source="user_once",
                decision="allow",
                reason="no exact rule is possible for this tool input",
            )
        except Exception:
            log.warning("permission add_rule failed for %s", req.request_id, exc_info=True)
            self._log_resolve_once(req, status="deny", source="error", reason="failed to create an allow rule")
            return _deny(_MSG_BROKER_ERROR)
        return await self._user_outcome(
            req, status="allow", source="user_always", decision="allow", rule_id=rule.id
        )

    async def _user_outcome(self, req: _PendingRequest, *, status, source, decision, reason=None, rule_id=None) -> dict:
        """Log a user/timeout outcome, tell the UI it is resolved, and return the CLI dict."""
        if req.notify is not None:
            frame = {
                "type": "approval_resolved",
                "request_id": req.request_id,
                "decision": decision,
                "source": source,
            }
            try:
                await _send_frame(req.notify, frame)
            except Exception:
                log.warning("approval_resolved notify failed for %s", req.request_id, exc_info=True)
                status, source, decision, rule_id, reason = (
                    "deny",
                    "error",
                    "deny",
                    None,
                    "failed to deliver approval_resolved frame",
                )

        if not self._log_resolve_once(req, status=status, source=source, rule_id=rule_id, reason=reason):
            return _deny(_MSG_BROKER_ERROR)

        if decision == "allow":
            return _allow(req.tool_input)
        if source == "timeout":
            return _deny(_MSG_TIMEOUT)
        if source == "user_deny":
            return _deny(_MSG_USER_DENIED)
        if source == "error":
            return _deny(_MSG_BROKER_ERROR)
        return _deny(_MSG_USER_DENIED)

    def _log_resolve_once(self, req: _PendingRequest, *, status, source, rule_id=None, reason=None) -> bool:
        """Write ``log_resolve`` exactly once per request. Never raises."""
        with self._lock:
            if req.log_done:
                return True
            req.log_done = True
        try:
            permissions.log_resolve(req.log_id, status=status, source=source, rule_id=rule_id, reason=reason)
            return True
        except Exception:
            with self._lock:
                req.log_done = False
            log.warning("permission log_resolve failed for %s", req.request_id, exc_info=True)
            return False

    def _resolve_log_id(self, log_id, *, status, source, rule_id=None, reason=None) -> bool:
        """Resolve a log entry that has no _PendingRequest behind it. Never raises."""
        try:
            permissions.log_resolve(log_id, status=status, source=source, rule_id=rule_id, reason=reason)
            return True
        except Exception:
            log.warning("permission log_resolve failed for log id %s", log_id, exc_info=True)
            return False


broker = PermissionBroker()
