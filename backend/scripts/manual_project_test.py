"""
Manual smoke test: create a project, create a conversation in it, then DELETE
BOTH AGAIN.

This script used to leak. It POSTed a project whose system_prompt was
"You always end every single reply with the exact word BANANA in all caps."
and never cleaned up. It was run four times against the live server, so the
database ended up with four identical "Legacy Project" rows, every one of them
carrying that prompt, and the app really did append BANANA to replies. Tracking
that down from the symptom cost hours, because a manual dev script is the last
place anyone looks for live application behaviour.

Two rules now, and they are why this file reads the way it does:

  1. IT CLEANS UP AFTER ITSELF. Deletion runs in `finally`, so it happens even
     if an assertion fails or the run is interrupted. Pass --keep to opt out
     deliberately, which prints what was left behind and how to remove it.

  2. THE PROMPT CANNOT CHANGE BEHAVIOUR. It is a marker string, not an
     instruction. A manual test has no business installing a standing order
     into a row the app reads on every turn. If you need to test that
     system_prompt is honoured end to end, do it in a conversation you delete,
     and use --keep only while you are watching.

tools/resync-check.mjs is the model for this pattern.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
KEEP = "--keep" in sys.argv[1:]

# A marker, deliberately inert. It identifies rows this script created without
# telling the model to do anything. Do not turn this back into an instruction.
MARKER = "[manual_project_test.py scratch project - safe to delete]"


def _req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def post(path, body):
    return _req("POST", path, body)


def delete(path):
    try:
        _req("DELETE", path)
        return True
    except urllib.error.HTTPError as e:
        print(f"  cleanup FAILED for {path}: HTTP {e.code}", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"  cleanup FAILED for {path}: {e.reason}", file=sys.stderr)
        return False


working_dir = r"C:\Users\hduenas\LLMs\Claude\Projects\GoClaudaddy\app\legacy"
project = None
conv = None

try:
    project = post(
        "/api/projects",
        {
            "name": "SCRATCH - manual_project_test",
            "working_dir": working_dir,
            "system_prompt": MARKER,
        },
    )
    print("project:", project)

    conv = post("/api/conversations", {"project_id": project["id"]})
    print("conversation:", conv)
    print(conv["id"])

finally:
    if KEEP:
        print("\n--keep given, leaving these behind ON PURPOSE:")
        if conv:
            print(f"  conversation {conv['id']}")
        if project:
            print(f"  project      {project['id']}")
        print("Remove them with:")
        if conv:
            print(f"  curl -X DELETE {BASE}/api/conversations/{conv['id']}")
        if project:
            print(f"  curl -X DELETE {BASE}/api/projects/{project['id']}")
    else:
        print("\ncleaning up:")
        ok = True
        # conversation first: deleting the project only SETs project_id to NULL
        # (ON DELETE SET NULL), so doing it the other way round orphans the
        # conversation instead of removing it.
        if conv:
            ok &= delete(f"/api/conversations/{conv['id']}")
            print(f"  conversation {conv['id']} deleted")
        if project:
            ok &= delete(f"/api/projects/{project['id']}")
            print(f"  project      {project['id']} deleted")
        if not ok:
            print("\nCLEANUP INCOMPLETE - remove the rows above by hand.", file=sys.stderr)
            sys.exit(1)
        print("  clean.")
