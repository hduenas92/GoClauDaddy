from app.services.claude_cli import build_command


def test_minimal_command():
    cmd = build_command(prompt="hi", model="claude-sonnet-4-6")
    assert cmd == [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "claude-sonnet-4-6",
        "hi",
    ]


def test_valid_permission_mode_included():
    cmd = build_command(prompt="hi", model="m", permission_mode="plan")
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "plan"


def test_all_real_permission_modes_pass_the_whitelist():
    # These are the exact values `claude --help` reports for --permission-mode.
    for mode in ("acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"):
        cmd = build_command(prompt="hi", model="m", permission_mode=mode)
        assert cmd[cmd.index("--permission-mode") + 1] == mode


def test_invalid_permission_mode_is_dropped_silently():
    """Never let an arbitrary client string reach argv."""
    cmd = build_command(prompt="hi", model="m", permission_mode="; rm -rf /")
    assert "--permission-mode" not in cmd
    assert "; rm -rf /" not in cmd


def test_none_permission_mode_omits_flag():
    cmd = build_command(prompt="hi", model="m", permission_mode=None)
    assert "--permission-mode" not in cmd


def test_system_prompt_included():
    cmd = build_command(prompt="hi", model="m", system_prompt="be terse")
    assert cmd[cmd.index("--system-prompt") + 1] == "be terse"


def test_resume_uses_session_id_not_continue():
    cmd = build_command(prompt="hi", model="m", session_id="sess-123")
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "sess-123"
    assert "--continue" not in cmd


def test_no_session_id_omits_resume():
    cmd = build_command(prompt="hi", model="m")
    assert "--resume" not in cmd


def test_prompt_is_always_last_argument():
    cmd = build_command(
        prompt="the actual prompt",
        model="m",
        permission_mode="plan",
        system_prompt="sys",
        session_id="s1",
    )
    assert cmd[-1] == "the actual prompt"
