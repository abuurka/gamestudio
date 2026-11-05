#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The website's large media: tutorial videos and promo clips, kept out of git.

    python tools/site_media.py fetch       # download what docs/ lacks (CI, or a local preview of the site)
    python tools/site_media.py manifest    # after changing a video: rewrite docs/site-media.json from docs/
    python tools/site_media.py check       # exit 1 when a local file differs from the manifest

The files live as assets of the GitHub pre-release tagged `site-media` (a pre-release, so the site's
Download button, which follows /releases/latest, never points at it). docs/site-media.json, which is
in git, lists each file's path under docs/, its size and SHA-256, so a deploy gets exactly the files
the commit names. .github/workflows/pages.yml fetches them before it publishes docs/ to GitHub Pages.

Changing a video: replace the file in docs/, run `manifest`, upload the file to the `site-media`
release (same asset name, replacing the old one), and commit docs/site-media.json.

Asset names are the file names, so every listed file must have a unique name. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MANIFEST = DOCS / "site-media.json"
REPO = "abuurka/gamestudio"
TAG = "site-media"
# What counts as site media: big binaries the site serves. Posters, captions and transcripts stay in git.
# (docs/videos/*.mp4 are local-only MP4 copies of the tutorials, never published.)
PATTERNS = ("videos/*.webm", "media/*.webm", "media/*.mp4")
CHUNK = 1 << 20


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def load() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def write_manifest() -> int:
    files = sorted({p for pattern in PATTERNS for p in DOCS.glob(pattern)})
    names = [p.name for p in files]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        sys.exit(f"[site-media] names must be unique (they are the release's asset names): {', '.join(duplicates)}")
    data = {
        "comment": "Large website files kept out of git; see tools/site_media.py. Rewrite with `python tools/site_media.py manifest`.",
        "release": f"https://github.com/{REPO}/releases/tag/{TAG}",
        "files": [{"path": p.relative_to(DOCS).as_posix(), "size": p.stat().st_size, "sha256": sha256(p)} for p in files],
    }
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    total = sum(f["size"] for f in data["files"])
    print(f"[site-media] {MANIFEST.relative_to(ROOT).as_posix()}: {len(files)} files, {total / 1e6:.1f} MB")
    return 0


def check() -> int:
    bad = 0
    for entry in load()["files"]:
        path = DOCS / entry["path"]
        if not path.is_file():
            print(f"[site-media] missing: docs/{entry['path']} (run: python tools/site_media.py fetch)")
            bad += 1
        elif path.stat().st_size != entry["size"] or sha256(path) != entry["sha256"]:
            print(f"[site-media] differs from the manifest: docs/{entry['path']}")
            bad += 1
    print(f"[site-media] {'all files match' if not bad else f'{bad} file(s) missing or different'}")
    return 1 if bad else 0


def fetch() -> int:
    base = f"https://github.com/{REPO}/releases/download/{TAG}/"
    entries = load()["files"]
    for entry in entries:
        path = DOCS / entry["path"]
        if path.is_file() and path.stat().st_size == entry["size"] and sha256(path) == entry["sha256"]:
            continue
        url = base + pathlib.PurePosixPath(entry["path"]).name
        print(f"[site-media] {url}", flush=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        digest = hashlib.sha256()
        request = urllib.request.Request(url, headers={"User-Agent": "GameStudio-site-media"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response, open(part, "wb") as out:
                while chunk := response.read(CHUNK):
                    out.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != entry["sha256"]:
                sys.exit(f"[site-media] {url}: SHA-256 {digest.hexdigest()} does not match the manifest")
            part.replace(path)
        finally:
            part.unlink(missing_ok=True)
    print(f"[site-media] {len(entries)} files present and verified")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("fetch", "manifest", "check"))
    args = parser.parse_args()
    return {"fetch": fetch, "manifest": write_manifest, "check": check}[args.command]()


if __name__ == "__main__":
    sys.exit(main())
