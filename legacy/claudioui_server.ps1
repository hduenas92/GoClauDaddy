# ClaudioUi — pure PowerShell HTTP server (no Python/Node required)
param([int]$Port = 8765, [switch]$NoOpen)

$Script:WorkDir    = $env:USERPROFILE
$Script:LogFile    = Join-Path $PSScriptRoot "claudioui.log"
$Script:CurrentProc = $null
$Script:LogEntries  = [System.Collections.Generic.List[object]]::new()
$Script:LogSeq      = 0
$Script:Sessions    = [System.Collections.Generic.Dictionary[string,string]]::new()

function Write-Log([string]$msg) {
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $msg"
    Write-Host $line
    try { Add-Content -Path $Script:LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
    $Script:LogEntries.Add([PSCustomObject]@{ seq = $Script:LogSeq; text = $line })
    $Script:LogSeq++
    while ($Script:LogEntries.Count -gt 500) { $Script:LogEntries.RemoveAt(0) }
}

function Strip-Ansi([string]$s) {
    return [regex]::Replace($s, "\x1B(?:[@-Z\-_]|\[[0-?]*[ -/]*[@-~])", "")
}

# ─── HTML (single-file UI) ──────────────────────────────────────
$HTML = @'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>ClaudioUi</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    /* Synthwave palette */
    --bg:#0d0b1e;--bg2:#070613;
    --card:#13112a;--card2:#1c1938;
    --glass:rgba(255,255,255,.04);--gb:rgba(255,255,255,.1);--gh:rgba(255,255,255,.07);
    --blue:#a78bfa;--bglow:rgba(167,139,250,.45);--bdim:#7c3aed;
    --purple:#f472b6;--pglow:rgba(244,114,182,.4);
    --teal:#22d3ee;--green:#34d399;--gglow:rgba(52,211,153,.4);
    --red:#f87171;--rglow:rgba(248,113,113,.4);--yellow:#fbbf24;
    --tx:#f1f5f9;--tx2:rgba(241,245,249,.62);--tx3:rgba(241,245,249,.28);
    --r:9px;--rs:6px;--rx:14px;
    --mono:"Cascadia Code","Fira Code",Consolas,monospace;
    --ui:"Poppins","Segoe UI Variable",system-ui,sans-serif;
    --tr:all .18s cubic-bezier(.4,0,.2,1);
    --lsb-w:230px;--rsb-w:260px;
    --shadow:0 4px 24px rgba(0,0,0,.55);
    --shadow-card:0 2px 20px rgba(0,0,0,.45);
    --btn-grad:linear-gradient(135deg,#7c3aed 0%,#ec4899 100%);
    --btn-grad-hover:linear-gradient(135deg,#6d28d9 0%,#db2777 100%);
    --btn-grad-danger:linear-gradient(135deg,#ef4444 0%,#f97316 100%);
    --accent-grad:linear-gradient(135deg,var(--blue),var(--purple));
    --aurora1:rgba(124,58,237,.12);--aurora2:rgba(236,72,153,.08);--aurora3:rgba(34,211,238,.07);
  }
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--tx);font-family:var(--ui);-webkit-font-smoothing:antialiased}
  ::-webkit-scrollbar{width:4px;height:4px}
  ::-webkit-scrollbar-track{background:transparent}
  ::-webkit-scrollbar-thumb{background:linear-gradient(180deg,#7c3aed,#ec4899);border-radius:4px}
  ::-webkit-scrollbar-thumb:hover{background:linear-gradient(180deg,#8b5cf6,#f472b6)}

  /* ── App shell ── */
  #app{
    display:flex;flex-direction:column;height:100vh;background:var(--bg);
    background-image:
      radial-gradient(ellipse at 18% 55%,var(--aurora1) 0%,transparent 52%),
      radial-gradient(ellipse at 82% 18%,var(--aurora2) 0%,transparent 48%),
      radial-gradient(ellipse at 50% 98%,var(--aurora3) 0%,transparent 42%);
  }

  /* ── Header ── */
  #hdr{
    background:var(--card);
    border-bottom:1px solid rgba(167,139,250,.2);
    box-shadow:0 1px 0 rgba(167,139,250,.12), var(--shadow-card);
    display:flex;align-items:center;padding:0 16px;gap:8px;
    height:56px;flex-shrink:0;z-index:100;
  }
  .logo{display:flex;align-items:center;gap:10px}
  .logo-icon{
    width:34px;height:34px;
    background:var(--btn-grad);
    border:none;color:#fff;border-radius:10px;
    display:flex;align-items:center;justify-content:center;
    font-size:15px;flex-shrink:0;
    box-shadow:0 4px 18px rgba(124,58,237,.5), 0 0 30px rgba(236,72,153,.2);
    animation:logo-glow 3s ease-in-out infinite;
  }
  @keyframes logo-glow{
    0%,100%{box-shadow:0 4px 18px rgba(124,58,237,.5),0 0 30px rgba(236,72,153,.2)}
    50%{box-shadow:0 4px 24px rgba(124,58,237,.7),0 0 40px rgba(236,72,153,.35)}
  }
  .logo h1{
    font-size:.95rem;font-weight:700;letter-spacing:.3px;
    background:var(--accent-grad);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  }
  #status-pill{
    display:flex;align-items:center;gap:6px;
    background:var(--glass);border:1px solid var(--gb);
    border-radius:20px;padding:3px 11px;font-size:.65rem;color:var(--tx2);
    letter-spacing:.3px;transition:var(--tr);
  }
  #status-pill.busy{background:rgba(29,140,248,.12);border-color:rgba(29,140,248,.45);color:var(--blue)}
  #dot{width:7px;height:7px;border-radius:50%;background:var(--green);flex-shrink:0;transition:var(--tr)}
  #dot.busy{background:var(--blue);animation:pdot .9s ease-in-out infinite}
  @keyframes pdot{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.6);opacity:.7}}
  .spc{flex:1}
  #dir-lbl{
    color:var(--tx2);font-size:.67rem;max-width:280px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
    background:var(--glass);border:1px solid var(--gb);
    border-radius:6px;padding:3px 10px;cursor:default;font-family:var(--mono);
  }
  .hbtn{
    background:var(--glass);color:var(--tx2);border:1px solid var(--gb);
    padding:5px 14px;border-radius:6px;cursor:pointer;
    font-size:.65rem;font-family:var(--ui);transition:var(--tr);white-space:nowrap;font-weight:500;
  }
  .hbtn:hover{
    background:rgba(167,139,250,.1);color:var(--tx);border-color:rgba(167,139,250,.35);
    transform:translateY(-1px);box-shadow:0 3px 12px rgba(124,58,237,.2);
  }
  .hbtn:active{transform:translateY(0)}
  .hbtn.danger{color:var(--red);border-color:rgba(248,113,113,.3)}
  .hbtn.danger:hover{background:rgba(248,113,113,.1);color:#fff;border-color:var(--red)}
  #lsb-tog,#rsb-tog{
    background:var(--glass);color:var(--tx2);border:1px solid var(--gb);
    width:28px;height:28px;border-radius:6px;cursor:pointer;
    font-size:.85rem;transition:var(--tr);display:flex;align-items:center;justify-content:center;
  }
  #lsb-tog:hover,#rsb-tog:hover{background:var(--gh);color:var(--tx)}

  /* ── Main row ── */
  #main{flex:1;display:flex;overflow:hidden}
  .col-resize{width:4px;flex-shrink:0;cursor:ew-resize;background:transparent;transition:background .15s}
  .col-resize:hover,.col-resize.dragging{background:linear-gradient(180deg,var(--blue),var(--purple));opacity:.7}
  .col-resize.hidden{display:none}
  body.col-dragging{user-select:none;-webkit-user-select:none;cursor:ew-resize}

  /* ── Left sidebar ── */
  #lsb{
    width:var(--lsb-w);min-width:var(--lsb-w);
    background:var(--card);border-right:1px solid var(--gb);
    display:flex;flex-direction:column;overflow-y:auto;flex-shrink:0;
    transition:width .26s cubic-bezier(.4,0,.2,1),min-width .26s,opacity .22s;
  }
  #lsb.collapsed{width:0;min-width:0;overflow:hidden;opacity:0;pointer-events:none}

  /* Accordion */
  .acc-hdr{
    display:flex;align-items:center;justify-content:space-between;
    padding:10px 14px;cursor:pointer;user-select:none;
    font-size:.67rem;font-weight:600;color:var(--tx2);letter-spacing:.3px;
    border-bottom:1px solid var(--gb);border-left:2px solid transparent;
    transition:background .14s,color .14s,border-color .14s;flex-shrink:0;
  }
  .acc-hdr:not(.closed):not(:has(+.acc-body.closed)){border-left-color:var(--blue)}
  .acc-hdr:hover{background:rgba(167,139,250,.06);color:var(--tx);border-left-color:var(--blue)}
  .acc-hdr .chv{font-size:.52rem;transition:transform .2s;display:inline-block;opacity:.45}
  .acc-hdr.closed .chv{transform:rotate(-90deg)}
  .acc-body{overflow:hidden;max-height:900px;transition:max-height .3s ease}
  .acc-body.closed{max-height:0}

  /* Conversations */
  #conv-new-btn{
    display:block;width:calc(100% - 16px);margin:8px 8px 4px;
    background:var(--btn-grad);color:#fff;border:none;border-radius:8px;
    padding:9px 12px;font-size:.7rem;cursor:pointer;font-family:var(--ui);
    font-weight:600;transition:var(--tr);
    box-shadow:0 4px 18px rgba(124,58,237,.4),0 0 25px rgba(236,72,153,.12);
  }
  #conv-new-btn:hover{
    background:var(--btn-grad-hover);
    box-shadow:0 6px 24px rgba(124,58,237,.6),0 0 35px rgba(236,72,153,.22);
    transform:translateY(-2px);
  }
  #conv-new-btn:active{transform:translateY(0)}
  .conv-item{
    display:flex;align-items:center;gap:5px;padding:8px 12px;cursor:pointer;
    border-bottom:1px solid var(--gb);border-left:3px solid transparent;transition:background .14s;
  }
  .conv-item:hover{background:var(--gh)}
  .conv-item.active{
    background:linear-gradient(90deg,rgba(124,58,237,.12),transparent);
    border-left-color:transparent;
    box-shadow:inset 3px 0 0 var(--blue);
  }
  .conv-info{flex:1;min-width:0}
  .conv-title{font-size:.77rem;color:var(--tx);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:400}
  .conv-item.active .conv-title{color:var(--blue);font-weight:600}
  .conv-time{font-size:.6rem;color:var(--tx3);margin-top:2px}
  .conv-del{
    background:none;border:none;color:var(--tx3);cursor:pointer;
    padding:2px 5px;border-radius:4px;flex-shrink:0;opacity:0;font-size:.7rem;transition:opacity .14s,color .14s;
  }
  .conv-item:hover .conv-del{opacity:1}
  .conv-del:hover{color:var(--red)}
  #conv-empty{padding:12px 14px;font-size:.68rem;color:var(--tx3)}

  /* Functions grid */
  .fn-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px;padding:10px}
  .fn-btn{
    background:var(--glass);color:var(--tx2);border:1px solid var(--gb);
    border-radius:7px;padding:8px 5px;font-size:.62rem;cursor:pointer;
    font-family:var(--ui);transition:var(--tr);text-align:center;font-weight:500;
  }
  .fn-btn:hover{
    background:rgba(167,139,250,.1);color:var(--tx);border-color:rgba(167,139,250,.35);
    transform:translateY(-1px);box-shadow:0 4px 14px rgba(124,58,237,.2);
  }
  .fn-btn:active{transform:translateY(0)}
  .fn-btn.danger{color:var(--red);border-color:rgba(248,113,113,.28)}
  .fn-btn.danger:hover{background:rgba(248,113,113,.1);color:#fff;border-color:var(--red);box-shadow:0 4px 14px rgba(248,113,113,.2)}
  .fn-btn.wide{grid-column:1/-1}

  /* Options */
  .opt-row{padding:9px 14px;border-bottom:1px solid var(--gb)}
  .opt-lbl{font-size:.6rem;color:var(--tx3);margin-bottom:5px;text-transform:uppercase;font-weight:600;letter-spacing:.5px}
  .opt-dir{font-size:.7rem;color:var(--tx);word-break:break-all;line-height:1.5;font-family:var(--mono)}
  .opt-sel{
    width:100%;background:var(--card2);color:var(--tx);
    border:1px solid var(--gb);border-radius:8px;
    padding:8px 12px;font-size:.72rem;font-family:var(--ui);cursor:pointer;outline:none;
    font-weight:400;transition:border-color .15s,box-shadow .15s,background .15s;
    box-shadow:0 1px 4px rgba(0,0,0,.25);
  }
  select.opt-sel{
    appearance:none;-webkit-appearance:none;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='rgba(167,139,250,.7)' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
    background-repeat:no-repeat;background-position:right 10px center;
    padding-right:32px;
  }
  select.opt-sel:hover{background-color:rgba(167,139,250,.06);border-color:rgba(167,139,250,.35)}
  select.opt-sel:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(124,58,237,.25);background-color:var(--card2)}
  .opt-sel:hover{border-color:rgba(255,255,255,.22);background:var(--gh)}
  .opt-sel:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(29,140,248,.2)}
  .opt-sel option{background:var(--card);color:var(--tx)}
  input[type="number"].opt-sel{appearance:textfield;-moz-appearance:textfield}
  input[type="number"].opt-sel::-webkit-inner-spin-button,
  input[type="number"].opt-sel::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}
  input[type="range"]{
    -webkit-appearance:none;appearance:none;width:100%;height:4px;
    background:var(--gb);border-radius:4px;outline:none;cursor:pointer;
    transition:background .15s;
  }
  input[type="range"]:hover{background:rgba(255,255,255,.18)}
  input[type="range"]::-webkit-slider-thumb{
    -webkit-appearance:none;appearance:none;
    width:16px;height:16px;border-radius:50%;
    background:var(--btn-grad);border:2px solid rgba(255,255,255,.2);
    box-shadow:0 2px 10px rgba(124,58,237,.55),0 0 16px rgba(236,72,153,.2);cursor:pointer;
    transition:transform .15s,box-shadow .15s;
  }
  input[type="range"]::-webkit-slider-thumb:hover{transform:scale(1.22);box-shadow:0 2px 16px rgba(124,58,237,.75),0 0 24px rgba(236,72,153,.35)}
  input[type="range"]::-moz-range-thumb{
    width:16px;height:16px;border-radius:50%;border:2px solid rgba(255,255,255,.15);
    background:var(--blue);box-shadow:0 2px 8px rgba(29,140,248,.5);cursor:pointer;
  }
  .opt-note{margin-top:5px;font-size:.62rem;line-height:1.55;color:var(--tx3)}
  .opt-note.warn{color:var(--yellow)}
  .opt-note.danger{color:var(--red)}
  .opt-toggle{display:flex;align-items:center;justify-content:space-between;padding:9px 14px;border-bottom:1px solid var(--gb)}
  .opt-toggle span{font-size:.7rem;color:var(--tx2)}
  .tsw{position:relative;width:36px;height:20px;flex-shrink:0}
  .tsw input{opacity:0;width:0;height:0;position:absolute}
  .tsw-track{
    position:absolute;inset:0;background:rgba(255,255,255,.12);
    border:1px solid var(--gb);border-radius:12px;cursor:pointer;transition:var(--tr);
  }
  .tsw input:checked+.tsw-track{background:var(--btn-grad);border-color:var(--bdim);box-shadow:0 0 12px rgba(124,58,237,.4)}
  .tsw-track::after{
    content:"";position:absolute;width:14px;height:14px;left:2px;top:2px;
    background:#fff;border-radius:50%;transition:transform .2s;box-shadow:0 1px 4px rgba(0,0,0,.35);
  }
  .tsw input:checked+.tsw-track::after{transform:translateX(16px)}

  /* ── Chat area ── */
  #chat-wrap{flex:1;overflow:hidden;display:flex;flex-direction:column}
  #chat{
    flex:1;overflow-y:auto;padding:24px 28px;
    display:flex;flex-direction:column;gap:18px;scroll-behavior:smooth;
  }

  /* ── Messages ── */
  .msg{display:flex;flex-direction:column;gap:5px;max-width:82%;animation:msgin .22s ease-out}
  @keyframes msgin{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:translateY(0)}}
  .msg.user{align-self:flex-end;align-items:flex-end}
  .msg.ai{align-self:flex-start;align-items:flex-start}
  .msg.sys{align-self:center;align-items:center;max-width:100%}
  .msg-meta{display:flex;align-items:center;gap:6px;padding:0 2px}
  .msg-av{
    width:26px;height:26px;border-radius:50%;
    display:flex;align-items:center;justify-content:center;
    font-size:10px;font-weight:700;flex-shrink:0;
  }
  .msg.user .msg-av{background:linear-gradient(135deg,var(--red),var(--purple));color:#fff}
  .msg.ai .msg-av{background:linear-gradient(135deg,var(--blue),var(--teal));color:#fff}
  .lbl{font-size:.62rem;font-weight:600;color:var(--tx3)}
  .msg.sys .lbl{font-style:normal}
  .bubble{
    padding:12px 16px;border-radius:14px;
    font-size:.91rem;line-height:1.7;
    white-space:pre-wrap;word-break:break-word;
  }
  .msg.user .bubble{
    background:linear-gradient(135deg,rgba(124,58,237,.18),rgba(236,72,153,.10));
    border:1px solid rgba(167,139,250,.2);
    box-shadow:var(--shadow-card),0 0 20px rgba(124,58,237,.08);
    border-bottom-right-radius:4px;
  }
  .msg.ai .bubble{
    background:var(--card);border:1px solid rgba(255,255,255,.07);
    box-shadow:var(--shadow-card);font-family:var(--mono);font-size:.85rem;
    border-bottom-left-radius:4px;
  }
  .msg.sys .bubble{
    background:transparent;color:var(--tx3);font-size:.66rem;padding:2px 0;border:none;
    text-transform:uppercase;letter-spacing:1.6px;font-family:var(--mono);
  }
  .bubble code{
    background:rgba(0,242,195,.08);padding:2px 6px;border-radius:5px;
    font-family:var(--mono);font-size:.84em;color:var(--teal);
  }
  .bubble pre{
    background:rgba(0,0,0,.5);border:1px solid var(--gb);border-radius:8px;
    padding:12px 15px;overflow-x:auto;margin:7px 0;
  }
  .bubble pre code{background:none;padding:0;border:none;color:var(--tx)}
  .bubble img{
    max-width:240px;max-height:180px;border-radius:8px;display:block;margin-top:6px;
    border:1px solid var(--gb);
  }
  .spinner{
    display:inline-block;width:16px;height:16px;
    border:2px solid rgba(29,140,248,.2);border-top-color:var(--blue);
    border-radius:50%;animation:spin .7s linear infinite;margin:4px 0;
  }
  @keyframes spin{to{transform:rotate(360deg)}}

  /* ── Right sidebar ── */
  #sidebar{
    width:var(--rsb-w);min-width:var(--rsb-w);
    background:var(--card);border-left:1px solid var(--gb);
    display:flex;flex-direction:column;overflow-y:auto;flex-shrink:0;
    box-shadow:var(--shadow-card);
    transition:width .28s cubic-bezier(.4,0,.2,1),min-width .28s,opacity .25s;
  }
  #sidebar.collapsed{width:0;min-width:0;overflow:hidden;opacity:0;pointer-events:none}

  /* Draggable panels */
  .sb-panel{border-bottom:1px solid var(--gb);user-select:none;transition:background .14s}
  .sb-panel:hover{background:rgba(255,255,255,.02)}
  .sb-panel.dragging{opacity:.4;background:var(--glass)}
  .sb-panel.drag-over{border-top:2px solid var(--blue)}
  .sb-ph{
    display:flex;align-items:center;justify-content:space-between;
    padding:11px 13px 5px;cursor:grab;
  }
  .sb-ph:active{cursor:grabbing}
  .sb-title{font-size:.6rem;font-weight:600;color:var(--tx3);text-transform:uppercase;letter-spacing:.4px}
  .sb-dh{color:var(--tx3);font-size:.7rem;opacity:.3;transition:opacity .14s}
  .sb-panel:hover .sb-dh{opacity:.6}
  .sb-pb{padding:3px 13px 13px}
  .sb-val{
    font-size:1.12rem;font-weight:500;color:var(--tx);
    font-family:var(--mono);font-variant-numeric:tabular-nums;
  }
  .sb-sub{font-size:.6rem;color:var(--tx3);margin-top:3px}
  .sb-val.live{animation:val-pulse 1.4s ease-in-out infinite}
  @keyframes val-pulse{0%,100%{opacity:1}50%{opacity:.6}}
  .stat-row{display:flex;align-items:baseline;gap:6px}
  .stat-badge{
    font-size:.56rem;font-weight:600;padding:2px 6px;border-radius:5px;
    background:rgba(29,140,248,.12);color:var(--blue);border:1px solid rgba(29,140,248,.28);
  }

  /* Identity disc */
  #think-row{display:flex;align-items:center;gap:10px}
  #think-orb{
    width:30px;height:30px;border-radius:50%;
    background:conic-gradient(from 0deg,transparent 0deg,var(--blue) 70deg,var(--purple) 140deg,transparent 200deg,transparent 360deg);
    flex-shrink:0;transition:opacity .4s,transform .4s,box-shadow .4s,filter .4s;position:relative;
  }
  #think-orb::after{
    content:'';position:absolute;inset:4px;border-radius:50%;
    background:var(--bg);border:1px solid rgba(0,212,255,.5);
  }
  #think-orb::before{
    content:'';position:absolute;inset:10px;border-radius:50%;
    background:var(--blue);z-index:2;box-shadow:0 0 10px var(--bglow);
  }
  #think-orb.idle{opacity:.25;transform:scale(.8);box-shadow:none;filter:saturate(.2)}
  #think-orb.active{
    opacity:1;transform:scale(1);
    box-shadow:0 0 16px var(--bglow),0 0 32px rgba(0,212,255,.3);
    animation:orb-spin 1.6s linear infinite;
  }
  @keyframes orb-spin{to{transform:rotate(360deg)}}
  #think-label{
    font-size:.7rem;font-weight:600;color:var(--blue);
    text-transform:uppercase;letter-spacing:2px;
    text-shadow:0 0 10px var(--bglow);
  }
  .dot-wave{display:flex;gap:4px;margin-top:5px;height:8px;align-items:center;opacity:0;transition:opacity .3s}
  .dot-wave.active{opacity:1}
  .dot-wave span{
    width:5px;height:5px;border-radius:50%;
    background:var(--blue);
    animation:dw .9s ease-in-out infinite;
  }
  .dot-wave span:nth-child(2){animation-delay:.18s}
  .dot-wave span:nth-child(3){animation-delay:.36s}
  @keyframes dw{0%,80%,100%{transform:rotate(45deg) translateY(0);opacity:.35}40%{transform:rotate(45deg) translateY(-4px);opacity:1}}

  /* Progress */
  #prog-meta{display:flex;justify-content:space-between;font-size:.62rem;margin-bottom:6px}
  #prog-pct{color:var(--blue);font-weight:600}
  #prog-eta-lbl{color:var(--tx3)}
  #prog-track{
    background:rgba(255,255,255,.06);border:1px solid var(--gb);
    border-radius:4px;height:6px;position:relative;overflow:hidden;
  }
  #prog-fill{
    position:absolute;top:0;left:0;height:100%;
    background:var(--btn-grad);
    border-radius:4px;width:0%;transition:width .5s ease;
    box-shadow:0 0 8px rgba(124,58,237,.5);
  }
  #prog-fill.sweep{
    background:linear-gradient(90deg,transparent,var(--blue) 45%,var(--purple) 55%,transparent);
    width:40%!important;transition:none;animation:sweep 1.8s ease-in-out infinite;
  }
  @keyframes sweep{0%{left:-40%}100%{left:110%}}

  /* Mini console in right sidebar */
  #con-sec{padding:8px 10px 10px;display:flex;flex-direction:column;flex:1;min-height:0;border-top:1px solid var(--gb)}
  #con-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
  #con-title{font-size:.6rem;font-weight:600;color:var(--tx3);text-transform:uppercase;letter-spacing:.4px}
  #con-clr{
    background:none;border:1px solid transparent;color:var(--tx3);cursor:pointer;
    font-size:.6rem;padding:2px 7px;border-radius:5px;transition:var(--tr);
  }
  #con-clr:hover{color:var(--tx);border-color:var(--gb)}
  #con-out{
    flex:1;min-height:80px;max-height:200px;overflow-y:auto;
    background:var(--bg2);border:1px solid var(--gb);border-radius:7px;
    padding:7px 9px;font-family:var(--mono);font-size:.63rem;color:var(--green);line-height:1.6;word-break:break-all;
  }
  .con-line{white-space:pre-wrap}
  .con-line.err{color:var(--red)}

  /* ── Bottom system-prompt panel ── */
  #sys-panel{
    flex-shrink:0;display:flex;flex-direction:column;
    background:var(--card);border-top:1px solid var(--gb);
    height:150px;min-height:0;overflow:hidden;transition:height .15s ease;
  }
  #sys-panel.sp-hidden{height:0!important;border:none}
  #sp-resize{height:5px;cursor:ns-resize;flex-shrink:0;background:transparent;transition:background .15s}
  #sp-resize:hover,#sp-resize.dragging{background:var(--blue)}
  #sp-bar{
    display:flex;align-items:center;gap:7px;padding:4px 14px;
    border-bottom:1px solid var(--gb);flex-shrink:0;
  }
  #sp-bar-title{font-size:.62rem;font-weight:600;color:var(--tx3)}
  #sp-state{
    font-size:.6rem;color:var(--tx3);margin-right:auto;
    border:1px solid transparent;padding:1px 7px;border-radius:5px;transition:var(--tr);
  }
  #sp-state.on{color:var(--green);border-color:rgba(0,242,195,.3);background:rgba(0,242,195,.08)}
  #sp-state.mod{color:var(--yellow);border-color:rgba(255,214,0,.3);background:rgba(255,214,0,.07)}
  .sp-btn{
    background:none;border:1px solid transparent;color:var(--tx3);cursor:pointer;
    font-size:.62rem;padding:3px 9px;border-radius:6px;font-family:var(--ui);transition:var(--tr);
  }
  .sp-btn:hover{color:var(--tx);border-color:var(--gb);background:var(--glass)}
  .sp-btn.primary{color:var(--blue);border-color:var(--gb)}
  .sp-btn.primary:hover{background:rgba(29,140,248,.1);color:var(--blue);border-color:var(--blue)}
  #sys-prompt-ta{
    flex:1;min-height:0;width:100%;resize:none;outline:none;
    background:var(--bg2);color:var(--tx);border:none;
    padding:9px 14px;font-family:var(--mono);font-size:.77rem;line-height:1.65;
    transition:box-shadow .18s;
  }
  #sys-prompt-ta::placeholder{color:var(--tx3)}

  /* ── Bottom panel ── */
  #btm{
    background:var(--card);border-top:1px solid var(--gb);
    box-shadow:0 -2px 16px rgba(0,0,0,.3);
    padding:10px 16px 13px;flex-shrink:0;
  }
  #tb{display:flex;align-items:center;gap:6px;margin-bottom:8px}
  .tbtn{
    background:var(--glass);color:var(--tx2);border:1px solid var(--gb);
    padding:5px 12px;border-radius:7px;cursor:pointer;font-size:.65rem;font-family:var(--ui);
    transition:var(--tr);font-weight:500;
  }
  .tbtn:hover{background:var(--gh);color:var(--tx);border-color:rgba(255,255,255,.18)}
  .tbtn.danger{color:var(--red);border-color:rgba(253,93,147,.3)}
  .tbtn.danger:hover{background:rgba(253,93,147,.1);color:#fff;border-color:var(--red)}
  .tbtn:disabled{opacity:.28;cursor:not-allowed}
  .tbtn:disabled:hover{background:var(--glass);color:var(--tx2);border-color:var(--gb)}
  .tbtn.danger:disabled:hover{color:var(--red);border-color:rgba(253,93,147,.3)}
  #stxt{margin-left:auto;color:var(--tx3);font-size:.62rem}
  #img-strip{display:none;flex-wrap:wrap;gap:6px;padding:0 0 8px}
  #img-strip.has-imgs{display:flex}
  .img-th{position:relative}
  .img-th img{
    width:58px;height:58px;object-fit:cover;border-radius:7px;
    border:1px solid var(--gb);display:block;
  }
  .img-th .rm{
    position:absolute;top:-5px;right:-5px;
    background:var(--card2);color:var(--red);border:1px solid var(--gb);border-radius:50%;
    width:16px;height:16px;font-size:8px;cursor:pointer;line-height:15px;text-align:center;padding:0;font-weight:700;
  }
  .img-th .rm:hover{background:var(--red);color:#fff;border-color:var(--red)}
  #ir{display:flex;gap:9px;align-items:flex-end}
  #input-wrap{flex:1;position:relative}
  #ibox{
    width:100%;background:rgba(0,212,255,.03);color:var(--tx);
    border:1px solid var(--gb);border-radius:10px;
    padding:10px 14px;font-family:var(--mono);font-size:.86rem;
    resize:none;line-height:1.6;outline:none;
    min-height:44px;max-height:180px;overflow-y:auto;
    transition:border-color .18s,box-shadow .18s;
  }
  #ibox::placeholder{color:var(--tx3)}
  #ibox:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(124,58,237,.25),0 0 20px rgba(167,139,250,.1)}
  #paste-hint{
    color:var(--teal);font-size:.62rem;opacity:0;transition:opacity .4s;
    position:absolute;top:-22px;left:0;pointer-events:none;white-space:nowrap;
  }
  #paste-hint.show{opacity:1}
  #sbtn{
    background:var(--btn-grad);color:#fff;border:none;
    padding:0 24px;border-radius:10px;cursor:pointer;
    font-size:.72rem;font-weight:600;font-family:var(--ui);
    white-space:nowrap;min-height:44px;transition:var(--tr);
    box-shadow:0 4px 20px rgba(124,58,237,.45),0 0 30px rgba(236,72,153,.15);
  }
  #sbtn:hover{
    background:var(--btn-grad-hover);
    box-shadow:0 6px 28px rgba(124,58,237,.65),0 0 40px rgba(236,72,153,.28);
    transform:translateY(-2px);
  }
  #sbtn:active{transform:translateY(0);box-shadow:0 2px 12px rgba(124,58,237,.4)}
  #sbtn:disabled{opacity:.35;cursor:not-allowed;box-shadow:none;transform:none}
  #hint{color:var(--tx3);font-size:.6rem;margin-top:6px;text-align:right}
  /* ── Unified panel move button ── */
  .panel-mv{
    background:none;border:none;color:var(--tx3);cursor:pointer;
    font-size:.9rem;line-height:1;padding:2px 6px;flex-shrink:0;
    opacity:0;transition:opacity .14s;
  }
  .sb-panel:hover .panel-mv,.lsb-panel:hover .panel-mv{opacity:1}
  .panel-mv:hover{color:var(--blue)}
  /* sb-panel when hosted in left sidebar */
  #lsb .sb-panel{border-bottom:1px solid var(--gb)}
  #lsb .sb-panel .sb-pb{padding:4px 13px 10px}
  /* Server panel status dot */
  .srv-pdot{
    width:7px;height:7px;border-radius:50%;
    display:inline-block;background:var(--green);
    flex-shrink:0;margin-left:4px;transition:background .3s;
  }
  .srv-pdot.err{background:var(--red);animation:pdot 1s ease-in-out infinite}

  /* ── Left sidebar modular panels ── */
  .lsb-panel{border-bottom:1px solid var(--gb);transition:background .14s}
  .lsb-panel:hover{background:rgba(255,255,255,.02)}
  .lsb-panel.dragging{opacity:.4;background:var(--glass)}
  .lsb-panel.drag-over{border-top:2px solid var(--blue)}
  .lsb-ph-row{display:flex;align-items:center;padding-right:4px}
  .lsb-grip{padding:8px 2px 8px 8px;cursor:grab;color:var(--tx3);font-size:.72rem;opacity:.22;flex-shrink:0;transition:opacity .14s}
  .lsb-panel:hover .lsb-grip{opacity:.6}
  .lsb-panel .acc-hdr{flex:1;border-left:none;border-bottom:none;padding-left:2px}
  .panel-xfer{
    background:none;border:none;color:var(--tx3);cursor:pointer;
    font-size:.9rem;padding:3px 6px;flex-shrink:0;line-height:1;
    opacity:0;transition:opacity .14s;
  }
  .lsb-panel:hover .panel-xfer,.sb-panel:hover .panel-xfer{opacity:1}
  .panel-xfer:hover{color:var(--blue)}
  /* System prompt panel in right sidebar */
  .sb-panel.sp-panel .sb-pb{padding-bottom:4px}
  #sys-prompt-ta{
    display:block;width:100%;background:var(--bg2);color:var(--tx);
    border:none;border-top:1px solid var(--gb);
    padding:8px 12px;font-family:var(--mono);font-size:.74rem;line-height:1.65;
    resize:none;outline:none;min-height:72px;transition:border-color .18s;
  }
  #sys-prompt-ta::placeholder{color:var(--tx3)}
  #sys-prompt-ta:focus{border-color:var(--blue)}
  .sp-panel-acts{display:flex;gap:5px;padding:5px 10px 7px}

  /* ── v2 design overhaul ── */
  .panel-mv,.panel-xfer{display:none!important}
  .lsb-grip,.sb-dh{display:none!important}
  /* Right-sidebar panel header – consistent, clickable */
  .sb-ph{cursor:pointer;user-select:none;display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--gb);background:rgba(0,212,255,.018);transition:background .15s}
  .sb-ph:hover{background:rgba(0,212,255,.042)}
  .sb-title{font-size:.6rem;font-weight:700;letter-spacing:2.4px;text-transform:uppercase;color:var(--blue);text-shadow:0 0 8px rgba(0,212,255,.35)}
  .sb-ph-chv{margin-left:auto;font-size:.55rem;color:var(--tx3);transition:transform .2s;flex-shrink:0}
  .sb-panel.acc-closed .sb-ph-chv{transform:rotate(-90deg)}
  .sb-panel.acc-closed .sb-pb{display:none}
  .sb-panel.acc-closed .sb-prog-wrap{display:none}
  /* Metrics grid */
  .sb-prog-wrap{padding:10px 14px 4px}
  .sb-stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:6px 14px 12px}
  .ssg-cell{background:rgba(0,212,255,.03);border:1px solid rgba(0,212,255,.09);border-left:2px solid rgba(0,212,255,.4);padding:9px 11px;transition:border-left-color .2s,background .15s}
  .ssg-cell:hover{border-left-color:var(--blue);background:rgba(0,212,255,.065)}
  .ssg-cell.span2{grid-column:span 2}
  .ssg-lbl{font-size:.5rem;text-transform:uppercase;letter-spacing:1.8px;color:var(--tx3);margin-bottom:4px}
  .ssg-val{font-size:.88rem;font-family:var(--mono);color:var(--blue);font-weight:600;text-shadow:0 0 7px rgba(0,212,255,.3);line-height:1.2}
  .ssg-sub{font-size:.56rem;color:var(--tx3);margin-top:2px;font-family:var(--mono)}
  /* Left sidebar panel header */
  .lsb-ph-row{padding:0}
  .lsb-panel .acc-hdr{padding:10px 14px;font-size:.61rem;font-weight:700;letter-spacing:2px;text-transform:uppercase}
  /* Consistent list items across all panels */
  .pi-item{display:flex;align-items:flex-start;gap:8px;padding:6px 14px;border-bottom:1px solid rgba(0,212,255,.05);font-size:.62rem;transition:background .12s}
  .pi-item:last-child{border-bottom:none}
  .pi-item:hover{background:rgba(0,212,255,.03)}
  .pi-dot{width:5px;height:5px;border-radius:50%;background:var(--blue);flex-shrink:0;margin-top:4px;box-shadow:0 0 5px rgba(0,212,255,.55)}
  .pi-dot.err{background:var(--red);box-shadow:0 0 5px rgba(255,117,24,.55)}
  .pi-name{color:var(--blue);font-family:var(--mono);font-weight:600;line-height:1.3}
  .pi-detail{color:var(--tx3);font-size:.56rem;margin-top:2px;font-family:var(--mono)}
  /* Unified fn-grid buttons */
  .fn-grid{display:flex;flex-wrap:wrap;gap:5px;padding:8px 14px 10px}
  .fn-btn{background:var(--glass);color:var(--tx2);border:1px solid var(--gb);padding:5px 10px;font-size:.6rem;cursor:pointer;font-family:var(--ui);text-transform:uppercase;letter-spacing:1.4px;transition:var(--tr);border-radius:0}
  .fn-btn:hover{background:rgba(0,212,255,.08);color:var(--blue);border-color:rgba(0,212,255,.4);box-shadow:0 0 8px rgba(0,212,255,.2)}
  .fn-btn.wide{width:100%}
  .fn-btn.danger{color:rgba(255,117,24,.7);border-color:rgba(255,117,24,.25)}
  .fn-btn.danger:hover{color:var(--red);border-color:var(--red);background:rgba(255,117,24,.08);box-shadow:0 0 8px rgba(255,117,24,.2)}
  /* History button */
  .tbtn.hst{border-color:rgba(125,249,255,.3);color:var(--purple)}
  .tbtn.hst:hover{background:rgba(125,249,255,.08);border-color:var(--purple);box-shadow:0 0 12px rgba(125,249,255,.25);color:#fff}
  /* Status panel think-row */
  #think-row{padding:10px 14px}
</style>
</head>
<body>
<div id="app">

  <!-- HEADER -->
  <div id="hdr">
    <button id="lsb-tog" onclick="toggleLsb()" title="Toggle left panel">&#10070;</button>
    <div class="logo">
      <div class="logo-icon">&#10022;</div>
      <h1>ClaudioUi</h1>
    </div>
    <div id="status-pill">
      <div id="dot"></div>
      <span id="status-lbl">Ready</span>
    </div>
    <div class="spc"></div>
    <span id="dir-lbl"></span>
    <button class="hbtn" onclick="browseDir()">Browse</button>
    <button class="hbtn" onclick="openDir()">Open Dir</button>
    <button class="hbtn danger" onclick="shutdownServer()">Shut Down</button>
    <button id="rsb-tog" onclick="toggleRsb()" title="Toggle right panel">&#8862;</button>
  </div>

  <!-- MAIN ROW -->
  <div id="main">

    <!-- LEFT SIDEBAR -->
    <div id="lsb">

      <div class="lsb-panel" draggable="true" data-lsb-panel="conversations" data-panel="conversations">
        <div class="lsb-ph-row">
          <span class="lsb-grip">&#8991;</span>
          <div class="acc-hdr" onclick="toggleAcc(this)">Conversations <span class="chv">&#9660;</span></div>
          <button class="panel-mv" onclick="event.stopPropagation();movePanel('conversations')" title="Move to right sidebar">&#8250;</button>
        </div>
        <div class="acc-body">
          <button id="conv-new-btn" onclick="newChat()">&#43; New Chat</button>
          <div id="conv-list"></div>
          <div id="conv-empty">No saved conversations yet.</div>
        </div>
      </div>

      <div class="lsb-panel" draggable="true" data-lsb-panel="functions" data-panel="functions">
        <div class="lsb-ph-row">
          <span class="lsb-grip">&#8991;</span>
          <div class="acc-hdr" onclick="toggleAcc(this)">Functions <span class="chv">&#9660;</span></div>
          <button class="panel-mv" onclick="event.stopPropagation();movePanel('functions')" title="Move to right sidebar">&#8250;</button>
        </div>
        <div class="acc-body">
          <div class="fn-grid">
            <button class="fn-btn wide" onclick="newChat()">&#43; New Chat</button>
            <button class="fn-btn" onclick="clearChat()">Clear Chat</button>
            <button class="fn-btn" onclick="browseDir()">Browse Dir</button>
            <button class="fn-btn" onclick="openDir()">Open Dir</button>
            <button class="fn-btn wide" onclick="restartServer()">&#x21BB; Restart Server</button>
            <button class="fn-btn danger wide" onclick="shutdownServer()">Shut Down Server</button>
          </div>
        </div>
      </div>

      <div class="lsb-panel" draggable="true" data-lsb-panel="options" data-panel="options">
        <div class="lsb-ph-row">
          <span class="lsb-grip">&#8991;</span>
          <div class="acc-hdr" onclick="toggleAcc(this)">Options <span class="chv">&#9660;</span></div>
          <button class="panel-mv" onclick="event.stopPropagation();movePanel('options')" title="Move to right sidebar">&#8250;</button>
        </div>
        <div class="acc-body">
          <div class="opt-row">
            <div class="opt-lbl">Working Directory</div>
            <div class="opt-dir" id="opt-dir-lbl">&#x2014;</div>
          </div>
          <div class="opt-row">
            <div class="opt-lbl">Permission Mode</div>
            <select class="opt-sel" id="perm-mode-sel" onchange="applyPermMode(this.value)">
              <option value="bypassPermissions">Bypass all</option>
              <option value="acceptEdits">Accept edits</option>
              <option value="plan">Plan only</option>
              <option value="default">Default (ask)</option>
            </select>
            <div class="opt-note" id="perm-note">&#x2014;</div>
          </div>
          <div class="opt-toggle">
            <span>Auto-scroll</span>
            <label class="tsw"><input type="checkbox" id="autoscroll-chk" checked onchange="autoScroll=this.checked"><span class="tsw-track"></span></label>
          </div>
          <div class="opt-toggle">
            <span>Word wrap</span>
            <label class="tsw"><input type="checkbox" id="wordwrap-chk" checked onchange="toggleWrap(this.checked)"><span class="tsw-track"></span></label>
          </div>
        </div>
      </div>

      <!-- ── Models ──────────────────────────────────────────────── -->
      <div class="lsb-panel" draggable="true" data-lsb-panel="models" data-panel="models">
        <div class="lsb-ph-row">
          <span class="lsb-grip">&#8991;</span>
          <div class="acc-hdr" onclick="toggleAcc(this)">Models <span class="chv">&#9660;</span></div>
          <button class="panel-mv" onclick="event.stopPropagation();movePanel('models')" title="Move to right sidebar">&#8250;</button>
        </div>
        <div class="acc-body">
          <div class="opt-row">
            <div class="opt-lbl">Active Model</div>
            <select class="opt-sel" id="model-sel" onchange="applyModel(this.value)">
              <option value="">Default</option>
              <option value="claude-opus-5">claude-opus-5</option>
              <option value="claude-sonnet-5">claude-sonnet-5</option>
              <option value="claude-fable-5">claude-fable-5</option>
              <option value="claude-haiku-4-5-20251001">claude-haiku-4-5</option>
              <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
              <option value="claude-opus-4-5">claude-opus-4-5</option>
            </select>
            <div class="opt-note" id="model-note">Using default model</div>
          </div>
        </div>
      </div>

      <!-- ── Agents ─────────────────────────────────────────────── -->
      <div class="lsb-panel" draggable="true" data-lsb-panel="agents" data-panel="agents">
        <div class="lsb-ph-row">
          <span class="lsb-grip">&#8991;</span>
          <div class="acc-hdr" onclick="toggleAcc(this)">Agents <span class="chv">&#9660;</span></div>
          <button class="panel-mv" onclick="event.stopPropagation();movePanel('agents')" title="Move to right sidebar">&#8250;</button>
        </div>
        <div class="acc-body">
          <div class="fn-grid">
            <button class="fn-btn wide" onclick="spawnAgent()">&#43; New Agent Window</button>
            <button class="fn-btn wide" onclick="listAgents()">&#x21BB; Refresh</button>
          </div>
          <div id="agent-list" style="padding:4px 8px;font-size:.62rem;color:var(--tx3);font-family:var(--mono);line-height:1.7">No tracked agents.</div>
        </div>
      </div>

      <!-- ── Plugins ────────────────────────────────────────────── -->
      <div class="lsb-panel" draggable="true" data-lsb-panel="plugins" data-panel="plugins">
        <div class="lsb-ph-row">
          <span class="lsb-grip">&#8991;</span>
          <div class="acc-hdr" onclick="toggleAcc(this)">Plugins <span class="chv">&#9660;</span></div>
          <button class="panel-mv" onclick="event.stopPropagation();movePanel('plugins')" title="Move to right sidebar">&#8250;</button>
        </div>
        <div class="acc-body">
          <div class="fn-grid">
            <button class="fn-btn wide" onclick="loadPlugins()">&#x21BB; Refresh Plugins</button>
          </div>
          <div id="plugin-list" style="padding:6px 8px;font-size:.62rem;color:var(--tx3);font-family:var(--mono);line-height:1.7">Click refresh to detect plugins.</div>
        </div>
      </div>

      <!-- ── Hooks ──────────────────────────────────────────────── -->
      <div class="lsb-panel" draggable="true" data-lsb-panel="hooks" data-panel="hooks">
        <div class="lsb-ph-row">
          <span class="lsb-grip">&#8991;</span>
          <div class="acc-hdr" onclick="toggleAcc(this)">Hooks <span class="chv">&#9660;</span></div>
          <button class="panel-mv" onclick="event.stopPropagation();movePanel('hooks')" title="Move to right sidebar">&#8250;</button>
        </div>
        <div class="acc-body">
          <div class="fn-grid">
            <button class="fn-btn wide" onclick="loadHooks()">&#x21BB; Refresh Hooks</button>
          </div>
          <div id="hooks-list" style="padding:6px 8px;font-size:.62rem;color:var(--tx2);font-family:var(--mono);line-height:1.7;max-height:140px;overflow-y:auto">Click refresh to read hooks.</div>
        </div>
      </div>

      <!-- ── MCPs ───────────────────────────────────────────────── -->
      <div class="lsb-panel" draggable="true" data-lsb-panel="mcps" data-panel="mcps">
        <div class="lsb-ph-row">
          <span class="lsb-grip">&#8991;</span>
          <div class="acc-hdr" onclick="toggleAcc(this)">MCPs <span class="chv">&#9660;</span></div>
          <button class="panel-mv" onclick="event.stopPropagation();movePanel('mcps')" title="Move to right sidebar">&#8250;</button>
        </div>
        <div class="acc-body">
          <div class="fn-grid">
            <button class="fn-btn wide" onclick="loadMCPs()">&#x21BB; Refresh MCPs</button>
          </div>
          <div id="mcp-list" style="padding:6px 8px;font-size:.62rem;color:var(--tx2);font-family:var(--mono);line-height:1.7;max-height:140px;overflow-y:auto">Click refresh to read MCP servers.</div>
        </div>
      </div>

      <!-- ── Loops ──────────────────────────────────────────────── -->
      <div class="lsb-panel" draggable="true" data-lsb-panel="loops" data-panel="loops">
        <div class="lsb-ph-row">
          <span class="lsb-grip">&#8991;</span>
          <div class="acc-hdr" onclick="toggleAcc(this)">Loops <span class="chv">&#9660;</span></div>
          <button class="panel-mv" onclick="event.stopPropagation();movePanel('loops')" title="Move to right sidebar">&#8250;</button>
        </div>
        <div class="acc-body">
          <div class="opt-row">
            <div class="opt-lbl">Prompt</div>
            <textarea id="loop-prompt" class="opt-sel" style="resize:vertical;min-height:48px;font-family:var(--mono);font-size:.65rem;padding:4px;width:100%" placeholder="Prompt to repeat on each loop..."></textarea>
          </div>
          <div class="opt-row">
            <div class="opt-lbl">Interval (seconds)</div>
            <input type="number" id="loop-interval" class="opt-sel" value="300" min="10" style="width:80px">
          </div>
          <div class="fn-grid">
            <button class="fn-btn" id="loop-start-btn" onclick="startLoop()">&#x25B6; Start</button>
            <button class="fn-btn danger" id="loop-stop-btn" onclick="stopLoop()" style="opacity:.4" disabled>&#x25A0; Stop</button>
          </div>
          <div id="loop-status" style="font-size:.62rem;color:var(--tx3);padding:4px 8px;font-family:var(--mono)">Inactive</div>
        </div>
      </div>

      <!-- ── Slash Commands ──────────────────────────────────────── -->
      <div class="lsb-panel" draggable="true" data-lsb-panel="slashcmds" data-panel="slashcmds">
        <div class="lsb-ph-row">
          <span class="lsb-grip">&#8991;</span>
          <div class="acc-hdr" onclick="toggleAcc(this)">Slash Commands <span class="chv">&#9660;</span></div>
          <button class="panel-mv" onclick="event.stopPropagation();movePanel('slashcmds')" title="Move to right sidebar">&#8250;</button>
        </div>
        <div class="acc-body">
          <div class="fn-grid">
            <button class="fn-btn" onclick="runSlash('/help')">/help</button>
            <button class="fn-btn" onclick="runSlash('/clear')">/clear</button>
            <button class="fn-btn" onclick="runSlash('/compact')">/compact</button>
            <button class="fn-btn" onclick="runSlash('/config')">/config</button>
            <button class="fn-btn" onclick="runSlash('/cost')">/cost</button>
            <button class="fn-btn" onclick="runSlash('/status')">/status</button>
            <button class="fn-btn" onclick="runSlash('/doctor')">/doctor</button>
            <button class="fn-btn" onclick="runSlash('/memory')">/memory</button>
            <button class="fn-btn" onclick="runSlash('/init')">/init</button>
            <button class="fn-btn" onclick="runSlash('/review')">/review</button>
            <button class="fn-btn" onclick="runSlash('/fast')">/fast</button>
            <button class="fn-btn" onclick="runSlash('/login')">/login</button>
          </div>
          <div style="padding:6px 8px 8px">
            <div class="opt-lbl" style="margin-bottom:4px">Custom</div>
            <div style="display:flex;gap:4px">
              <input type="text" id="slash-custom" class="opt-sel" placeholder="/command" style="flex:1;font-size:.65rem">
              <button class="fn-btn" style="flex-shrink:0;padding:6px 10px" onclick="runSlash(document.getElementById('slash-custom').value)">Run</button>
            </div>
          </div>
        </div>
      </div>

      <!-- ── Thinking ────────────────────────────────────────────── -->
      <div class="lsb-panel" draggable="true" data-lsb-panel="thinking" data-panel="thinking">
        <div class="lsb-ph-row">
          <span class="lsb-grip">&#8991;</span>
          <div class="acc-hdr" onclick="toggleAcc(this)">Thinking <span class="chv">&#9660;</span></div>
          <button class="panel-mv" onclick="event.stopPropagation();movePanel('thinking')" title="Move to right sidebar">&#8250;</button>
        </div>
        <div class="acc-body">
          <div class="opt-row" style="display:flex;align-items:center;justify-content:space-between">
            <div class="opt-lbl" style="margin:0">Extended Thinking</div>
            <label class="tsw"><input type="checkbox" id="thinking-chk" onchange="applyThinking()"><span class="tsw-track"></span></label>
          </div>
          <div class="opt-row" id="thinking-budget-row" style="opacity:.4;pointer-events:none">
            <div class="opt-lbl">Budget (tokens)</div>
            <input type="range" id="thinking-range" min="1024" max="16000" step="512" value="5000"
              style="width:100%;accent-color:var(--blue);margin:4px 0"
              oninput="document.getElementById('thinking-val').textContent=this.value;thinkingBudget=parseInt(this.value);applyThinking()">
            <div style="display:flex;justify-content:space-between;font-size:.58rem;color:var(--tx3);margin-top:1px">
              <span>1 K</span>
              <span id="thinking-val" style="color:var(--blue);font-weight:600">5000</span>
              <span>16 K</span>
            </div>
          </div>
          <div id="thinking-note" class="opt-note" style="padding:0 13px 8px">Off — standard response mode</div>
        </div>
      </div>

    </div>

    <div id="lsb-resize" class="col-resize" title="Drag to resize &bull; double-click to reset"></div>

    <!-- CHAT -->
    <div id="chat-wrap">
      <div id="chat"></div>
    </div>

    <div id="rsb-resize" class="col-resize" title="Drag to resize &bull; double-click to reset"></div>

    <!-- RIGHT SIDEBAR (draggable panels) -->
    <div id="sidebar">

      <div class="sb-panel" draggable="true" data-panel="server">
        <div class="sb-ph" onclick="toggleSbPanel(this)">
          <span class="sb-title">Server</span>
          <span class="srv-pdot" id="srv-pdot"></span>
          <span class="sb-ph-chv">&#9660;</span>
        </div>
        <div class="sb-pb">
          <div class="stat-row" style="margin-bottom:5px">
            <div class="sb-val" id="srv-panel-val" style="font-size:.9rem;color:var(--green)">Running</div>
          </div>
          <div class="sb-sub" style="margin-bottom:8px">localhost:8765</div>
          <div style="display:flex;flex-wrap:wrap;gap:4px">
            <button class="fn-btn" onclick="restartServer()" style="font-size:.58rem;padding:5px 8px">&#x21BB; Restart</button>
            <button class="fn-btn" onclick="openSrvBrowser()" style="font-size:.58rem;padding:5px 8px">Browser</button>
            <button class="fn-btn danger" onclick="shutdownServer()" style="font-size:.58rem;padding:5px 8px">Shut Down</button>
          </div>
        </div>
      </div>

      <div class="sb-panel" draggable="true" data-panel="status">
        <div class="sb-ph" onclick="toggleSbPanel(this)"><span class="sb-title">Status</span><span class="sb-ph-chv">&#9660;</span></div>
        <div class="sb-pb">
          <div id="think-row">
            <div id="think-orb" class="idle"></div>
            <div>
              <div id="think-label">Ready</div>
              <div class="dot-wave"><span></span><span></span><span></span></div>
            </div>
          </div>
        </div>
      </div>

      <div class="sb-panel" data-panel="metrics">
        <div class="sb-ph" onclick="toggleSbPanel(this)">
          <span class="sb-title">Metrics</span>
          <span class="sb-ph-chv">&#9660;</span>
        </div>
        <div class="sb-pb">
          <div class="sb-prog-wrap">
            <div id="prog-meta">
              <span id="prog-pct">&#8212;</span>
              <span id="prog-eta-lbl">ETA &#8212;</span>
            </div>
            <div id="prog-track"><div id="prog-fill"></div></div>
          </div>
          <div class="sb-stat-grid">
            <div class="ssg-cell">
              <div class="ssg-lbl">Elapsed</div>
              <div class="ssg-val" id="sb-timer">&#8212;</div>
              <div class="ssg-sub">duration</div>
            </div>
            <div class="ssg-cell">
              <div class="ssg-lbl">Tokens (est.)</div>
              <div class="ssg-val" id="sb-tokens">&#8212;</div>
              <div class="ssg-sub" id="sb-speed">&#8212; tok/s</div>
            </div>
            <div class="ssg-cell">
              <div class="ssg-lbl">Last Response</div>
              <div class="ssg-val" id="sb-last-tokens">&#8212;</div>
              <div class="ssg-sub" id="sb-last-time">&#8212;</div>
            </div>
            <div class="ssg-cell">
              <div class="ssg-lbl">This Chat</div>
              <div class="ssg-val" id="sb-chat-tok">&#8212;</div>
              <div class="ssg-sub" id="sb-chat-sub">&#8212;</div>
            </div>
            <div class="ssg-cell span2">
              <div class="ssg-lbl">All Chats</div>
              <div class="ssg-val" id="sb-total-tok">&#8212;</div>
              <div class="ssg-sub" id="sb-total-sub">&#8212;</div>
            </div>
          </div>
        </div>
      </div>

      <div class="sb-panel sp-panel" draggable="true" data-panel="sysprompt">
        <div class="sb-ph" onclick="toggleSbPanel(this)"><span class="sb-title">System Prompt</span><span class="sb-ph-chv">&#9660;</span></div>
        <div class="sb-pb" style="padding-bottom:4px">
          <span id="sp-state" class="sb-sub">inactive</span>
        </div>
        <textarea id="sys-prompt-ta" rows="4" placeholder="Persistent instructions sent with every message&#8230;"></textarea>
        <div class="sp-panel-acts">
          <button class="sp-btn" onclick="clearSysPrompt()">Clear</button>
          <button class="sp-btn primary" onclick="applySysPrompt()">Apply</button>
        </div>
      </div>

      <div id="con-sec">
        <div id="con-hdr">
          <span id="con-title">Console</span>
          <button id="con-clr" onclick="clearConsole()">CLR</button>
        </div>
        <div id="con-out"></div>
      </div>

    </div>
  </div>


  <!-- BOTTOM INPUT -->
  <div id="btm">
    <div id="tb">
      <button class="tbtn" onclick="newChat()">New Chat</button>
      <button class="tbtn" onclick="clearChat()">Clear</button>
      <button class="tbtn" onclick="loadHistory()">Load History</button>
      <button class="tbtn danger" id="stop-btn" onclick="stopIt()" disabled>Stop</button>
      <button class="tbtn hst" onclick="loadHistory()">&#x29C9; History</button>
      <span id="stxt">Ready</span>
    </div>
    <div id="img-strip"></div>
    <div id="ir">
      <div id="input-wrap">
        <div id="paste-hint">&#128203; Image pasted</div>
        <textarea id="ibox" rows="2" placeholder="Message Claude&#8230; (Enter to send, Shift+Enter for new line, Ctrl+V paste image)"></textarea>
      </div>
      <button id="sbtn" onclick="sendMsg()">Send &#8629;</button>
    </div>
    <div id="hint">Enter = send &nbsp;&bull;&nbsp; Shift+Enter = new line &nbsp;&bull;&nbsp; Ctrl+V = paste image</div>
  </div>
</div>

<script>
const chat   = document.getElementById("chat");
const ibox   = document.getElementById("ibox");
const sbtn   = document.getElementById("sbtn");
const stpBtn = document.getElementById("stop-btn");
const stxt   = document.getElementById("stxt");
const dot    = document.getElementById("dot");
const pill   = document.getElementById("status-pill");
const sLbl   = document.getElementById("status-lbl");
const dirLbl = document.getElementById("dir-lbl");
const sidebar= document.getElementById("sidebar");
const lsb    = document.getElementById("lsb");

let isFirst=true, busy=false, abortCtrl=null;

fetch("/api/state").then(r=>r.json()).then(d=>{
  dirLbl.textContent=d.dir; dirLbl.title=d.dir;
  updateOptDir(d.dir);
  sys("Working directory: "+d.dir);
  sys("Type a message below and press Enter.");
});

// ── Sidebar toggles ─────────────────────────────────────────
function toggleLsb(){
  lsb.classList.toggle("collapsed");
  try{localStorage.setItem("cc-lsb",lsb.classList.contains("collapsed")?"0":"1");}catch(e){}
  syncHandles();
}
function toggleRsb(){
  sidebar.classList.toggle("collapsed");
  try{localStorage.setItem("cc-rsb",sidebar.classList.contains("collapsed")?"0":"1");}catch(e){}
  syncHandles();
}
function restoreSidebars(){
  try{if(localStorage.getItem("cc-lsb")==="0")lsb.classList.add("collapsed");}catch(e){}
  try{if(localStorage.getItem("cc-rsb")==="0")sidebar.classList.add("collapsed");}catch(e){}
  syncHandles();
}

// ── Sidebar drag-to-resize ───────────────────────────────────
// Widths live in the CSS custom properties the sidebars already read, so the
// .collapsed rule (width:0) still wins over whatever we set here.
var COL_MIN=150, COL_MAX=560, LSB_DEF=220, RSB_DEF=256;

function syncHandles(){
  var lh=document.getElementById("lsb-resize");
  var rh=document.getElementById("rsb-resize");
  if(lh) lh.classList.toggle("hidden",lsb.classList.contains("collapsed"));
  if(rh) rh.classList.toggle("hidden",sidebar.classList.contains("collapsed"));
}

function setColWidth(cssVar,storeKey,w){
  w=Math.max(COL_MIN,Math.min(COL_MAX,Math.round(w)));
  document.documentElement.style.setProperty(cssVar,w+"px");
  try{localStorage.setItem(storeKey,String(w));}catch(e){}
  return w;
}

// dir: +1 when dragging right should grow the panel (left sidebar),
//      -1 when dragging right should shrink it (right sidebar).
function initColResize(handleId,el,cssVar,storeKey,dir,defW){
  var handle=document.getElementById(handleId);
  if(!handle) return;
  var dragging=false,startX=0,startW=0;

  handle.addEventListener("mousedown",function(e){
    if(el.classList.contains("collapsed")) return;
    dragging=true; startX=e.clientX; startW=el.offsetWidth;
    handle.classList.add("dragging");
    document.body.classList.add("col-dragging");
    el.style.transition="none";      // the .28s width transition lags the cursor
    e.preventDefault();
  });

  document.addEventListener("mousemove",function(e){
    if(!dragging) return;
    setColWidth(cssVar,storeKey,startW+dir*(e.clientX-startX));
  });

  document.addEventListener("mouseup",function(){
    if(!dragging) return;
    dragging=false;
    handle.classList.remove("dragging");
    document.body.classList.remove("col-dragging");
    el.style.transition="";
  });

  handle.addEventListener("dblclick",function(){
    setColWidth(cssVar,storeKey,defW);
  });
}

function restoreColWidths(){
  [["cc-lsb-w","--lsb-w"],["cc-rsb-w","--rsb-w"]].forEach(function(pair){
    try{
      var v=parseInt(localStorage.getItem(pair[0]),10);
      if(v>=COL_MIN&&v<=COL_MAX) document.documentElement.style.setProperty(pair[1],v+"px");
    }catch(e){}
  });
}

// ── Unified panel system ──────────────────────────────────────
var dragSrc=null;

function _getAllPanels(container){
  return [].slice.call(container.querySelectorAll(".sb-panel,.lsb-panel"));
}

function _attachDndOnce(panel){
  if(panel.dataset.dndInit) return;
  panel.dataset.dndInit="1";
  panel.addEventListener("dragstart",function(e){
    dragSrc=panel; panel.classList.add("dragging");
    e.dataTransfer.effectAllowed="move";
    e.stopPropagation();
  });
  panel.addEventListener("dragend",function(){
    panel.classList.remove("dragging");
    _getAllPanels(panel.parentElement).forEach(function(p){p.classList.remove("drag-over");});
    savePanelLayout();
  });
  panel.addEventListener("dragover",function(e){
    e.preventDefault(); e.dataTransfer.dropEffect="move";
    if(panel!==dragSrc){
      _getAllPanels(panel.parentElement).forEach(function(p){p.classList.remove("drag-over");});
      panel.classList.add("drag-over");
    }
  });
  panel.addEventListener("drop",function(e){
    e.preventDefault();
    if(panel!==dragSrc&&dragSrc){
      if(dragSrc.parentElement===panel.parentElement){
        var list=_getAllPanels(panel.parentElement);
        var si=list.indexOf(dragSrc),di=list.indexOf(panel);
        if(si<di) panel.after(dragSrc); else panel.before(dragSrc);
      } else {
        // Cross-sidebar move: insert before target panel
        var mvBtn=dragSrc.querySelector('.panel-mv');
        if(panel.closest('#sidebar')){
          if(mvBtn){mvBtn.innerHTML='&#8249;';mvBtn.title='Move to left sidebar';}
        } else {
          if(mvBtn){mvBtn.innerHTML='&#8250;';mvBtn.title='Move to right sidebar';}
        }
        panel.before(dragSrc);
        _attachDndOnce(dragSrc);
      }
      savePanelLayout();
    }
    panel.classList.remove("drag-over");
  });
}

function initAllPanels(){
  _getAllPanels(sidebar).concat(_getAllPanels(lsb)).forEach(function(p){
    _attachDndOnce(p);
  });
}

function movePanel(id){
  var panel=document.querySelector('[data-panel="'+id+'"]');
  if(!panel) return;
  var mvBtn=panel.querySelector('.panel-mv');
  if(panel.closest('#sidebar')){
    // Move to left sidebar
    if(mvBtn){mvBtn.innerHTML='&#8250;';mvBtn.title='Move to right sidebar';}
    lsb.appendChild(panel);
  } else {
    // Move to right sidebar
    if(mvBtn){mvBtn.innerHTML='&#8249;';mvBtn.title='Move to left sidebar';}
    var conSec=document.getElementById('con-sec');
    if(conSec) sidebar.insertBefore(panel,conSec); else sidebar.appendChild(panel);
  }
  _attachDndOnce(panel);
  savePanelLayout();
}

function savePanelLayout(){
  var rsbOrder=[].slice.call(sidebar.querySelectorAll("[data-panel]")).map(function(p){return p.dataset.panel;});
  var lsbOrder=[].slice.call(lsb.querySelectorAll("[data-panel]")).map(function(p){return p.dataset.panel;});
  try{localStorage.setItem("cc-panel-rsb",JSON.stringify(rsbOrder));}catch(e){}
  try{localStorage.setItem("cc-panel-lsb",JSON.stringify(lsbOrder));}catch(e){}
}

function restorePanelLayout(){
  try{
    var allPanels={};
    document.querySelectorAll("[data-panel]").forEach(function(p){allPanels[p.dataset.panel]=p;});

    // Move any panels saved to left sidebar
    var lsbSaved=JSON.parse(localStorage.getItem("cc-panel-lsb")||"null");
    if(lsbSaved) lsbSaved.forEach(function(id){
      var p=allPanels[id];
      if(p&&!p.closest('#lsb')){
        var mvBtn=p.querySelector('.panel-mv');
        if(mvBtn){mvBtn.innerHTML='&#8250;';mvBtn.title='Move to right sidebar';}
        lsb.appendChild(p);
      }
    });

    // Restore right sidebar order
    var rsbSaved=JSON.parse(localStorage.getItem("cc-panel-rsb")||"null");
    var conSec=document.getElementById('con-sec');
    if(rsbSaved) rsbSaved.forEach(function(id){
      var p=allPanels[id];
      if(p&&p.closest('#sidebar')){
        if(conSec) sidebar.insertBefore(p,conSec); else sidebar.appendChild(p);
      }
    });

    // Restore left sidebar order
    if(lsbSaved) lsbSaved.forEach(function(id){
      var p=allPanels[id];
      if(p&&p.closest('#lsb')) lsb.appendChild(p);
    });

    // Sync all move button directions
    sidebar.querySelectorAll("[data-panel] .panel-mv").forEach(function(b){b.innerHTML='&#8249;';b.title='Move to left sidebar';});
    lsb.querySelectorAll("[data-panel] .panel-mv").forEach(function(b){b.innerHTML='&#8250;';b.title='Move to right sidebar';});
  }catch(e){}
}

// ── Message helpers ──────────────────────────────────────────
function h(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function md(s){
  s=h(s);
  s=s.replace(/```[\s\S]*?```/g,function(m){
    var code=m.replace(/^```\w*\n?/,"").replace(/```$/,"");
    return "<pre><code>"+code+"</code></pre>";
  });
  s=s.replace(/`([^`\n]+)`/g,"<code>$1</code>");
  s=s.replace(/\*\*(.+?)\*\*/g,"<strong>$1</strong>");
  return s;
}
function addMsg(role,html,id){
  var w=document.createElement("div"); w.className="msg "+role;
  if(id) w.id=id;
  var lbl=role==="user"?"You":role==="ai"?"Claude":"";
  var av=role==="user"?"Y":role==="ai"?"&#10022;":"";
  var meta=lbl?("<div class=\"msg-meta\">"+(role==="user"
    ?"<span class=\"lbl\">"+lbl+"</span><div class=\"msg-av\">"+av+"</div>"
    :"<div class=\"msg-av\">"+av+"</div><span class=\"lbl\">"+lbl+"</span>")+"</div>"):"";
  w.innerHTML=meta+"<div class=\"bubble\">"+html+"</div>";
  chat.appendChild(w);
  if(!id&&(role==="user"||role==="ai")) currentMsgs.push({role:role,html:html,text:html.replace(/<[^>]+>/g,"")});
  scrollDown(); return w;
}
function sys(t){addMsg("sys",h(t));}
function scrollDown(){if(autoScroll) chat.scrollTop=chat.scrollHeight;}

// ── Left sidebar / conversations ─────────────────────────────
var autoScroll=true, currentMsgs=[], activeConvId=null, sysPromptVal="";
var CONV_KEY="claude_gui_convs";

function toggleAcc(hdr){
  hdr.classList.toggle("closed");
  var panel=hdr.closest(".lsb-panel");
  var body=panel?panel.querySelector(".acc-body"):hdr.nextElementSibling;
  if(body) body.classList.toggle("closed");
}
function getConvs(){try{return JSON.parse(localStorage.getItem(CONV_KEY)||"[]");}catch{return[];}}
function putConvs(a){localStorage.setItem(CONV_KEY,JSON.stringify(a));}

function fmtAgo(ts){
  var s=Math.round((Date.now()-ts)/1000);
  if(s<60) return "just now";
  if(s<3600) return Math.floor(s/60)+"m ago";
  if(s<86400) return Math.floor(s/3600)+"h ago";
  return Math.floor(s/86400)+"d ago";
}

function renderConvList(){
  var convs=getConvs().slice().reverse();
  var list=document.getElementById("conv-list");
  var empty=document.getElementById("conv-empty");
  empty.style.display=convs.length?"none":"";
  list.innerHTML=convs.map(function(c){
    return "<div class=\"conv-item"+(c.id===activeConvId?" active":"")+"\" onclick=\"loadConv('"+c.id+"')\">"
      +"<div class=\"conv-info\">"
      +"<div class=\"conv-title\">"+h(c.title)+"</div>"
      +"<div class=\"conv-time\">"+fmtAgo(c.ts)+" &bull; "+c.msgs.filter(function(m){return m.role==="user";}).length+" msg</div>"
      +"</div>"
      +"<button class=\"conv-del\" onclick=\"delConv(event,'"+c.id+"')\">&#x2715;</button>"
      +"</div>";
  }).join("");
}

function saveCurrentConv(){
  var userMsgs=currentMsgs.filter(function(m){return m.role==="user";});
  if(!userMsgs.length) return;
  var title=userMsgs[0].text.slice(0,50)||"Untitled";
  var convs=getConvs();
  var id=activeConvId||String(Date.now());
  var idx=convs.findIndex(function(c){return c.id===id;});
  var entry={id:id,title:title,ts:Date.now(),msgs:currentMsgs.slice(),
             stats:{tokens:chatStats.tokens,ms:chatStats.ms,responses:chatStats.responses}};
  if(idx>=0) convs[idx]=entry; else convs.push(entry);
  putConvs(convs);
  activeConvId=id;
  renderConvList();
  recomputeTotals(); renderAggStats();
}

function loadConv(id,silent){
  if(busy) return;
  saveCurrentConv();
  var conv=getConvs().find(function(c){return c.id===id;});
  if(!conv) return;
  activeConvId=id; currentMsgs=conv.msgs.slice();
  var cs=conv.stats||{};
  chatStats={tokens:cs.tokens||0,ms:cs.ms||0,responses:cs.responses||0};
  recomputeTotals(); renderAggStats();
  chat.innerHTML="";
  conv.msgs.forEach(function(m){
    var w=document.createElement("div"); w.className="msg "+m.role;
    var lbl=m.role==="user"?"You":m.role==="ai"?"Claude":"";
    var av=m.role==="user"?"Y":m.role==="ai"?"&#10022;":"";
    var meta=lbl?("<div class=\"msg-meta\">"+(m.role==="user"
      ?"<span class=\"lbl\">"+lbl+"</span><div class=\"msg-av\">"+av+"</div>"
      :"<div class=\"msg-av\">"+av+"</div><span class=\"lbl\">"+lbl+"</span>")+"</div>"):"";
    w.innerHTML=meta+"<div class=\"bubble\">"+m.html+"</div>";
    chat.appendChild(w);
  });
  isFirst=false; autoScroll=true;
  chat.scrollTop=chat.scrollHeight;
  renderConvList();
  if(!silent) sys("Loaded: "+conv.msgs.filter(function(m){return m.role==="user";}).length+" messages.");
}

function delConv(evt,id){
  evt.stopPropagation();
  putConvs(getConvs().filter(function(c){return c.id!==id;}));
  if(activeConvId===id) activeConvId=null;
  renderConvList();
  recomputeTotals(); renderAggStats();
}

function refreshSpState(){
  if(!spState) return;
  var cur=spTa.value.trim();
  if(cur!==sysPromptVal){spState.textContent="unapplied";spState.className="mod";}
  else if(sysPromptVal){spState.textContent="active - "+sysPromptVal.length+" ch";spState.className="on";}
  else{spState.textContent="inactive";spState.className="";}
}
function applySysPrompt(){
  sysPromptVal=spTa.value.trim();
  refreshSpState();
  sys(sysPromptVal?"System prompt applied.":"System prompt cleared.");
}
function clearSysPrompt(){
  spTa.value=""; sysPromptVal=""; refreshSpState();
  sys("System prompt cleared.");
}
// ── Permission mode ──────────────────────────────────────────
// -p is non-interactive: there is no TTY for an approval prompt, so whatever
// this mode does NOT pre-approve will simply fail mid-request.
var permMode="bypassPermissions";
var PERM_NOTES={
  acceptEdits:       ["File edits auto-approve. Commands still gated.",""],
  bypassPermissions:["All tools run unprompted - no confirmation step.","danger"],
  plan:              ["Read-only: Claude plans but will not edit.",""],
  "default":         ["Prompts cannot be shown in -p mode - gated tools will fail.","warn"]
};
function renderPermNote(){
  var el=document.getElementById("perm-note");
  if(!el) return;
  var info=PERM_NOTES[permMode]||["",""];
  el.textContent=info[0];
  el.className="opt-note"+(info[1]?" "+info[1]:"");
}
function applyPermMode(v){
  permMode=v||"acceptEdits";
  try{localStorage.setItem("cc-perm-mode",permMode);}catch(e){}
  renderPermNote();
  sys("Permission mode -> "+permMode);
}
function restorePermMode(){
  try{
    var v=localStorage.getItem("cc-perm-mode");
    if(v&&PERM_NOTES[v]) permMode=v;
  }catch(e){}
  var sel=document.getElementById("perm-mode-sel");
  if(sel) sel.value=permMode;
  renderPermNote();
}

function toggleWrap(on){
  document.querySelectorAll(".bubble").forEach(function(b){b.style.whiteSpace=on?"pre-wrap":"pre";});
}
function updateOptDir(dir){
  var el=document.getElementById("opt-dir-lbl");
  if(el) el.textContent=dir;
}

// ── Right sidebar stats ──────────────────────────────────────
var thinkOrb=document.getElementById("think-orb");
var thinkLabel=document.getElementById("think-label");
var dotWave=document.querySelector(".dot-wave");
var sbTimer=document.getElementById("sb-timer");
var progFill=document.getElementById("prog-fill");
var progPct=document.getElementById("prog-pct");
var progEta=document.getElementById("prog-eta-lbl");
var sbTokens=document.getElementById("sb-tokens");
var sbLastTok=document.getElementById("sb-last-tokens");
var sbLastTime=document.getElementById("sb-last-time");
var sbChatTok=document.getElementById("sb-chat-tok");
var sbChatSub=document.getElementById("sb-chat-sub");
var sbTotTok=document.getElementById("sb-total-tok");
var sbTotSub=document.getElementById("sb-total-sub");

var startTime=0, timerInterval=null, responseHistory=[];
var tokenChars=0, rateWindow=[];

// Committed totals for the active conversation; persisted into its conv entry.
var chatStats={tokens:0,ms:0,responses:0};
// Sum over every OTHER stored conversation. The active chat is always counted
// via chatStats instead, so totals stay correct no matter when a save lands.
var totalsCache={tokens:0,ms:0,responses:0,chats:0};

function recomputeTotals(){
  var t={tokens:0,ms:0,responses:0,chats:0};
  getConvs().forEach(function(c){
    t.chats++;
    if(c.id===activeConvId) return;
    var s=c.stats||{};
    t.tokens+=s.tokens||0; t.ms+=s.ms||0; t.responses+=s.responses||0;
  });
  if(!activeConvId&&chatStats.responses>0) t.chats++;
  totalsCache=t;
}

function renderAggStats(){
  var liveTok=startTime?Math.round(tokenChars/4):0;
  var liveMs=startTime?(Date.now()-startTime):0;
  var liveRes=startTime?1:0;

  var cTok=chatStats.tokens+liveTok, cMs=chatStats.ms+liveMs, cRes=chatStats.responses+liveRes;
  sbChatTok.textContent=cTok>0?cTok.toLocaleString():"\u2014";
  sbChatSub.innerHTML=cRes+(cRes===1?" response":" responses")+" &middot; "+fmtTime(cMs);

  var tTok=totalsCache.tokens+cTok, tMs=totalsCache.ms+cMs, tRes=totalsCache.responses+cRes;
  var nChats=Math.max(totalsCache.chats,cRes>0?1:0);
  sbTotTok.textContent=tTok>0?tTok.toLocaleString():"\u2014";
  sbTotSub.innerHTML=nChats+(nChats===1?" chat":" chats")+" &middot; "+tRes+" resp &middot; "+fmtTime(tMs);

  var on=startTime>0;
  sbChatTok.className="sb-val"+(on?" live":"");
  sbTotTok.className="sb-val"+(on?" live":"");
}

function fmtTime(ms){
  var s=Math.floor(ms/1000),m=Math.floor(s/60),hr=Math.floor(m/60);
  if(hr>0) return hr+":"+String(m%60).padStart(2,"0")+":"+String(s%60).padStart(2,"0");
  return m+":"+String(s%60).padStart(2,"0");
}

function updateSB(){
  if(!startTime) return;
  var now=Date.now(), el=now-startTime;
  sbTimer.textContent=fmtTime(el);
  rateWindow=rateWindow.filter(function(p){return now-p.t<2000;});
  var tok=Math.round(tokenChars/4);
  if(tok>0){
    if(progFill.classList.contains("sweep")){progFill.className="";progFill.style.width="0%";}
    var oldest=rateWindow[0];
    var rateTok=oldest&&(now-oldest.t)>200?((tokenChars-oldest.chars)/4)/((now-oldest.t)/1000):0;
    var est=responseHistory.length?responseHistory.slice(-5).reduce(function(a,r){return a+r.tokens;},0)/Math.min(5,responseHistory.length):600;
    var pct=Math.min(94,Math.round(tok/est*100));
    var eta=rateTok>1?Math.max(0,Math.round((est-tok)/rateTok)):null;
    sbTokens.textContent=tok.toLocaleString()+" tok";
    progPct.textContent=pct+"%";
    progEta.textContent=eta!==null?"ETA ~"+eta+"s":"ETA --";
    progFill.style.width=pct+"%";
    if(rateTok>1) document.getElementById("sb-speed").textContent=Math.round(rateTok)+" tok/s";
  }
  renderAggStats();
}

function sidebarBusy(){
  thinkOrb.className="active"; thinkLabel.textContent="Thinking...";
  dotWave.classList.add("active");
  progFill.className="sweep"; progFill.style.width="";
  progPct.textContent="--"; progEta.textContent="ETA --";
  sbTimer.textContent="0:00"; sbTokens.textContent="--";
  tokenChars=0; rateWindow=[];
  startTime=Date.now();
  timerInterval=setInterval(updateSB,250);
}
function sidebarDone(stopped,txt){
  clearInterval(timerInterval); timerInterval=null;
  var el=startTime?Date.now()-startTime:0;
  var tok=tokenChars>0?Math.round(tokenChars/4):(txt?Math.round(txt.length/4):0);
  if(!stopped&&tok>10){
    responseHistory.push({tokens:tok,ms:el});
    if(responseHistory.length>10) responseHistory.shift();
  }
  // Commit to the running totals even if stopped - those tokens were really produced.
  if(tok>0){chatStats.tokens+=tok;chatStats.ms+=el;chatStats.responses++;}
  thinkOrb.className="idle";
  thinkLabel.textContent=stopped?"Stopped":"Ready";
  dotWave.classList.remove("active");
  progFill.className=""; progFill.style.width=stopped?"0%":"100%";
  progPct.textContent=stopped?"--":"100%"; progEta.textContent="ETA --";
  if(tok>0){
    var rate=el>0?Math.round(tok/(el/1000)):0;
    sbTokens.textContent=tok.toLocaleString()+" tok";
    document.getElementById("sb-speed").textContent=rate>0?rate+" tok/s":"-- tok/s";
    sbLastTok.textContent=tok.toLocaleString()+" tok";
    sbLastTime.textContent=fmtTime(el);
  }
  tokenChars=0; rateWindow=[]; startTime=0;
  renderAggStats();
}

function setBusy(b,txt){
  busy=b; sbtn.disabled=b; stpBtn.disabled=!b;
  dot.className=b?"busy":""; pill.className=b?"busy":"";
  sLbl.textContent=b?"Thinking...":"Ready";
  stxt.textContent=b?"Claude is thinking...":"Ready";
  if(b) sidebarBusy(); else sidebarDone(false,txt);
}

// ── Console (right sidebar) ──────────────────────────────────
var conOut=document.getElementById("con-out");
var conLastSeq=-1;

function _addLine(container,text,isErr){
  var line=document.createElement("div");
  line.className="con-line"+(isErr?" err":"");
  line.textContent=text;
  container.appendChild(line);
  container.scrollTop=container.scrollHeight;
  while(container.children.length>500) container.removeChild(container.firstChild);
}
function appendConsole(text,isErr){_addLine(conOut,text,isErr);}
function clearConsole(){conOut.innerHTML="";}

// ── System prompt (right sidebar panel) ──────────────────────
var spState=document.getElementById("sp-state");
var spTa=document.getElementById("sys-prompt-ta");

function pollLogs(){
  fetch("/api/logs?since="+conLastSeq)
    .then(function(r){return r.json();})
    .then(function(entries){
      if(!Array.isArray(entries)) return;
      entries.forEach(function(e){appendConsole(e.text);if(e.seq>conLastSeq) conLastSeq=e.seq;});
    }).catch(function(){});
}
setInterval(function(){if(!busy) pollLogs();}, 1800);
pollLogs();

// ── Image paste ──────────────────────────────────────────────
var pendingImgs=[];
var imgStrip=document.getElementById("img-strip");
var pasteHint=document.getElementById("paste-hint");

function renderStrip(){
  if(!pendingImgs.length){imgStrip.className="";imgStrip.innerHTML="";return;}
  imgStrip.className="has-imgs";
  imgStrip.innerHTML=pendingImgs.map(function(u,i){
    return "<div class=\"img-th\"><img src=\""+u+"\"><button class=\"rm\" onclick=\"rmImg("+i+")\">&#x2715;</button></div>";
  }).join("");
}
function rmImg(i){pendingImgs.splice(i,1);renderStrip();}

function flashPaste(){pasteHint.classList.add("show");setTimeout(function(){pasteHint.classList.remove("show");},1800);}

document.addEventListener("paste",function(e){
  if(!e.clipboardData) return;
  var got=false;
  for(var i=0;i<e.clipboardData.items.length;i++){
    var item=e.clipboardData.items[i];
    if(item.type.startsWith("image/")){
      e.preventDefault(); got=true;
      var reader=new FileReader();
      (function(r){r.onload=function(ev){pendingImgs.push(ev.target.result);renderStrip();flashPaste();};r.readAsDataURL(item.getAsFile());})(reader);
    }
  }
  if(got) ibox.focus();
});

// ── Input auto-grow ──────────────────────────────────────────
ibox.addEventListener("input",function(){
  ibox.style.height="auto";
  ibox.style.height=Math.min(ibox.scrollHeight,180)+"px";
});
ibox.addEventListener("keydown",function(e){
  if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendMsg();}
});

// ── Send message ─────────────────────────────────────────────
function sendMsg(){
  if(busy) return;
  var txt=ibox.value.trim();
  if(!txt&&!pendingImgs.length) return;
  ibox.value=""; ibox.style.height="auto";

  var snapImgs=pendingImgs.slice();
  pendingImgs=[]; renderStrip();

  var imgHtml=snapImgs.map(function(u){return "<img src=\""+u+"\">";}).join("");
  addMsg("user",h(txt)+imgHtml);
  var aiEl=addMsg("ai","<div class='spinner'></div>","ai-pending");
  var aiBubble=aiEl.querySelector(".bubble");
  var aiText="", aiStarted=false;
  setBusy(true);

  var xhr=new XMLHttpRequest();
  abortCtrl={abort:function(){xhr.abort();}};
  xhr.open("POST","/api/chat");
  xhr.setRequestHeader("Content-Type","application/json");

  var offset=0;
  xhr.onprogress=function(){
    var raw=xhr.responseText;
    var chunk=raw.slice(offset); offset=raw.length;
    chunk.split("\n").forEach(function(line){
      if(!line.startsWith("data: ")) return;
      try{
        var p=JSON.parse(line.slice(6));
        if(p.type==="text"){
          if(!aiStarted){aiBubble.innerHTML="";aiStarted=true;}
          aiText+=p.data;
          tokenChars+=p.data.length;
          rateWindow.push({t:Date.now(),chars:tokenChars});
          aiBubble.innerHTML=md(aiText);
          if(autoScroll) chat.scrollTop=chat.scrollHeight;
        }
        if(p.type==="error"){
          if(!aiStarted){aiBubble.innerHTML="";aiStarted=true;}
          aiBubble.innerHTML+="<span style=\"color:var(--red)\">"+h(p.data)+"</span>";
          if(autoScroll) chat.scrollTop=chat.scrollHeight;
        }
        if(p.type==="log") appendConsole(p.data);
        if(p.type==="done"){
          var el=document.getElementById("ai-pending");
          if(el) el.id="";
          if(!aiStarted) aiBubble.innerHTML="<em style=\"color:var(--tx3)\">No response.</em>";
          currentMsgs.push({role:"ai",html:aiBubble.innerHTML,text:aiText});
          isFirst=false;
          setBusy(false,aiText);
          saveCurrentConv();
          if(document.hidden||!document.hasFocus()) document.title='● '+originalTitle;
          if(autoScroll) chat.scrollTop=chat.scrollHeight;
          pollLogs();
        }
      }catch(err){}
    });
  };
  xhr.onerror=function(){var el=document.getElementById("ai-pending");if(el)el.id="";setBusy(false,null);};
  xhr.onabort=function(){
    var el=document.getElementById("ai-pending");if(el)el.id="";
    busy=false;sbtn.disabled=false;stpBtn.disabled=true;
    dot.className="";pill.className="";sLbl.textContent="Stopped";stxt.textContent="Stopped";
    sidebarDone(true,null);
    saveCurrentConv();
  };

  if (!activeConvId) { activeConvId = 'conv_' + Date.now(); }
  xhr.send(JSON.stringify({
    prompt:txt,first:isFirst,convId:activeConvId,
    images:snapImgs.map(function(u){return u.split(",")[1];}),
    systemPrompt:sysPromptVal||undefined,
    permissionMode:permMode,
    model:selectedModel||undefined,
    thinkingBudget:thinkingEnabled?thinkingBudget:undefined
  }));
}

function stopIt(){
  if(abortCtrl){abortCtrl.abort();abortCtrl=null;}
  fetch("/api/stop",{method:"POST"}).catch(function(){});
}
function newChat(){
  saveCurrentConv(); isFirst=true; currentMsgs=[]; activeConvId=null;
  chatStats={tokens:0,ms:0,responses:0};
  recomputeTotals(); renderAggStats();
  sys("-- New conversation --"); renderConvList();
}
function clearChat(){chat.innerHTML="";currentMsgs=[];}
function toggleSbPanel(h){
  var p=h.closest('.sb-panel');
  if(!p) return;
  p.classList.toggle('acc-closed');
  try{
    var saved=JSON.parse(localStorage.getItem('cc-sb-panels')||'{}');
    saved[p.dataset.panel]=p.classList.contains('acc-closed')?'0':'1';
    localStorage.setItem('cc-sb-panels',JSON.stringify(saved));
  }catch(e){}
}
function loadHistory(){
  fetch("/api/history").then(function(r){return r.json();}).then(function(msgs){
    if(!Array.isArray(msgs)||!msgs.length){sys("No terminal history found for this directory.");return;}
    sys("── Terminal history ──");
    // Use a dummy id so addMsg does not push into currentMsgs (display-only)
    msgs.forEach(function(m,i){
      if(m.role==="user") addMsg("user",h(m.text),"hist-"+i);
      else if(m.role==="assistant") addMsg("ai",md(m.text),"hist-"+i);
    });
    isFirst=false; // next send continues this conversation thread
    sys("── "+msgs.length+" messages loaded · continue chatting below ──");
  }).catch(function(){sys("Could not load terminal history.");});
}
function browseDir(){
  fetch("/api/browse",{method:"POST"}).then(function(r){return r.json();}).then(function(d){
    if(d.dir){dirLbl.textContent=d.dir;dirLbl.title=d.dir;updateOptDir(d.dir);sys("Working directory -> "+d.dir);}
  });
}
function openDir(){fetch("/api/open-dir",{method:"POST"});}
function restartServer(){
  if(!confirm("Restart the server?")) return;
  fetch("/api/restart",{method:"POST"}).catch(function(){});
  document.body.innerHTML="<div style='display:flex;height:100vh;align-items:center;justify-content:center;background:#000308;color:#d6f4ff;font-family:Segoe UI,sans-serif;flex-direction:column;gap:16px'>"
    +"<div style='font-size:.85rem;color:#00d4ff;text-transform:uppercase;letter-spacing:3px;text-shadow:0 0 14px rgba(0,212,255,.6)'>&#x21BB; Reinitializing system</div>"
    +"<div id='rs-dots' style='display:flex;gap:6px'>"
    +"<span class='rs-dot' style='width:6px;height:6px;border-radius:50%;background:#00d4ff;animation:rsp .9s ease-in-out infinite'></span>"
    +"<span class='rs-dot' style='width:6px;height:6px;border-radius:50%;background:#00d4ff;animation:rsp .9s ease-in-out .3s infinite'></span>"
    +"<span class='rs-dot' style='width:6px;height:6px;border-radius:50%;background:#00d4ff;animation:rsp .9s ease-in-out .6s infinite'></span>"
    +"</div>"
    +"<div id='rs-msg' style='color:#38606f;font-size:.58rem;text-transform:uppercase;letter-spacing:2px'>Waiting for server…</div>"
    +"<style>@keyframes rsp{0%,100%{opacity:.2;transform:scale(.7)}50%{opacity:1;transform:scale(1)}}</style>"
    +"</div>";
  var attempts=0;
  var poll=setInterval(function(){
    attempts++;
    var msg=document.getElementById("rs-msg");
    if(msg) msg.textContent="Waiting for server… ("+attempts+"s)";
    fetch("/api/state",{cache:"no-store"})
      .then(function(r){ if(r.ok){ clearInterval(poll); location.reload(); } })
      .catch(function(){});
  },1000);
  setTimeout(function(){ clearInterval(poll); location.reload(); },60000);
}
function shutdownServer(){
  if(!confirm("Shut down the ClaudioUi server?")) return;
  fetch("/api/shutdown",{method:"POST"}).catch(function(){});
  document.body.innerHTML="<div style='display:flex;height:100vh;align-items:center;justify-content:center;background:#000308;color:#38606f;font-family:Segoe UI,sans-serif;font-size:.7rem;text-transform:uppercase;letter-spacing:2.5px'>System offline &mdash; close this tab</div>";
}

// ── Server health check ──────────────────────────────────────
var srvPanelVal=document.getElementById("srv-panel-val");
var srvPdot=document.getElementById("srv-pdot");
var srvFails=0;

function openSrvBrowser(){ window.open("http://localhost:8765"); }

function checkSrvHealth(){
  if(busy) return;
  var ctrl=typeof AbortSignal!=="undefined"&&AbortSignal.timeout?{signal:AbortSignal.timeout(3000)}:{};
  fetch("/api/state",ctrl)
    .then(function(r){
      if(r.ok){
        srvFails=0;
        if(srvPanelVal){srvPanelVal.textContent="Running";srvPanelVal.style.color="var(--green)";}
        if(srvPdot) srvPdot.className="srv-pdot";
      } else { onSrvFail(); }
    })
    .catch(function(){ onSrvFail(); });
}
function onSrvFail(){
  srvFails++;
  if(srvPanelVal){srvPanelVal.textContent=srvFails>=2?"Down":"No response";srvPanelVal.style.color="var(--red)";}
  if(srvPdot) srvPdot.className="srv-pdot err";
}
setInterval(checkSrvHealth,7000);

// ── Dynamic panel auto-refresh ────────────────────────────────
var originalTitle=document.title;

function isPanelOpen(id){
  var panel=document.querySelector('[data-panel="'+id+'"]');
  if(!panel) return false;
  var body=panel.querySelector('.acc-body');
  return body&&!body.classList.contains('closed');
}
function refreshOpenPanels(){
  if(isPanelOpen('hooks'))   loadHooks();
  if(isPanelOpen('mcps'))    loadMCPs();
  if(isPanelOpen('plugins')) loadPlugins();
  if(isPanelOpen('agents'))  listAgents();
}
// Refresh open panels every 30s; conv timestamps every 60s
setInterval(refreshOpenPanels, 30000);
setInterval(renderConvList,     60000);

// Cross-tab localStorage sync
window.addEventListener('storage', function(e){
  if(e.key===CONV_KEY)      renderConvList();
  if(e.key==='cc-agents')   listAgents();
  if(e.key==='cc-model'){
    selectedModel=e.newValue||"";
    var sel=document.getElementById('model-sel');
    if(sel) sel.value=selectedModel;
    _updateModelNote();
  }
});

// Refresh everything when tab becomes active again
document.addEventListener('visibilitychange', function(){
  if(!document.hidden){
    document.title=originalTitle;
    checkSrvHealth();
    refreshOpenPanels();
    renderConvList();
  }
});

// Clear title badge on window focus
window.addEventListener('focus', function(){ document.title=originalTitle; });

// ── Models ────────────────────────────────────────────────────
var selectedModel="";
var defaultModelName="";

function _setDefaultOptionLabel(name){
  var sel=document.getElementById("model-sel");
  if(!sel) return;
  var opt=sel.querySelector('option[value=""]');
  if(opt) opt.textContent=name?"Default ("+name+")":"Default";
}

function _updateModelNote(){
  var note=document.getElementById("model-note");
  if(!note) return;
  if(selectedModel) note.textContent="Active: "+selectedModel;
  else note.textContent=defaultModelName?"Active: "+defaultModelName+" (default)":"Using default model";
}

function applyModel(v){
  selectedModel=v;
  try{localStorage.setItem("cc-model",v);}catch(e){}
  _updateModelNote();
  sys("Model → "+(v||"default ("+defaultModelName+")"));
}

function restoreModel(){
  try{selectedModel=localStorage.getItem("cc-model")||"";}catch(e){}
  var sel=document.getElementById("model-sel");
  if(sel&&selectedModel) sel.value=selectedModel;
  _updateModelNote();
}

function fetchDefaultModel(){
  fetch("/api/settings").then(function(r){return r.json();}).then(function(d){
    if(!d) return;
    var m=d.model||"";
    if(m){
      defaultModelName=m;
      _setDefaultOptionLabel(m);
      _updateModelNote();
    }
  }).catch(function(){});
}

// ── Agents ────────────────────────────────────────────────────
var agentWindows=[];
function spawnAgent(){
  window.open(window.location.href,"_blank");
  agentWindows.push({id:Date.now(),opened:new Date().toLocaleTimeString()});
  try{localStorage.setItem("cc-agents",JSON.stringify(agentWindows.slice(-20)));}catch(e){}
  renderAgentList();
}
function listAgents(){
  try{agentWindows=JSON.parse(localStorage.getItem("cc-agents")||"[]");}catch(e){agentWindows=[];}
  renderAgentList();
}
function renderAgentList(){
  var el=document.getElementById("agent-list");
  if(!el) return;
  if(!agentWindows.length){el.innerHTML="<div class='pi-item'><span class='pi-detail'>No tracked agents.</span></div>";return;}
  el.innerHTML=agentWindows.slice(-8).map(function(a,i){
    return "<div class='pi-item'><span class='pi-dot'></span><div><div class='pi-name'>Agent "+(i+1)+"</div><div class='pi-detail'>Opened "+a.opened+"</div></div></div>";
  }).join("");
}

// ── Settings reader (shared by Hooks + MCPs) ──────────────────
function _loadSettings(cb){
  fetch("/api/settings").then(function(r){return r.json();}).then(cb).catch(function(){cb(null);});
}

// ── Plugins ───────────────────────────────────────────────────
function loadPlugins(){
  var el=document.getElementById("plugin-list");
  if(el) el.textContent="Detecting...";
  fetch("/api/plugins").then(function(r){return r.json();}).then(function(d){
    if(!el) return;
    if(d.plugins&&d.plugins.length)
      el.innerHTML=d.plugins.map(function(p){return "<div class='pi-item'><span class='pi-dot'></span><div class='pi-name'>"+p+"</div></div>";}).join("");
    else el.innerHTML="<div class='pi-item'><span class='pi-detail'>No plugins detected.</span></div>";
  }).catch(function(){if(el) el.textContent="Error loading plugins.";});
}

// ── Hooks ─────────────────────────────────────────────────────
function loadHooks(){
  var el=document.getElementById("hooks-list");
  if(el) el.textContent="Loading...";
  _loadSettings(function(d){
    if(!el) return;
    if(!d){el.textContent="Could not read settings.json.";return;}
    var hooks=d.hooks||{};
    var keys=Object.keys(hooks);
    if(!keys.length){el.innerHTML="<div class='pi-item'><span class='pi-detail'>No hooks configured.</span></div>";return;}
    el.innerHTML=keys.map(function(k){
      var items=hooks[k];
      var rows=Array.isArray(items)?items.map(function(h){
        var cmd=h.command||(h.hooks&&h.hooks[0]&&h.hooks[0].command)||JSON.stringify(h);
        return "<div class='pi-item'><span class='pi-dot'></span><div><div class='pi-name'>"+k+"</div><div class='pi-detail'>"+cmd+"</div></div></div>";
      }).join(""):"";
      return rows;
    }).join("");
  });
}

// ── MCPs ──────────────────────────────────────────────────────
function loadMCPs(){
  var el=document.getElementById("mcp-list");
  if(el) el.textContent="Loading...";
  _loadSettings(function(d){
    if(!el) return;
    if(!d){el.textContent="Could not read settings.json.";return;}
    var mcps=d.mcpServers||{};
    var keys=Object.keys(mcps);
    if(!keys.length){el.innerHTML="<div class='pi-item'><span class='pi-detail'>No MCP servers configured.</span></div>";return;}
    el.innerHTML=keys.map(function(k){
      var s=mcps[k];
      var detail=s.command||(s.url||s.type||"configured");
      return "<div class='pi-item'><span class='pi-dot'></span><div><div class='pi-name'>"+k+"</div><div class='pi-detail'>"+detail+"</div></div></div>";
    }).join("");
  });
}

// ── Loops ─────────────────────────────────────────────────────
var loopTimer=null,loopCount=0;
function startLoop(){
  var pEl=document.getElementById("loop-prompt");
  var iEl=document.getElementById("loop-interval");
  var sEl=document.getElementById("loop-status");
  var prompt=pEl?pEl.value.trim():"";
  var secs=iEl?Math.max(10,parseInt(iEl.value)||300):300;
  if(!prompt){sys("Loop: enter a prompt first.");return;}
  if(loopTimer) clearInterval(loopTimer);
  loopCount=0;
  var sb=document.getElementById("loop-start-btn");
  var eb=document.getElementById("loop-stop-btn");
  if(sb){sb.disabled=true;sb.style.opacity=".4";}
  if(eb){eb.disabled=false;eb.style.opacity="1";}
  function tick(){
    loopCount++;
    if(sEl) sEl.textContent="Run #"+loopCount+" — "+new Date().toLocaleTimeString();
    ibox.value=prompt;
    sendMsg();
  }
  tick();
  loopTimer=setInterval(tick,secs*1000);
  sys("Loop started — every "+secs+"s");
}
function stopLoop(){
  if(loopTimer){clearInterval(loopTimer);loopTimer=null;}
  var sEl=document.getElementById("loop-status");
  if(sEl) sEl.textContent="Stopped after "+loopCount+" run"+(loopCount===1?"":"s")+".";
  var sb=document.getElementById("loop-start-btn");
  var eb=document.getElementById("loop-stop-btn");
  if(sb){sb.disabled=false;sb.style.opacity="1";}
  if(eb){eb.disabled=true;eb.style.opacity=".4";}
  sys("Loop stopped.");
}

// ── Thinking ──────────────────────────────────────────────────
var thinkingEnabled=false,thinkingBudget=5000;
function applyThinking(){
  var chk=document.getElementById("thinking-chk");
  var row=document.getElementById("thinking-budget-row");
  var note=document.getElementById("thinking-note");
  thinkingEnabled=chk?chk.checked:false;
  if(row) row.style.cssText=thinkingEnabled?"opacity:1;pointer-events:auto":"opacity:.4;pointer-events:none";
  if(note) note.textContent=thinkingEnabled
    ?"On — budget: "+thinkingBudget+" tokens"
    :"Off — standard response mode";
  try{localStorage.setItem("cc-thinking",JSON.stringify({enabled:thinkingEnabled,budget:thinkingBudget}));}catch(e){}
}
function restoreThinking(){
  try{
    var d=JSON.parse(localStorage.getItem("cc-thinking")||"{}");
    if(d.budget) thinkingBudget=d.budget;
    if(d.enabled){
      thinkingEnabled=true;
      var chk=document.getElementById("thinking-chk");
      if(chk) chk.checked=true;
      var rng=document.getElementById("thinking-range");
      if(rng){rng.value=thinkingBudget;document.getElementById("thinking-val").textContent=thinkingBudget;}
      applyThinking();
    }
  }catch(e){}
}

// ── Slash Commands ────────────────────────────────────────────
function runSlash(cmd){
  var c=(cmd||"").trim();
  if(!c) return;
  ibox.value=c;
  sendMsg();
}

// ── Init ─────────────────────────────────────────────────────
restoreColWidths();
restoreSidebars();
initColResize("lsb-resize",lsb,     "--lsb-w","cc-lsb-w", 1,LSB_DEF);
initColResize("rsb-resize",sidebar, "--rsb-w","cc-rsb-w",-1,RSB_DEF);
restorePanelLayout();
initAllPanels();
restoreModel();
fetchDefaultModel();
restoreThinking();
listAgents();
renderConvList();
(function(){var _c=getConvs();if(_c.length){var _l=_c.slice().sort(function(a,b){return b.ts-a.ts;})[0];loadConv(_l.id,true);}})();
spTa.addEventListener("input",refreshSpState);
refreshSpState();
recomputeTotals();
renderAggStats();
restorePermMode();
</script>
</body>
</html>
'@

# ─── Send HTTP response ─────────────────────────────────────────
function Send-Http {
    param($ctx, [int]$code = 200, [string]$type = "application/json", [string]$body = "{}")
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
        $ctx.Response.StatusCode      = $code
        $ctx.Response.ContentType     = $type
        $ctx.Response.ContentLength64 = $bytes.Length
        try { $ctx.Response.AddHeader("Access-Control-Allow-Origin", "*") } catch {}
        $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    } catch { Write-Log "Send-Http error: $_" }
    finally  { try { $ctx.Response.OutputStream.Close() } catch {} }
}

# ─── Start server ───────────────────────────────────────────────
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://localhost:$Port/")

try {
    $listener.Start()
    Write-Log "Server started on port $Port"
} catch {
    Write-Log "FATAL: Cannot bind port $Port - $_"
    exit 1
}

$url = "http://localhost:$Port"
Write-Host ""
Write-Host "  ClaudioUi -> $url" -ForegroundColor Cyan
Write-Host "  Close this window to stop." -ForegroundColor DarkGray
Write-Host ""
if (-not $NoOpen) { Start-Process $url }

# ─── Request loop (blocking GetContext — most reliable) ─────────
try {
    while ($listener.IsListening) {
        # GetContext blocks until a request arrives
        $ctx = $null
        try { $ctx = $listener.GetContext() }
        catch { Write-Log "GetContext error: $_"; if (-not $listener.IsListening) { break }; continue }

        $path   = $ctx.Request.Url.LocalPath
        $method = $ctx.Request.HttpMethod
        if (-not ($method -eq "GET" -and $path -eq "/api/logs")) { Write-Log "$method $path" }

        try {
            # GET /
            if ($method -eq "GET" -and ($path -eq "/" -or $path -eq "/index.html")) {
                $b = [System.Text.Encoding]::UTF8.GetBytes($HTML)
                $ctx.Response.ContentType     = "text/html; charset=utf-8"
                $ctx.Response.ContentLength64 = $b.Length
                $ctx.Response.OutputStream.Write($b, 0, $b.Length)
                $ctx.Response.OutputStream.Close()
                continue
            }

            # GET /api/state
            if ($method -eq "GET" -and $path -eq "/api/state") {
                Send-Http $ctx -body ([PSCustomObject]@{ dir = $Script:WorkDir } | ConvertTo-Json -Compress)
                continue
            }

            # GET /api/history — read most recent Claude Code terminal conversation
            if ($method -eq "GET" -and $path -eq "/api/history") {
                $projectName = ($Script:WorkDir -replace '[^a-zA-Z0-9]', '-')
                $claudeProjectDir = Join-Path $env:USERPROFILE ".claude\projects\$projectName"
                $messages = [System.Collections.Generic.List[object]]::new()
                if (Test-Path $claudeProjectDir) {
                    $jsonlFile = Get-ChildItem $claudeProjectDir -Filter "*.jsonl" -File -ErrorAction SilentlyContinue |
                        Sort-Object LastWriteTime -Descending |
                        Select-Object -First 1
                    if ($jsonlFile) {
                        Get-Content $jsonlFile.FullName -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object {
                            if (-not $_) { return }
                            try {
                                $obj = $_ | ConvertFrom-Json
                                if ($obj.type -eq "user" -and $obj.message.role -eq "user") {
                                    $content = $obj.message.content
                                    if ($content -is [string] -and $content.Trim()) {
                                        $messages.Add([PSCustomObject]@{ role = "user"; text = $content.Trim() })
                                    }
                                } elseif ($obj.type -eq "assistant" -and $obj.message.role -eq "assistant") {
                                    $text = ""
                                    foreach ($block in $obj.message.content) {
                                        if ($block.type -eq "text") { $text += $block.text }
                                    }
                                    if ($text.Trim()) {
                                        $messages.Add([PSCustomObject]@{ role = "assistant"; text = $text.Trim() })
                                    }
                                }
                            } catch {}
                        }
                    }
                }
                # Force array even for 0 or 1 item — ConvertTo-Json collapses single items to objects
                $json = ConvertTo-Json -InputObject ([object[]]$messages.ToArray()) -Compress -Depth 5
                Send-Http $ctx -body $json
                continue
            }

            # GET /api/logs?since=N
            if ($method -eq "GET" -and $path -eq "/api/logs") {
                $sinceRaw = $ctx.Request.QueryString["since"]
                $sinceSeq = if ($sinceRaw) { [int]$sinceRaw } else { -1 }
                $slice = @($Script:LogEntries | Where-Object { $_.seq -gt $sinceSeq })
                $body  = if ($slice.Count -gt 0) { $slice | ConvertTo-Json -Compress } else { "[]" }
                Send-Http $ctx -body $body
                continue
            }

            # POST /api/chat — streaming SSE
            if ($method -eq "POST" -and $path -eq "/api/chat") {
                $reader = [System.IO.StreamReader]::new($ctx.Request.InputStream, [System.Text.Encoding]::UTF8)
                $data   = $reader.ReadToEnd() | ConvertFrom-Json

                # Save any pasted images to temp files and append paths to prompt
                $tempFiles = @()
                if ($data.images) {
                    foreach ($b64 in $data.images) {
                        try {
                            $bytes   = [Convert]::FromBase64String($b64)
                            $tmpPath = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "claudioui_img_$(Get-Date -Format 'yyyyMMddHHmmssfff').png")
                            [System.IO.File]::WriteAllBytes($tmpPath, $bytes)
                            $tempFiles += $tmpPath
                        } catch { Write-Log "Image save error: $_" }
                    }
                }
                $prompt = $data.prompt
                if ($tempFiles.Count -gt 0) {
                    $imgNotes = ($tempFiles | ForEach-Object { "[Attached image: $_]" }) -join "`n"
                    $prompt   = $prompt + "`n`n" + $imgNotes
                }

                # Set up SSE streaming response
                $ctx.Response.ContentType = "text/event-stream; charset=utf-8"
                $ctx.Response.SendChunked = $true
                try { $ctx.Response.Headers.Add("Cache-Control", "no-cache") } catch {}
                $ostream = $ctx.Response.OutputStream
                $utf8    = [System.Text.Encoding]::UTF8

                # Emit an SSE event (throws on write error so caller can break/catch)
                $emitSSE = {
                    param([string]$type, [string]$val = "")
                    $obj   = [PSCustomObject]@{ type = $type; data = $val }
                    $json  = $obj | ConvertTo-Json -Compress
                    $bytes = $utf8.GetBytes("data: $json`n`n")
                    $ostream.Write($bytes, 0, $bytes.Length)
                    $ostream.Flush()
                }

                # Per-conversation session tracking — never use --continue (it bleeds into terminal)
                $convId = if ($data.convId) { [string]$data.convId } else { "" }
                $projectsDirPath = Join-Path $env:USERPROFILE ".claude\projects"
                $preSessions = [System.Collections.Generic.HashSet[string]]::new()
                if ($data.first -and (Test-Path $projectsDirPath)) {
                    Get-ChildItem $projectsDirPath -Recurse -Filter "*.jsonl" -ErrorAction SilentlyContinue |
                        ForEach-Object { $null = $preSessions.Add($_.BaseName) }
                }

                # Build argument string — always quote each part and escape inner quotes
                $argParts = [System.Collections.Generic.List[string]]::new()
                $argParts.Add("-p")
                # Permission mode. Whitelist only - this value arrives from the browser
                # and lands on a command line, so never pass it through unchecked.
                # "default" (or anything unknown) sends no flag at all.
                $validModes = @('acceptEdits','bypassPermissions','plan')
                if ($data.permissionMode -and $validModes -contains $data.permissionMode) {
                    $argParts.Add("--permission-mode")
                    $argParts.Add([string]$data.permissionMode)
                    Write-Log "permission-mode: $($data.permissionMode)"
                }
                if (-not $data.first -and $convId -and $Script:Sessions.ContainsKey($convId)) {
                    $argParts.Add("--resume")
                    $argParts.Add($Script:Sessions[$convId])
                    Write-Log "resume session: $($Script:Sessions[$convId]) for conv $convId"
                }
                if ($data.systemPrompt) {
                    $argParts.Add("--system-prompt")
                    $argParts.Add($data.systemPrompt)
                }
                if ($data.model) {
                    $argParts.Add("--model")
                    $argParts.Add([string]$data.model)
                    Write-Log "model: $($data.model)"
                }
                if ($data.thinkingBudget -gt 0) {
                    $argParts.Add("--thinking-budget")
                    $argParts.Add([string][int]$data.thinkingBudget)
                    Write-Log "thinking-budget: $($data.thinkingBudget)"
                }
                $argParts.Add($prompt)
                $argStr = ($argParts | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join ' '

                $proc = $null
                try {
                    $psi = [System.Diagnostics.ProcessStartInfo]::new()
                    $psi.FileName            = "claude"
                    $psi.Arguments           = $argStr
                    $psi.UseShellExecute     = $false
                    $psi.RedirectStandardOutput = $true
                    $psi.RedirectStandardError  = $true   # drained async -> no deadlock
                    $psi.RedirectStandardInput  = $true   # fully detach from parent terminal
                    $psi.CreateNoWindow              = $true
                    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
                    $psi.StandardErrorEncoding  = [System.Text.Encoding]::UTF8
                    $psi.WorkingDirectory    = $Script:WorkDir

                    $proc = [System.Diagnostics.Process]::new()
                    $proc.StartInfo = $psi
                    $Script:CurrentProc = $proc
                    $null = $proc.Start()
                    Write-Log "claude started (pid $($proc.Id))"
                    try { & $emitSSE "log" "claude started (pid $($proc.Id))" } catch {}

                    # Drain stderr on a background thread so stdout never blocks
                    $stderrTask = $proc.StandardError.ReadToEndAsync()

                    while (-not $proc.StandardOutput.EndOfStream) {
                        $line = $proc.StandardOutput.ReadLine()
                        if ($null -ne $line) {
                            $clean = Strip-Ansi $line
                            try { & $emitSSE "text" ($clean + "`n") } catch { break }
                            try { & $emitSSE "log"  $clean           } catch {}
                        }
                    }
                    $proc.WaitForExit()
                    $rc = $proc.ExitCode

                    # Emit stderr lines to console
                    $stderrText = $stderrTask.Result
                    if ($stderrText) {
                        ($stderrText -split "`r?`n") | ForEach-Object {
                            $se = Strip-Ansi $_.Trim()
                            if ($se) {
                                Write-Log "stderr: $se"
                                try { & $emitSSE "log" "stderr: $se" } catch {}
                            }
                        }
                    }

                    # Capture new session ID after first message so subsequent messages use --resume
                    if ($data.first -and $rc -eq 0 -and $convId -and (Test-Path $projectsDirPath)) {
                        $newSession = Get-ChildItem $projectsDirPath -Recurse -Filter "*.jsonl" -ErrorAction SilentlyContinue |
                            Where-Object { -not $preSessions.Contains($_.BaseName) } |
                            Sort-Object LastWriteTime -Descending |
                            Select-Object -First 1
                        if ($newSession) {
                            $Script:Sessions[$convId] = $newSession.BaseName
                            Write-Log "captured session: $($newSession.BaseName) for conv $convId"
                        }
                    }

                    Write-Log "claude exited rc=$rc"
                    try { & $emitSSE "log" "claude exited rc=$rc" } catch {}
                    if ($rc -notin @(0, 1, -1073741816)) {
                        try { & $emitSSE "error" ("`n[exited $rc]`n") } catch {}
                    }
                } catch {
                    Write-Log "Chat stream error: $_"
                    try { & $emitSSE "error" ("`nError: $_`n") } catch {}
                } finally {
                    try { & $emitSSE "done" "" } catch {}
                    try { $ostream.Flush(); $ostream.Close() } catch {}
                    $Script:CurrentProc = $null
                    if ($proc -and -not $proc.HasExited) { try { $proc.Kill() } catch {} }
                    foreach ($p in $tempFiles) { try { Remove-Item $p -Force -ErrorAction SilentlyContinue } catch {} }
                }
                continue
            }

            # POST /api/stop
            if ($method -eq "POST" -and $path -eq "/api/stop") {
                if ($Script:CurrentProc -and -not $Script:CurrentProc.HasExited) {
                    try { $Script:CurrentProc.Kill() } catch {}
                }
                Send-Http $ctx -body '{"ok":true}'
                continue
            }

            # POST /api/shutdown — gracefully stop the server
            if ($method -eq "POST" -and $path -eq "/api/shutdown") {
                Send-Http $ctx -body '{"ok":true}'
                Write-Log "Shutdown requested from browser"
                $listener.Stop()
                break
            }

            # POST /api/restart — relaunch and stop current instance
            if ($method -eq "POST" -and $path -eq "/api/restart") {
                Send-Http $ctx -body '{"ok":true}'
                Write-Log "Restart requested from browser"
                Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$PSCommandPath`" -NoOpen" -WorkingDirectory $PSScriptRoot
                Start-Sleep -Milliseconds 400
                $listener.Stop()
                break
            }

            # POST /api/browse — use Shell COM (works without STA)
            if ($method -eq "POST" -and $path -eq "/api/browse") {
                try {
                    $shell  = New-Object -ComObject Shell.Application
                    $folder = $shell.BrowseForFolder(0, "Choose working directory", 0, $Script:WorkDir)
                    if ($folder) { $Script:WorkDir = $folder.Self.Path }
                } catch { Write-Log "Browse error: $_" }
                Send-Http $ctx -body ([PSCustomObject]@{ dir = $Script:WorkDir } | ConvertTo-Json -Compress)
                continue
            }

            # GET /api/settings — read merged global + project settings.json
            if ($method -eq "GET" -and $path -eq "/api/settings") {
                $merged = [PSCustomObject]@{}
                $settingsPaths = @(
                    (Join-Path $env:USERPROFILE ".claude\settings.json"),
                    (Join-Path $Script:WorkDir ".claude\settings.json")
                )
                foreach ($sp in $settingsPaths) {
                    if (Test-Path $sp) {
                        try {
                            $parsed = Get-Content $sp -Raw -Encoding UTF8 | ConvertFrom-Json
                            foreach ($prop in $parsed.PSObject.Properties) {
                                $merged | Add-Member -NotePropertyName $prop.Name -NotePropertyValue $prop.Value -Force
                            }
                        } catch { Write-Log "Settings parse error ($sp): $_" }
                    }
                }
                Send-Http $ctx -body ($merged | ConvertTo-Json -Compress -Depth 10)
                continue
            }

            # GET /api/plugins — detect installed Claude plugins
            if ($method -eq "GET" -and $path -eq "/api/plugins") {
                $plugins = [System.Collections.Generic.List[string]]::new()
                # Check known plugin paths
                $claudeMemPath = Join-Path $env:USERPROFILE ".claude-mem"
                if (Test-Path $claudeMemPath) { $plugins.Add("claude-mem") }
                # Scan npm global for claude-* packages
                try {
                    $npmOut = & npm list -g --depth=0 --json 2>$null
                    if ($npmOut) {
                        $npmJson = $npmOut | ConvertFrom-Json
                        if ($npmJson.dependencies) {
                            foreach ($dep in $npmJson.dependencies.PSObject.Properties) {
                                if ($dep.Name -like "claude*" -and -not $plugins.Contains($dep.Name)) {
                                    $plugins.Add($dep.Name)
                                }
                            }
                        }
                    }
                } catch {}
                Send-Http $ctx -body ([PSCustomObject]@{ plugins = @($plugins) } | ConvertTo-Json -Compress)
                continue
            }

            # POST /api/open-dir
            if ($method -eq "POST" -and $path -eq "/api/open-dir") {
                Start-Process explorer.exe -ArgumentList $Script:WorkDir
                Send-Http $ctx -body '{"ok":true}'
                continue
            }

            # 404
            Send-Http $ctx -code 404 -body '{"error":"not found"}'

        } catch {
            Write-Log "Handler error ($method $path): $_"
            try { Send-Http $ctx -code 500 -body "{`"error`":`"$($_.Exception.Message -replace '"','\"')`"}" } catch {}
        }
    }
} catch {
    Write-Log "Fatal server error: $_"
} finally {
    $listener.Stop()
    Write-Log "Server stopped"
}
