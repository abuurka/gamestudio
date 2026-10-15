# SPDX-License-Identifier: GPL-3.0-or-later
"""Stands in for `npm run dev` in tests (plain Python, no bpy).

Starts a child process (to check that Blender kills the whole tree), a tiny
hub that answers hello with welcome, and writes .gamestudio/session.json the
way the real dev server does.
"""

import datetime
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time

root = os.getcwd()
gs_dir = os.path.join(root, ".gamestudio")
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
with open(os.path.join(gs_dir, "child.pid"), "w", encoding="utf-8") as f:
    f.write(str(child.pid))

server = socket.create_server(("127.0.0.1", 0))
port = server.getsockname()[1]
token = secrets.token_hex(16)


def serve(conn):
    reader = conn.makefile("rb")
    for line in reader:
        msg = json.loads(line)
        if msg.get("t") == "hello":
            if msg.get("token") == token:
                reply = {"t": "welcome", "protocol": 1, "peer": "e1", "peers": []}
            else:
                reply = {"t": "error", "code": "auth", "msg": "bad token"}
            conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))


time.sleep(0.3)  # pretend to boot
session = {
    "pid": os.getpid(),
    "url": "http://localhost:5173/",
    "editorPort": port,
    "gamePort": port + 1,
    "token": token,
    "startedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
tmp = os.path.join(gs_dir, "session.json.tmp")
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(session, f)
os.replace(tmp, os.path.join(gs_dir, "session.json"))
print("fake dev server ready on", port, "args", sys.argv[1:], flush=True)

while True:
    conn, _addr = server.accept()
    threading.Thread(target=serve, args=(conn,), daemon=True).start()
