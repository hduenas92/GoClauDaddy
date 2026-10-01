"""Tests for run.py desktop shortcut creation (backend/run.py)."""

import subprocess
from pathlib import Path


def test_shortcut_failure_does_not_write_flag(tmp_path, monkeypatch):
    """A non-zero PowerShell exit must leave the flag absent so the next start retries."""
    import run

    flag = tmp_path / ".shortcut_created"
    monkeypatch.setattr(run, "_SHORTCUT_FLAG", flag)
    monkeypatch.setattr(run.sys, "platform", "win32")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0] if args else [],
            returncode=1,
            stdout=b"",
            stderr=b"fatal: desktop not found",
        )

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    run._create_desktop_shortcut()

    assert not flag.exists()


def test_shortcut_command_resolves_desktop_inside_powershell(tmp_path, monkeypatch):
    """The .lnk path must be built by PowerShell, not from Path.home()/Desktop."""
    import run

    flag = tmp_path / ".shortcut_created"
    monkeypatch.setattr(run, "_SHORTCUT_FLAG", flag)
    monkeypatch.setattr(run.sys, "platform", "win32")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(run.subprocess, "run", fake_run)

    run._create_desktop_shortcut()

    ps = captured["cmd"][-1]
    assert "[Environment]::GetFolderPath('Desktop')" in ps
    assert str(Path.home() / "Desktop") not in ps
    assert flag.exists()
