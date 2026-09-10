#!/usr/bin/env python3
"""ClaudioUi -- local HTTP server + Claude CLI streaming."""

import base64, datetime, json, os, re, subprocess, sys, tempfile, threading, time, uuid, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

ANSI      = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
DATA_DIR  = Path.home() / ".claudioui"
CONV_FILE = DATA_DIR / "conversations.json"
CFG_FILE  = DATA_DIR / "config.json"

MODELS = [
    ("claude-sonnet-4-6",         "Sonnet 4.6"),
    ("claude-opus-4-5",           "Opus 4.5"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5 -- Steve"),
    ("claude-sonnet-5",           "Sonnet 5 -- Phillip"),
    ("claude-fable-5",            "Fable 5 -- Ernie"),
    ("claude-opus-5",             "Opus 5"),
]


def _load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path, obj):
    try:
        DATA_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_conversations():
    d = _load_json(CONV_FILE, {})
    return d if isinstance(d, dict) else {}


def save_conversations(convs):
    _save_json(CONV_FILE, convs)


def load_config():
    defaults = {"model": "claude-sonnet-4-6", "max_tokens": 4096, "system_prompt": ""}
    saved = _load_json(CFG_FILE, {})
    if isinstance(saved, dict):
        defaults.update(saved)
    return defaults


def save_config(cfg):
    _save_json(CFG_FILE, cfg)


def make_conversation(name=""):
    ts = int(time.time())
    if not name:
        name = "Chat " + datetime.datetime.now().strftime("%b %d %H:%M")
    return {"id": str(uuid.uuid4()), "name": name, "session_id": None,
            "created_at": ts, "updated_at": ts, "status": "idle",
            "message_count": 0}


_cfg = load_config()
state = {
    "dir":           os.path.expanduser("~"),
    "proc":          None,
    "conversations": load_conversations(),
    "active_conv":   None,
    "model":         _cfg.get("model", "claude-sonnet-4-6"),
    "max_tokens":    int(_cfg.get("max_tokens", 4096)),
    "system_prompt": _cfg.get("system_prompt", ""),
    "server_start":  time.time(),
    "request_count": 0,
    "chat_count":    0,
    "total_tokens_in":  0,
    "total_tokens_out": 0,
    "total_cost":       0.0,
    "last_session_tokens": {"in": 0, "out": 0},
}
if state["conversations"]:
    try:
        state["active_conv"] = max(
            state["conversations"].values(), key=lambda c: c.get("updated_at", 0)
        )["id"]
    except Exception:
        pass


def strip_ansi(s):
    return ANSI.sub("", s)


def browse_dir_sync():
    import tkinter as tk
    from tkinter import filedialog
    result = {"path": ""}
    done = threading.Event()
    def _pick():
        root = tk.Tk(); root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(initialdir=state["dir"])
        root.destroy(); result["path"] = path; done.set()
    threading.Thread(target=_pick, daemon=True).start()
    done.wait(timeout=120)
    return result["path"]


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ClaudioUI</title>
<style>
/* ── Reset & Variables ─────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:        #000308;
  --bg1:       #020810;
  --bg2:       #040e18;
  --bg3:       #071524;
  --surf0:     #0a1e30;
  --surf1:     #0d2538;
  --surf2:     #112d44;
  --text:      #c8eeff;
  --text2:     #7ab8d4;
  --text3:     #4a7a94;
  --cyan:      #00d4ff;
  --cyan2:     #00a8cc;
  --cyan3:     #006688;
  --teal:      #00ffcc;
  --magenta:   #ff00aa;
  --purple:    #9b30ff;
  --orange:    #ff7518;
  --green:     #00ff9c;
  --red:       #ff3366;
  --yellow:    #ffee00;
  --dim:       #1a3a52;
  --border:    #0d2d44;
  --border2:   #1a4560;
  --glow-c:    0 0 8px #00d4ff88, 0 0 20px #00d4ff33;
  --glow-m:    0 0 8px #ff00aa88, 0 0 20px #ff00aa33;
  --glow-t:    0 0 8px #00ffcc88, 0 0 20px #00ffcc33;
  --lsb-w:     280px;
  --rsb-w:     300px;
  --hdr-h:     48px;
  --snap:      24px;
  font-size: 13px;
}
body {
  background: var(--bg);
  color: var(--text);
  font-family: "Segoe UI", system-ui, sans-serif;
  height: 100vh;
  overflow: hidden;
  display: grid;
  grid-template-rows: var(--hdr-h) 1fr;
  grid-template-columns: 1fr;
}

/* ── Scrollbars ──────────────────────────────────────────────── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg1); }
::-webkit-scrollbar-thumb { background: var(--cyan3); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--cyan2); }

/* ── Header ──────────────────────────────────────────────────── */
#header {
  background: var(--bg1);
  border-bottom: 1px solid var(--border2);
  display: flex;
  align-items: center;
  padding: 0 14px;
  gap: 12px;
  position: relative;
  z-index: 100;
}
#header::after {
  content: '';
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--cyan), var(--magenta), var(--cyan), transparent);
  opacity: .6;
}
.hdr-logo {
  font-size: 15px;
  font-weight: 700;
  color: var(--cyan);
  text-shadow: var(--glow-c);
  letter-spacing: 2px;
  text-transform: uppercase;
  flex-shrink: 0;
}
.hdr-logo span { color: var(--magenta); text-shadow: var(--glow-m); }
.hdr-sep { width: 1px; height: 24px; background: var(--border2); flex-shrink: 0; }
#hdr-status {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--text3);
}
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 6px var(--green); flex-shrink: 0; }
.status-dot.offline { background: var(--red); box-shadow: 0 0 6px var(--red); }
.status-dot.busy    { background: var(--yellow); box-shadow: 0 0 6px var(--yellow); animation: pulse 1s infinite; }
#hdr-metrics { display: flex; gap: 16px; margin-left: auto; font-size: 11px; color: var(--text3); }
.hdr-metric { display: flex; align-items: center; gap: 4px; }
.hdr-metric .val { color: var(--cyan); font-family: monospace; }
#hdr-model-badge {
  background: var(--surf0);
  border: 1px solid var(--cyan3);
  border-radius: 3px;
  padding: 2px 8px;
  font-size: 11px;
  color: var(--cyan2);
}
.hdr-btn {
  background: var(--surf0);
  border: 1px solid var(--border2);
  color: var(--text2);
  padding: 4px 10px;
  border-radius: 3px;
  cursor: pointer;
  font-size: 11px;
  transition: all .15s;
}
.hdr-btn:hover { border-color: var(--cyan); color: var(--cyan); box-shadow: var(--glow-c); }

/* ── Main Layout ─────────────────────────────────────────────── */
#main {
  display: flex;
  overflow: hidden;
  height: 100%;
}

/* ── Sidebars ────────────────────────────────────────────────── */
.sidebar {
  display: flex;
  flex-direction: column;
  background: var(--bg1);
  overflow: hidden;
  position: relative;
  flex-shrink: 0;
}
#lsb { width: var(--lsb-w); border-right: 1px solid var(--border2); }
#rsb { width: var(--rsb-w); border-left: 1px solid var(--border2); }

.sidebar-inner {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  gap: 0;
}

/* ── Resize Handles ──────────────────────────────────────────── */
.resize-handle {
  position: absolute;
  top: 0; bottom: 0;
  width: 6px;
  cursor: col-resize;
  z-index: 50;
  background: transparent;
  transition: background .15s;
}
.resize-handle:hover, .resize-handle.active { background: var(--cyan3); }
#lsb .resize-handle { right: -3px; }
#rsb .resize-handle { left: -3px; }

/* ── Panel System ────────────────────────────────────────────── */
.panel {
  background: var(--bg2);
  border-bottom: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: box-shadow .15s;
  min-height: 32px;
  position: relative;
}
.panel.dragging { opacity: .5; }
.panel.drag-over { box-shadow: inset 0 2px 0 var(--cyan); }
.panel-header {
  display: flex;
  align-items: center;
  padding: 0 10px;
  height: 32px;
  background: var(--bg3);
  border-bottom: 1px solid var(--border);
  cursor: grab;
  user-select: none;
  flex-shrink: 0;
  gap: 6px;
}
.panel-header:active { cursor: grabbing; }
.panel-title {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--text3);
  flex: 1;
}
.panel-toggle {
  background: none; border: none; color: var(--text3);
  cursor: pointer; font-size: 10px; padding: 2px 4px;
  border-radius: 2px; transition: color .15s;
  flex-shrink: 0;
}
.panel-toggle:hover { color: var(--cyan); }
.panel-body {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.panel.collapsed .panel-body { display: none; }
.panel.collapsed { min-height: 32px; }
.panel-icon { font-size: 11px; }

/* Panel that expands to fill remaining space */
.panel.flex-fill { flex: 1; min-height: 80px; }
/* Panel fixed height */
.panel.fixed-h-sm .panel-body { height: 120px; }
.panel.fixed-h-md .panel-body { height: 200px; }
.panel.fixed-h-lg .panel-body { height: 260px; }

/* ── Conversations Panel ─────────────────────────────────────── */
#conv-list {
  list-style: none;
  overflow-y: auto;
  flex: 1;
  padding: 4px 0;
}
.conv-item {
  display: flex;
  align-items: center;
  padding: 6px 10px;
  gap: 6px;
  cursor: pointer;
  border-left: 2px solid transparent;
  transition: all .15s;
  position: relative;
}
.conv-item:hover { background: var(--surf0); }
.conv-item.active {
  background: var(--surf1);
  border-left-color: var(--cyan);
}
.conv-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--text3); flex-shrink: 0;
  transition: all .2s;
}
.conv-dot.idle    { background: var(--text3); }
.conv-dot.active  { background: var(--cyan);    box-shadow: 0 0 6px var(--cyan); }
.conv-dot.busy    { background: var(--yellow);  box-shadow: 0 0 6px var(--yellow); animation: pulse 1s infinite; }
.conv-dot.error   { background: var(--red);     box-shadow: 0 0 6px var(--red); }
.conv-dot.attention { background: var(--magenta); box-shadow: 0 0 6px var(--magenta); animation: pulse .8s infinite; }
.conv-name {
  flex: 1; font-size: 12px; color: var(--text2);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.conv-item.active .conv-name { color: var(--text); }
.conv-time { font-size: 10px; color: var(--text3); flex-shrink: 0; }
.conv-rename-input {
  flex: 1; background: var(--surf2); border: 1px solid var(--cyan3);
  color: var(--text); padding: 2px 6px; font-size: 12px; border-radius: 2px;
  outline: none;
}
.conv-actions { display: none; align-items: center; flex-shrink: 0; }
.conv-item:hover .conv-actions,
.conv-item.menu-open .conv-actions { display: flex; }
.conv-menu-btn {
  background: none; border: none; color: var(--text3); cursor: pointer;
  padding: 1px 5px; border-radius: 2px; font-size: 15px; line-height: 1;
}
.conv-menu-btn:hover { color: var(--cyan); background: var(--surf2); }
.conv-ctx-menu {
  position: fixed; background: var(--surf1); border: 1px solid var(--border2);
  border-radius: 4px; box-shadow: 0 4px 14px rgba(0,0,0,.45);
  z-index: 9999; min-width: 148px; padding: 4px 0; display: none;
}
.conv-ctx-menu.open { display: block; }
.conv-ctx-item {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 14px; cursor: pointer;
  font-size: 12px; color: var(--text2); transition: background .1s;
}
.conv-ctx-item:hover { background: var(--surf2); color: var(--text); }
.conv-ctx-item.danger:hover { color: var(--red); }
#conv-new-btn {
  margin: 6px 8px;
  width: calc(100% - 16px);
  background: var(--surf0);
  border: 1px dashed var(--cyan3);
  color: var(--cyan2);
  padding: 6px;
  border-radius: 3px;
  cursor: pointer;
  font-size: 11px;
  text-align: center;
  transition: all .15s;
  flex-shrink: 0;
}
#conv-new-btn:hover { border-color: var(--cyan); color: var(--cyan); background: var(--surf1); box-shadow: var(--glow-c); }

/* ── Custom Dropdown ─────────────────────────────────────────── */
.custom-select {
  position: relative;
  width: 100%;
}
.custom-select-trigger {
  display: flex;
  align-items: center;
  background: var(--surf0);
  border: 1px solid var(--border2);
  border-radius: 3px;
  padding: 6px 10px;
  cursor: pointer;
  user-select: none;
  transition: all .15s;
  gap: 6px;
}
.custom-select-trigger:hover { border-color: var(--cyan3); }
.custom-select-trigger.open { border-color: var(--cyan); box-shadow: var(--glow-c); }
.custom-select-val { flex: 1; color: var(--text2); font-size: 12px; }
.custom-select-arrow {
  width: 0; height: 0;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-top: 6px solid var(--text3);
  transition: transform .2s, border-top-color .15s;
  flex-shrink: 0;
}
.custom-select-trigger.open .custom-select-arrow { transform: rotate(180deg); border-top-color: var(--cyan); }
.custom-select-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 0; right: 0;
  background: var(--bg3);
  border: 1px solid var(--cyan3);
  border-radius: 3px;
  box-shadow: var(--glow-c), 0 8px 24px #00000080;
  z-index: 200;
  max-height: 200px;
  overflow-y: auto;
  display: none;
}
.custom-select-dropdown.open { display: block; }
.custom-select-option {
  padding: 7px 12px;
  cursor: pointer;
  font-size: 12px;
  color: var(--text2);
  transition: all .1s;
  border-left: 2px solid transparent;
}
.custom-select-option:hover { background: var(--surf1); color: var(--text); border-left-color: var(--cyan); }
.custom-select-option.selected { color: var(--cyan); border-left-color: var(--cyan); }

/* ── Form Elements ───────────────────────────────────────────── */
.field { padding: 8px 10px; display: flex; flex-direction: column; gap: 4px; }
.field label { font-size: 10px; color: var(--text3); text-transform: uppercase; letter-spacing: 1px; }
.field input[type=number], .field input[type=text] {
  background: var(--surf0); border: 1px solid var(--border2);
  color: var(--text); padding: 5px 8px; border-radius: 3px;
  font-size: 12px; outline: none; transition: border-color .15s;
  width: 100%;
}
.field input:focus { border-color: var(--cyan); }
.field-row { display: flex; gap: 6px; }
.btn-apply {
  background: var(--surf0); border: 1px solid var(--cyan3);
  color: var(--cyan2); padding: 5px 12px; border-radius: 3px;
  cursor: pointer; font-size: 11px; transition: all .15s; white-space: nowrap;
}
.btn-apply:hover { background: var(--surf1); border-color: var(--cyan); color: var(--cyan); box-shadow: var(--glow-c); }
.btn-danger { border-color: var(--red); color: var(--red); }
.btn-danger:hover { border-color: var(--red); color: var(--red); box-shadow: 0 0 8px #ff336688; }

/* ── Directory Browser ───────────────────────────────────────── */
#dir-path {
  padding: 6px 10px;
  font-size: 11px;
  color: var(--cyan2);
  font-family: monospace;
  background: var(--bg3);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 0;
}
#dir-browse-btn {
  margin: 6px 8px;
  width: calc(100% - 16px);
  background: var(--surf0);
  border: 1px solid var(--border2);
  color: var(--text2);
  padding: 5px;
  border-radius: 3px;
  cursor: pointer;
  font-size: 11px;
  transition: all .15s;
  flex-shrink: 0;
}
#dir-browse-btn:hover { border-color: var(--cyan3); color: var(--cyan2); }

/* ── Console Panel ───────────────────────────────────────────── */
#console-log {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
  font-family: "Cascadia Code", "Consolas", monospace;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text3);
}
.log-line { padding: 1px 4px; border-radius: 2px; }
.log-info  { color: var(--text3); }
.log-ok    { color: var(--green); }
.log-warn  { color: var(--yellow); }
.log-error { color: var(--red); }
.log-cmd   { color: var(--cyan2); }
.log-ts    { color: var(--text3); opacity: .6; margin-right: 4px; }

/* ── Server Info Panel (right sidebar) ──────────────────────── */
.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  padding: 6px;
}
.info-cell {
  background: var(--bg3);
  border-radius: 2px;
  padding: 6px 8px;
}
.info-cell .lbl { font-size: 10px; color: var(--text3); text-transform: uppercase; letter-spacing: .8px; }
.info-cell .val { font-size: 13px; color: var(--cyan); font-family: monospace; margin-top: 2px; }
.info-cell.span2 { grid-column: span 2; }
.server-btn-row { display: flex; gap: 6px; padding: 6px 8px; flex-shrink: 0; }
.server-btn {
  flex: 1; background: var(--surf0); border: 1px solid var(--border2);
  color: var(--text2); padding: 5px; border-radius: 3px; cursor: pointer;
  font-size: 11px; transition: all .15s; text-align: center;
}
.server-btn:hover { border-color: var(--cyan3); color: var(--cyan2); }
.server-btn.danger:hover { border-color: var(--red); color: var(--red); }

/* ── System Prompt Panel ─────────────────────────────────────── */
#sys-prompt-ta {
  flex: 1;
  background: var(--bg3);
  border: none;
  border-top: 1px solid var(--border);
  color: var(--text);
  font-family: "Cascadia Code", "Consolas", monospace;
  font-size: 11px;
  padding: 8px;
  resize: none;
  outline: none;
  line-height: 1.5;
}
#sys-prompt-ta:focus { border-color: var(--cyan); }
#sys-prompt-actions {
  display: flex;
  gap: 6px;
  padding: 6px 8px;
  flex-shrink: 0;
  background: var(--bg3);
  border-top: 1px solid var(--border);
}
#sys-prompt-status {
  flex: 1; font-size: 10px; color: var(--text3);
  display: flex; align-items: center;
}

/* ── Token Metrics Panel ─────────────────────────────────────── */
.metric-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  padding: 6px;
}
.metric-cell {
  background: var(--bg3);
  border-radius: 2px;
  padding: 6px 8px;
}
.metric-cell .m-lbl { font-size: 10px; color: var(--text3); text-transform: uppercase; letter-spacing: .8px; }
.metric-cell .m-val { font-size: 14px; color: var(--teal); font-family: monospace; margin-top: 2px; font-weight: 600; }
.metric-cell .m-sub { font-size: 10px; color: var(--text3); margin-top: 1px; }
.metric-cell.span2 { grid-column: span 2; }
.metric-bar {
  height: 3px;
  background: var(--bg3);
  border-radius: 2px;
  margin: 4px 8px;
  overflow: hidden;
}
.metric-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--cyan), var(--teal));
  border-radius: 2px;
  transition: width .3s;
}

/* ── Chat Area ───────────────────────────────────────────────── */
#chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg);
  min-width: 300px;
  position: relative;
}
#chat-header {
  background: var(--bg1);
  border-bottom: 1px solid var(--border2);
  padding: 0 16px;
  height: 40px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
#chat-title {
  font-size: 13px;
  color: var(--text);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
#chat-conv-status {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--text3);
}
#msgs {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
/* ── Messages ────────────────────────────────────────────────── */
.msg {
  display: flex;
  flex-direction: column;
  max-width: 88%;
  gap: 4px;
  animation: fadeSlide .2s ease;
}
.msg.user { align-self: flex-end; }
.msg.assistant { align-self: flex-start; }
.msg-bubble {
  padding: 10px 14px;
  border-radius: 6px;
  line-height: 1.6;
  font-size: 13px;
  word-break: break-word;
  position: relative;
}
.msg.user .msg-bubble {
  background: var(--surf2);
  border: 1px solid var(--cyan3);
  color: var(--text);
  border-bottom-right-radius: 2px;
}
.msg.assistant .msg-bubble {
  background: var(--bg3);
  border: 1px solid var(--border2);
  color: var(--text);
  border-bottom-left-radius: 2px;
}
.msg.assistant .msg-bubble.thinking {
  border-color: var(--purple);
  color: var(--text2);
  font-style: italic;
}
.msg.assistant .msg-bubble.thinking::before {
  content: '◆ ';
  color: var(--purple);
  font-style: normal;
}
.msg-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 10px;
  color: var(--text3);
}
.msg.user .msg-meta { align-self: flex-end; flex-direction: row-reverse; }
.msg.assistant .msg-meta { align-self: flex-start; }
.msg-time { font-family: monospace; }
/* ── Status Badge ─────────────────────────────────────────────── */
.status-badge {
  display: inline-flex; align-items: center; gap: 3px;
  padding: 1px 6px; border-radius: 10px;
  font-size: 9px; font-weight: 700; letter-spacing: .8px;
  text-transform: uppercase;
}
.s-sent     { background: #00d4ff18; color: var(--cyan2);    border: 1px solid var(--cyan3); }
.s-received { background: #9b30ff18; color: var(--purple);   border: 1px solid #6a20bb; }
.s-processing { background: #ffee0018; color: var(--yellow); border: 1px solid #aa9900; animation: pulse 1s infinite; }
.s-done     { background: #00ff9c18; color: var(--teal);     border: 1px solid #00aa66; }
.s-failed   { background: #ff336618; color: var(--red);      border: 1px solid #aa2244; }
.s-queued   { background: #ff751818; color: var(--orange);   border: 1px solid #aa4400; }
/* ── Queue Indicator ─────────────────────────────────────────── */
#queue-bar {
  display: none;
  background: var(--bg2);
  border-top: 1px solid var(--border);
  padding: 5px 14px;
  font-size: 11px;
  color: var(--orange);
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
}
#queue-bar.visible { display: flex; }
#queue-count { font-weight: 700; color: var(--yellow); }
/* ── Thinking Banner ─────────────────────────────────────────── */
#thinking-banner {
  display: none;
  background: var(--bg2);
  border-top: 1px solid var(--purple);
  padding: 5px 14px;
  font-size: 11px;
  color: var(--purple);
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
}
#thinking-banner.visible { display: flex; }
.thinking-dots::after {
  content: '...';
  animation: dots 1.5s steps(3,end) infinite;
}
/* ── Input Area ──────────────────────────────────────────────── */
#input-area {
  border-top: 1px solid var(--border2);
  background: var(--bg1);
  padding: 10px 14px;
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
#msg-input {
  flex: 1;
  background: var(--surf0);
  border: 1px solid var(--border2);
  color: var(--text);
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 13px;
  outline: none;
  resize: none;
  font-family: inherit;
  line-height: 1.5;
  min-height: 38px;
  max-height: 140px;
  transition: border-color .15s;
}
#msg-input:focus { border-color: var(--cyan); }
#msg-input:disabled { opacity: .6; }
#send-btn {
  background: var(--cyan3);
  border: 1px solid var(--cyan2);
  color: var(--text);
  padding: 8px 16px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: .5px;
  transition: all .15s;
  align-self: flex-end;
  white-space: nowrap;
}
#send-btn:hover { background: var(--cyan2); box-shadow: var(--glow-c); }
#send-btn.queuing { background: var(--surf1); border-color: var(--orange); color: var(--orange); }
#send-btn:disabled { background: var(--surf1); border-color: var(--border2); color: var(--text3); cursor: not-allowed; opacity: .6; box-shadow: none; }
/* ── System message ──────────────────────────────────────────── */
.sys-msg {
  align-self: center;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 5px 12px;
  font-size: 11px;
  color: var(--text3);
  font-style: italic;
  max-width: 70%;
  text-align: center;
}
/* ── Code blocks ─────────────────────────────────────────────── */
pre {
  background: var(--bg);
  border: 1px solid var(--border2);
  border-radius: 4px;
  padding: 10px 14px;
  overflow-x: auto;
  margin: 6px 0;
  font-family: "Cascadia Code", "Consolas", monospace;
  font-size: 12px;
  line-height: 1.5;
}
code { font-family: "Cascadia Code", "Consolas", monospace; font-size: 12px; color: var(--cyan2); }
pre code { color: var(--text2); }
/* ── Animations ──────────────────────────────────────────────── */
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
@keyframes dots  { 0%{content:'.'} 33%{content:'..'} 66%{content:'...'} }
@keyframes fadeSlide { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
@keyframes scan {
  0%   { background-position: 0 0; }
  100% { background-position: 0 100vh; }
}
/* Scanline overlay */
body::after {
  content: '';
  position: fixed;
  inset: 0;
  background: repeating-linear-gradient(
    0deg, transparent, transparent 2px,
    rgba(0,212,255,.015) 2px, rgba(0,212,255,.015) 4px
  );
  pointer-events: none;
  z-index: 9999;
}
/* ── Neon borders on key panels ─────────────────────────────── */
#chat-area { box-shadow: inset 0 0 1px var(--cyan3), inset -0 0 1px var(--cyan3); }
</style>
</head>
<body>

<!-- ── Header ──────────────────────────────────────────────────── -->
<div id="header">
  <div class="hdr-logo">Claud<span>io</span>UI</div>
  <div class="hdr-sep"></div>
  <div id="hdr-status">
    <div class="status-dot" id="srv-dot"></div>
    <span id="srv-lbl">Online</span>
  </div>
  <div class="hdr-sep"></div>
  <div id="hdr-model-badge">claude-sonnet-4-6</div>
  <div id="hdr-metrics">
    <div class="hdr-metric"><span>↑</span><span class="val" id="m-tok-in">0</span><span>in</span></div>
    <div class="hdr-metric"><span>↓</span><span class="val" id="m-tok-out">0</span><span>out</span></div>
    <div class="hdr-metric"><span>⏱</span><span class="val" id="m-uptime">0m</span></div>
    <div class="hdr-metric"><span>#</span><span class="val" id="m-reqs">0</span><span>req</span></div>
  </div>
  <button class="hdr-btn" onclick="newConversation()">+ New Chat</button>
  <button class="hdr-btn" onclick="togglePanel('lsb')">&#9776;</button>
  <button class="hdr-btn" onclick="togglePanel('rsb')">&#9776;</button>
</div>

<!-- ── Main ────────────────────────────────────────────────────── -->
<div id="main">

  <!-- ── Left Sidebar ─────────────────────────────────────────── -->
  <div class="sidebar" id="lsb">
    <div class="resize-handle" id="lsb-resize"></div>
    <div class="sidebar-inner" id="lsb-inner">

      <!-- Conversations Panel -->
      <div class="panel flex-fill" id="panel-convs" draggable="true" data-panel="convs">
        <div class="panel-header">
          <span class="panel-icon">&#9776;</span>
          <span class="panel-title">Conversations</span>
          <button class="panel-toggle" onclick="togglePanel('panel-convs')">&#9660;</button>
        </div>
        <div class="panel-body">
          <ul id="conv-list"></ul>
          <button id="conv-new-btn" onclick="newConversation()">+ New Conversation</button>
        </div>
      </div>

      <!-- Model Config Panel -->
      <div class="panel fixed-h-md" id="panel-model" draggable="true" data-panel="model">
        <div class="panel-header">
          <span class="panel-icon">&#9881;</span>
          <span class="panel-title">Model Config</span>
          <button class="panel-toggle" onclick="togglePanel('panel-model')">&#9660;</button>
        </div>
        <div class="panel-body" style="overflow-y:auto">
          <div class="field">
            <label>Model</label>
            <div class="custom-select" id="model-select-wrap">
              <div class="custom-select-trigger" id="model-trigger" onclick="toggleSelect('model-select-wrap')">
                <span class="custom-select-val" id="model-val">Sonnet 4.6</span>
                <span class="custom-select-arrow"></span>
              </div>
              <div class="custom-select-dropdown" id="model-dropdown"></div>
            </div>
          </div>
          <div class="field">
            <label>Max Tokens</label>
            <div class="field-row">
              <input type="number" id="max-tokens-inp" min="256" max="100000" step="256" value="4096">
              <button class="btn-apply" onclick="applyModel()">Apply</button>
            </div>
          </div>
          <div class="field">
            <label>Session Cost</label>
            <div style="font-size:13px;color:var(--teal);font-family:monospace;padding:2px 0" id="cost-display">$0.0000</div>
          </div>
        </div>
      </div>

      <!-- Directory Panel -->
      <div class="panel" id="panel-dir" draggable="true" data-panel="dir" style="min-height:80px">
        <div class="panel-header">
          <span class="panel-icon">&#128193;</span>
          <span class="panel-title">Directory</span>
          <button class="panel-toggle" onclick="togglePanel('panel-dir')">&#9660;</button>
        </div>
        <div class="panel-body">
          <div id="dir-path">/</div>
          <button id="dir-browse-btn" onclick="browseDir()">&#128194; Browse...</button>
        </div>
      </div>

      <!-- Console Panel — pinned to bottom -->
      <div class="panel" id="panel-console" draggable="true" data-panel="console" style="flex:0 0 160px;min-height:60px">
        <div class="panel-header">
          <span class="panel-icon">&#9654;</span>
          <span class="panel-title">Console</span>
          <button class="panel-toggle" onclick="togglePanel('panel-console')">&#9660;</button>
        </div>
        <div class="panel-body">
          <div id="console-log"></div>
        </div>
      </div>

    </div><!-- /lsb-inner -->
  </div><!-- /lsb -->

  <!-- ── Chat Area ─────────────────────────────────────────────── -->
  <div id="chat-area">
    <div id="chat-header">
      <div id="chat-title">Select or create a conversation</div>
      <div id="chat-conv-status"></div>
    </div>
    <div id="msgs"></div>
    <div id="thinking-banner"><span>&#9900;</span><span>Claude is thinking<span class="thinking-dots"></span></span></div>
    <div id="queue-bar"><span>&#9205;</span> <span id="queue-count">0</span> message(s) queued</div>
    <div id="input-area">
      <textarea id="msg-input" rows="1" placeholder="Type a message… (Enter to send, Shift+Enter for newline)"></textarea>
      <button id="send-btn" onclick="sendMessage()">Send</button>
    </div>
  </div>

  <!-- ── Right Sidebar ─────────────────────────────────────────── -->
  <div class="sidebar" id="rsb">
    <div class="resize-handle" id="rsb-resize"></div>
    <div class="sidebar-inner" id="rsb-inner">

      <!-- System Prompt Panel -->
      <div class="panel flex-fill" id="panel-sysprompt" draggable="true" data-panel="sysprompt">
        <div class="panel-header">
          <span class="panel-icon">&#9998;</span>
          <span class="panel-title">System Prompt</span>
          <button class="panel-toggle" onclick="togglePanel('panel-sysprompt')">&#9660;</button>
        </div>
        <div class="panel-body">
          <textarea id="sys-prompt-ta" placeholder="Enter a system prompt for Claude..."></textarea>
          <div id="sys-prompt-actions">
            <span id="sys-prompt-status">Not applied</span>
            <button class="btn-apply" onclick="applySystemPrompt()">Apply</button>
            <button class="btn-apply btn-danger" onclick="clearSystemPrompt()">Clear</button>
          </div>
        </div>
      </div>

      <!-- Server Info Panel -->
      <div class="panel" id="panel-server" draggable="true" data-panel="server" style="flex-shrink:0">
        <div class="panel-header">
          <span class="panel-icon">&#9679;</span>
          <span class="panel-title">Server Info</span>
          <button class="panel-toggle" onclick="togglePanel('panel-server')">&#9660;</button>
        </div>
        <div class="panel-body">
          <div class="info-grid">
            <div class="info-cell"><div class="lbl">Port</div><div class="val">8765</div></div>
            <div class="info-cell"><div class="lbl">Uptime</div><div class="val" id="srv-uptime">0m</div></div>
            <div class="info-cell"><div class="lbl">Requests</div><div class="val" id="srv-reqs">0</div></div>
            <div class="info-cell"><div class="lbl">Status</div><div class="val" id="srv-status-val" style="color:var(--green)">Online</div></div>
            <div class="info-cell span2"><div class="lbl">Host</div><div class="val">127.0.0.1:8765</div></div>
          </div>
          <div class="server-btn-row">
            <button class="server-btn" onclick="restartServer()">&#8635; Restart</button>
            <button class="server-btn danger" onclick="stopServer()">&#9632; Stop</button>
            <button class="server-btn" onclick="window.open('http://127.0.0.1:8765','_blank')">&#8599; Open</button>
          </div>
        </div>
      </div>

      <!-- Token Metrics Panel -->
      <div class="panel" id="panel-tokens" draggable="true" data-panel="tokens" style="flex-shrink:0">
        <div class="panel-header">
          <span class="panel-icon">&#128200;</span>
          <span class="panel-title">Token Metrics</span>
          <button class="panel-toggle" onclick="togglePanel('panel-tokens')">&#9660;</button>
        </div>
        <div class="panel-body">
          <div class="metric-grid">
            <div class="metric-cell"><div class="m-lbl">Tokens In</div><div class="m-val" id="mt-in">0</div><div class="m-sub">session total</div></div>
            <div class="metric-cell"><div class="m-lbl">Tokens Out</div><div class="m-val" id="mt-out">0</div><div class="m-sub">session total</div></div>
            <div class="metric-cell"><div class="m-lbl">Last In</div><div class="m-val" id="mt-last-in" style="color:var(--cyan)">0</div></div>
            <div class="metric-cell"><div class="m-lbl">Last Out</div><div class="m-val" id="mt-last-out" style="color:var(--cyan)">0</div></div>
            <div class="metric-cell span2"><div class="m-lbl">Est. Cost</div><div class="m-val" id="mt-cost" style="color:var(--teal)">$0.0000</div></div>
          </div>
          <div class="metric-bar"><div class="metric-bar-fill" id="mt-bar" style="width:0%"></div></div>
        </div>
      </div>

      <!-- Connection Info Panel -->
      <div class="panel" id="panel-conn" draggable="true" data-panel="conn" style="flex-shrink:0">
        <div class="panel-header">
          <span class="panel-icon">&#128279;</span>
          <span class="panel-title">Connection</span>
          <button class="panel-toggle" onclick="togglePanel('panel-conn')">&#9660;</button>
        </div>
        <div class="panel-body">
          <div class="info-grid">
            <div class="info-cell span2"><div class="lbl">API Endpoint</div><div class="val" style="font-size:11px">http://127.0.0.1:8765</div></div>
            <div class="info-cell"><div class="lbl">Latency</div><div class="val" id="ci-latency">--</div></div>
            <div class="info-cell"><div class="lbl">Last Ping</div><div class="val" id="ci-ping">--</div></div>
            <div class="info-cell span2"><div class="lbl">Transport</div><div class="val" style="color:var(--teal)">SSE / HTTP</div></div>
          </div>
        </div>
      </div>

    </div><!-- /rsb-inner -->
  </div><!-- /rsb -->

</div><!-- /main -->

<script>
// ── State ────────────────────────────────────────────────────────
const S = {
  convs: {},
  active: null,
  thinking: false,
  queue: [],
  tokIn: 0, tokOut: 0, cost: 0,
  lastTokIn: 0, lastTokOut: 0,
  chatCount: 0,
  model: "claude-sonnet-4-6",
  srvStart: Date.now(),
  history: [],   // sent message history (up to 100)
  histIdx: -1,   // current nav position (-1 = not navigating)
  histDraft: "", // draft saved when history nav begins
};

const STATUS_CFG = {
  "sent":       {cls:"s-sent",       icon:"&#9654;",  label:"SENT"},
  "received":   {cls:"s-received",   icon:"&#8594;",  label:"RCVD"},
  "processing": {cls:"s-processing", icon:"&#9900;",  label:"PROC"},
  "done":       {cls:"s-done",       icon:"&#10003;", label:"DONE"},
  "failed":     {cls:"s-failed",     icon:"&#10005;", label:"FAIL"},
  "queued":     {cls:"s-queued",     icon:"&#9205;",  label:"WAIT"},
};

// ── Init ─────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  loadState();
  buildModelDropdown();
  loadConversations();
  setupResize();
  setupDragDrop();
  setupInput();
  setInterval(pollState, 5000);
  setInterval(updateUptime, 30000);
  updateUptime();
  log("ClaudioUI initialized", "ok");
  log("Session isolation: --resume mode", "info");
});

// ── Utilities ─────────────────────────────────────────────────────
function $(id) { return document.getElementById(id); }
function fmtTime(ts) {
  const d = new Date(ts);
  return d.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"});
}
function fmtNum(n) {
  if (n >= 1e6) return (n/1e6).toFixed(1)+"M";
  if (n >= 1e3) return (n/1e3).toFixed(1)+"k";
  return String(n);
}
function relTime(ts) {
  const d = Math.floor((Date.now() - ts) / 1000);
  if (d < 60) return d+"s ago";
  if (d < 3600) return Math.floor(d/60)+"m ago";
  return Math.floor(d/3600)+"h ago";
}

function log(msg, type="info") {
  const el = $("console-log");
  const line = document.createElement("div");
  line.className = `log-line log-${type}`;
  const ts = new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"});
  line.innerHTML = `<span class="log-ts">${ts}</span>${escHtml(msg)}`;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
  // Keep last 200 lines
  while (el.children.length > 200) el.removeChild(el.firstChild);
}
function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function sysMsg(text) {
  const el = document.createElement("div");
  el.className = "sys-msg";
  el.textContent = text;
  $("msgs").appendChild(el);
  scrollChat();
}

// ── Persistence ───────────────────────────────────────────────────
function saveLS() {
  localStorage.setItem("claudioui_lsb", getComputedStyle(document.documentElement).getPropertyValue("--lsb-w").trim());
  localStorage.setItem("claudioui_rsb", getComputedStyle(document.documentElement).getPropertyValue("--rsb-w").trim());
}
function loadState() {
  const lw = localStorage.getItem("claudioui_lsb");
  const rw = localStorage.getItem("claudioui_rsb");
  if (lw) document.documentElement.style.setProperty("--lsb-w", lw);
  if (rw) document.documentElement.style.setProperty("--rsb-w", rw);
  const collapsed = JSON.parse(localStorage.getItem("claudioui_collapsed") || "[]");
  collapsed.forEach(id => { const p = $(id); if (p) p.classList.add("collapsed"); });
  // Panel order
  const lorder = JSON.parse(localStorage.getItem("claudioui_lorder") || "null");
  const rorder = JSON.parse(localStorage.getItem("claudioui_rorder") || "null");
  if (lorder) restoreOrder("lsb-inner", lorder);
  if (rorder) restoreOrder("rsb-inner", rorder);
}
function restoreOrder(containerId, order) {
  const container = $(containerId);
  if (!container) return;
  order.forEach(id => { const el = $(id); if (el) container.appendChild(el); });
}
function savePanelOrder() {
  const lids = [...$("lsb-inner").querySelectorAll(".panel")].map(p=>p.id);
  const rids = [...$("rsb-inner").querySelectorAll(".panel")].map(p=>p.id);
  localStorage.setItem("claudioui_lorder", JSON.stringify(lids));
  localStorage.setItem("claudioui_rorder", JSON.stringify(rids));
}
function saveCollapsed() {
  const ids = [...document.querySelectorAll(".panel.collapsed")].map(p=>p.id);
  localStorage.setItem("claudioui_collapsed", JSON.stringify(ids));
}

// ── Panel Toggle ─────────────────────────────────────────────────
function togglePanel(id) {
  const el = $(id);
  if (!el) return;
  if (el.classList.contains("sidebar")) {
    el.style.display = el.style.display === "none" ? "" : "none";
    return;
  }
  el.classList.toggle("collapsed");
  const btn = el.querySelector(".panel-toggle");
  if (btn) btn.innerHTML = el.classList.contains("collapsed") ? "&#9650;" : "&#9660;";
  saveCollapsed();
}

// ── Drag & Drop Panels ────────────────────────────────────────────
let dragSrc = null;
function setupDragDrop() {
  document.querySelectorAll(".panel[draggable]").forEach(p => {
    p.addEventListener("dragstart", onPanelDragStart);
    p.addEventListener("dragend",   onPanelDragEnd);
    p.addEventListener("dragover",  onPanelDragOver);
    p.addEventListener("dragleave", onPanelDragLeave);
    p.addEventListener("drop",      onPanelDrop);
  });
  // Allow drag over the whole layout so cross-sidebar drags work across chat-area
  $("main").addEventListener("dragover", e => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; });
  // Keep the "move" cursor when crossing the chat area so the drag isn't aborted
  $("chat-area").addEventListener("dragover", e => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; });
  // Also allow dropping into empty sidebar areas
  ["lsb-inner","rsb-inner"].forEach(id => {
    const el = $(id);
    el.addEventListener("dragover", e => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; });
    el.addEventListener("drop", e => {
      e.preventDefault();
      e.stopPropagation();
      if (dragSrc) el.appendChild(dragSrc);
      savePanelOrder();
    });
  });
}
function onPanelDragStart(e) {
  dragSrc = this;
  this.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", this.id);
}
function onPanelDragEnd() {
  dragSrc = null;
  document.querySelectorAll(".panel").forEach(p => {
    p.classList.remove("dragging","drag-over");
  });
  savePanelOrder();
}
function onPanelDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  if (dragSrc && dragSrc !== this) this.classList.add("drag-over");
}
function onPanelDragLeave() { this.classList.remove("drag-over"); }
function onPanelDrop(e) {
  e.preventDefault();
  e.stopPropagation();
  if (dragSrc && dragSrc !== this) {
    const parent = this.parentNode;
    const rect = this.getBoundingClientRect();
    const mid = rect.top + rect.height / 2;
    if (e.clientY < mid) parent.insertBefore(dragSrc, this);
    else parent.insertBefore(dragSrc, this.nextSibling);
  }
  this.classList.remove("drag-over");
  savePanelOrder();
}

// ── Sidebar Resize ────────────────────────────────────────────────
function setupResize() {
  setupResizeHandle("lsb-resize", "lsb", "--lsb-w", true);
  setupResizeHandle("rsb-resize", "rsb", "--rsb-w", false);
}
function setupResizeHandle(handleId, sidebarId, cssVar, isLeft) {
  const handle = $(handleId);
  if (!handle) return;
  let startX, startW;
  handle.addEventListener("mousedown", e => {
    e.preventDefault();
    startX = e.clientX;
    startW = $(sidebarId).offsetWidth;
    handle.classList.add("active");
    const onMove = ev => {
      let dx = isLeft ? ev.clientX - startX : startX - ev.clientX;
      let w = Math.max(180, Math.min(520, startW + dx));
      // Snap to grid
      w = Math.round(w / 24) * 24;
      document.documentElement.style.setProperty(cssVar, w+"px");
    };
    const onUp = () => {
      handle.classList.remove("active");
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      saveLS();
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

// ── Custom Select ─────────────────────────────────────────────────
function toggleSelect(wrapId) {
  const wrap = $(wrapId);
  const trigger = wrap.querySelector(".custom-select-trigger");
  const dd = wrap.querySelector(".custom-select-dropdown");
  const isOpen = dd.classList.contains("open");
  // Close all
  document.querySelectorAll(".custom-select-dropdown.open").forEach(d=>d.classList.remove("open"));
  document.querySelectorAll(".custom-select-trigger.open").forEach(t=>t.classList.remove("open"));
  if (!isOpen) { dd.classList.add("open"); trigger.classList.add("open"); }
}
document.addEventListener("click", e => {
  if (!e.target.closest(".custom-select")) {
    document.querySelectorAll(".custom-select-dropdown.open").forEach(d=>d.classList.remove("open"));
    document.querySelectorAll(".custom-select-trigger.open").forEach(t=>t.classList.remove("open"));
  }
});
function modelLabel(id) {
  const m = (window._MODELS_ || []).find(([v]) => v === id);
  return m ? m[1].split(" --")[0] : id.split("/").pop();
}
function buildModelDropdown() {
  const dd = $("model-dropdown");
  const models = window._MODELS_ || [];
  dd.innerHTML = "";
  models.forEach(([val,label]) => {
    const opt = document.createElement("div");
    opt.className = "custom-select-option";
    opt.dataset.value = val;
    opt.textContent = label;
    if (val === S.model) { opt.classList.add("selected"); $("model-val").textContent = label; $("hdr-model-badge").textContent = modelLabel(val); }
    opt.onclick = () => {
      S.model = val;
      $("model-val").textContent = label;
      dd.querySelectorAll(".custom-select-option").forEach(o=>o.classList.remove("selected"));
      opt.classList.add("selected");
      toggleSelect("model-select-wrap");
      $("hdr-model-badge").textContent = label.split(" --")[0];
    };
    dd.appendChild(opt);
  });
}

// ── Conversations ─────────────────────────────────────────────────
function loadConversations() {
  fetch("/api/state").then(r=>r.json()).then(data => {
    S.convs = data.conversations || {};
    S.active = data.active_conv;
    S.model = data.model || S.model;
    S.tokIn = data.total_tokens_in || 0;
    S.tokOut = data.total_tokens_out || 0;
    S.cost = data.total_cost || 0;
    S.lastTokIn = (data.last_session_tokens||{}).in || 0;
    S.lastTokOut = (data.last_session_tokens||{}).out || 0;
    S.chatCount = data.chat_count || 0;
    S.srvStart = data.server_start ? data.server_start * 1000 : Date.now();
    renderConvList();
    if (S.active) loadConvMessages(S.active);
    $("sys-prompt-ta").value = data.system_prompt || "";
    $("max-tokens-inp").value = data.max_tokens || 4096;
    updateMetrics();
    updateModelUI();
    buildModelDropdown();
    updateUptime();
  }).catch(e => {
    log("Failed to load state: "+e, "error");
    $("srv-dot").className = "status-dot offline";
    $("srv-lbl").textContent = "Offline";
  });
}
function updateModelUI() {
  const model = S.model;
  $("hdr-model-badge").textContent = modelLabel(model);
  const dd = $("model-dropdown");
  if (!dd) return;
  dd.querySelectorAll(".custom-select-option").forEach(o => {
    const sel = o.dataset.value === model;
    o.classList.toggle("selected", sel);
    if (sel) $("model-val").textContent = o.textContent;
  });
}
function renderConvList() {
  const ul = $("conv-list");
  ul.innerHTML = "";
  const sorted = Object.values(S.convs).sort((a,b) => (b.updated_at||0)-(a.updated_at||0));
  sorted.forEach(c => {
    const li = document.createElement("li");
    li.className = "conv-item" + (c.id === S.active ? " active" : "");
    li.dataset.id = c.id;
    const dotCls = convStatusCls(c);
    li.innerHTML = `
      <span class="conv-dot ${dotCls}"></span>
      <span class="conv-name" ondblclick="startRename('${c.id}')">${escHtml(c.name||"Untitled")}</span>
      <span class="conv-time">${relTime((c.updated_at||0)*1000)}</span>
      <span class="conv-actions">
        <button class="conv-menu-btn" title="Options" onclick="openConvMenu('${c.id}',event)">&#8942;</button>
      </span>
    `;
    li.addEventListener("click", e => {
      if (e.target.classList.contains("conv-menu-btn") || e.target.classList.contains("conv-rename-input")) return;
      switchConv(c.id);
    });
    ul.appendChild(li);
  });
}
let _ctxMenu = null, _ctxConvId = null;
function _initCtxMenu() {
  if (_ctxMenu) return;
  _ctxMenu = document.createElement("div");
  _ctxMenu.className = "conv-ctx-menu";
  _ctxMenu.innerHTML = `
    <div class="conv-ctx-item" onclick="_ctxRename()">&#9998;&ensp;Rename</div>
    <div class="conv-ctx-item danger" onclick="_ctxDelete()">&#10005;&ensp;Delete</div>`;
  document.body.appendChild(_ctxMenu);
  document.addEventListener("click", e => {
    if (_ctxMenu && !_ctxMenu.contains(e.target)) _closeCtxMenu();
  }, true);
}
function openConvMenu(id, e) {
  e.stopPropagation();
  _initCtxMenu();
  _ctxConvId = id;
  const r = e.currentTarget.getBoundingClientRect();
  _ctxMenu.style.top = (r.bottom + 2) + "px";
  _ctxMenu.style.left = r.left + "px";
  document.querySelectorAll(".conv-item").forEach(el => el.classList.remove("menu-open"));
  e.currentTarget.closest(".conv-item").classList.add("menu-open");
  _ctxMenu.classList.add("open");
}
function _closeCtxMenu() {
  if (_ctxMenu) _ctxMenu.classList.remove("open");
  document.querySelectorAll(".conv-item.menu-open").forEach(el => el.classList.remove("menu-open"));
}
function _ctxRename() { _closeCtxMenu(); if (_ctxConvId) startRename(_ctxConvId); }
function _ctxDelete() { _closeCtxMenu(); if (_ctxConvId) deleteConv(_ctxConvId); }
function convStatusCls(c) {
  const st = c.status || "idle";
  if (st === "busy") return "busy";
  if (st === "error") return "error";
  if (st === "active") return "active";
  return "idle";
}
function switchConv(id) {
  S.active = id;
  fetch("/api/conversations/active", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({id})
  });
  renderConvList();
  loadConvMessages(id);
  const c = S.convs[id];
  if (c) { $("chat-title").textContent = c.name || "Untitled"; }
}
function loadConvMessages(id) {
  const conv = S.convs[id];
  if (!conv) return;
  $("chat-title").textContent = conv.name || "Untitled";
  $("msgs").innerHTML = "";
  if (conv.messages) conv.messages.forEach(m => appendMsg(m, false));
  scrollChat();
}
function newConversation() {
  fetch("/api/conversations", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({})})
    .then(r=>r.json()).then(c => {
      S.convs[c.id] = c;
      S.active = c.id;
      renderConvList();
      $("msgs").innerHTML = "";
      $("chat-title").textContent = c.name;
      sysMsg("New conversation started — clean slate.");
      log("New conversation: "+c.name, "ok");
    });
}
function startRename(id) {
  const li = $("conv-list").querySelector(`[data-id="${id}"]`);
  if (!li) return;
  const nameEl = li.querySelector(".conv-name");
  const inp = document.createElement("input");
  inp.className = "conv-rename-input";
  inp.value = nameEl.textContent;
  nameEl.replaceWith(inp);
  inp.focus(); inp.select();
  const done = () => {
    const newName = inp.value.trim() || nameEl.textContent;
    fetch(`/api/conversations/${id}`, {
      method:"PATCH", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({name: newName})
    }).then(() => {
      if (S.convs[id]) S.convs[id].name = newName;
      renderConvList();
      if (id === S.active) $("chat-title").textContent = newName;
    });
  };
  inp.addEventListener("keydown", e => { if (e.key==="Enter") { e.preventDefault(); done(); } if (e.key==="Escape") renderConvList(); });
  inp.addEventListener("blur", done);
}
function deleteConv(id) {
  if (!confirm("Delete this conversation?")) return;
  fetch(`/api/conversations/${id}`, {method:"DELETE"}).then(() => {
    delete S.convs[id];
    if (S.active === id) { S.active = null; $("msgs").innerHTML = ""; $("chat-title").textContent = "Select a conversation"; }
    renderConvList();
  });
}

// ── Send & Receive ────────────────────────────────────────────────
const REDIRECT_RE = /^(actually|wait,?|no,?|stop,?|ignore|forget|different|instead|wrong|not quite|hold on)/i;

function setupInput() {
  const inp = $("msg-input");
  inp.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!S.thinking) sendMessage();
      return;
    }
    if (e.key === "ArrowUp" && !inp.value.includes("\n")) {
      if (S.history.length === 0) return;
      e.preventDefault();
      if (S.histIdx === -1) { S.histDraft = inp.value; S.histIdx = S.history.length - 1; }
      else if (S.histIdx > 0) S.histIdx--;
      inp.value = S.history[S.histIdx];
      inp.style.height = "auto";
      inp.style.height = Math.min(inp.scrollHeight, 140) + "px";
      setTimeout(() => { inp.selectionStart = inp.selectionEnd = inp.value.length; }, 0);
      return;
    }
    if (e.key === "ArrowDown" && S.histIdx !== -1) {
      e.preventDefault();
      if (S.histIdx < S.history.length - 1) { S.histIdx++; inp.value = S.history[S.histIdx]; }
      else { S.histIdx = -1; inp.value = S.histDraft; }
      inp.style.height = "auto";
      inp.style.height = Math.min(inp.scrollHeight, 140) + "px";
      setTimeout(() => { inp.selectionStart = inp.selectionEnd = inp.value.length; }, 0);
    }
  });
  inp.addEventListener("input", () => {
    S.histIdx = -1;
    inp.style.height = "auto";
    inp.style.height = Math.min(inp.scrollHeight, 140) + "px";
  });
}

function sendMessage() {
  const inp = $("msg-input");
  const text = inp.value.trim();
  if (!text) return;
  inp.value = "";
  inp.style.height = "auto";
  if (!S.active) { sysMsg("Please select or create a conversation first."); return; }
  if (S.thinking) {
    S.queue.push({text, ts: Date.now()});
    updateQueueBar();
    sysMsg(`Message queued (${S.queue.length} in queue)`);
    log("Message queued: "+text.slice(0,40), "warn");
    return;
  }
  doSend(text);
}

function doSend(text) {
  if (S.history.length === 0 || S.history[S.history.length - 1] !== text) {
    S.history.push(text);
    if (S.history.length > 100) S.history.shift();
  }
  S.histIdx = -1;
  S.histDraft = "";
  const msgId = "msg-"+Date.now();
  const ts = Date.now();
  appendMsg({role:"user", content:text, id:msgId, ts, status:"sent"}, true);
  scrollChat();
  setThinking(true);
  // Store message in conv
  const conv = S.convs[S.active];
  if (conv) {
    if (!conv.messages) conv.messages = [];
    conv.messages.push({role:"user", content:text, id:msgId, ts, status:"sent"});
  }
  log("Sending: "+text.slice(0,50)+(text.length>50?"...":""), "cmd");
  setMsgStatus(msgId, "received");
  setTimeout(() => setMsgStatus(msgId, "processing"), 300);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/chat");
  xhr.setRequestHeader("Content-Type", "application/json");
  const aId = "resp-"+Date.now();
  let buf = "", aEl = null, started = false;
  xhr.onprogress = () => {
    const chunk = xhr.responseText.slice(buf.length);
    buf = xhr.responseText;
    const lines = chunk.split("\n");
    lines.forEach(line => {
      line = line.trim();
      if (!line || line === "data: [DONE]") return;
      if (line.startsWith("data: ")) {
        try {
          const ev = JSON.parse(line.slice(6));
          handleStreamEvent(ev, aId, msgId, () => {
            if (!aEl) {
              aEl = appendMsg({role:"assistant", content:"", id:aId, ts:Date.now(), status:"processing"}, true);
              started = true;
            }
            const bubble = aEl.querySelector(".msg-bubble");
            if (bubble) {
              bubble.innerHTML = renderMarkdown(ev.text || ev.content || "");
            }
            const conv = S.convs[S.active];
            if (conv && started) {
              const existing = conv.messages ? conv.messages.find(m=>m.id===aId) : null;
              if (existing) existing.content = ev.text || ev.content || "";
              else { if (!conv.messages) conv.messages=[]; conv.messages.push({role:"assistant",content:ev.text||ev.content||"",id:aId,ts:Date.now(),status:"processing"}); }
            }
            scrollChat();
          });
        } catch(e) {}
      }
    });
  };
  xhr.onload = () => {
    setThinking(false);
    setMsgStatus(msgId, "done");
    if (aEl) setMsgStatus(aId, "done");
    const conv = S.convs[S.active];
    if (conv) {
      conv.updated_at = Math.floor(Date.now()/1000);
      conv.status = "idle";
    }
    renderConvList();
    drainQueue();
    pollState();
    log("Response complete", "ok");
  };
  xhr.onerror = () => {
    setThinking(false);
    setMsgStatus(msgId, "failed");
    sysMsg("Connection error — check server.");
    log("XHR error", "error");
    drainQueue();
  };
  xhr.send(JSON.stringify({
    conv_id: S.active,
    message: text,
    model: S.model,
    max_tokens: parseInt($("max-tokens-inp").value)||4096
  }));
}

function handleStreamEvent(ev, aId, userMsgId, onText) {
  if (!ev) return;
  if (ev.type === "thinking" || ev.type === "assistant_thinking") {
    const el = appendThinking(ev.thinking || ev.content || "");
    scrollChat();
    return;
  }
  if (ev.type === "text" || ev.type === "assistant") {
    onText();
    return;
  }
  if (ev.type === "usage" || ev.usage) {
    const u = ev.usage || ev;
    S.lastTokIn = u.input_tokens || 0;
    S.lastTokOut = u.output_tokens || 0;
    S.tokIn += S.lastTokIn;
    S.tokOut += S.lastTokOut;
    S.cost += (S.lastTokIn * 0.000003 + S.lastTokOut * 0.000015);
    updateMetrics();
    return;
  }
  if (ev.type === "session" && ev.session_id) {
    const conv = S.convs[S.active];
    if (conv && !conv.session_id) conv.session_id = ev.session_id;
    return;
  }
  if (ev.content || ev.text) { onText(); }
}

function drainQueue() {
  if (S.queue.length === 0) return;
  const next = S.queue.shift();
  updateQueueBar();
  const isRedirect = REDIRECT_RE.test(next.text.trim());
  if (isRedirect) sysMsg("Queued message may redirect — sending in 2.5s…");
  setTimeout(() => { doSend(next.text); }, isRedirect ? 2500 : 800);
}

function setThinking(v) {
  S.thinking = v;
  $("thinking-banner").classList.toggle("visible", v);
  const btn = $("send-btn");
  btn.disabled = v;
  btn.classList.toggle("queuing", false);
  if (!v) $("msg-input").focus();
  if (S.active && S.convs[S.active]) S.convs[S.active].status = v ? "busy" : "idle";
  renderConvList();
}

function updateQueueBar() {
  const bar = $("queue-bar");
  bar.classList.toggle("visible", S.queue.length > 0);
  $("queue-count").textContent = S.queue.length;
}

// ── Message Rendering ─────────────────────────────────────────────
function appendMsg(msg, scroll) {
  const div = document.createElement("div");
  div.className = "msg " + msg.role;
  div.id = msg.id || ("m"+Date.now());
  const sc = STATUS_CFG[msg.status||"done"]||STATUS_CFG.done;
  div.innerHTML = `
    <div class="msg-bubble${msg.thinking?' thinking':''}">${renderMarkdown(msg.content||"")}</div>
    <div class="msg-meta">
      <span class="msg-time">${fmtTime(msg.ts||Date.now())}</span>
      <span class="status-badge ${sc.cls}" id="status-${div.id}">${sc.icon} ${sc.label}</span>
    </div>
  `;
  $("msgs").appendChild(div);
  if (scroll) scrollChat();
  return div;
}
function appendThinking(text) {
  const div = document.createElement("div");
  div.className = "msg assistant";
  div.innerHTML = `<div class="msg-bubble thinking">${escHtml(text)}</div>`;
  $("msgs").appendChild(div);
  return div;
}
function setMsgStatus(msgId, status) {
  const badge = $("status-"+msgId);
  if (!badge) return;
  const sc = STATUS_CFG[status]||STATUS_CFG.done;
  badge.className = "status-badge "+sc.cls;
  badge.innerHTML = sc.icon+" "+sc.label;
}
function scrollChat() { const m=$("msgs"); m.scrollTop=m.scrollHeight; }

function renderMarkdown(text) {
  if (!text) return "";
  let s = escHtml(text);
  s = s.replace(/```[\w]*\n([\s\S]*?)```/g, (_, c) => `<pre><code>${c}</code></pre>`);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  s = s.replace(/\n/g, "<br>");
  return s;
}

// ── Metrics ───────────────────────────────────────────────────────
function updateMetrics() {
  $("m-tok-in").textContent  = fmtNum(S.tokIn);
  $("m-tok-out").textContent = fmtNum(S.tokOut);
  $("m-reqs").textContent    = S.chatCount;
  $("mt-in").textContent     = fmtNum(S.tokIn);
  $("mt-out").textContent    = fmtNum(S.tokOut);
  $("mt-last-in").textContent  = fmtNum(S.lastTokIn);
  $("mt-last-out").textContent = fmtNum(S.lastTokOut);
  const costStr = "$"+S.cost.toFixed(4);
  $("mt-cost").textContent = costStr;
  $("cost-display").textContent = costStr;
  const pct = Math.min(100, (S.tokOut / Math.max(S.tokIn+S.tokOut,1))*100);
  $("mt-bar").style.width = pct+"%";
}
function updateUptime() {
  const secs = Math.floor((Date.now() - S.srvStart) / 1000);
  const mins = Math.floor(secs/60);
  const hrs  = Math.floor(mins/60);
  const str  = hrs > 0 ? hrs+"h "+( mins%60)+"m" : mins+"m";
  $("m-uptime").textContent  = str;
  $("srv-uptime").textContent = str;
}

// ── Poll State ────────────────────────────────────────────────────
let _pingTs = Date.now();
function pollState() {
  const t0 = Date.now();
  fetch("/api/state").then(r=>r.json()).then(data => {
    const latency = Date.now()-t0;
    $("ci-latency").textContent = latency+"ms";
    $("ci-ping").textContent = new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"});
    $("srv-dot").className = "status-dot";
    $("srv-lbl").textContent = "Online";
    $("srv-status-val").textContent = "Online";
    $("srv-status-val").style.color = "var(--green)";
    $("srv-reqs").textContent = data.chat_count||0;
    if (data.total_tokens_in  !== undefined) { S.tokIn  = data.total_tokens_in;  }
    if (data.total_tokens_out !== undefined) { S.tokOut = data.total_tokens_out; }
    if (data.total_cost       !== undefined) { S.cost   = data.total_cost; }
    if (data.last_session_tokens) {
      S.lastTokIn  = data.last_session_tokens.in  || 0;
      S.lastTokOut = data.last_session_tokens.out || 0;
    }
    // Sync model if it changed externally (e.g. from persona hook)
    if (data.model && data.model !== S.model) {
      S.model = data.model;
      updateModelUI();
    }
    // Sync any new conversations
    if (data.conversations) {
      Object.assign(S.convs, data.conversations);
      renderConvList();
    }
    if (data.chat_count !== undefined) S.chatCount = data.chat_count;
    updateMetrics();
    updateUptime();
  }).catch(() => {
    $("srv-dot").className = "status-dot offline";
    $("srv-lbl").textContent = "Offline";
    $("srv-status-val").textContent = "Offline";
    $("srv-status-val").style.color = "var(--red)";
    log("Server unreachable", "warn");
  });
}

// ── System Prompt ─────────────────────────────────────────────────
function applySystemPrompt() {
  const val = $("sys-prompt-ta").value.trim();
  fetch("/api/config", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({system_prompt: val})
  }).then(r=>r.json()).then(() => {
    $("sys-prompt-status").textContent = val ? "Applied ✓" : "Cleared";
    $("sys-prompt-status").style.color = "var(--green)";
    log("System prompt updated", "ok");
    setTimeout(()=>{ $("sys-prompt-status").textContent = ""; $("sys-prompt-status").style.color=""; }, 3000);
  }).catch(e => { log("Failed to apply system prompt: "+e,"error"); });
}
function clearSystemPrompt() {
  $("sys-prompt-ta").value = "";
  applySystemPrompt();
}

// ── Model Apply ───────────────────────────────────────────────────
function applyModel() {
  const model = S.model;
  const maxTok = parseInt($("max-tokens-inp").value)||4096;
  fetch("/api/config", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({model, max_tokens: maxTok})
  }).then(r=>r.json()).then(() => {
    $("hdr-model-badge").textContent = modelLabel(model);
    log("Model set to "+model+", max_tokens="+maxTok, "ok");
  });
}

// ── Directory ─────────────────────────────────────────────────────
function browseDir() {
  fetch("/api/browse").then(r=>r.json()).then(d => {
    if (d.path) { $("dir-path").textContent = d.path; log("Dir: "+d.path,"ok"); }
  });
}

// ── Server Control ────────────────────────────────────────────────
function restartServer() {
  if (!confirm("Restart the server?")) return;
  log("Restarting server…","warn");
  fetch("/api/restart", {method:"POST"}).catch(()=>{});
  setTimeout(()=>pollState(), 3500);
}
function stopServer() {
  if (!confirm("Stop the server?")) return;
  fetch("/api/shutdown", {method:"POST"}).catch(()=>{});
  log("Shutdown requested","warn");
}
</script>
<!-- Model list injected by server -->
<script id="models-script"></script>
</body>
</html>"""


def strip_ansi(s):
    return ANSI.sub("", s)


# ─── HTTP Handler ──────────────────────────────────────────────────────────────

CREATE_NO_WINDOW = 0x08000000

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass  # suppress default logging

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        state["request_count"] += 1
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            # Inject model list into the page
            models_js = "window._MODELS_ = " + json.dumps(MODELS) + ";"
            page = HTML.replace('<script id="models-script"></script>',
                                f'<script id="models-script">{models_js}</script>')
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/state":
            convs_copy = {k: {**v, "messages": v.get("messages", [])}
                          for k, v in state["conversations"].items()}
            self._json({
                "dir":           state["dir"],
                "active_conv":   state["active_conv"],
                "conversations": convs_copy,
                "model":         state["model"],
                "max_tokens":    state["max_tokens"],
                "system_prompt": state["system_prompt"],
                "request_count": state["request_count"],
                "chat_count":    state["chat_count"],
                "server_start":  state["server_start"],
                "total_tokens_in":  state["total_tokens_in"],
                "total_tokens_out": state["total_tokens_out"],
                "total_cost":       state["total_cost"],
                "last_session_tokens": state["last_session_tokens"],
            })
            return

        if path == "/api/tokens":
            self._json({
                "total_tokens_in":  state["total_tokens_in"],
                "total_tokens_out": state["total_tokens_out"],
                "total_cost":       state["total_cost"],
                "last": state["last_session_tokens"],
            })
            return

        if path == "/api/browse":
            p = browse_dir_sync()
            if p: state["dir"] = p
            self._json({"path": state["dir"]})
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        state["request_count"] += 1
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/chat":
            self._handle_chat()
            return

        if path == "/api/conversations":
            conv = make_conversation()
            state["conversations"][conv["id"]] = conv
            state["active_conv"] = conv["id"]
            save_conversations(state["conversations"])
            self._json(conv)
            return

        if path == "/api/conversations/active":
            body = self._read_body()
            cid = body.get("id")
            if cid and cid in state["conversations"]:
                state["active_conv"] = cid
            self._json({"ok": True})
            return

        if path == "/api/config":
            body = self._read_body()
            if "model" in body:
                state["model"] = body["model"]
            if "max_tokens" in body:
                state["max_tokens"] = int(body["max_tokens"])
            if "system_prompt" in body:
                state["system_prompt"] = body["system_prompt"]
            save_config({
                "model": state["model"],
                "max_tokens": state["max_tokens"],
                "system_prompt": state["system_prompt"],
            })
            self._json({"ok": True})
            return

        if path == "/api/restart":
            self._json({"ok": True})
            threading.Thread(target=self._restart_server, daemon=True).start()
            return

        if path == "/api/shutdown":
            self._json({"ok": True})
            threading.Thread(target=lambda: (time.sleep(.3), os._exit(0)), daemon=True).start()
            return

        self.send_response(404)
        self.end_headers()

    def do_PATCH(self):
        state["request_count"] += 1
        parsed = urlparse(self.path)
        parts = parsed.path.split("/")
        # /api/conversations/<id>
        if len(parts) == 4 and parts[1] == "api" and parts[2] == "conversations":
            cid = parts[3]
            body = self._read_body()
            if cid in state["conversations"]:
                conv = state["conversations"][cid]
                if "name" in body:
                    conv["name"] = body["name"]
                conv["updated_at"] = int(time.time())
                save_conversations(state["conversations"])
                self._json(conv)
            else:
                self._json({"error": "not found"}, 404)
            return
        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        state["request_count"] += 1
        parsed = urlparse(self.path)
        parts = parsed.path.split("/")
        if len(parts) == 4 and parts[1] == "api" and parts[2] == "conversations":
            cid = parts[3]
            if cid in state["conversations"]:
                del state["conversations"][cid]
                if state["active_conv"] == cid:
                    state["active_conv"] = None
                save_conversations(state["conversations"])
            self._json({"ok": True})
            return
        self.send_response(404)
        self.end_headers()

    def _handle_chat(self):
        try:
            body = self._read_body()
        except Exception:
            self._json({"error": "bad request"}, 400)
            return

        prompt    = body.get("message", "").strip()
        conv_id   = body.get("conv_id") or state.get("active_conv")
        model_req = body.get("model") or state["model"]
        max_tok   = int(body.get("max_tokens") or state["max_tokens"])

        if not prompt:
            self._json({"error": "empty message"}, 400)
            return

        state["chat_count"] += 1

        conv = state["conversations"].get(conv_id) if conv_id else None

        cmd = ["claude", "-p", "--output-format", "stream-json",
               "--model", model_req,
               "--max-tokens", str(max_tok)]

        if state["system_prompt"]:
            cmd += ["--system", state["system_prompt"]]

        if conv and conv.get("session_id"):
            cmd += ["--resume", conv["session_id"]]

        cmd.append(prompt)

        flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self._cors()
        self.end_headers()

        def write_event(obj):
            data = "data: " + json.dumps(obj) + "\n\n"
            try:
                enc = data.encode("utf-8")
                self.wfile.write(("%X\r\n" % len(enc)).encode())
                self.wfile.write(enc)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except Exception:
                pass

        tok_in = tok_out = 0
        session_id = None

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                cwd=state["dir"],
            )
            state["proc"] = proc

            for raw in proc.stdout:
                line = strip_ansi(raw.decode("utf-8", errors="replace").strip())
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    write_event({"type": "text", "text": line})
                    continue

                ev_type = ev.get("type", "")

                if ev_type == "system" and ev.get("subtype") == "init":
                    sid = ev.get("session_id")
                    if sid:
                        session_id = sid
                        if conv:
                            conv["session_id"] = sid
                            save_conversations(state["conversations"])
                        write_event({"type": "session", "session_id": sid})

                elif ev_type == "assistant":
                    msg = ev.get("message", {})
                    for block in msg.get("content", []):
                        bt = block.get("type", "")
                        if bt == "thinking":
                            write_event({"type": "thinking", "thinking": block.get("thinking", "")})
                        elif bt == "text":
                            write_event({"type": "text", "text": block.get("text", "")})
                    usage = msg.get("usage", {})
                    if usage:
                        tok_in  += usage.get("input_tokens", 0)
                        tok_out += usage.get("output_tokens", 0)

                elif ev_type == "result":
                    usage = ev.get("usage", {})
                    if usage:
                        tok_in  += usage.get("input_tokens", 0)
                        tok_out += usage.get("output_tokens", 0)
                    write_event({"type": "usage", "usage": {
                        "input_tokens": tok_in, "output_tokens": tok_out
                    }})
                    cost = tok_in * 0.000003 + tok_out * 0.000015
                    state["total_tokens_in"]  += tok_in
                    state["total_tokens_out"] += tok_out
                    state["total_cost"]       += cost
                    state["last_session_tokens"] = {"in": tok_in, "out": tok_out}
                    if conv:
                        conv["updated_at"] = int(time.time())
                        conv["status"] = "idle"
                        mc = conv.get("message_count", 0)
                        conv["message_count"] = mc + 1
                        save_conversations(state["conversations"])

            proc.wait()

        except Exception as exc:
            write_event({"type": "error", "error": str(exc)})

        finally:
            state["proc"] = None
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass

    def _restart_server(self):
        time.sleep(0.5)
        script = Path(__file__)
        flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.Popen(
            [sys.executable, str(script)],
            creationflags=flags,
        )
        time.sleep(0.3)
        os._exit(0)


def main():
    DATA_DIR.mkdir(exist_ok=True)
    port = 8765
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"ClaudioUI running at http://127.0.0.1:{port}")
    try:
        webbrowser.open(f"http://127.0.0.1:{port}")
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
