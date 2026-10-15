# SPDX-License-Identifier: GPL-3.0-or-later
"""New Component / New System: TypeScript files from script_templates/.

A component named HealthPack goes to src/components/health-pack.ts and a system
named Regen (or RegenSystem) to src/systems/regen.ts as RegenSystem, the naming
in the starter template's ASSISTANT.md. Those folders are what src/main.ts loads with
import.meta.glob('./components/*.ts' | './systems/*.ts', { eager: true }), so a
new file needs no registration. The dev server sees it, reloads the game and,
for a component, regenerates .gamestudio/components.json, which puts it in the
Inspector's Add Component menu.

Nothing is ever overwritten: an existing file or a name that is already taken
(in the registry or in another script) is refused with a ScaffoldError.
"""

import pathlib
import re

from . import registry, scripts

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "script_templates"
# The engine's rule for component names (API.md §2), used for systems too.
NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]{0,40}$")
SYSTEM_SUFFIX = "System"
KINDS = ("component", "system")


class ScaffoldError(ValueError):
    pass


def kebab(name: str) -> str:
    """HealthPack -> health-pack, HUDLabel -> hud-label, Level2Door -> level2-door."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "-", name)
    return words.lower()


def check_name(name: str, kind: str) -> str:
    """The name to define (systems get the System suffix). Raises ScaffoldError when it is not PascalCase."""
    if kind not in KINDS:
        raise ScaffoldError(f"Unknown script kind '{kind}'")
    if not name:
        raise ScaffoldError(f"Enter a name for the {kind}")
    base = name[:-len(SYSTEM_SUFFIX)] if kind == "system" and name.endswith(SYSTEM_SUFFIX) else name
    if not NAME_RE.match(name) or not base:
        raise ScaffoldError(f"'{name}' is not a valid name. Use PascalCase: a capital letter, then letters "
                            f"and digits (e.g. {'HealthPack' if kind == 'component' else 'Regen'})")
    full = base + SYSTEM_SUFFIX if kind == "system" else name
    if not NAME_RE.match(full):
        raise ScaffoldError(f"'{name}' is too long (at most 41 characters)")
    return full


def file_name(name: str, kind: str) -> str:
    base = name[:-len(SYSTEM_SUFFIX)] if kind == "system" and name.endswith(SYSTEM_SUFFIX) else name
    return kebab(base) + ".ts"


def target_path(root, name: str, kind: str) -> pathlib.Path:
    return pathlib.Path(root) / scripts.FOLDERS[kind] / file_name(name, kind)


def _import_path(source: str) -> str:
    """'src/components/coin.ts' -> '../components/coin' (as seen from src/systems/)."""
    rel = source.replace("\\", "/")
    if rel.startswith("src/"):
        rel = rel[4:]
    return "../" + re.sub(r"\.(m?[jt]s|tsx)$", "", rel)


def component_import(component: str, root=None) -> tuple:
    """(engine names, extra import line) for using `component` in a system."""
    entry = registry.schema_ref(component)
    if entry is not None and entry.get("builtin"):
        return [component], ""
    source = entry.get("source") if entry else None
    if not source and root is not None:
        for script in scripts.listing(root)["component"]:
            if component in script["names"]:
                source = script["rel"]
                break
    if not source:
        raise ScaffoldError(f"Component '{component}' was not found in the registry or in src/components")
    return [], f"import {{ {component} }} from '{_import_path(source)}';\n"


def render(kind: str, name: str, component: str = "", root=None) -> str:
    """The new file's text."""
    if kind == "component":
        template = "component.ts"
        values = {"name": name}
    elif component:
        engine, extra = component_import(component, root)
        template = "system.ts"
        values = {"name": name, "component": component, "engine": "".join(f", {n}" for n in engine),
                  "imports": extra}
    else:
        template = "system_global.ts"
        values = {"name": name}
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), text)


def taken(name: str, kind: str, root) -> str:
    """Why `name` cannot be used ("" when it can)."""
    if kind == "component":
        entry = registry.schema_ref(name)
        if entry is not None:
            where = "built in to the engine" if entry.get("builtin") else f"defined in {entry.get('source') or '?'}"
            return f"A component named {name} already exists ({where})"
        if name in scripts.component_names(root):
            return f"A component named {name} is already defined in src/components"
    else:
        if name in scripts.system_names(root):
            return f"A system named {name} is already defined in src/systems"
    return ""


def plan(root, kind: str, text: str, component: str = "") -> dict:
    """Validate a New Component/System request: {"name", "path", "rel"}. Raises ScaffoldError."""
    if root is None:
        raise ScaffoldError("Save the .blend inside a GameStudio project first")
    name = check_name((text or "").strip(), kind)
    path = target_path(root, name, kind)
    rel = path.relative_to(root).as_posix()
    if path.exists():
        raise ScaffoldError(f"{rel} already exists. Pick another name")
    reason = taken(name, kind, root)
    if reason:
        raise ScaffoldError(reason)
    return {"name": name, "path": path, "rel": rel, "kind": kind, "component": component}


def create(root, kind: str, text: str, component: str = "") -> dict:
    """Write the new script. Returns plan() plus "line" (where to start editing) and "warning"
    (a discovery problem, or ""). Never overwrites."""
    info = plan(root, kind, text, component)
    content = render(kind, info["name"], component, root)
    info["path"].parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(info["path"], "x", encoding="utf-8", newline="\n") as f:  # "x": fails if it appeared meanwhile
            f.write(content)
    except FileExistsError:
        raise ScaffoldError(f"{info['rel']} already exists. Pick another name") from None
    except OSError as exc:
        raise ScaffoldError(f"Could not write {info['rel']}: {exc}") from exc
    scripts.invalidate()
    info["line"] = focus_line(content)
    info["warning"] = discovery_warning(root, kind)
    return info


def focus_line(text: str) -> int:
    """The 1-based line to put the cursor on: the first field of a component, the body of update()."""
    lines = text.splitlines()
    for marker in ("fields: {", "update("):
        for number, line in enumerate(lines, start=1):
            if marker in line:
                return min(number + 1, len(lines))
    return 1


def discovery_warning(root, kind: str) -> str:
    """A hint when src/main.ts does not load the folder (a customised entry point), else ""."""
    main = pathlib.Path(root) / "src" / "main.ts"
    try:
        text = main.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    folder = "components" if kind == "component" else "systems"
    if re.search(r"import\.meta\.glob\(\s*\[?\s*['\"`]\./" + folder + r"/\*\.ts['\"`]", text):
        return ""
    return (f"src/main.ts does not load src/{folder}/*.ts. Add "
            f"import.meta.glob('./{folder}/*.ts', {{ eager: true }}); or import the new file there")
