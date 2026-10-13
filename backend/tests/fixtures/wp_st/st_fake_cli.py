r"""WP-ST oracle fake `claude`: replays a real-CLI stream-json fixture (fx\<scenario>.jsonl) line by line.
Scenario = contents of the file named by ST_CTRL, else env ST_SCENARIO, else "ok". ST_SLEEP seconds between lines
(default 0.3). If ST_CALLS is set, one line per invocation is appended to that file. Exit code from fx\exitcodes.json.
Arguments are ignored. Written by Opus for WP-ST; frozen with the oracles."""
import json, os, pathlib, sys, time
FX = pathlib.Path(__file__).resolve().parent / "fx"
ctrl = os.environ.get("ST_CTRL")
name = (pathlib.Path(ctrl).read_text(encoding="utf-8").strip() if ctrl and pathlib.Path(ctrl).exists() else "") or os.environ.get("ST_SCENARIO", "ok")
if os.environ.get("ST_CALLS"):
    with open(os.environ["ST_CALLS"], "a", encoding="utf-8") as f: f.write(name + "\n")
delay = float(os.environ.get("ST_SLEEP", "0.3"))
out = sys.stdout.buffer
for line in (FX / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
    out.write(line.encode("utf-8") + b"\n"); out.flush(); time.sleep(delay)
sys.exit(json.loads((FX / "exitcodes.json").read_text(encoding="utf-8"))[name])
