"""Screenshot a running Streamlit app.

    ./.venv/bin/python scripts/screenshot_app.py http://localhost:8501 out.png

Chrome's `--screenshot` flag cannot do this. It fires on the load event, and a Streamlit page
is empty at that point: the app arrives later over a websocket. Adding `--virtual-time-budget`
makes it worse, because fast-forwarding the clock breaks the websocket handshake. Both were
tried on 2026-09-19 and both returned the grey skeleton.

So this drives Chrome over the DevTools Protocol instead: navigate, wait for the app to paint,
then capture. It exists because `fe-theme-spec.md` verifies FE-1 and FE-2 by screenshot, and
there was no way to take one.

There is no full-page option, and that is not an oversight. Streamlit scrolls an inner
container rather than the document, so the page is always exactly the viewport height and
`captureBeyondViewport` has nothing to reach. To capture a long screen, ask for a tall window:
`--height=2600`. Streamlit lays out against the viewport it is given.
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Streamlit paints its skeleton first, then the app. Two seconds is not enough on a cold run.
SETTLE_SECONDS = 6.0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def page_target(port: int, timeout: float = 20.0) -> str:
    """The websocket URL of the page target, once Chrome is listening."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1).read()
            for t in json.loads(raw):
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"Chrome never exposed a page target on port {port}")


async def capture(ws_url: str, out: str) -> None:
    async with websockets.connect(ws_url, max_size=200 * 1024 * 1024) as ws:
        async def call(method, **params):
            call.n = getattr(call, "n", 0) + 1
            await ws.send(json.dumps({"id": call.n, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == call.n:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result", {})

        await call("Page.enable")
        await asyncio.sleep(SETTLE_SECONDS)
        shot = await call("Page.captureScreenshot", format="png")
        import base64
        with open(out, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    url, out = sys.argv[1], sys.argv[2]
    width = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--width=")), 1440))
    height = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--height=")), 1000))

    port = free_port()
    profile = tempfile.mkdtemp(prefix="shot-profile-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
         f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         f"--window-size={width},{height}", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        asyncio.run(capture(page_target(port), out))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)

    print(f"wrote {out} ({os.path.getsize(out):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
