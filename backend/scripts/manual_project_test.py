import json
import urllib.request

BASE = "http://127.0.0.1:8765"


def post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


working_dir = r"C:\Users\hduenas\LLMs\Claude\Projects\ClaudioUI\legacy"
project = post(
    "/api/projects",
    {
        "name": "Legacy Project",
        "working_dir": working_dir,
        "system_prompt": "You always end every single reply with the exact word BANANA in all caps.",
    },
)
print("project:", project)

conv = post("/api/conversations", {"project_id": project["id"]})
print("conversation:", conv)
print(conv["id"])
