#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Retired: the GameStudio MCP server is now the Node package runtime/packages/mcp (@gamestudio/mcp).

The old bridge only staged proposals through the dev-server hub. The new server talks to Blender
directly (agents act, protected by undo steps and checkpoints) and to the running game. See
docs/MCP.md. Projects register it in .mcp.json:

    { "mcpServers": { "gamestudio": { "command": "node",
        "args": ["node_modules/@gamestudio/mcp/bin/gamestudio-mcp.js"] } } }

This shim keeps old registrations working: it runs the new server on stdio.

    python tools/mcp/gamestudio_mcp.py --project examples/demo-game
"""

import argparse
import pathlib
import shutil
import subprocess
import sys

SERVER = pathlib.Path(__file__).resolve().parents[2] / "runtime" / "packages" / "mcp" / "bin" / "gamestudio-mcp.js"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=".", help="Path inside the GameStudio project")
    args = parser.parse_args()
    node = shutil.which("node")
    if node is None:
        print("gamestudio_mcp.py: Node.js 22+ is required (the server is runtime/packages/mcp)", file=sys.stderr)
        return 2
    project = pathlib.Path(args.project).resolve()
    return subprocess.call([node, str(SERVER), "--project", str(project)], cwd=project)


if __name__ == "__main__":
    sys.exit(main())
