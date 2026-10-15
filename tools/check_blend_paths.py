# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the .blend files GameStudio ships for absolute paths and user names.

    python tools/check_blend_paths.py                       # the shipped files (shipped_files())
    python tools/check_blend_paths.py dist/gamestudio-X.zip # the .blend files in a built zip
    python tools/check_blend_paths.py PATH...               # .blend files or folders
    python tools/check_blend_paths.py --blender PATH --name EXTRA_NAME

A .blend keeps whatever paths Blender put in it: image, sound and library paths, the render
output, and the folder of every File Browser. Blender opens the Shading workspace's File Browser in
the Documents folder, so a file saved from the factory startup says C:\\Users\\<you>\\Documents.
Each file is checked twice, in Blender in the background (factory settings, a scratch profile):

- its bytes, decompressed: every printable run with a drive letter (C:\\), a network path
  (\\\\server\\), a home folder (/Users/, /home/), ~/, an e-mail address or one of this machine's
  user names (the login, the home folder's name, git's user.name and user.email, and --name).
  Blender writes whole fixed-size string buffers, and a shorter value written over a longer one
  leaves the old tail after the terminator, so this sees what bpy does not.
- bpy: every string and bytes property reachable from bpy.data (screens, areas, spaces,
  ID properties, text lines), so a hit says where it is; a path property that is absolute fails
  too, except Blender's default render output (/tmp\\).

Prints each hit and exits 1 if there is one. Standard library only; Blender is found as
tools/blender_dev.py finds it.
"""

import argparse
import bisect
import getpass
import io
import json
import os
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
# What tools/build_extension.py ships: blender/gamestudio (resources/, project_templates/, ...)
# and the examples.
SHIPPED_ROOTS = (REPO / "blender" / "gamestudio", REPO / "examples")
SKIP_PARTS = {"node_modules", ".gamestudio", "dist", "__pycache__", "_vendor", ".vite"}
MARK = "CHECK_BLEND_PATHS"
# Too generic to mean anyone (CI machines log in as "runner").
GENERIC_NAMES = {"user", "users", "admin", "administrator", "root", "runner", "default", "guest",
                 "public", "owner", "home", "build", "dev"}
ALLOWED_PATHS = {"/tmp/", "/tmp\\"}  # Blender's default render output
PATTERNS = {
    "absolute path": r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]",
    "network path": r"(?<![\\])\\\\[A-Za-z0-9_.$-]{2,}\\",
    "home folder": r"(?<![\w.])/(?:Users|home|Volumes|mnt|media|root|private/var|var/folders)/",
    "home-relative path": r"(?<![\w.])~[\\/]",
    "e-mail address": r"[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
}
# Filled in by Blender at runtime from this computer (bookmarks, drives, studio lights): never in the file.
RUNTIME_PROPS = {"system_folders", "system_bookmarks", "bookmarks", "recent_folders", "selected_studio_light"}
# Big arrays without strings, and what is not data.
SKIP_PROPS = RUNTIME_PROPS | {
    "rna_type", "bl_rna", "depsgraph", "object_instances", "keyconfigs", "vertices", "edges", "polygons",
    "loops", "loop_triangles", "loop_triangle_polygons", "points", "keyframe_points", "pixels",
    "attributes", "color_attributes", "uv_layers", "vertex_colors", "polygon_normals", "vertex_normals",
    "corner_normals", "key_blocks", "data"}
MAX_DEPTH, MAX_ITEMS = 9, 2000


# --- Outside Blender ---------------------------------------------------------------


def shipped_files() -> list[pathlib.Path]:
    return [p for root in SHIPPED_ROOTS for p in blend_files(root)]


def blend_files(folder: pathlib.Path) -> list[pathlib.Path]:
    if folder.is_file():
        return [folder]
    return sorted(p for p in folder.rglob("*.blend") if not SKIP_PARTS & set(p.relative_to(folder).parts))


def machine_names(extra=()) -> list[str]:
    """User names this computer could leave in a file: login, home folder, git identity."""
    found = [*extra, os.environ.get("USERNAME"), os.environ.get("USER"), pathlib.Path.home().name]
    try:
        found.append(getpass.getuser())
    except Exception:
        pass
    for key in ("user.name", "user.email"):
        try:
            value = subprocess.run(["git", "config", "--get", key], cwd=REPO, capture_output=True,
                                   text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        found += [value, *value.split(), value.split("@")[0]] if value else []
    names = {n.strip() for n in found if n and len(n.strip()) >= 3}
    return sorted(n for n in names if n.lower() not in GENERIC_NAMES)


def run_blender(paths: list[pathlib.Path], names: list[str], blender: str | None) -> int:
    sys.path.insert(0, str(REPO / "tools"))
    import blender_dev

    exe = blender_dev.find_blender(blender)
    scratch = REPO / ".dev" / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gs_check_blend_", dir=scratch) as tmp:
        files = []
        for path in paths:
            if path.suffix == ".zip":  # check what a built extension ships
                with zipfile.ZipFile(path) as zf:
                    for member in zf.namelist():
                        if member.endswith(".blend"):
                            target = pathlib.Path(tmp, "zip", path.stem, member)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(zf.read(member))
                            files.append((f"{path.name}/{member}", target))
            else:
                files += [(display(p), p) for p in blend_files(path)]
        if not files:
            print("[blend-paths] no .blend files to check")
            return 0
        profile = pathlib.Path(tmp, "profile")
        profile.mkdir()
        job = pathlib.Path(tmp, "job.json")
        job.write_text(json.dumps({"files": [[name, str(p)] for name, p in files], "names": names}),
                       encoding="utf-8")
        cmd = [str(exe), "--background", "--factory-startup", "--python-exit-code", "1",
               "--python", str(pathlib.Path(__file__).resolve()), "--", "--job", str(job)]
        result = subprocess.run(cmd, env=blender_dev.dev_env(profile), capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    hits, done = [], None
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith(f"{MARK}_HIT "):
            hits.append(json.loads(line.split(" ", 1)[1]))
        elif line.startswith(f"{MARK}_DONE "):
            done = int(line.split()[1])
    if done is None:
        print(result.stdout + result.stderr)
        print("[blend-paths] Blender did not finish the check")
        return 1
    for hit in hits:
        print(f"[blend-paths] {hit['file']}: {hit['what']} {hit['value']!r} in {hit['where']}")
    print(f"[blend-paths] {done} file(s) checked, {len(hits)} problem(s)")
    return 1 if hits else 0


def display(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--job" in argv:  # inside Blender
        return check_in_blender(pathlib.Path(argv[argv.index("--job") + 1]))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help=".blend files, folders or extension zips (default: the shipped files)")
    parser.add_argument("--blender", help="blender executable (default: found automatically)")
    parser.add_argument("--name", action="append", default=[], help="another user name to look for")
    args = parser.parse_args(argv)
    paths = [pathlib.Path(p) for p in args.paths] or shipped_files()
    return run_blender(paths, machine_names(args.name), args.blender)


# --- Inside Blender ------------------------------------------------------------------


def decompressed(data: bytes) -> bytes:
    if data[:4] == b"\x28\xb5\x2f\xfd":
        import zstandard  # bundled with Blender

        return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data), read_across_frames=True).read()
    if data[:2] == b"\x1f\x8b":
        import gzip

        return gzip.decompress(data)
    return data


class BlendLayout:
    """The blocks of a decompressed .blend and its SDNA, enough to name the field at an offset."""

    def __init__(self, data: bytes):
        if data[7:9].isdigit():  # Blender 5.0+: "BLENDER17-01v0502", 32-byte block headers
            pos, fmt, size = int(data[7:9]), "<4siQqq", 32
        else:  # "BLENDER-v405": 24-byte block headers
            pos, fmt, size = 12, "<4siQii", 24
        self.blocks = []  # (code, sdna index, data start, length, count)
        while pos + size <= len(data):
            if size == 32:
                code, sdna, _old, length, count = struct.unpack_from(fmt, data, pos)
            else:
                code, length, _old, sdna, count = struct.unpack_from(fmt, data, pos)
            pos += size
            self.blocks.append((code.rstrip(b"\0").decode("latin-1"), sdna, pos, length, count))
            if code == b"ENDB":
                break
            pos += length
        self.starts = [block[2] for block in self.blocks]
        self.structs = []
        dna = next((b for b in self.blocks if b[0] == "DNA1"), None)
        if dna is not None:
            try:
                self._read_sdna(data, dna[2])
            except (struct.error, ValueError, IndexError):
                self.structs = []

    def _read_sdna(self, d: bytes, base: int):
        p = base + 8  # "SDNA" "NAME"
        names, p = self._strings(d, p, base)
        types, p = self._strings(d, p + 4, base)  # "TYPE"
        lengths = struct.unpack_from(f"<{len(types)}h", d, p + 4)  # "TLEN"
        p = base + ((p + 4 - base + 2 * len(types) + 3) & ~3) + 4  # "STRC"
        count, p = struct.unpack_from("<i", d, p)[0], p + 4
        for _ in range(count):
            kind, nfields = struct.unpack_from("<hh", d, p)
            p += 4
            fields, offset = [], 0
            for _ in range(nfields):
                ftype, fname = struct.unpack_from("<hh", d, p)
                p += 4
                name = names[fname]
                size = 8 if name.startswith(("*", "(*")) else lengths[ftype]
                for dim in re.findall(r"\[(\d+)\]", name):
                    size *= int(dim)
                fields.append((name, offset, size))
                offset += size
            self.structs.append((types[kind], lengths[kind], fields))

    @staticmethod
    def _strings(d: bytes, p: int, base: int):
        count, p = struct.unpack_from("<i", d, p)[0], p + 4
        out = []
        for _ in range(count):
            end = d.index(b"\0", p)
            out.append(d[p:end].decode("latin-1"))
            p = end + 1
        return out, base + ((p - base + 3) & ~3)

    def skip(self, offset: int) -> bool:
        """Thumbnail pixels and the SDNA itself: no strings of the file, only noise."""
        block = self._block(offset)
        return block is not None and block[0] in ("TEST", "DNA1", "REND")

    def _block(self, offset: int):
        i = bisect.bisect_right(self.starts, offset) - 1
        if i >= 0 and offset < self.blocks[i][2] + self.blocks[i][3]:
            return self.blocks[i]
        return None

    def where(self, offset: int) -> str:
        block = self._block(offset)
        if block is None:
            return f"byte {offset}"
        code, sdna, start, length, count = block
        if not 0 <= sdna < len(self.structs):
            return f"a {code} block, byte {offset}"
        name, size, fields = self.structs[sdna]
        if name == "raw_data" or not size:
            return f"a {code} block of raw data, byte {offset}"
        inner = (offset - start) % size
        field = next((f for f, off, fsize in fields if off <= inner < off + fsize), "?")
        return f"{name}.{field.split('[')[0].lstrip('*')} ({code} block, byte {offset})"


def compile_patterns(names):
    patterns = {what: re.compile(p) for what, p in PATTERNS.items()}
    if names:
        patterns["user name"] = re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)),
                                           re.IGNORECASE)
    return patterns


def problems(text: str, patterns) -> list[str]:
    return [what for what, pattern in patterns.items() if pattern.search(text)]


def raw_hits(path: pathlib.Path, patterns) -> list[dict]:
    data = decompressed(path.read_bytes())
    layout = BlendLayout(data)
    hits = {}
    for match in re.finditer(rb"[\x20-\x7e\x80-\xff]{3,}", data):
        text = match.group().decode("utf-8", "replace")
        found = problems(text, patterns)
        if found and not layout.skip(match.start()):
            hits.setdefault(text, (found, match.start()))
    return [{"what": ", ".join(found), "value": text, "where": layout.where(offset)}
            for text, (found, offset) in hits.items()]


def bpy_hits(patterns) -> list[dict]:
    import bpy

    out, seen = [], set()

    def check(value, where, subtype="NONE"):
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if not isinstance(value, str) or not value:
            return
        found = problems(value, patterns)
        if (subtype in ("FILE_PATH", "DIR_PATH") and not value.startswith("//") and value not in ALLOWED_PATHS
                and (value.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", value))):
            found = found or ["absolute path"]
        if found:
            out.append({"what": ", ".join(found), "value": value, "where": where})

    def idprop(value, where):
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        elif hasattr(value, "to_list"):
            value = value.to_list()
        if isinstance(value, dict):
            for key, item in value.items():
                check(key, f"{where} (key)")
                idprop(item, f"{where}[{key!r}]")
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value[:MAX_ITEMS]):
                idprop(item, f"{where}[{i}]")
        else:
            check(value, where)

    def walk(obj, where, depth):
        try:
            key = (type(obj).__name__, obj.as_pointer())
        except (ReferenceError, AttributeError):
            return
        if depth > MAX_DEPTH or key in seen:
            return
        seen.add(key)
        try:
            for name in obj.keys():
                check(name, f"{where} (property name)")
                idprop(obj[name], f'{where}["{name}"]')
        except (TypeError, AttributeError, ReferenceError):
            pass
        for prop in obj.bl_rna.properties:
            ident = prop.identifier
            if ident in SKIP_PROPS:
                continue
            try:
                value = getattr(obj, ident)
            except (AttributeError, ReferenceError, RuntimeError, TypeError):
                continue
            if prop.type == "STRING":
                check(value, f"{where}.{ident}", prop.subtype)
            elif prop.type == "POINTER" and value is not None and not isinstance(value, bpy.types.ID):
                walk(value, f"{where}.{ident}", depth + 1)
            elif prop.type == "COLLECTION":
                try:
                    items = list(value)[:MAX_ITEMS]
                except (TypeError, ReferenceError, RuntimeError):
                    continue
                for i, item in enumerate(items):
                    try:
                        if hasattr(item, "bl_rna") and not isinstance(item, bpy.types.ID):
                            walk(item, f"{where}.{ident}[{i}]", depth + 1)
                    except ReferenceError:
                        continue

    for prop in bpy.data.bl_rna.properties:
        if prop.type != "COLLECTION" or prop.identifier in SKIP_PROPS:
            continue
        for idb in getattr(bpy.data, prop.identifier):
            if not isinstance(idb, bpy.types.ID):
                break
            where = f"{prop.identifier}[{idb.name!r}]"
            check(idb.name, f"{where} (name)")
            walk(idb, where, 0)
            if isinstance(idb, bpy.types.Text):
                for i, line in enumerate(idb.as_string().splitlines()):
                    check(line, f"{where} line {i + 1}")
    return out


def check_in_blender(job_path: pathlib.Path) -> int:
    import bpy

    job = json.loads(job_path.read_text(encoding="utf-8"))
    patterns = compile_patterns(job["names"])
    for name, path in job["files"]:
        path = pathlib.Path(path)
        raw = raw_hits(path, patterns)
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=True)  # factory settings: no scripts run
        data = decompressed(path.read_bytes()).decode("latin-1")
        # Only what the file holds (a string and its terminator): Blender fills some values in at
        # load, from this computer. A raw hit bpy found too is reported once, where bpy found it.
        located = [hit for hit in bpy_hits(patterns)
                   if hit["value"].encode("utf-8").decode("latin-1") + "\0" in data]
        values = {hit["value"] for hit in located}
        for hit in located + [h for h in raw if h["value"] not in values]:
            print(f"{MARK}_HIT " + json.dumps({"file": name, **hit}), flush=True)
    print(f"{MARK}_DONE {len(job['files'])}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        import bpy  # noqa: F401
    except ImportError:
        sys.exit(main())
    sys.exit(main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []))
