import asyncio
import json
import sys

import websockets


async def turn(ws, message):
    await ws.send(json.dumps({"type": "send", "message": message, "model": "claude-sonnet-4-6"}))
    while True:
        raw = await ws.recv()
        ev = json.loads(raw)
        print(ev.get("type"), "->", {k: v for k, v in ev.items() if k != "type"})
        if ev.get("type") == "done":
            break


async def main():
    conv_id = sys.argv[1] if len(sys.argv) > 1 else "manual-test-1"
    async with websockets.connect(f"ws://127.0.0.1:8765/ws/chat/{conv_id}") as ws:
        print("=== turn 1 ===")
        await turn(ws, "Reply with exactly the word: PONG")
        print("=== turn 2 (should resume session) ===")
        await turn(ws, "What word did you just say? Reply with just that word.")


asyncio.run(main())
