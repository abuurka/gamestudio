# SPDX-License-Identifier: GPL-3.0-or-later
"""Game projects: the recent list, New Project and Open Project.

A GameStudio project is a folder with gamestudio.json. Its scenes are .blend
files in scenes/. The launcher, the GameStudio menu and the Project tab use
these operators.

New Project:
  1. copy a project template, record its id in gamestudio.json ("template") and point the project
     at the @gamestudio packages;
  2. on a worker thread (environment.py): Node.js if the system has none, the shared packages
     the first time (installed extension), the game browser when there is no Chrome or Edge, links
     from the project to them (core/toolchain.py), `npm install` for anything they lack, then
     `npm run registry`. The dialog lists what that downloads; Create agrees to it;
  3. open the start scene: the one the template ships (scenes/<startScene>.blend, as user templates
     have), exported so Play works; else build it with components: the template's level script
     (tools/build_level.py) when it ships one, else a small starter scene. A built scene starts
     with the templates' Color Management (VIEW_TRANSFORM), which the game follows. Before that, a
     template whose template.json says "starterUi" (the starter's, which templates built on it do
     not inherit) gets the starter's game UI, ui/ui.blend: its title, pause and win menus and HUD. A
     ui/ui.blend the template ships (a user template's) gets the project's name instead (name_shipped_ui).
     Step 3 replaces the open file, so it waits when that file has unsaved changes as step 2 ends
     (wait_to_open): GameStudio says the project is ready and opens it when the user says so, after
     Save, Don't Save or Cancel (GameStudio > Open <name>, or Open Project on it).

Nothing is written when step 2 would have to download while online access is off (offline_problem).
.gamestudio/setup-pending.json marks a project from step 1 until step 3 is done: Open Project, or New
Project on the same folder, then runs steps 2 and 3 again (resume_setup) and keeps the files. Once
step 2 is done the note says so ("ready"): Open Project then runs step 3 only.

Templates are the folders in project_templates/. A template.json there (never copied) gives the
picker's label and one-line description, and may name a "base" template: the project then gets the
base's files (minus the template's "exclude" paths) with the template's own files added on top, so
shared files such as the agent skills have one source of truth. A text file holding the line
{{base}} gets the base's file of the same path there, without its title line.

A project's instructions for coding agents are AGENTS.md (Claude Code and Codex) and a CLAUDE.md that
imports it (@AGENTS.md); the genre templates add their section to the starter's AGENTS.md with
{{base}} and take its CLAUDE.md as it is. A template with its instructions only in CLAUDE.md (one
saved by an older GameStudio) gives new projects that same shape (core/agentfiles.py).

User templates are folders in user_templates_dir() (Documents/GameStudio/Templates, or
GAMESTUDIO_TEMPLATES) laid out the same way; a built-in template wins over a user template of the
same id. Save as Template (save_as_template) makes one from a project: a copy of its files with its
name and package paths turned back into placeholders, and instructions only in CLAUDE.md moved to
AGENTS.md. docs/TEMPLATES.md has the details.

Opening a project (Open Project, Open Recent, Examples, or loading one of its scenes) links its packages
the same way, also replacing links into another GameStudio install, and runs GameStudio's readiness
check (environment.ensure_ready) for the rest. Examples shipped inside an install are copied to
Documents/GameStudio/Examples on first open, so the install folder never holds user work. Clicked in
the UI, Open Project asks first when the open file has unsaved changes, as File > Open does; called
from Python it opens at once.

The project's own code (its npm scripts, tools/build_level.py and tools/build_ui.py) runs only in a
trusted project (core/trust.py): New Project and the copy of an example are trusted as GameStudio makes
them; for any other folder environment.py asks first.
"""

import json
import os
import pathlib
import re
import shutil
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import environment
from .core import agentfiles, components, export, project, registry, skills, toolchain, trust
from .core.log import get_logger
from .core.util import any_window
from .host import compat

log = get_logger(__name__)

RECENT_FILE = "gamestudio_projects.json"
MAX_RECENT = 12
TEMPLATES = pathlib.Path(__file__).resolve().parent / "project_templates"
TEMPLATE_INFO = "template.json"
LEVEL_SCRIPT = "tools/build_level.py"  # builds the start scene of templates that ship one
UI_SCRIPT = "tools/build_ui.py"  # builds ui/ui.blend, the game UI, of templates that ship one
# Marks a text of the game UI whose body is the game's name (gameui.text project_name, which sets this
# gameui.build.PROJECT_NAME_PROP), such as the title screen's GameTitle: a game made from a template that
# ships ui/ui.blend shows its own name there (name_shipped_ui).
UI_PROJECT_NAME = "gs_ui_project_name"
# The view transform of the start scenes New Project builds. The game tone-maps as the scene's Color
# Management says (the manifest carries it); Khronos PBR Neutral keeps the palette's colours.
VIEW_TRANSFORM = "Khronos PBR Neutral"
SETUP_PENDING = "setup-pending.json"  # in .gamestudio/ until New Project's setup has finished
BASE_MARKER = "{{base}}"
TEXT_SUFFIXES = {".json", ".ts", ".md", ".html", ".gitignore", ".txt", ".css"}
SKIP_IN_TEMPLATES = {"__pycache__", ".gamestudio", "node_modules"}
# @gamestudio packages a project template can depend on ({{engine}}, ... in its package.json).
PACKAGES = ("engine", "devserver", "mcp")
USER_TEMPLATES_ENV = "GAMESTUDIO_TEMPLATES"  # overrides user_templates_dir()
TEMPLATE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")  # the folder name of a user template
USER_TEMPLATE_ICON = "USER"
# What Save as Template copies: these top-level folders and files of the project, without the
# NOT_IN_TEMPLATES folders, Blender's backups (*.blend1, ...) and the LOCAL_FILES anywhere below.
# .codex holds Codex's config.toml (made portable) and .agents the person's own Codex skills: the
# copies GameStudio makes there of .claude/skills stay behind (codex.generated_paths). So do the
# person's own permissions and GameStudio's record of the skills it put in the project (LOCAL_FILES).
TEMPLATE_DIRS = ("src", "scenes", "ui", "assets", "public", "audio", "tools", ".claude", ".codex", ".agents")
TEMPLATE_FILES = ("AGENTS.md", "CLAUDE.md", "ASSISTANT.md", ".mcp.json", "gamestudio.json", "package.json",
                  "index.html", "tsconfig.json", "vite.config.ts", ".gitignore", ".gitattributes")
NOT_IN_TEMPLATES = {"node_modules", ".gamestudio", "dist", "__pycache__", ".git"}
LOCAL_FILES = {".claude/settings.local.json", f"{skills.SKILLS}/{skills.RECORD}"}
BACKUP_FILE = re.compile(r"\.blend\d+$|\.blend@$|~$|\.pyc$")


# ------------------------------------------------------------------ recent projects


def _recent_path() -> pathlib.Path:
    return pathlib.Path(compat.user_config_dir()) / RECENT_FILE


def recent() -> list[dict]:
    try:
        entries = json.loads(_recent_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in entries if (pathlib.Path(e.get("path", "")) / "gamestudio.json").is_file()]


def remember(root: pathlib.Path) -> None:
    root = pathlib.Path(root).resolve()
    name = project.read_config(root).get("name") or root.name
    entries = [e for e in recent() if pathlib.Path(e["path"]).resolve() != root]
    entry = {"path": str(root), "name": name, "opened": time.time()}
    if not trust.is_trusted(root):
        entry["untrusted"] = True  # opened without trusting it: trust.migrate must not trust it later
    entries.insert(0, entry)
    try:
        path = _recent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries[:MAX_RECENT], indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not save recent projects: %s", exc)


def explain_os_error(exc: OSError, folder) -> str:
    """An OSError as the user can act on it. macOS refuses Blender a folder such as Documents until
    the user allows it ([Errno 1] Operation not permitted): say where."""
    import errno
    import sys

    if sys.platform == "darwin" and getattr(exc, "errno", None) in (errno.EPERM, errno.EACCES):
        return (f"macOS did not let Blender use {folder}. Allow it in System Settings > Privacy & Security > Files "
                "and Folders > Blender (Documents Folder), or pick another location")
    return str(exc)


def start_scene(root: pathlib.Path) -> pathlib.Path:
    name = project.read_config(root).get("startScene") or "main"
    return pathlib.Path(root) / "scenes" / f"{name}.blend"


def _install_roots():
    """Folders that may hold runtime/ and examples/: each parent of this file (a development
    checkout), and <parent>/gamestudio (the packaged layout, docs/fork/RELEASING.md)."""
    return toolchain.install_roots(__file__)


def examples() -> list[pathlib.Path]:
    """Example projects shipped next to the editor (development checkouts and full installs)."""
    for parent in _install_roots():
        folder = parent / "examples"
        if folder.is_dir():
            return sorted(p for p in folder.iterdir() if (p / "gamestudio.json").is_file())
    return []


def examples_home() -> pathlib.Path:
    return pathlib.Path.home() / "Documents" / "GameStudio" / "Examples"


def _inside(path: pathlib.Path, folder: pathlib.Path) -> bool:
    try:
        pathlib.Path(path).resolve().relative_to(pathlib.Path(folder).resolve())
        return True
    except ValueError:
        return False


def prepare_example(root: pathlib.Path) -> pathlib.Path:
    """The folder to open for a project. An example inside a packaged install is copied to
    examples_home() once (an existing copy is the user's and is reused); anything else is
    returned unchanged, so development checkouts open their examples in place."""
    bundle = toolchain.bundle_root(__file__)
    if bundle is None or not _inside(root, bundle):
        return root
    target = examples_home() / root.name
    if (target / "gamestudio.json").is_file():
        trust.trust(target, "copied")  # an earlier copy, maybe from before trust was recorded
        return target
    skip = shutil.ignore_patterns("node_modules", ".gamestudio", "dist", "*.blend1", "__pycache__")
    shutil.copytree(root, target, ignore=skip, dirs_exist_ok=True)
    shared = toolchain.sdk(__file__)
    manifest_path = target / "package.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = None
    if manifest is not None and shared is not None:
        # Workspace versions ("0.1.0") mean nothing outside the install: point npm at it.
        for field in ("dependencies", "devDependencies"):
            for name in list((manifest.get(field) or {})):
                if name.startswith(toolchain.SCOPE):
                    manifest[field][name] = "file:" + (shared.packages / name[len(toolchain.SCOPE):]).as_posix()
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    _inline_tsconfig_base(target, root)
    from .claude_code import localize_mcp_config

    localize_mcp_config(target)  # its own node_modules/@gamestudio/mcp, not the repository's ../../node_modules
    trust.trust(target, "copied")  # GameStudio's own example: its scripts run without asking
    log.info("Copied the example %s to %s", root.name, target)
    return target


def _inline_tsconfig_base(target: pathlib.Path, source: pathlib.Path) -> None:
    """Examples extend the workspace's tsconfig.base.json by a relative path, which a copy
    elsewhere cannot reach (Vite reads tsconfig.json to compile the game): fold the base in."""
    path = target / "tsconfig.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        reference = config.get("extends")
        if not isinstance(reference, str) or not reference.startswith("."):
            return
        base = json.loads((source / reference).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    base.pop("extends", None)
    merged = {**base, **{key: value for key, value in config.items() if key != "extends"}}
    merged["compilerOptions"] = {**base.get("compilerOptions", {}), **config.get("compilerOptions", {})}
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8", newline="\n")


def repair_package_paths(root: pathlib.Path) -> list[str]:
    """Point `file:` specs of @gamestudio packages that lead nowhere (the project came from another
    computer, or GameStudio moved) or into another GameStudio install at this GameStudio's packages
    (toolchain.repoint_specs). Returns the names changed."""
    shared = toolchain.sdk(__file__)
    changed = toolchain.repoint_specs(root, shared)
    if changed:
        log.info("Pointed %s in %s at %s", ", ".join(changed), pathlib.Path(root) / "package.json", shared.packages)
    return changed


def ensure_dependencies(root: pathlib.Path) -> dict:
    """Link missing packages from the shared tree (fast, offline), and link again those that lead into
    another GameStudio install (an older portable one, another Blender version's). Opening a project
    cloned from elsewhere, or after GameStudio moved, then plays without downloading anything."""
    shared = toolchain.sdk(__file__)
    foreign = toolchain.foreign_links(root, shared)
    if not foreign and not toolchain.missing_dependencies(root):
        return {"linked": [], "unavailable": []}
    repair_package_paths(root)
    result = toolchain.link_project(root, shared, relink=foreign)
    if result["linked"]:
        log.info("Linked %s into %s", ", ".join(result["linked"]), root / "node_modules")
    if result["unavailable"]:
        log.info("Not linked in %s: %s (GameStudio's setup downloads them)", root, ", ".join(result["unavailable"]))
    return result


@persistent
def _on_load_post(*_args):
    root = project.project_root()
    if root is not None:
        remember(root)
        try:
            ensure_dependencies(root)
        except OSError as exc:
            log.warning("Could not link the packages of %s: %s", root, exc)
        # The rest of the readiness check (an update's packages, downloads, the component list) from a
        # timer: loading never waits for it, and a dialog never shows in the middle of a load.
        environment.invalidate()
        environment.check_later(root, "load")


# -------------------------------------------------------------------- scaffolding


def runtime_packages() -> pathlib.Path | None:
    """Folder holding @gamestudio/engine and @gamestudio/devserver (GAMESTUDIO_RUNTIME first)."""
    shared = toolchain.sdk(__file__)
    return shared.packages if shared is not None else None


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "game"


def user_templates_dir() -> pathlib.Path:
    """The folder of the user's own templates (Save as Template writes there, New Project lists
    them): GAMESTUDIO_TEMPLATES, else Documents/GameStudio/Templates."""
    folder = os.environ.get(USER_TEMPLATES_ENV, "").strip()
    if folder:
        return pathlib.Path(folder).expanduser()
    return pathlib.Path.home() / "Documents" / "GameStudio" / "Templates"


def template_info(template: str) -> dict | None:
    """A template's picker entry: {"id", "label", "description", "icon", "order", "base",
    "exclude", "starter_ui", "source_name", "path", "user"}, or None if <template> is not a template (a
    folder with a template.json, or a gamestudio.json for an older template without one). project_templates/
    comes first; then user_templates_dir(), whose ids must match TEMPLATE_ID ("user": True).
    starter_ui is template.json's "starterUi": New Project builds the starter's game UI
    (build_starter_ui). It is the template's own: a template built on it does not inherit it. source_name
    is the name of the game Save as Template saved it from ("sourceName", else "from"'s "project"), or ""."""
    if not isinstance(template, str) or template.startswith((".", "_")):
        return None
    info = _read_template(TEMPLATES / template, template, user=False)
    if info is None and TEMPLATE_ID.fullmatch(template):
        info = _read_template(user_templates_dir() / template, template, user=True)
    return info


def _read_template(folder: pathlib.Path, template: str, user: bool) -> dict | None:
    if not folder.is_dir():
        return None
    meta = {}
    if (folder / TEMPLATE_INFO).is_file():
        try:
            meta = json.loads((folder / TEMPLATE_INFO).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Skipped the project template %s: %s", template, exc)
            return None
        if not isinstance(meta, dict):
            log.warning("Skipped the project template %s: its %s is not an object", template, TEMPLATE_INFO)
            return None
    elif not (folder / "gamestudio.json").is_file():
        return None
    source = meta.get("from") if isinstance(meta.get("from"), dict) else {}  # saved before sourceName
    return {"id": template,
            "label": str(meta.get("label") or template.replace("_", " ").title()),
            "description": str(meta.get("description") or f"Start from the {template} template"),
            "icon": str(meta.get("icon") or (USER_TEMPLATE_ICON if user else "FILE_BLANK")),
            "order": meta.get("order", 100) if isinstance(meta.get("order", 100), (int, float)) else 100,
            "base": meta.get("base") or None,
            "exclude": [str(p).strip("/") for p in meta.get("exclude") or []],
            "starter_ui": meta.get("starterUi") is True,
            "source_name": str(meta.get("sourceName") or source.get("project") or ""),
            "path": folder,
            "user": user}


def _folder_names(folder: pathlib.Path) -> list[str]:
    try:
        return sorted(f.name for f in folder.iterdir() if f.is_dir())
    except OSError:  # missing, or unreadable
        return []


def templates() -> list[dict]:
    """Every project template (see template_info) in the picker's order: the built-in ones, then
    the user's."""
    ids = dict.fromkeys([*_folder_names(TEMPLATES), *_folder_names(user_templates_dir())])
    found = [template_info(template) for template in ids]
    return sorted((t for t in found if t is not None), key=lambda t: (t["user"], t["order"], t["label"].lower()))


def template_files(template: str, _chain: tuple = ()) -> dict[str, list[pathlib.Path]]:
    """What a template puts into a new project: project-relative POSIX path -> the source files
    that make it, base templates' first (see the module docstring)."""
    info = template_info(template)
    if info is None:
        raise ValueError(f"Unknown project template '{template}'")
    if template in _chain:
        raise ValueError(f"Project template '{template}' is its own base")
    files = {}
    if info["base"]:
        files = template_files(info["base"], (*_chain, template))
        for prefix in info["exclude"]:
            files = {rel: srcs for rel, srcs in files.items() if rel != prefix and not rel.startswith(prefix + "/")}
    for src in sorted(info["path"].rglob("*")):
        rel = src.relative_to(info["path"]).as_posix()
        if src.is_dir() or rel == TEMPLATE_INFO or src.suffix == ".pyc" or SKIP_IN_TEMPLATES & set(rel.split("/")):
            continue
        files[rel] = [*files.get(rel, []), src]
    return files


def _compose_text(sources: list[pathlib.Path]) -> str:
    text = sources[-1].read_text(encoding="utf-8")
    if BASE_MARKER not in text:
        return text
    if len(sources) < 2:
        raise ValueError(f"{sources[-1]} asks for {BASE_MARKER}, but its base template has no such file")
    base = _compose_text(sources[:-1]).splitlines(keepends=True)
    if base and base[0].startswith("# "):
        base = base[1:]  # the title: the template's file has its own
    return text.replace(BASE_MARKER, "".join(base).strip("\n"))


def is_text_file(path: pathlib.Path) -> bool:
    """A template file that is composed and filled in (render_text), not copied as is."""
    return path.suffix in TEXT_SUFFIXES or path.name == ".gitignore"


def render_text(sources: list[pathlib.Path], values: dict) -> str:
    """A template's text file as a project gets it (sources from template_files): composed with its
    base's file ({{base}}), then each {{key}} of values filled in."""
    text = _compose_text(sources)
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def project_template(root, default: str = "starter") -> str:
    """The template a project was made from (gamestudio.json "template"), or default for a project
    made before New Project recorded it, or from a template this GameStudio does not have (built
    in, or in the user templates folder)."""
    template = project.read_config(root).get("template")
    if isinstance(template, str) and template in {t["id"] for t in templates()}:
        return template
    if template:
        log.info("%s names the project template %r, which this GameStudio does not have; using %s",
                 root, template, default)
    return default


def engine_version() -> str:
    """The version of @gamestudio/engine a new project gets: the bundle's in an installed extension
    (the shared copy may not be there yet), else the shared packages'. "" when there is none."""
    bundled = toolchain.bundled_packages(__file__) if toolchain.is_extension_bundle(__file__) else None
    for folder in (bundled, runtime_packages()):
        version = toolchain.read_manifest(folder / "engine").get("version") if folder is not None else None
        if isinstance(version, str) and version:
            return version
    return ""


def _record_template(path: pathlib.Path, template: str, engine: str = "") -> None:
    """Write the template's id into the new project's gamestudio.json, after its name, and the
    version of @gamestudio/engine it was made with ("engine": for compatibility checks later)."""
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # no gamestudio.json, or a name that broke its JSON
        log.warning("Could not record the template in %s: %s", path, exc)
        return
    if not isinstance(config, dict):
        return
    config.pop("template", None)
    config.pop("engine", None)  # a user template's, from the game it was saved from
    first = {"name": config.pop("name")} if "name" in config else {}
    config = {**first, "template": template, **({"engine": engine} if engine else {}), **config}
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def scaffold(root: pathlib.Path, name: str, template: str = "starter") -> None:
    """Copy a project template into an empty folder. Raises ValueError with a readable message."""
    files = template_files(template)
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"{root} is not empty. Pick a new folder name.")
    packages = runtime_packages()
    if packages is None:
        raise ValueError("Could not find the GameStudio runtime packages (set GAMESTUDIO_RUNTIME).")
    values = {"name": name, "slug": slugify(name)}
    for package in PACKAGES:
        values[package] = "file:" + (packages / package).as_posix()
    for rel, sources in files.items():
        src, dst = sources[-1], root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if is_text_file(src):
            dst.write_text(render_text(sources, values), encoding="utf-8", newline="\n")
        else:
            shutil.copy2(src, dst)
    # A template saved by an older GameStudio has its instructions only in CLAUDE.md: the new project gets
    # them in AGENTS.md, which Codex reads too, and a CLAUDE.md that imports it, as the built-in ones have.
    agentfiles.migrate_instructions(root, consent=True)
    _record_template(root / project.PROJECT_FILE, template, engine_version())
    _drop_unknown_packages(root / "package.json")
    (root / "scenes").mkdir(exist_ok=True)
    (root / ".gamestudio").mkdir(exist_ok=True)
    if not untrusted_templates(template):
        trust.trust(root, "created")  # GameStudio made it: its setup and scripts run without asking


def untrusted_templates(template: str) -> list[pathlib.Path]:
    """The user templates that <template> is or is built on (base) that are not trusted: someone else's, or
    saved by a GameStudio from before trust was recorded. Built-in templates are GameStudio's own."""
    found, seen = [], set()
    while template and template not in seen:
        seen.add(template)
        info = template_info(template)
        if info is None:
            break
        if info["user"] and not trust.recorded(info["path"]):
            found.append(pathlib.Path(info["path"]))
        template = info["base"]
    return found


def _drop_unknown_packages(path: pathlib.Path) -> None:
    """Remove @gamestudio dependencies this GameStudio does not ship, so `npm install` does not fail
    on a `file:` path that does not exist."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    sources = [toolchain.bundled_packages(__file__) if toolchain.is_extension_bundle(__file__) else None]
    shared = toolchain.sdk(__file__)
    sources.append(shared.packages if shared is not None else None)
    dropped = []
    for field in ("dependencies", "devDependencies"):
        for name in list((manifest.get(field) or {})):
            short = name[len(toolchain.SCOPE):]
            if name.startswith(toolchain.SCOPE) and not any(
                    folder is not None and (folder / short / "package.json").is_file() for folder in sources):
                del manifest[field][name]
                dropped.append(name)
    if dropped:
        log.warning("This GameStudio does not ship %s; the new project goes without", ", ".join(dropped))
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")


def use_template_color_management(scene) -> None:
    """Give a start scene New Project builds the templates' Color Management: VIEW_TRANSFORM and no
    look (the game does not carry looks). Exposure stays as it is: the game follows it too."""
    scene.view_settings.view_transform = VIEW_TRANSFORM
    scene.view_settings.look = "None"


MUSIC = {"clip": "assets/music/theme.ogg", "volume": 0.5, "loop": True, "autoplay": True, "spatial": False,
         "channel": "music"}  # the starter level's Music object (AudioSource)


def build_starter_scene(root: pathlib.Path) -> pathlib.Path:
    """Fill the current (empty) file with a playable starter scene, built from kits as the skills
    describe, and save it into the project."""
    from math import radians

    from mathutils import Vector

    from . import kits

    scene = bpy.context.scene
    scene.name = project.read_config(root).get("startScene") or "main"
    scene.render.fps, scene.render.fps_base = 30, 1.0  # kit animation clips are authored at 30 fps
    use_template_color_management(scene)
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    sky = (0.45, 0.65, 0.92)
    scene.world = bpy.data.worlds.new("Sky")
    scene.world.color = sky
    if scene.world.node_tree and "Background" in scene.world.node_tree.nodes:
        scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (*sky, 1.0)  # wins over color
    path = start_scene(root)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))  # now project_root() resolves to `root`
    project.invalidate_cache()
    registry.reload_registry(root)

    def kit(kind, params, location=(0, 0, 0)):
        return kits.create(kind, params, location=location, collection=scene.collection)[0]

    ground = kit("platform", {"name": "Ground", "width": 30, "depth": 30, "height": 0.5, "bevel": 0.1,
                              "top_material": "grass", "side_material": "dirt"}, (0, 0, -0.5))
    components.add_component(ground, "Collider")

    # The character kit: origin at the feet, facing away from the camera (+Y). It stands on the floor.
    player = kit("character", {"name": "Player", "color": "#3B82F6"}, (0, 0, 0))
    player.rotation_euler.z = radians(180)
    components.add_component(player, "RigidBody", {"type": "kinematic"})
    components.add_component(player, "Collider", {"shape": "capsule"})
    components.add_component(player, "CharacterController")

    for i, (x, y) in enumerate([(3, 3), (-3, 4), (0, 7)]):
        item = kit("collectible", {"name": f"Collectible{i + 1}", "style": "gem", "size": 0.5, "seed": i}, (x, y, 1.0))
        components.add_component(item, "Collectible", {"points": 1})

    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.lens = 24
    scene.collection.objects.link(cam)
    cam.location = (0, -8, 5)
    cam.rotation_euler = (Vector((0, 0, 1)) - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    components.add_component(cam, "FollowCamera", {"target": "Player"})

    # The sky kit: a gradient sky, which the game draws too, and its sun, in front and to the right. Its
    # ground colour (below the horizon, past the lawn's edge) is a sunlit green, so the lawn runs on.
    kit("sky", {"name": "Sun", "preset": "day", "ground": "#A3C46E", "sun_strength": 4.0, "sun_elevation": 43,
                "sun_azimuth": 135})

    # The background music: a loop on the music channel, which plays on under menus, so the Options screen's
    # Music slider (ui/ui.blend) is heard at once. The theme is mastered to -14 LUFS: at 0.5 it sits under the
    # sound effects. Not spatial, so where the object stands does not matter.
    music = bpy.data.objects.new("Music", None)
    music.empty_display_type, music.empty_display_size = "SPHERE", 0.3
    scene.collection.objects.link(music)
    music.location = (-5, -5, 1.5)
    components.add_component(music, "AudioSource", MUSIC)

    bpy.ops.wm.save_mainfile()
    export.export_scene(scene)
    bpy.ops.wm.save_mainfile()
    remember(root)
    return path


# The starter's game UI: one font (gs.gameui's default, Blender's Inter, copied to assets/fonts/) and one
# accent, the engine's focus colour, on a 1920 x 1080 design frame.
UI_ACCENT = "#FFD400"
UI_SOFT_TEXT = "#E6EAF2"  # subtitles and values
UI_MUTED_TEXT = "#B8C1CF"  # the controls hint
UI_CARD = {"color": "#12161FE6", "radius": 40, "border": 2, "border_color": "#FFFFFF1F", "anchor": "center"}
UI_BACKDROP = (0.0, 0.0, 0.0, 0.55)  # linear RGBA over the game, behind the menus
UI_CLICK = "assets/sfx/click.wav"  # the starter's own sound, played by every button
UI_TRANSITION = {"open": "pop", "close": "fade", "duration": 0.25}  # every menu's open and close animations
# The title's 3D model (GAME-UI §11.1): the level's teal gem (the collectible kit's gem, in its gem material),
# turning slowly under studio light, seen from three-quarters.
UI_GEM = {"kit": "collectible", "kit_params": {"style": "gem", "size": 0.5}, "view": "threeQuarter", "zoom": 1.0,
          "spin": 0.6, "light": "studio"}
# The library's Shine on Play's focus art (GAME-UI §11.4): a soft white band sweeps across the yellow every few
# seconds (the first as the title opens), so the call to action catches the eye without flashing.
UI_SHINE = {"color": [1.0, 1.0, 1.0, 0.4], "width": 0.25, "speed": 1.2, "interval": 3.5}


def build_starter_ui(root: pathlib.Path) -> pathlib.Path:
    """Write the starter's game UI, <root>/ui/ui.blend (docs/GAME-UI.md), with gs.gameui in this Blender,
    which is left with the UI file open (the caller opens the level next). Its canvases:

    - Title: a menu that opens on the first start and pauses: a teal gem turning above the game's name
      (TitleGem, a 3D model: UI_GEM), the name (GameTitle, marked UI_PROJECT_NAME), "Collect every gem",
      the controls, and Play (action close, focused first; a Shine sweeps its yellow focus art, UI_SHINE)
      and Options (opens Options) in the centred group TitleButtons, a column that more buttons join;
    - Pause: the pause menu (Escape, P or gamepad Start; Escape resumes): Resume, Options and Restart;
    - Options: from the title or the pause menu (Escape and Back go back to it): Music and Sound sliders,
      which set the music and sfx channels' volumes (the game saves them; no code), Language (action
      locale: the next language, its label the current one's name, "{ui.localeName}"), and Back, focused
      first;
    - Win: opened by the starter's code, ctx.ui.open('Win'): "You win!", "Score {score}", Play again;
    - HUD: opens on every start: "Score {score}" at the top left.

    The menus pop open and fade out (UI_TRANSITION). The template ships the French table of these texts,
    ui/locales/fr.json (scaffolded before this runs), which the save exports with the screens. It refuses
    (ValueError) when the UI file exists, not when ui/ does. On an error what it wrote is removed (the ui/
    folder, or the file when ui/ was there; the UI export, when the project had none), and the error is
    raised."""
    from math import radians

    from .core import gameui as ui_core
    from .gameui import build as ui, files as ui_files

    root = pathlib.Path(root)
    path = ui_core.ui_file(root)
    if path.exists():
        raise ValueError(f"{path} already exists")
    new_folder = not path.parent.exists()  # ui/ may hold the template's translations (ui/locales) already
    new_export = not ui_core.export_dir(root).exists()
    name = str(project.read_config(root).get("name") or root.name)
    registry.reload_registry(root)  # the UI components are engine built-ins in the project's registry
    menu = {"kind": "menu", "backdrop": UI_BACKDROP}
    try:
        ui_files.create_here(path, root, [
            {"name": "Title", **menu, "open": "firstStart"},
            {"name": "Pause", **menu, "pause_menu": True, "back": "resume"},
            {"name": "Options", **menu, "back": "previous"},
            {"name": "Win", **menu},
            {"name": "HUD", "kind": "hud", "open": "start"},
        ])
        # Title: the name, anchored to the top with its subtitle. Its box has a width and no height: in
        # the game, a name too long for one line shrinks (UiText fit) instead of wrapping.
        # It is marked as the game's name: a game made from a template saved from this one gets its own there.
        title = ui.text("Title", "GameTitle", name, (960, 340), size=120, box=(1680, 0), anchor="top",
                        shadow=(0, 6, 18), project_name=True)
        components.add_component(title, "UiText", {"fit": True})
        ui.panel("Title", "TitleAccent", (960, 430), (120, 8), color=UI_ACCENT, radius=4, anchor="top")
        ui.text("Title", "Subtitle", "Collect every gem", (960, 486), size=48, color=UI_SOFT_TEXT, anchor="top",
                shadow=(0, 3, 10))
        # Above the name, the gem the game is about turns in a view of its own: the game draws it lit, as in
        # the level, and it pops in and fades out with the menu. Anchored to the top with the name.
        ui.model("Title", "TitleGem", (960, 160), size=(220, 220), anchor="top", **UI_GEM)
        # The title's buttons: a column under one centred group, which more buttons join below Options.
        ui.group("Title", "TitleButtons", (960, 715), anchor="center")
        play = ui.button("Title", "PlayButton", "Play", (960, 660), action="close", parent="TitleButtons",
                         sound=UI_CLICK)
        if registry.shader_schema_ref("Shine") is not None:  # the engine's library, listed by npm run registry
            ui.update("Title", ui.find_part(play, "focus"), shader="Shine", shader_values=UI_SHINE)
        else:
            log.warning("The registry has no Shine game shader: the title's Play button goes without its sweep")
        ui.button("Title", "OptionsButton", "Options", (960, 770), action="open", target="Options",
                  parent="TitleButtons", sound=UI_CLICK)
        ui.text("Title", "Hint", "WASD move · Space jump · Esc pause", (960, 1010), size=30, color=UI_MUTED_TEXT,
                anchor="bottom")
        # Pause, Options and Win: a card in the middle with a title and the controls stacked below it.
        ui.panel("Pause", "PauseCard", (960, 540), (640, 590), **UI_CARD)
        ui.text("Pause", "PauseTitle", "Paused", (960, 335), size=88, parent="PauseCard")
        ui.panel("Pause", "PauseAccent", (960, 410), (96, 8), color=UI_ACCENT, radius=4, parent="PauseCard")
        ui.button("Pause", "ResumeButton", "Resume", (960, 515), action="resume", parent="PauseCard", sound=UI_CLICK)
        ui.button("Pause", "PauseOptionsButton", "Options", (960, 625), action="open", target="Options",
                  parent="PauseCard", sound=UI_CLICK)
        ui.button("Pause", "RestartButton", "Restart", (960, 735), action="restart", parent="PauseCard",
                  sound=UI_CLICK)
        # Options: a row per setting, its name on the left and its control on the right. The sliders write the
        # channel's volume (audio.music, audio.sfx) as they change; the game saves it. A selected slider shows a
        # ring (UiState), as a selected button turns yellow. Language (action locale, no target) switches the UI
        # to the next language of ui/locales and shows the current one's name ({ui.localeName}: "English",
        # "Français"); the game saves the choice. Back (previous) returns to the title or the pause menu.
        ui.panel("Options", "OptionsCard", (960, 540), (760, 690), **UI_CARD)
        ui.text("Options", "OptionsTitle", "Options", (960, 285), size=88, parent="OptionsCard")
        ui.panel("Options", "OptionsAccent", (960, 360), (96, 8), color=UI_ACCENT, radius=4, parent="OptionsCard")
        for y, label, slider, value in ((465, "Music", "MusicSlider", "audio.music"),
                                        (565, "Sound", "SoundSlider", "audio.sfx")):
            ui.text("Options", f"{label}Label", label, (660, y), size=44, align="left", color=UI_SOFT_TEXT,
                    parent="OptionsCard")
            control = ui.slider("Options", slider, (1055, y), value=value, parent="OptionsCard", sound=UI_CLICK)
            ring = ui.panel("Options", f"{slider}Focus", (1055, y), (412, 64), color="#FFD4001F", radius=32,
                            border=3, border_color=UI_ACCENT, parent=slider,
                            z=ui.world_matrix(control).translation.z)  # under the slider's own parts
            components.add_component(ring, "UiState", {"show": ui.FOCUS_STATE})
        ui.text("Options", "LanguageLabel", "Language", (660, 665), size=44, align="left", color=UI_SOFT_TEXT,
                parent="OptionsCard")
        ui.button("Options", "LanguageButton", "{ui.localeName}", (1055, 665), size=(360, 64), radius=32,
                  text_size=34, action="locale", target="", parent="OptionsCard", sound=UI_CLICK)
        ui.button("Options", "BackButton", "Back", (960, 785), action="previous", parent="OptionsCard",
                  sound=UI_CLICK)
        ui.panel("Win", "WinCard", (960, 540), (640, 480), **UI_CARD)
        ui.text("Win", "WinTitle", "You win!", (960, 395), size=104, color=UI_ACCENT, parent="WinCard")
        ui.text("Win", "WinScore", "Score {score}", (960, 520), size=52, color=UI_SOFT_TEXT, parent="WinCard")
        ui.button("Win", "PlayAgainButton", "Play again", (960, 670), action="restart", parent="WinCard",
                  sound=UI_CLICK)
        # HUD: the score on a pill in the top-left corner, after a gem (a square turned 45 degrees).
        ui.panel("HUD", "ScorePanel", (180, 84), (264, 72), color="#0000008C", radius=36, anchor="top-left")
        gem = ui.panel("HUD", "GemIcon", (92, 84), (22, 22), color=UI_ACCENT, radius=4, parent="ScorePanel")
        gem.rotation_euler.z = radians(45)
        ui.text("HUD", "Score", "Score {score}", (124, 84), size=40, align="left", parent="ScorePanel")
        for canvas, button in (("Title", "PlayButton"), ("Pause", "ResumeButton"), ("Options", "BackButton"),
                               ("Win", "PlayAgainButton")):
            ui.canvas(canvas, focus=button)  # selected first when the menu opens
            ui.animate(canvas, **UI_TRANSITION)
        bpy.ops.wm.save_mainfile()
        path.with_name(path.name + "1").unlink(missing_ok=True)  # Blender's save version of the half-built file
    except Exception:
        bpy.ops.wm.read_homefile(use_empty=True)
        if new_folder:  # the project had no game UI: none again
            shutil.rmtree(path.parent, ignore_errors=True)
        else:  # ui/ held the template's translations: they stay
            for leftover in (path, path.with_suffix(".scene.json")):  # the file and its twin
                leftover.unlink(missing_ok=True)
        if new_export:  # nor an export of it (the save exports the screens and the translations)
            shutil.rmtree(ui_core.export_dir(root), ignore_errors=True)
        # else the next UI export drops what this file exported
        raise
    return path


# ------------------------------------------------------------------- async job

JOB = environment.JOB


def build_level(root: pathlib.Path) -> pathlib.Path:
    """Run the project's level script: its build(gs, root) makes, saves and exports the start
    scene and returns the .blend's path. The current scene, which the scripts build in, gets the
    templates' Color Management first; a script may set its own."""
    import runpy
    import sys

    if not trust.is_trusted(root):  # the project's own Python, run in this Blender
        raise ValueError(environment.untrusted_message())
    use_template_color_management(bpy.context.scene)
    script = pathlib.Path(root) / LEVEL_SCRIPT
    build = runpy.run_path(str(script), run_name="gamestudio_level").get("build")
    if not callable(build):
        raise ValueError(f"{script} has no build(gs, root) function")
    path = pathlib.Path(build(sys.modules[__package__], pathlib.Path(root)))
    remember(root)
    return path


def build_ui(root: pathlib.Path) -> pathlib.Path:
    """Run the project's UI script: its build(gs, root) writes <root>/ui/ui.blend with gs.gameui (start with
    gs.gameui.files.create_here(path, root, canvases), build, then save, which exports the canvases) and
    returns its path; this Blender is left with the UI file open (the caller opens the level next). On an
    error what it wrote is removed, as for the starter's UI (build_starter_ui), and the error is raised."""
    import runpy
    import sys

    from .core import gameui as ui_core

    root = pathlib.Path(root)
    if not trust.is_trusted(root):  # the project's own Python, run in this Blender
        raise ValueError(environment.untrusted_message())
    path = ui_core.ui_file(root)
    if path.exists():
        raise ValueError(f"{path} already exists")
    new_folder = not path.parent.exists()
    registry.reload_registry(root)  # the UI components are engine built-ins in the project's registry
    script = root / UI_SCRIPT
    try:
        build = runpy.run_path(str(script), run_name="gamestudio_ui").get("build")
        if not callable(build):
            raise ValueError(f"{script} has no build(gs, root) function")
        result = pathlib.Path(build(sys.modules[__package__], root))
        path.with_name(path.name + "1").unlink(missing_ok=True)  # Blender's save version of the half-built file
    except Exception:
        bpy.ops.wm.read_homefile(use_empty=True)
        if new_folder:  # the project had no game UI: none again
            shutil.rmtree(path.parent, ignore_errors=True)
            shutil.rmtree(ui_core.export_dir(root), ignore_errors=True)
        else:
            for leftover in (path, path.with_suffix(".scene.json")):
                leftover.unlink(missing_ok=True)
        raise
    return result


def open_shipped_scene(root: pathlib.Path) -> pathlib.Path:
    """Open the start scene a template shipped (a user template has the scenes of the game it was
    saved from) and export it, so Play works at once."""
    path = start_scene(root)
    backup = path.with_name(path.name + "1")  # Blender's save version, main.blend1
    had_backup = backup.exists()
    bpy.ops.wm.open_mainfile(filepath=str(path))  # _on_load_post links the project's packages
    project.invalidate_cache()
    registry.reload_registry(root)
    try:
        export.export_scene(bpy.context.scene)
        bpy.ops.wm.save_mainfile()  # keeps the ids the export may have given
        if not had_backup:
            backup.unlink(missing_ok=True)  # it would only back up the template's own copy
    except export.ExportError as exc:
        log.warning("Could not export %s: %s (Play exports it again)", path, exc)
    remember(root)
    return path


def name_shipped_ui(root: pathlib.Path, source_name: str = "") -> list[str]:
    """Give the game UI a template shipped (ui/ui.blend, as user templates have) the new project's name: the
    body of every text marked UI_PROJECT_NAME, and of unmarked texts whose body is exactly source_name (the
    name of the game the template was saved from, template.json "sourceName"). Opens the UI file in this
    Blender, which is left with it open (the caller opens the level next), and saves it, which exports its
    screens, only when a text changed. Returns the names of the texts it renamed."""
    from .core import gameui as ui_core

    root = pathlib.Path(root)
    path = ui_core.ui_file(root)
    name = str(project.read_config(root).get("name") or root.name)
    backup = path.with_name(path.name + "1")  # Blender's save version, ui.blend1
    had_backup = backup.exists()
    bpy.ops.wm.open_mainfile(filepath=str(path))  # opening exports the screens when they never were
    project.invalidate_cache()
    registry.reload_registry(root)
    renamed = []
    for scene in ui_core.canvas_scenes():
        for obj in scene.objects:
            if obj.type != "FONT" or obj.library is not None or obj.data.library is not None:
                continue
            body = obj.data.body
            if body != name and (obj.get(UI_PROJECT_NAME) or (source_name and body == source_name)):
                obj.data.body = name
                renamed.append(obj.name)
    if renamed:
        bpy.ops.wm.save_mainfile()  # exports the screens
        if not had_backup:
            backup.unlink(missing_ok=True)  # it would only back up the template's own copy
        log.info("Gave %s the game's name: %s", path, ", ".join(renamed))
    return renamed


def finish_new_project(root: pathlib.Path) -> pathlib.Path:
    """Open the new project's start scene (shipped, built by its level script or the starter) on
    the Game workspace. First build the game UI: the template's UI script (tools/build_ui.py), else the
    starter's for a template with "starterUi"; the game plays without it when that fails (logged). A UI
    file the template shipped gets the project's name instead (name_shipped_ui); New Project goes on when
    that fails (logged). It replaces the open file without asking: New Project runs it through
    open_new_project, once the open file has no unsaved changes or the user said what to do with them."""
    from .core import gameui as ui_core

    info = template_info(project.read_config(root).get("template")) or {}  # as scaffold() recorded it
    if ui_core.ui_file(root).exists():  # the template's, as saved: built-in templates build theirs
        try:
            name_shipped_ui(root, info.get("source_name", ""))
        except Exception:  # the level matters more: the UI keeps the name it was saved with
            log.exception("Could not give the game UI %s the project's name", ui_core.ui_file(root))
    elif info.get("starter_ui") or (pathlib.Path(root) / UI_SCRIPT).is_file():
        try:  # the template's own UI script wins over the starter's UI
            build_ui(root) if (pathlib.Path(root) / UI_SCRIPT).is_file() else build_starter_ui(root)
        except Exception:  # the level matters more: New Project goes on without the UI file
            log.exception("Could not build the game UI %s", ui_core.ui_file(root))
    if start_scene(root).is_file():
        path = open_shipped_scene(root)
    else:
        bpy.ops.wm.read_homefile(use_empty=False)
        path = build_level(root) if (pathlib.Path(root) / LEVEL_SCRIPT).is_file() else build_starter_scene(root)
    from . import workspaces

    workspaces.show_game()
    return path


def _new_project_done(root: pathlib.Path):
    def done(ok, _message):
        if not ok:  # the folder keeps its setup-pending marker
            JOB.message = (JOB.message.rstrip(".") + ". Open Project on the folder, or New Project with the same "
                           "name, resumes the setup")
            return
        if unsaved_changes():  # the start scene would replace them: the project waits for the user
            wait_to_open(root)
            return
        open_new_project(root)
        JOB.message = f"Project ready: {root}"
    return done


def unsaved_changes() -> bool:
    """The open file has changes that are not saved, agent edits included (a --background session does
    not mark those dirty; the bridge keeps track of them)."""
    from .bridge import api

    return api.is_dirty()


def _project_name(root) -> str:
    return str(project.read_config(root).get("name") or pathlib.Path(root).name)


def open_new_project(root) -> pathlib.Path:
    """The end of New Project: make and open the start scene (finish_new_project), then drop the setup
    note. Replaces the open file without asking. Returns the start scene's path."""
    root = pathlib.Path(root)
    path = finish_new_project(root)
    (project.gs_dir(root) / SETUP_PENDING).unlink(missing_ok=True)
    _waiting[:] = [r for r in _waiting if r != root.resolve()]
    log.info("Created %s", path)
    return path


# The projects whose setup ended while the open file had unsaved changes (wait_to_open), this session.
_waiting: list = []


def wait_to_open(root) -> None:
    """Keep a project New Project set up from replacing the open file, which has unsaved changes: its
    note says the setup is done ("ready"), Open Recent lists it, and a popup says it is ready, with Save,
    Don't Save and Later (ask_to_open). GameStudio > Open <name> opens it later; after a restart, Open
    Project or Open Recent on it does."""
    root = pathlib.Path(root)
    note = setup_pending(root) or {}
    note["ready"] = time.strftime("%Y-%m-%d %H:%M")
    try:
        (project.gs_dir(root) / SETUP_PENDING).write_text(json.dumps(note, indent=2, ensure_ascii=False) + "\n",
                                                          encoding="utf-8", newline="\n")
    except OSError as exc:  # Open Project then runs the setup again before it opens the project
        log.warning("Could not note that %s is set up: %s", root, exc)
    remember(root)
    if root.resolve() not in _waiting:
        _waiting.append(root.resolve())
    name = _project_name(root)
    JOB.message = f"Project ready: {name}. Open it with GameStudio > Open {name}"
    environment.notify(f"{name} is ready. The open file has unsaved changes, so it stays open: "
                       f"GameStudio > Open {name} opens the project", "INFO")
    if not bpy.app.background:
        bpy.app.timers.register(lambda: _say_ready(root), first_interval=0.1)


def _say_ready(root) -> None:
    window = any_window()
    if window is None:
        return None
    try:
        with bpy.context.temp_override(window=window, screen=window.screen):
            ask_to_open(root, ready=True)
    except RuntimeError as exc:
        log.warning("Could not say that %s is ready: %s", root, exc)
    return None


def ask_to_open(root, ready: bool = False) -> None:
    """Blender's question before a file with unsaved changes closes, for a project that waits: Save,
    Don't Save, Cancel (GAMESTUDIO_OT_project_open_new does what the user picks). With `ready`, as the
    setup ends: it says the project is ready, and Cancel is Later."""
    root = pathlib.Path(root)
    title = f"{_project_name(root)} is ready" if ready else "Save changes before closing?"
    bpy.context.window_manager.popup_menu(lambda menu, _context: _draw_question(menu.layout, root, ready),
                                          title=title, icon="CHECKMARK" if ready else "QUESTION")


def _draw_question(layout, root, ready: bool) -> None:
    file = bpy.path.basename(bpy.data.filepath) or "Untitled.blend"  # as Blender's own question names it
    layout.operator_context = "INVOKE_DEFAULT"
    layout.label(text=f"Save changes to {file} before opening {_project_name(root)}?" if ready else file)
    row = layout.row()
    row.emboss = "NORMAL"  # buttons, as in Blender's question, not menu items
    for text, unsaved in (("Save", "SAVE"), ("Don't Save", "DISCARD"), ("Later" if ready else "Cancel", "CANCEL")):
        op = row.operator("gamestudio.project_open_new", text=text)
        op.path, op.unsaved = str(root), unsaved


def waiting_projects() -> list[pathlib.Path]:
    """The projects of this session that wait to be opened (wait_to_open), while their note is there."""
    return [root for root in _waiting if (project.gs_dir(root) / SETUP_PENDING).is_file()]


def draw_waiting_projects(layout) -> None:
    """Open <name> for each project that waits (the GameStudio menu, the launcher)."""
    for root in waiting_projects():
        layout.operator("gamestudio.project_open_new", text=f"Open {_project_name(root)}",
                        icon="CHECKMARK").path = str(root)


def link_locally(root: pathlib.Path) -> list[str]:
    """Give the project its own node_modules link for each declared package that Node finds only in
    a parent folder (a project made inside another Node project, such as a development checkout):
    .mcp.json starts the MCP server by a path inside the project. Returns the names linked."""
    root = pathlib.Path(root)
    shared = toolchain.sdk(__file__)
    linked = []
    for name, spec in toolchain.declared_dependencies(toolchain.read_manifest(root)).items() if shared else ():
        link = root / "node_modules" / name
        target = toolchain.shared_package(name, shared)
        if os.path.lexists(link) or target is None or not toolchain.resolvable(root, name):
            continue  # already local, or missing: link_project and npm install deal with those
        if not name.startswith(toolchain.SCOPE) and not toolchain.satisfies(
                toolchain.read_manifest(target).get("version", ""), str(spec)):
            continue
        toolchain.make_link(target, link)
        for command, script in toolchain.package_bins(target).items():
            toolchain.write_bin_shims(root / "node_modules" / ".bin", command,
                                      f"../{name}/{pathlib.PurePosixPath(script)}")
        linked.append(name)
    return linked


def template_downloads(template: str) -> list[str]:
    """The third-party packages of a template's package.json that the shared runtime cannot link, so
    that `npm install` downloads them (a user template may add some)."""
    try:
        sources = template_files(template).get("package.json")
        manifest = json.loads(render_text(sources, {})) if sources else {}
    except (OSError, ValueError):
        return []  # an unknown template: scaffold() says so
    shared = toolchain.sdk(__file__)
    missing = []
    for name, spec in toolchain.declared_dependencies(manifest if isinstance(manifest, dict) else {}).items():
        if name.startswith(toolchain.SCOPE):
            continue  # GameStudio's own: linked, or dropped by scaffold()
        target = toolchain.shared_package(name, shared) if shared is not None else None
        if target is None or not toolchain.satisfies(toolchain.read_manifest(target).get("version", ""), str(spec)):
            missing.append(name)
    return missing


def offline_problem(root=None, template: str | None = None) -> str:
    """Why setting up a project cannot work now: it has to download something (Node.js, GameStudio's
    packages the first time, the project's or the template's packages) and online access is off.
    "" when it can. New Project asks before it writes anything."""
    if environment.online():
        return ""
    needed = environment.needs_download(root)
    if environment.npm_command() != "npm":
        needed = [n for n in needed if n != "node"]  # a custom npm command: Node.js is not downloaded
    packages = template_downloads(template) if template is not None and "runtime" not in needed else []
    what = [text for key, text in (("node", "Node.js"), ("runtime", "GameStudio's packages (the first time only)"))
            if key in needed]
    if "packages" in needed or packages:
        what.append("the project's packages" + (f" ({', '.join(packages)})" if packages else ""))
    if not what:
        return ""
    return (f"Setting up the project downloads {' and '.join(what)}, but online access is off. Turn on "
            "Preferences > System > Network > Allow Online Access, then try again.")


def setup_pending(root) -> dict | None:
    """New Project's note in a project whose setup has not finished ({"name", "template", "started"}, and
    "ready" once the setup is done but the start scene waits: wait_to_open), or None. It is written after
    scaffolding and removed once the start scene is open."""
    path = project.gs_dir(root) / SETUP_PENDING
    if not path.is_file():
        return None
    try:
        note = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        note = {}
    return note if isinstance(note, dict) else {}


def _start_setup(root: pathlib.Path, name: str) -> str:
    return environment.start(f"Creating {name}", root, build_registry=True, on_done=_new_project_done(root),
                             extra_steps=[lambda _job: link_locally(root)], browser=True)


def resume_setup(root) -> str:
    """Run New Project's setup again in a project whose setup failed or was cancelled (setup_pending):
    packages, components, then its start scene. Nothing in the folder is removed. Returns "" when it
    started (or asks first whether to trust the project, and starts once it is trusted), else why not."""
    root = pathlib.Path(root)
    if JOB.running():
        return f"Wait for '{JOB.title}' to finish"
    # Its npm scripts and its tools run: a folder with a setup note is not always one GameStudio made.
    problem = environment.require_trust(root, then=_resume_when_trusted(root))
    if problem:
        return "" if problem == environment.ASKING else problem
    problem = offline_problem(root)
    if problem:
        return problem
    name = (setup_pending(root) or {}).get("name") or project.read_config(root).get("name") or root.name
    return _start_setup(root, str(name))


def _resume_when_trusted(root):
    def done(ok, message):
        problem = resume_setup(root) if ok else message
        if problem:
            environment.notify(problem, "ERROR" if ok else "WARNING")
    return done


def create_project(root: pathlib.Path, name: str, template: str = "starter") -> str:
    """Scaffold a project and start setting it up; its start scene opens when it is ready. A folder
    whose setup did not finish is set up again instead (resume_setup). Returns "" when it started,
    else why not; nothing is written when setup would have to download while online access is off.
    Raises OSError or ValueError (nothing to set up)."""
    if JOB.running():
        return f"Wait for '{JOB.title}' to finish"
    if setup_pending(root) is not None:
        return resume_setup(root)
    problem = offline_problem(template=template)
    if problem:
        return problem
    root.mkdir(parents=True, exist_ok=True)
    scaffold(root, name, template)
    note = {"name": name, "template": template, "started": time.strftime("%Y-%m-%d %H:%M")}
    (project.gs_dir(root) / SETUP_PENDING).write_text(json.dumps(note, indent=2, ensure_ascii=False) + "\n",
                                                      encoding="utf-8", newline="\n")
    if trust.is_trusted(root):
        return _start_setup(root, name)
    # Made from a template GameStudio cannot vouch for (scaffold): ask once. Trusting the project trusts
    # its templates too, so the next project made from them does not ask.
    problem = environment.require_trust(root, then=_created_when_trusted(root, name, template))
    return "" if problem == environment.ASKING else problem


def _created_when_trusted(root, name: str, template: str):
    def done(ok, message):
        if not ok:
            environment.notify(message, "WARNING")
            return
        for folder in untrusted_templates(template):
            trust.trust(folder, "template")
        problem = _start_setup(root, name)
        if problem:
            environment.notify(problem, "ERROR")
    return done


# ----------------------------------------------------------------- user templates


class TemplateError(ValueError):
    """Save as Template refused: the message says why, `hint` what to do instead."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def has_unsaved_changes(root) -> bool:
    """True when the open .blend belongs to the project and has unsaved changes, agent edits included
    (a --background session does not mark those dirty; the bridge keeps track of them)."""
    if not bpy.data.filepath or not _inside(pathlib.Path(bpy.data.filepath), pathlib.Path(root)):
        return False
    from .bridge import api

    return api.is_dirty()


def project_files_for_template(root: pathlib.Path) -> tuple[list[str], list[str]]:
    """What Save as Template copies from a project (project-relative POSIX paths, see TEMPLATE_DIRS),
    and the other top-level names, which it leaves out (the NOT_IN_TEMPLATES folders aside)."""
    from .codex import generated_paths

    root = pathlib.Path(root)
    files, left_out = [], []
    generated = generated_paths(root)
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name in TEMPLATE_DIRS:
            for path in sorted(entry.rglob("*")):
                rel = path.relative_to(root).as_posix()
                if path.is_file() and not (NOT_IN_TEMPLATES & set(rel.split("/")) or rel in LOCAL_FILES
                                           or rel in generated or BACKUP_FILE.search(path.name)):
                    files.append(rel)
        elif entry.is_file() and entry.name in TEMPLATE_FILES:
            files.append(entry.name)
        elif entry.name not in NOT_IN_TEMPLATES:
            left_out.append(entry.name + ("/" if entry.is_dir() else ""))
    return files, left_out


def _edit_json(text: str, change) -> str:
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(data, dict):
        return text
    changed = change(dict(data))
    return text if changed == data else json.dumps(changed, indent=2, ensure_ascii=False) + "\n"


def _config_placeholders(config: dict) -> dict:
    """gamestudio.json: the name becomes {{name}}; the template and the engine version are recorded
    again by New Project."""
    return {"name": "{{name}}", **{k: v for k, v in config.items() if k not in ("name", "template", "engine")}}


def _manifest_placeholders(manifest: dict) -> dict:
    """package.json: the name becomes {{slug}}, and each @gamestudio package of PACKAGES {{<package>}}."""
    manifest["name"] = "{{slug}}"
    for field in ("dependencies", "devDependencies"):
        deps = manifest.get(field)
        if isinstance(deps, dict):
            short = {dep: dep[len(toolchain.SCOPE):] for dep in deps if dep.startswith(toolchain.SCOPE)}
            manifest[field] = {dep: "{{" + short[dep] + "}}" if short.get(dep) in PACKAGES else spec
                               for dep, spec in deps.items()}
    return manifest


def _to_placeholders(folder: pathlib.Path, name: str) -> list[str]:
    """Put the placeholders scaffold() fills in back into a copy of a project, where the built-in
    templates have them: {{name}} in gamestudio.json, the title of index.html, the title line of
    AGENTS.md, CLAUDE.md and ASSISTANT.md and `title: "<name>"` in code; {{slug}} and the @gamestudio packages
    ({{engine}}, ...) in package.json. A .mcp.json that reaches the MCP server in a parent folder
    gets the project's own path back, and `node` by name instead of this computer's path to it; so
    does .codex/config.toml (Codex's). Returns the files changed."""
    from .claude_code import MCP_ENTRY, portable_mcp_text
    from .codex import CONFIG, portable_codex_text

    plain = re.escape(name)
    title = re.compile(r"(\btitle\s*[:=]\s*)([\"'`])" + plain + r"\2")
    edits = {
        "gamestudio.json": lambda text: _edit_json(text, _config_placeholders),
        "package.json": lambda text: _edit_json(text, _manifest_placeholders),
        "index.html": lambda text: re.sub(r"(<title>\s*)" + plain + r"(\s*</title>)", r"\g<1>{{name}}\g<2>", text,
                                          count=1),
        "CLAUDE.md": lambda text: re.sub(r"\A# " + plain + r"(?=[:\r\n]|$)", "# {{name}}", text),
        ".mcp.json": lambda text: portable_mcp_text(re.sub(r"(?:\.\./)+" + re.escape(MCP_ENTRY), MCP_ENTRY, text)),
        CONFIG: portable_codex_text,
    }
    edits["ASSISTANT.md"] = edits["AGENTS.md"] = edits["CLAUDE.md"]
    code = sorted(p.relative_to(folder).as_posix() for p in (folder / "src").rglob("*")
                  if p.suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs"))
    for rel in code:
        edits[rel] = lambda text: title.sub(r"\g<1>\g<2>{{name}}\g<2>", text)
    changed = []
    for rel, edit in edits.items():
        path = folder / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # not in the project, or not text
        new = edit(text)
        if new != text:
            path.write_text(new, encoding="utf-8", newline="\n")
            changed.append(rel)
    return changed


def _template_warnings(root: pathlib.Path, folder: pathlib.Path, files: list[str]) -> list[str]:
    """What may not work in projects made from the copy at folder."""
    warnings = []
    start = start_scene(root).relative_to(root).as_posix()
    if start not in files:
        warnings.append(f"The start scene {start} does not exist: projects made from the template get a new one.")
    if bpy.data.filepath and _inside(pathlib.Path(bpy.data.filepath), root):
        for lib in bpy.data.libraries:
            if not _inside(pathlib.Path(bpy.path.abspath(lib.filepath, library=lib.library)), root):
                warnings.append(f"The open scene links {lib.filepath} from outside the project: projects made from "
                                "the template link that same file.")
    needles = {str(root), root.as_posix(), str(root).replace("\\", "\\\\")}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and is_text_file(path):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if any(needle in text for needle in needles):
                warnings.append(f"{path.relative_to(folder).as_posix()} names the project's folder ({root}), "
                                "which projects made from the template do not share.")
    return warnings


def _template_instructions(folder: pathlib.Path) -> list[str]:
    """Give the copy of a project at folder the instructions the built-in templates have: AGENTS.md and a
    CLAUDE.md that imports it. Instructions only in CLAUDE.md (or in it and GameStudio's unedited copy
    for Codex) move to AGENTS.md; separate files stay as they are. Returns warnings."""
    try:
        result = agentfiles.migrate_instructions(folder, consent=True)
    except ValueError as exc:  # not UTF-8 text: copied as it is
        return [f"Could not read the instructions for coding agents ({exc}): they are copied as they are."]
    if result["action"] == agentfiles.MOVED:
        log.info("The template gets the instructions of CLAUDE.md in AGENTS.md, which CLAUDE.md imports")
    if result["state"] == agentfiles.AGENTS_MISSING:
        return ["CLAUDE.md imports AGENTS.md, which the project does not have: projects made from the template "
                "get no instructions for coding agents. Add AGENTS.md and save again."]
    return []


TEMPLATE_BACKUPS = ".backups"  # in the user templates folder; replaced templates go here (dot: never a template)
KEEP_TEMPLATE_BACKUPS = 5  # per template id


def _replace_folder(new: pathlib.Path, target: pathlib.Path, keep: pathlib.Path | None = None) -> None:
    """Move the folder new to target, replacing what is there: that moves to keep when given, else it
    is deleted. If the move fails, target is as before."""
    old = target.with_name(f".{target.name}.old")
    shutil.rmtree(old, ignore_errors=True)
    if target.exists():
        target.rename(old)
    try:
        new.rename(target)
    except OSError:
        if old.exists() and not target.exists():
            old.rename(target)
        raise
    if keep is not None and old.exists():
        keep.parent.mkdir(parents=True, exist_ok=True)
        old.rename(keep)
    shutil.rmtree(old, ignore_errors=True)


def _template_backup(base: pathlib.Path, template_id: str) -> pathlib.Path:
    """A new folder name for the replaced copy of a template: <base>/.backups/<id>@<time>."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = base / TEMPLATE_BACKUPS
    backup, n = folder / f"{template_id}@{stamp}", 1
    while backup.exists():
        n += 1
        backup = folder / f"{template_id}@{stamp}-{n}"
    return backup


def _prune_template_backups(base: pathlib.Path, template_id: str) -> None:
    """Keep only the KEEP_TEMPLATE_BACKUPS newest backups of a template."""
    folder = base / TEMPLATE_BACKUPS
    if not folder.is_dir():
        return
    backups = sorted((d for d in folder.iterdir() if d.is_dir() and d.name.startswith(template_id + "@")),
                     key=lambda d: d.stat().st_mtime)
    for old in backups[:-KEEP_TEMPLATE_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)


def save_as_template(root, template_id: str, label: str = "", description: str = "", *, icon: str = "",
                     overwrite: bool = False, folder=None) -> dict:
    """Save a project as a user template that New Project lists: copy the files that
    project_files_for_template names to <folder>/<template_id>/ (folder: user_templates_dir()), put
    the placeholders back (_to_placeholders) and write its template.json, with the game's name as
    "sourceName" (New Project gives ui/ui.blend's texts that say it the new name: name_shipped_ui).
    Instructions only in CLAUDE.md move to the template's AGENTS.md, with a CLAUDE.md that imports it
    (_template_instructions). The project itself is not changed. Returns {"id", "label", "description",
    "path", "files", "bytes", "placeholders", "left_out", "warnings", "replaced"}: replaced is where the
    template it overwrote was moved
    (<folder>/.backups/<id>@<time>, the newest KEEP_TEMPLATE_BACKUPS kept), or None.

    Raises TemplateError (a ValueError) when template_id is not a valid id or is a built-in
    template's, when the template exists and overwrite is False, or when the project's open scene has
    unsaved changes; OSError when copying fails, in which case nothing is left behind."""
    root = pathlib.Path(root).resolve()
    if not (root / project.PROJECT_FILE).is_file():
        raise TemplateError(f"{root} is not a GameStudio project (it has no {project.PROJECT_FILE}).")
    template_id = str(template_id or "").strip()
    if not TEMPLATE_ID.fullmatch(template_id):
        raise TemplateError(f"'{template_id}' is not a valid template id.",
                            hint="Use up to 64 lowercase letters, digits, '-' and '_', starting with a letter or "
                                 "digit, such as 'arena-shooter'.")
    known = template_info(template_id)
    if known is not None and not known["user"]:
        raise TemplateError(f"'{template_id}' is the id of a built-in template.", hint="Pick another id.")
    config = project.read_config(root)
    name = str(config.get("name") or root.name)
    label = " ".join(str(label or "").split()) or template_id.replace("-", " ").replace("_", " ").title()
    description = " ".join(str(description or "").split()) or f"Starts as a copy of {name}"
    if len(label) > 40 or len(description) > 100:
        raise TemplateError("The label (40 characters at most) or the description (100) is too long.",
                            hint="New Project shows them on one line.")
    if has_unsaved_changes(root):
        raise TemplateError(f"{pathlib.Path(bpy.data.filepath).name} has unsaved changes.",
                            hint="Save the scene first, so the template gets what you see.")
    # New projects from the template run its scripts without asking (scaffold), so it must not be a way
    # around the question for this one.
    if not trust.is_trusted(root):
        raise TemplateError("This project is not trusted, so it cannot be saved as a template.",
                            hint="Trust it first: GameStudio > Trust This Project.")
    base = pathlib.Path(folder) if folder is not None else user_templates_dir()
    target = base / template_id
    if _inside(target, root) or _inside(root, target):
        raise TemplateError(f"The template would be saved to {target}, inside the project or around it.",
                            hint=f"Set {USER_TEMPLATES_ENV} to a folder outside the project.")
    if target.exists() and not overwrite:
        raise TemplateError(f"The template '{template_id}' already exists in {base}.",
                            hint="Pick another id, or replace it (overwrite).")
    files, left_out = project_files_for_template(root)
    backup = _template_backup(base, template_id) if target.exists() else None
    staging = base / f".{template_id}.saving"  # dot folders are never templates
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.mkdir(parents=True)
        size = 0
        for rel in files:
            dst = staging / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, dst)
            size += dst.stat().st_size
        instructions = _template_instructions(staging)
        placeholders = _to_placeholders(staging, name)
        _inline_tsconfig_base(staging, root)
        warnings = _template_warnings(root, staging, files) + instructions
        meta = {"label": label, "description": description, "icon": icon or USER_TEMPLATE_ICON,
                "sourceName": name,  # New Project renames ui/ui.blend's texts that say it (name_shipped_ui)
                "saved": time.strftime("%Y-%m-%d %H:%M"), "from": {"project": name}}
        if config.get("template"):
            meta["from"]["template"] = config["template"]
        (staging / TEMPLATE_INFO).write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                                             encoding="utf-8", newline="\n")
        _replace_folder(staging, target, keep=backup)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if backup is not None:
        _prune_template_backups(base, template_id)
    trust.trust(target, "template")  # saved from a trusted project: projects made from it are trusted
    log.info("Saved %s as the template %s in %s", root, template_id, target)
    return {"id": template_id, "label": label, "description": description, "path": str(target),
            "files": len(files), "bytes": size, "placeholders": placeholders, "left_out": left_out,
            "warnings": warnings, "replaced": str(backup) if backup is not None else None}


# --------------------------------------------------------------------- operators


_ENUM_ITEMS: list = []  # Blender needs the strings of dynamic enum items to stay referenced


def _templates_enum(_self, _context):
    items = [(t["id"], t["label"], t["description"]) for t in templates()] or [("starter", "Starter", "")]
    if items != _ENUM_ITEMS:
        _ENUM_ITEMS[:] = items
    return _ENUM_ITEMS


def _icon(name: str) -> str:
    icons = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items
    return name if name in icons else "FILE_BLANK"


def draw_template_picker(layout, operator) -> None:
    """One row per template: its name as a radio button and its one-line description. The user's
    own templates follow the built-in ones under a heading."""
    col = layout.column(align=True)
    col.label(text="Template")
    heading = False
    for info in templates():
        if info["user"] and not heading:
            heading = True
            col.label(text="Your templates")
        split = col.split(factor=0.3, align=True)
        split.prop_enum(operator, "template", info["id"], text=info["label"], icon=_icon(info["icon"]))
        row = split.row()
        row.active = operator.template == info["id"]
        row.label(text=info["description"])


class GAMESTUDIO_OT_project_new(bpy.types.Operator):
    bl_idname = "gamestudio.project_new"
    bl_label = "New Game Project"
    bl_description = "Create a new game project folder with a playable starter scene"

    name: StringProperty(name="Name", default="My Game")
    location: StringProperty(name="Location", subtype="DIR_PATH",
                             description="Folder the project folder is created in")
    template: EnumProperty(name="Template", items=_templates_enum)

    def invoke(self, context, event):
        if not self.location:
            docs = pathlib.Path.home() / "Documents" / "GameStudio"
            self.location = str(docs)
        # The first look into ~/Documents (the user templates, the new folder) happens here, on the click:
        # macOS asks for access then, not in the middle of drawing the dialog.
        templates()
        setup_pending(pathlib.Path(bpy.path.abspath(self.location)) / slugify(self.name))
        _notes.update(time=0.0)
        return context.window_manager.invoke_props_dialog(self, width=640, confirm_text="Create")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "name")
        layout.prop(self, "location")
        draw_template_picker(layout, self)
        target = pathlib.Path(bpy.path.abspath(self.location or ".")) / slugify(self.name)
        pending = setup_pending(target)
        if pending is not None:
            label = (template_info(str(pending.get("template") or "")) or {}).get("label", "its template")
            layout.label(text=f"{target} holds a project whose setup did not finish", icon="ERROR")
            layout.label(text=f"Create resumes it, from {label}, and keeps its files", icon="RECOVER_LAST")
        else:
            layout.label(text=f"Creates {target}", icon="FILE_FOLDER")
        draw_download_notes(layout)
        if unsaved_changes():
            layout.label(text="The open file has unsaved changes: GameStudio asks before the project replaces it",
                         icon="INFO")

    def execute(self, context):
        root = pathlib.Path(bpy.path.abspath(self.location)) / slugify(self.name)
        resuming = setup_pending(root) is not None
        if self.options.is_invoke:
            environment.give_consent()  # the dialog said what setting up downloads, and where
        try:
            problem = create_project(root, self.name, self.template)
        except OSError as exc:
            self.report({"ERROR"}, explain_os_error(exc, root.parent))
            return {"CANCELLED"}
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        verb = "Resuming the setup of" if resuming else "Creating"
        self.report({"INFO"}, f"{verb} {root}. Its start scene opens when the packages are ready")
        return {"FINISHED"}


_notes = {"time": 0.0, "needs": []}


def _download_needs() -> list:
    """What setting up a new project downloads (environment.Need), checked at most once a second: the
    dialog draws it on every redraw."""
    now = time.monotonic()
    if now - _notes["time"] > 1.0:
        _notes.update(time=now, needs=[n for n in environment.check(None, "new") if n.download])
    return _notes["needs"]


def draw_download_notes(layout, root=None) -> None:
    """What setting up will download, how much and where, shown before the user confirms: Create
    agrees to it (GameStudio's one-time setup, environment.py)."""
    needs = _download_needs()
    if not needs:
        return
    col = layout.column(align=True)
    col.active = False
    col.label(text="GameStudio sets itself up first (one time):", icon="IMPORT")
    for need in needs:
        size = f" (about {need.size_mb} MB)" if need.size_mb else ""
        col.label(text=f"{need.text[0].upper()}{need.text[1:]}{size}", icon="BLANK1")
    col.label(text=f"They go in {environment.folder_label()}", icon="BLANK1")
    if not environment.online():
        col.label(text="Online access is off (Preferences > System > Network)", icon="ERROR")
        row = layout.row()
        row.operator("screen.userpref_show", text="Open Network Preferences", icon="PREFERENCES").section = "SYSTEM"


def open_scene_file(path, ask: bool = False) -> bool:
    """Open a .blend in place of the open file. With `ask` (the user clicked Open) and unsaved changes,
    Blender asks first, as File > Open does: Save, Don't Save or Cancel (Save on a file never saved shows
    Save As). True when the file opened now; False while Blender asks: it opens once answered."""
    if ask and unsaved_changes():
        return "FINISHED" in _open_asking(path)
    bpy.ops.wm.open_mainfile(filepath=str(path))
    return True


def _open_asking(path) -> set:
    return bpy.ops.wm.open_mainfile("INVOKE_DEFAULT", filepath=str(path), display_file_selector=False)


def _open_waiting(op, root) -> set:
    """open_new_project for an operator: it reports what went wrong, and the project waits on."""
    name = _project_name(root)
    # Its level and UI scripts run: a folder with a "ready" note is not always one GameStudio made.
    problem = environment.require_trust(root, then=_open_when_trusted(root))
    if problem == environment.ASKING:
        return {"FINISHED"}
    if problem:
        op.report({"ERROR"}, problem)
        return {"CANCELLED"}
    try:
        open_new_project(root)
    except Exception as exc:  # noqa: BLE001 - a level or UI script failed: Open tries again
        log.exception("Could not open %s", root)
        op.report({"ERROR"}, f"Could not open {name}: {exc}")
        return {"CANCELLED"}
    op.report({"INFO"}, f"Opened {name}")
    return {"FINISHED"}


def _open_when_trusted(root):
    def done(ok, message):
        if not ok:
            environment.notify(message, "WARNING")
            return
        try:
            open_new_project(root)
        except Exception as exc:  # noqa: BLE001 - as _open_waiting: the project waits on
            log.exception("Could not open %s", root)
            environment.notify(f"Could not open {_project_name(root)}: {exc}", "ERROR")
    return done


class GAMESTUDIO_OT_project_open(bpy.types.Operator):
    bl_idname = "gamestudio.project_open"
    bl_label = "Open Game Project"
    bl_description = "Open a game project folder (the one containing gamestudio.json)"

    directory: StringProperty(subtype="DIR_PATH")
    path: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        """Rows for a specific project (recent, examples) show its full path as the tooltip."""
        path = getattr(properties, "path", "")
        bundle = toolchain.bundle_root(__file__) if path else None
        if bundle is not None and _inside(pathlib.Path(path), bundle):
            return f"Opens your copy of this example: {examples_home() / pathlib.Path(path).name}"
        return path or cls.bl_description

    def invoke(self, context, event):
        if self.path:
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        chosen = pathlib.Path(bpy.path.abspath(self.path or self.directory or ""))
        root = project.find_project_root(str(chosen / "gamestudio.json")) if chosen.name else None
        if root is None:
            self.report({"ERROR"}, f"No gamestudio.json in {chosen} or its parents.")
            return {"CANCELLED"}
        try:
            root = prepare_example(pathlib.Path(root))
        except OSError as exc:
            self.report({"ERROR"}, f"Could not copy the example to {examples_home()}: "
                                   f"{explain_os_error(exc, examples_home())}")
            return {"CANCELLED"}
        note = setup_pending(root)
        if note is not None and note.get("ready"):  # set up while the open file had unsaved changes
            if self.options.is_invoke and unsaved_changes():
                ask_to_open(root)
                return {"FINISHED"}
            return _open_waiting(self, root)
        if note is not None:  # New Project's setup failed or was cancelled: run it again
            problem = resume_setup(root)
            if problem:
                self.report({"ERROR"}, problem)
                return {"CANCELLED"}
            self.report({"INFO"}, f"Resuming the setup of {root}. Its start scene opens when the packages are ready")
            return {"FINISHED"}
        scene_file = start_scene(root)
        if not scene_file.is_file():
            self.report({"ERROR"}, f"The start scene {scene_file.relative_to(root)} does not exist yet.")
            return {"CANCELLED"}
        remember(root)
        if not open_scene_file(scene_file, ask=self.options.is_invoke):  # links what it can (_on_load_post)
            return {"FINISHED"}  # Blender asks first; once it opens the file, _on_load_post checks the setup
        # What the project still needs (an update's packages, downloads, its component list) is set up in
        # the background; the first time that downloads, GameStudio asks once.
        state = environment.ensure_ready(root, "open")
        if state in (environment.STARTED, environment.ASKING):
            self.report({"INFO"}, "Setting up GameStudio for this project (see the status bar)...")
        elif state != environment.READY:
            self.report({"WARNING"}, state)
        return {"FINISHED"}


UNSAVED_ITEMS = (
    ("ASK", "Ask", "Ask what to do with the open file's unsaved changes (from Python: refuse)"),
    ("SAVE", "Save", "Save the open file first"),
    ("DISCARD", "Don't Save", "Open the project without saving the open file's changes"),
    ("CANCEL", "Cancel", "Leave the open file as it is: the project waits"),
)


class GAMESTUDIO_OT_project_open_new(bpy.types.Operator):
    """GameStudio > Open <name>: the end of New Project for a project that waits (wait_to_open). Invoked,
    it asks first when the open file has unsaved changes (ask_to_open); from Python, `unsaved` says what
    to do (ASK refuses)."""

    bl_idname = "gamestudio.project_open_new"
    bl_label = "Open New Project"
    bl_description = ("Open the project New Project set up while the open file had unsaved changes: its start "
                      "scene is made and opened")

    path: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    unsaved: EnumProperty(name="Unsaved Changes", items=UNSAVED_ITEMS, default="ASK", options={"SKIP_SAVE"})

    def invoke(self, context, event):
        if self.unsaved == "ASK" and unsaved_changes():
            ask_to_open(self.path)
            return {"FINISHED"}
        return self.execute(context)

    def execute(self, context):
        if self.unsaved == "CANCEL":
            return {"CANCELLED"}
        root = pathlib.Path(bpy.path.abspath(self.path)) if self.path else None
        if root is None or not (setup_pending(root) or {}).get("ready"):
            self.report({"ERROR"}, f"No new project waits to be opened in {self.path or 'this folder'}")
            return {"CANCELLED"}
        if unsaved_changes():
            if self.unsaved == "ASK":
                self.report({"ERROR"}, "The open file has unsaved changes: save it first, or pass "
                                       "unsaved='SAVE' or 'DISCARD'")
                return {"CANCELLED"}
            if self.unsaved == "SAVE" and not bpy.data.filepath:
                if not self.options.is_invoke:
                    self.report({"ERROR"}, "The open file was never saved: save it first, or pass unsaved='DISCARD'")
                    return {"CANCELLED"}
                # As Blender's own question does: Save As, and the project waits for the next Open.
                bpy.ops.wm.save_as_mainfile("INVOKE_DEFAULT")
                name = _project_name(root)
                self.report({"INFO"}, f"Save the file, then open {name} again (GameStudio > Open {name})")
                return {"FINISHED"}
            if self.unsaved == "SAVE" and "FINISHED" not in bpy.ops.wm.save_mainfile():
                self.report({"ERROR"}, "The open file could not be saved, so the project did not open")
                return {"CANCELLED"}
        return _open_waiting(self, root)


class GAMESTUDIO_OT_project_reveal(bpy.types.Operator):
    bl_idname = "gamestudio.project_reveal"
    bl_label = "Show Project Folder"
    bl_description = "Open the current project's folder in the file manager"

    @classmethod
    def poll(cls, context):
        return project.project_root() is not None

    def execute(self, context):
        bpy.ops.wm.path_open(filepath=str(project.project_root()))
        return {"FINISHED"}


class GAMESTUDIO_OT_project_save_template(bpy.types.Operator):
    bl_idname = "gamestudio.project_save_template"
    bl_label = "Save as Template"
    bl_description = ("Save the current project as a template that New Project offers: its scenes, code, assets, "
                      "instructions for coding agents (AGENTS.md) and custom skills, with the game's name left for "
                      "the new project")

    template_id: StringProperty(name="Id", options={"SKIP_SAVE"},
                                description="The template's folder name: lowercase letters, digits, - and _")
    label: StringProperty(name="Label", options={"SKIP_SAVE"}, description="Its name in the New Project picker")
    summary: StringProperty(name="Description", options={"SKIP_SAVE"},
                            description="One line for the New Project picker")
    overwrite: BoolProperty(name="Replace the existing template", options={"SKIP_SAVE"},
                            description="Replace the template saved with this id")
    save_changes: BoolProperty(name="Save the scene first", default=True,
                               description="Save the open scene first when it has unsaved changes")

    @classmethod
    def poll(cls, context):
        return project.project_root() is not None

    def invoke(self, context, event):
        root = project.project_root()
        name = project.read_config(root).get("name") or root.name
        self.template_id = self.template_id or slugify(name)[:64]
        self.label = self.label or name[:40]
        return context.window_manager.invoke_props_dialog(self, width=520, confirm_text="Save Template")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "template_id")
        layout.prop(self, "label")
        layout.prop(self, "summary")
        target = user_templates_dir() / (self.template_id or "?")
        layout.label(text=f"Saves to {target}", icon="FILE_FOLDER")
        if target.exists():
            layout.prop(self, "overwrite")
        root = project.project_root()
        if root is not None and has_unsaved_changes(root):
            layout.prop(self, "save_changes")

    def execute(self, context):
        root = project.project_root()
        if self.save_changes and has_unsaved_changes(root):
            bpy.ops.wm.save_mainfile()
        try:
            result = save_as_template(root, self.template_id, self.label, self.summary, overwrite=self.overwrite)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, f"{exc} {getattr(exc, 'hint', '')}".strip())
            return {"CANCELLED"}
        for warning in result["warnings"]:
            self.report({"WARNING"}, warning)
        self.report({"INFO"}, f"Saved the template '{result['label']}' to {result['path']}. New Project lists it")
        return {"FINISHED"}


class GAMESTUDIO_MT_projects(bpy.types.Menu):
    """The GameStudio menu in the top bar. editors/project.py adds the code editor and script
    actions, menus.py the workspaces, Node.js and help."""

    bl_idname = "GAMESTUDIO_MT_projects"
    bl_label = "GameStudio"

    def draw(self, context):
        draw_project_actions(self.layout, context)


class GAMESTUDIO_MT_recent(bpy.types.Menu):
    bl_idname = "GAMESTUDIO_MT_recent"
    bl_label = "Open Recent Project"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_DEFAULT"
        entries = recent()
        if not entries:
            layout.label(text="No recent projects yet")
        for entry in entries:
            op = layout.operator("gamestudio.project_open", text=entry.get("name", "?"), icon="FILE_FOLDER")
            op.path = entry["path"]


class GAMESTUDIO_MT_examples(bpy.types.Menu):
    bl_idname = "GAMESTUDIO_MT_examples"
    bl_label = "Examples"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "INVOKE_DEFAULT"
        found = examples()
        if not found:
            layout.label(text="No examples in this installation")
        for root in found:
            op = layout.operator("gamestudio.project_open", text=project.read_config(root).get("name", root.name),
                                 icon="PACKAGE")
            op.path = str(root)


def draw_project_actions(layout, context) -> None:
    layout.operator_context = "INVOKE_DEFAULT"
    layout.operator("gamestudio.project_new", text="New Project...", icon="FILE_NEW")
    draw_waiting_projects(layout)  # Open <name>: a new project that waits for the open file's unsaved changes
    layout.operator("gamestudio.project_open", text="Open Project...", icon="FILE_FOLDER")
    layout.menu("GAMESTUDIO_MT_recent", text="Open Recent", icon="RECOVER_LAST")
    layout.menu("GAMESTUDIO_MT_examples", icon="PACKAGE")
    layout.separator()
    layout.operator("gamestudio.project_reveal", icon="FOLDER_REDIRECT")
    layout.operator("gamestudio.project_save_template", text="Save as Template...", icon="PRESET_NEW")
    environment.draw_menu_status(layout, project.project_root())


CLASSES = (GAMESTUDIO_OT_project_new, GAMESTUDIO_OT_project_open, GAMESTUDIO_OT_project_open_new,
           GAMESTUDIO_OT_project_reveal, GAMESTUDIO_OT_project_save_template, GAMESTUDIO_MT_recent,
           GAMESTUDIO_MT_examples, GAMESTUDIO_MT_projects)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    # Once, before any project loads (loading one adds it to the list): the projects already worked on
    # are trusted, so this version does not ask about them.
    if trust.migrate([entry["path"] for entry in recent() if entry.get("path") and not entry.get("untrusted")]):
        log.info("Trusted the recent projects (GameStudio runs only trusted projects' scripts now)")
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
