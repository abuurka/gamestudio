# SPDX-License-Identifier: GPL-3.0-or-later
"""Translations of the game UI (docs/GAME-UI.md §11.3): the tables ui/locales/<code>.json, and what the ui_strings
and ui_translate tools read and write.

A table is {"name": "Français", "strings": {"<authored text>": "<translation>"}}: keys are text bodies exactly as
authored, bindings included ("Score {score}"), and a translation keeps the same bindings. gamestudio.json "locale"
(default "en") is the language the texts are authored in, so it has no table.

    strings(locale=None, root=None) -> dict      every translatable text of the UI with the table's translation
    translate(locale, strings, name=None, remove=(), root=None) -> dict   create or update a table; the counts

The texts come from the open UI file, or, with another file open, from the exported canvases
(.gamestudio/export/ui/*.ui.json, as the game has them). Writing a table exports the tables
(core.gameui_export.export_locales) and sends them to running games (core.gameui_live.send_locales).
"""

from __future__ import annotations

import json
import pathlib
import re

import bpy

from ..core import gameui as core, project
from ..core.log import get_logger
from . import build, files

log = get_logger(__name__)

# BCP 47-ish: a language (2-3 letters), an optional script (4 letters) and an optional region (2 letters or 3
# digits): "fr", "pt-BR", "zh-Hans", "zh-Hant-TW", "es-419".
CODE_RE = re.compile(r"^([A-Za-z]{2,3})(?:[-_]([A-Za-z]{4}))?(?:[-_]([A-Za-z]{2}|[0-9]{3}))?$")
BINDING_RE = re.compile(r"\{([^{}]*)\}")
NAME_MAX = 60
EXAMPLE = '{"locale": "fr", "name": "Français", "strings": {"Play": "Jouer", "Score {score}": "Score {score}"}}'


def _error(message: str, hint: str | None = None, code: str | None = None):
    return build.GameUIError(message, hint=hint, code=code)


def normalize(code) -> str:
    """A language code in its usual form ("pt-br" -> "pt-BR", "zh_hans" -> "zh-Hans"); GameUIError otherwise."""
    match = CODE_RE.match(code.strip()) if isinstance(code, str) else None
    if match is None:
        raise _error(f"{code!r} is not a language code.",
                     hint="Use a code such as 'fr', 'de', 'ja', 'pt-BR' or 'zh-Hans' (a language, then an optional "
                          "script and region).", code="invalid_locale")
    language, script, region = match.groups()
    return "-".join(part for part in (language.lower(), script.title() if script else None,
                                      region.upper() if region else None) if part)


def _root(root=None) -> pathlib.Path:
    root = pathlib.Path(root) if root is not None else project.project_root()
    if root is None:
        raise _error("No GameStudio project: the open file is not saved inside one.",
                     hint="Open a level or the UI file of the project first.", code="no_project")
    return root


def locales_dir(root) -> pathlib.Path:
    return core.locales_dir(root)  # <root>/ui/locales


def source_locale(root) -> str:
    """The language the texts are authored in: gamestudio.json "locale", else "en"."""
    value = project.read_config(root).get("locale")
    try:
        return normalize(value) if isinstance(value, str) and value.strip() else "en"
    except build.GameUIError:
        return value.strip()


def table_path(root, code: str) -> pathlib.Path:
    return locales_dir(root) / f"{code}.json"


def read_table(root, code: str) -> dict | None:
    """The table of a language ({"name", "strings"}), or None when there is none. GameUIError for a broken one."""
    path = table_path(root, code)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _error(f"{files.project_relative(path)} is not valid JSON: {exc}",
                     hint="Fix or delete the file, then try again.") from None
    if not isinstance(data, dict):
        raise _error(f"{files.project_relative(path)} must hold an object {{name, strings}}.")
    strings = data.get("strings") if isinstance(data.get("strings"), dict) else {}
    return {"name": str(data.get("name") or code),
            "strings": {k: v for k, v in strings.items() if isinstance(k, str) and isinstance(v, str)}}


def tables(root) -> list[dict]:
    """[{code, name, file, strings}] of every table in ui/locales/, by code."""
    folder = locales_dir(root)
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        try:
            code = normalize(path.stem)
            table = read_table(root, code)
        except build.GameUIError as exc:
            log.warning("%s", exc)
            continue
        if table is not None:
            out.append({"code": code, "name": table["name"], "file": files.project_relative(path),
                        "strings": len(table["strings"])})
    return out


def bindings(text: str) -> list[str]:
    """The bindings of a text body ("{score}", "{time:m:ss}"), sorted; {{ and }} are literal braces."""
    plain = text.replace("{{", "").replace("}}", "")
    return sorted(m.group(1).strip() for m in BINDING_RE.finditer(plain))


def translatable(text: str) -> bool:
    """A body with words to translate: letters outside its bindings ("Score {score}" yes, "{score}" and "3" no)."""
    plain = BINDING_RE.sub("", text.replace("{{", "").replace("}}", ""))
    return any(ch.isalpha() for ch in plain)


# --- The texts of the UI ---------------------------------------------------------------------------------------


def _open_texts() -> list[dict]:
    """[{canvas, element, text, project_name?}] of the open UI file's text elements, canvas by canvas in draw
    order (button labels included)."""
    out = []
    for scene in core.canvas_scenes():
        for obj in build.elements(scene):
            if obj.type == "FONT":
                entry = {"canvas": scene.name, "element": obj.name, "text": obj.data.body}
                if obj.get(build.PROJECT_NAME_PROP):
                    entry["project_name"] = True
                out.append(entry)
    return out


def _exported_texts(root) -> list[dict]:
    """The same from the exported canvases (.gamestudio/export/ui/), in the index's order. The export has no
    project_name mark, so a text whose body is the game's name (gamestudio.json "name") has it."""
    folder = core.export_dir(root)
    try:
        index = json.loads((folder / core.INDEX_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    game = str(project.read_config(root).get("name") or pathlib.Path(root).name)
    out = []
    for canvas, entry in (index.get("canvases") or {}).items():
        try:
            data = json.loads((folder / entry["file"]).read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for element in data.get("elements") or ():
            body = (element.get("text") or {}).get("body") if element.get("type") == "text" else None
            if isinstance(body, str):
                item = {"canvas": canvas, "element": element.get("name"), "text": body}
                if body == game:
                    item["project_name"] = True
                out.append(item)
    return out


def ui_texts(root) -> tuple[str, list[dict]]:
    """(where they come from: the open UI file's path or "export", [{canvas, element, text, ...}])."""
    if core.is_ui_file():
        return files.project_relative(bpy.data.filepath), _open_texts()
    return "export", _exported_texts(root)


def _grouped(texts) -> list[dict]:
    """The translatable texts, each once: {text, elements: ["Canvas/Name", ...], project_name?}."""
    out, by_text = [], {}
    for entry in texts:
        text = entry["text"]
        if not translatable(text):
            continue
        item = by_text.get(text)
        if item is None:
            item = by_text[text] = {"text": text, "elements": []}
            out.append(item)
        item["elements"].append(f"{entry['canvas']}/{entry['element']}")
        if entry.get("project_name"):
            item["project_name"] = True
    return out


# --- ui_strings and ui_translate ------------------------------------------------------------------------------


def _missing(items, known) -> int:
    """The texts a table lacks, without the game's name (project_name): a title usually stays as it is."""
    return sum(1 for item in items if known.get(item["text"]) is None and not item.get("project_name"))


def strings(locale=None, root=None) -> dict:
    """Every translatable text of the UI (each once, with the elements that show it) and, with a locale, its
    translation or None, plus the table's strings no element uses (code strings for ctx.ui.translate, or texts
    that changed since). missing leaves out the game's name (project_name), which is listed all the same."""
    root = _root(root)
    source, texts = ui_texts(root)
    items = _grouped(texts)
    out = {"source": source, "source_locale": source_locale(root), "locales": tables(root)}
    if locale is not None:
        code = normalize(locale)
        table = read_table(root, code)
        known = (table or {}).get("strings", {})
        for item in items:
            item["translation"] = known.get(item["text"])
        used = {item["text"] for item in items}
        out.update(locale=code, name=table["name"] if table else None,
                   file=files.project_relative(table_path(root, code)), exists=table is not None)
        out["texts"] = items
        out["translated"] = sum(1 for item in items if item["translation"] is not None)
        out["missing"] = _missing(items, known)
        out["unused"] = {k: v for k, v in known.items() if k not in used}
    else:
        out["texts"] = items
    if not texts:
        out["hint"] = ("The UI has no texts yet." if source != "export" else
                       "No exported UI texts: open ui/ui.blend (ui_open), or export the UI first.")
    return out


def _check_strings(given) -> dict:
    if not isinstance(given, dict):
        raise _error(f"strings must be an object of authored text: translation, got {given!r}", hint=EXAMPLE)
    problems = []
    for text, translation in given.items():
        if not isinstance(text, str) or not text:
            problems.append(f"{text!r}: the authored text must be a non-empty string")
        elif not isinstance(translation, str) or not translation.strip():
            problems.append(f"{text!r}: the translation must be a non-empty string (remove drops a text)")
        elif bindings(text) != bindings(translation):
            want = ", ".join("{" + b + "}" for b in bindings(text)) or "none"
            problems.append(f"{text!r} -> {translation!r}: a translation keeps the text's bindings ({want})")
    if problems:
        raise _error("Some strings cannot be used: " + "; ".join(problems[:10])
                     + (f" (and {len(problems) - 10} more)" if len(problems) > 10 else "") + ".",
                     hint="Bindings such as {score} or {time:m:ss} stay as they are, in any order. " + EXAMPLE)
    return dict(given)


def translate(locale, strings, name=None, remove=(), root=None) -> dict:
    """Create or update ui/locales/<locale>.json: add or change `strings` ({authored text: translation}), drop the
    texts in `remove`, and set the language's own `name` (needed when the table is new). Everything is checked
    before the file is written. Returns the counts, and what the UI still lacks in this language."""
    root = _root(root)
    code = normalize(locale)
    base = source_locale(root)
    if code == base:
        raise _error(f"'{code}' is the language the UI is written in (gamestudio.json locale), so it has no table.",
                     hint="Translate into another language, e.g. 'fr' or 'de'.")
    given = _check_strings(strings)
    if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX):
        raise _error(f"name must be the language's own name, 1 to {NAME_MAX} characters (e.g. 'Français'), "
                     f"got {name!r}")
    remove = list(remove or ())
    if any(not isinstance(text, str) for text in remove):
        raise _error(f"remove must be a list of authored texts, got {remove!r}")
    both = sorted(set(remove) & set(given))
    if both:
        raise _error(f"{both[0]!r} is in both strings and remove.")
    table = read_table(root, code)
    created = table is None
    if created and name is None:
        raise _error(f"There is no {code} table yet: name the language to create it.",
                     hint="Pass name, the language's own name as players see it in a Language button, e.g. "
                          + EXAMPLE)
    known = dict(table["strings"]) if table else {}
    added = sum(1 for text in given if text not in known)
    changed = sum(1 for text, value in given.items() if text in known and known[text] != value)
    removed = [text for text in remove if text in known]
    for text in removed:
        del known[text]
    known.update(given)
    data = {"name": name.strip() if name is not None else table["name"], "strings": known}
    path = table_path(root, code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("Wrote %s (%d strings)", path, len(known))
    source, texts = ui_texts(root)
    items = _grouped(texts)
    used = {item["text"] for item in items}
    out = {"locale": code, "name": data["name"], "file": files.project_relative(path), "created": created,
           "added": added, "changed": changed, "removed": len(removed), "strings": len(known),
           "missing": _missing(items, known),
           "unused": sum(1 for text in known if text not in used)}
    not_found = [text for text in remove if text not in removed]
    if not_found:
        out["not_found"] = not_found
    out.update(export_locales(root))
    return out


def export_locales(root) -> dict:
    """Export the translation tables so the game has them (core.gameui_export.export_locales: the tables in
    .gamestudio/export/ui/locales/ and the index's sourceLocale and locales), and send them to running games
    (gameui_live.send_locales, ui.locales). Returns {"exported": {"locales": [codes], "changed", "warnings"?},
    "sent": bool}, or {"export_error": message}: the table is written either way, and the next export or Play
    takes it. Never raises."""
    from ..core import gameui_export, gameui_live

    try:
        result = gameui_export.export_locales(root)
    except Exception as exc:  # noqa: BLE001 - the table is written; the export can follow on the next save or Play
        log.warning("Exporting the UI translations failed: %s", exc)
        return {"export_error": str(exc)}
    exported = {"locales": sorted(result.get("locales") or ())}
    if "changed" in result:
        exported["changed"] = bool(result["changed"])
    if result.get("warnings"):
        exported["warnings"] = list(result["warnings"])[:10]
    sent = False
    send = getattr(gameui_live, "send_locales", None)
    if send is not None:
        try:
            sent = bool(send())
        except Exception:  # noqa: BLE001 - a game that is not there, or a link that broke: the export stands
            log.exception("Sending the UI translations failed")
    return {"exported": exported, "sent": sent}
