# SPDX-License-Identifier: GPL-3.0-or-later
"""GameStudio's first-run setup: what game projects need on this computer, checked before the actions
that need it, and set up on a worker thread with progress in the UI. Nobody runs npm, installs
Node.js or a browser by hand.

  Node.js         the system's when it is 22.12+, else the pinned LTS that GameStudio downloads into
                  the extension's user folder (core/nodejs.py)
  game packages   installed extension only: the bundled @gamestudio packages, copied to the user
                  folder, and the third-party packages they and the projects use (three, vite,
                  puppeteer-core, ...), filled by one `npm install` there the first time, at the
                  versions core/runtime.lock.json lists (core/toolchain.py). An update of the
                  extension copies its packages again, offline when it needs nothing new.
  game browser    Edge, Chrome or Chromium; else Chrome Headless Shell, which @gamestudio/devserver's
                  gamestudio-browser downloads into <user folder>/browser. The Game View, the Scene
                  view and the agent's headless playtests run the game in it.
  project         links into the shared packages (links into ANOTHER GameStudio install are replaced;
                  a development checkout's are left alone), `npm install` for whatever they lack, and
                  the component registry (`npm run registry`) when it is missing or older than the
                  packages.

ensure_ready(root, purpose, then) is the one readiness check. Play, the Scene view, New Project,
Open Project (Open Recent, Examples), Open in Claude Code and loading a project's scene call it (the
last one from a timer, check_later). With nothing missing it costs a few cached file checks and
returns READY; else it starts the setup job, or asks first, and calls then(ok, message) when the job
ends, so the action goes on: Play plays, the Scene view turns on, Claude Code opens.

Consent: the first plan of a session that downloads anything asks once (GAMESTUDIO_OT_setup: what,
about how big, where it goes). Local work (links, the registry, the copy after an update) never asks.
The answer holds for the session; the auto_setup preference (or GAMESTUDIO_AUTO_SETUP=1) skips the
question. Downloads respect Preferences > System > Network > Allow Online Access; the dialog offers
to turn it on. Without a browser (declined, offline) Play shows the game in a window instead
(core/play.py falls back when core/stream.py cannot stream).

Trust: the project's own code (its packages' install scripts, `npm run registry`, the dev server) runs
only in a project GameStudio trusts (core/trust.py). Before it would run in another, GameStudio asks once
a session per project (GAMESTUDIO_OT_trust_project: Trust, or Cancel for not now); without the answer the
shared setup and the links go on, the rest waits, and Play and the Scene view say how to trust it. With
nobody to ask (no window, an agent's call) it says so instead, for the agent to pass on.

One job runs at a time (JOB). Its steps only touch files and processes; the main thread polls it with
a timer, redraws the UI (status bar, GameStudio menu, launcher) and runs its continuations. A failure
says what failed in one sentence, with Retry and Show Log.
"""

import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from typing import NamedTuple

import bpy

from .core import nodejs, project, registry, toolchain, trust
from .core.log import get_logger
from .core.util import any_window, tag_redraw

log = get_logger(__name__)

TEMPLATE_PACKAGE = pathlib.Path(__file__).resolve().parent / "project_templates" / "starter" / "package.json"
NPM_INSTALL = ["install", "--no-audit", "--no-fund", "--prefer-offline"]
# The game packages: npm installs the lock beside their package.json (core/runtime.lock.json) and runs
# no install scripts (only fsevents, macOS, has one, and its prebuilt copy works without it). `npm
# install`, not `npm ci`: it changes only what the lock changed, where `npm ci` deletes node_modules
# and downloads all of it again on every update (the setup empties npm's cache).
RUNTIME_INSTALL = [*NPM_INSTALL, "--ignore-scripts"]
SHOW_FAILURE_FOR = 120.0
SETUP_TITLE = "Setting up GameStudio"
SETUP_LOG = "setup.log"           # <user folder>/setup.log: what each setup did
BROWSER_SCRIPT = "gamestudio-browser.js"  # @gamestudio/devserver/bin: `install` and `path`
AUTO_SETUP_ENV = "GAMESTUDIO_AUTO_SETUP"  # 1: set up without asking (scripts, Blender without a window)
OFFLINE = ("Blender's online access is off (Preferences > System > Network > Allow Online Access), so GameStudio "
           "cannot download what it needs")
# About how much each download is (compressed), for the consent dialog. Node.js comes from node.lock.json.
RUNTIME_MB = 70            # the game packages, the first time (41 MB of packages, the rest their metadata)
RUNTIME_UPDATE_MB = 5      # what an update of the extension adds
BROWSER_MB = 110           # Chrome Headless Shell
PACKAGES_MB = 5            # packages a project adds to the template's
READY_TTL = 30.0           # a check that found nothing missing holds this long
RERUN_GUARD = 30.0         # the same setup right after it succeeded means it did not help: say so instead

READY, STARTED, ASKING = "ready", "started", "asking"
DOWNLOADS = ("node", "runtime", "browser", "packages")
# What each purpose cannot do without; the rest is set up when allowed but skipped when not.
ESSENTIAL = {
    "play": {"node", "runtime", "refresh", "packages", "links"},
    "sceneview": {"node", "runtime", "refresh", "packages", "links", "browser"},
    "claude": {"node", "runtime", "refresh", "packages", "links"},
    "codex": {"node", "runtime", "refresh", "packages", "links"},
}
# Play and the Scene view: the dev server writes it.
REGISTRY_PURPOSES = ("open", "load", "new", "claude", "codex", "setup")
QUIET = ("load", "open")   # set up in the background: a declined question is not asked again
# What runs the project's own code: `npm install` (its packages' install scripts) and `npm run registry`.
# Links run nothing.
PROJECT_CODE = ("packages", "registry")
RUNS_PROJECT = ("play", "sceneview")  # their action runs it too: the game's dev server
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SetupError(RuntimeError):
    pass


def user_dir() -> pathlib.Path:
    """The extension's user folder; a folder in Blender's config when the package is not loaded as
    an extension (the add-on tests import it from the checkout)."""
    try:
        return pathlib.Path(bpy.utils.extension_path_user(__package__, create=True))
    except ValueError:
        return pathlib.Path(bpy.utils.user_resource("CONFIG", path="gamestudio", create=True))


def online() -> bool:
    """Preferences > System > Network > Allow Online Access."""
    return bool(getattr(bpy.app, "online_access", True))


def npm_command() -> str:
    from .prefs import get_prefs

    return (get_prefs().npm_command or "npm").strip()


def template_dependencies() -> dict:
    """The third-party packages of the project template, which the shared runtime provides."""
    return toolchain.third_party_dependencies(toolchain.read_manifest(TEMPLATE_PACKAGE.parent))


# --- The game browser -------------------------------------------------------------------------


def find_browser() -> str | None:
    """Edge, Chrome or Chromium (GAMESTUDIO_BROWSER first), else the browser GameStudio downloaded."""
    from .core import stream

    return stream.browser_path()


def browser_installer() -> pathlib.Path | None:
    """@gamestudio/devserver's bin/gamestudio-browser.js: in the shared packages, else in the bundle
    (the setup copies it to the shared packages first). None in a GameStudio without one."""
    shared = toolchain.sdk(__file__)
    for packages in (shared.packages if shared is not None else None, toolchain.bundled_packages(__file__)):
        if packages is not None:
            script = pathlib.Path(packages) / "devserver" / "bin" / BROWSER_SCRIPT
            if script.is_file():
                return script
    return None


# --- What is missing --------------------------------------------------------------------------


class Need(NamedTuple):
    key: str        # "node", "runtime", "refresh", "browser", "packages", "links", "registry"
    text: str       # what it is, for the user
    size_mb: int    # about how much it downloads (0: nothing)

    @property
    def download(self) -> bool:
        return self.key in DOWNLOADS


_ready: dict = {}        # (root, purpose) -> when a check found nothing missing
_known: dict = {}        # (root, purpose) -> the needs the last check found (drawing reads this)


def invalidate() -> None:
    """Check again next time (a setup ended, a file loaded, a preference changed)."""
    _ready.clear()
    try:
        from .core import stream

        stream._available_cache.clear()
    except Exception:  # noqa: BLE001 - no stream client in this build
        pass


def _key(root, purpose) -> tuple:
    return (str(pathlib.Path(root).resolve()) if root is not None else "", purpose)


def check(root=None, purpose: str = "setup") -> list[Need]:
    """What `purpose` needs that this computer or the project lacks, in the order the setup does it;
    [] when everything is there. File checks only (and `node --version` now and then), cached while
    nothing is missing; never call it from drawing code (cached_needs)."""
    key = _key(root, purpose)
    when = _ready.get(key)
    if when is not None and time.monotonic() - when < READY_TTL:
        return []
    needs = _check(pathlib.Path(root) if root is not None else None, purpose)
    _known[key] = needs
    _known[_key(None, "setup")] = [n for n in needs if n.key in ("node", "runtime", "refresh", "browser")]
    if needs:
        _ready.pop(key, None)
    else:
        _ready[key] = time.monotonic()
    return needs


def cached_needs(root=None, purpose: str | None = None) -> list[Need]:
    """What the last check found (for drawing: no file access). Any purpose of the project, or the
    computer's needs without a project."""
    if purpose is not None:
        return list(_known.get(_key(root, purpose), []))
    if root is not None:
        prefix = _key(root, "")[0]
        for (path, _purpose), needs in _known.items():
            if path == prefix and needs:
                return list(needs)
    return list(_known.get(_key(None, "setup"), []))


def _server_running(root: pathlib.Path) -> bool:
    try:
        from .core import devserver

        return devserver.running_session(root, check_port=False) is not None
    except Exception:  # noqa: BLE001 - a broken session file: check the project
        return False


def _check(root: pathlib.Path | None, purpose: str) -> list[Need]:
    needs = []
    if npm_command() == "npm" and not toolchain.node_status(__file__)["ok"]:
        entry = nodejs.release()
        needs.append(Need("node", f"Node.js {entry['version']}" if entry else "Node.js",
                          int(entry["size_mb"]) if entry else 0))
    template = template_dependencies()
    state = toolchain.shared_runtime_state(template, __file__)
    if state == "install":
        first = not toolchain.shared_runtime_installed_once()
        needs.append(Need("runtime", "the game packages (three.js, Vite, TypeScript, ...)" if first
                          else "the game packages this update adds", RUNTIME_MB if first else RUNTIME_UPDATE_MB))
    elif state == "refresh":
        needs.append(Need("refresh", "copies the game packages of this GameStudio version", 0))
    if find_browser() is None and browser_installer() is not None:
        needs.append(Need("browser", "a game browser (there is no Chrome or Edge)", browser_size_mb()))
    if root is not None and (root / "package.json").is_file() and not _server_running(root):
        needs += _project_needs(root, purpose, pending=set() if not state else
                                set(template) | {f"{toolchain.SCOPE}{n}" for n in _bundled_names()})
    return needs


def _bundled_names() -> list[str]:
    packages = toolchain.bundled_packages(__file__)
    return toolchain.package_names(packages) if packages is not None else []


def _project_needs(root: pathlib.Path, purpose: str, pending: set) -> list[Need]:
    """The project's part: `pending` are names the shared runtime provides once its setup is done."""
    shared = toolchain.sdk(__file__)
    missing = toolchain.missing_dependencies(root)
    unlinkable = [name for name in toolchain.unlinkable_dependencies(root, shared) if name not in pending]
    foreign = toolchain.foreign_links(root, shared)
    needs = []
    if unlinkable:
        shown = ", ".join(unlinkable[:3]) + (", ..." if len(unlinkable) > 3 else "")
        needs.append(Need("packages", f"the packages this project adds ({shown})", PACKAGES_MB))
    if foreign or set(missing) - set(unlinkable):
        needs.append(Need("links", "links the project to them", 0))
    if purpose in REGISTRY_PURPOSES and registry_stale(root):
        needs.append(Need("registry", "reads its components", 0))
    return needs


def registry_stale(root) -> bool:
    """The project's .gamestudio/components.json is missing, or older than the shared packages it was
    read with (an update since). Projects without a `registry` script have none."""
    scripts = toolchain.read_manifest(root).get("scripts") or {}
    if "registry" not in scripts:
        return False
    try:
        written = project.registry_path(root).stat().st_mtime
    except OSError:
        return True
    return written < toolchain.shared_runtime_time()


def needs_download(root=None) -> list[str]:
    """What a project's setup would download: "node", "runtime", "packages"; [] when everything is
    here (the game browser aside: Play works without it)."""
    keys = [n.key for n in _check(pathlib.Path(root) if root is not None else None, "new")]
    return [key for key in ("node", "runtime", "packages") if key in keys]


def missing_essentials(root, purpose: str) -> list[Need]:
    """What `purpose` cannot do without and is still missing (after a declined or failed setup)."""
    return [n for n in check(root, purpose) if n.key in ESSENTIAL.get(purpose, ())]


SHORT_NAMES = {"node": "Node.js", "runtime": "game packages", "browser": "a game browser",
               "packages": "the project's packages"}
NOUNS = {"node": "Node.js", "runtime": "the game packages", "refresh": "the updated game packages",
         "browser": "the game browser", "packages": "the project's packages", "links": "the project's links",
         "registry": "the component list"}


def _summary(needs: list[Need]) -> str:
    texts = [n.text for n in needs]
    if len(texts) > 1:
        text = ", ".join(texts[:-1]) + " and " + texts[-1]
    else:
        text = texts[0] if texts else ""
    size = sum(n.size_mb for n in needs)
    return f"{text} (about {size} MB)" if size else text


# --- Consent ----------------------------------------------------------------------------------

_consent = None      # this session's answer: True (set up), False (not now), None (not asked)
_asking = None       # while the dialog is open: {"root", "purpose", "then": [callbacks]}


def consented() -> bool:
    from .prefs import get_prefs

    if _consent is True or getattr(get_prefs(), "auto_setup", False):
        return True
    return os.environ.get(AUTO_SETUP_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def give_consent() -> None:
    """The user agreed to the downloads (the New Project dialog lists them)."""
    global _consent
    _consent = True


def declined() -> bool:
    return _consent is False


def _can_ask() -> bool:
    return not bpy.app.background and any_window() is not None


ASK_TIMEOUT = 600.0  # a dialog nobody answered (Blender did not report its closing): ask again after this


def _ask(root, purpose: str, then) -> None:
    """Show the consent dialog (from a timer: the caller may be loading a file or inside a dialog)."""
    global _asking
    _asking = {"root": root, "purpose": purpose, "then": [then] if then is not None else [],
               "since": time.monotonic()}

    def show():
        if _asking is None:
            return None
        if JOB.running():
            return 0.5  # a local setup started meanwhile: ask once it is done
        window = any_window()
        if window is None:
            _answer(False)
            return None
        try:
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.ops.gamestudio.setup("INVOKE_DEFAULT")
        except RuntimeError as exc:
            log.warning("Could not ask before setting up GameStudio: %s", exc)
            _answer(False)
        return None

    bpy.app.timers.register(show, first_interval=0.05, persistent=True)


def _answer(yes: bool) -> None:
    """The consent dialog closed: set up and go on, or tell the waiting actions."""
    global _consent, _asking
    pending, _asking = _asking, None
    _consent = bool(yes)
    invalidate()
    if pending is None:
        return
    callbacks = pending["then"]
    if not yes:
        _call(callbacks, False, "GameStudio's setup was skipped")
        return
    state = ensure_ready(pending["root"], pending["purpose"], then=_fan_out(callbacks), ask=False)
    if state == READY:
        _call(callbacks, True, "")
    elif state not in (STARTED, ASKING):
        _call(callbacks, False, state)


def _fan_out(callbacks):
    return (lambda ok, message: _call(callbacks, ok, message)) if callbacks else None


def _call(callbacks, ok: bool, message: str) -> None:
    for callback in list(callbacks):
        try:
            callback(ok, message)
        except Exception:  # noqa: BLE001 - one broken continuation must not stop the others
            log.exception("A continuation after GameStudio's setup failed")


# --- Trust ------------------------------------------------------------------------------------

_trusting = None       # while the trust question is open: {"root", "then": [callbacks], "since"}
_not_now: set = set()  # the projects (trust.key) the person said not now to, this session


def can_ask_trust() -> bool:
    """Someone can answer the trust question: Blender has a window, and no agent's call runs (the agent
    would wait for an answer only the person can give)."""
    return _can_ask() and not _agent_calling()


def _agent_calling() -> bool:
    from .bridge import api

    return getattr(api, "calls", 0) > 0


def untrusted_message() -> str:
    """Why the project's code does not run, and how to trust it."""
    return trust.ASK if can_ask_trust() else trust.ASK_USER


def said_not_now(root) -> bool:
    return trust.key(root) in _not_now


def require_trust(root, then=None) -> str:
    """"" when GameStudio may run the project's code. Else ASKING when it asks (once a session per
    project; then(ok, message) follows the answer), or why it cannot now, to report as is."""
    if trust.is_trusted(root):
        return ""
    if can_ask_trust() and not said_not_now(root):
        ask_trust(root, then)
        return ASKING
    return untrusted_message()


def ask_trust(root, then=None) -> None:
    """Ask whether to trust the project (from a timer, as _ask does). One question at a time: another
    action waits for the open one's answer."""
    global _trusting
    if _trusting is not None and time.monotonic() - _trusting["since"] < ASK_TIMEOUT:
        if then is not None:
            _trusting["then"].append(then)
        return
    _trusting = {"root": pathlib.Path(root), "then": [then] if then is not None else [], "since": time.monotonic()}

    def show():
        if _trusting is None:
            return None
        window = any_window()
        if window is None:
            answer_trust(False)
            return None
        try:
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.ops.gamestudio.trust_project("INVOKE_DEFAULT")
        except RuntimeError as exc:
            log.warning("Could not ask whether to trust %s: %s", root, exc)
            answer_trust(False)
        return None

    bpy.app.timers.register(show, first_interval=0.05, persistent=True)


def answer_trust(yes: bool) -> None:
    """The trust question closed: remember the answer and tell the actions waiting for it."""
    global _trusting
    pending, _trusting = _trusting, None
    if pending is None:
        return
    root = pending["root"]
    if yes:
        trust.trust(root)
        _not_now.discard(trust.key(root))
    else:
        _not_now.add(trust.key(root))
        log.info("GameStudio does not run the scripts of %s for now (GameStudio > Trust This Project)", root)
    invalidate()
    _call(pending["then"], yes, "" if yes else untrusted_message())


def _after_trust(root, purpose: str, then):
    """ensure_ready()'s continuation once the trust question is answered: check again, now trusted or
    without the project's code."""
    then = then or (lambda _ok, _message: None)

    def resume(_ok, _message):
        state = ensure_ready(root, purpose, then)
        if state == READY:
            then(True, "")
        elif state not in (STARTED, ASKING):
            then(False, state)
    return resume


# --- The readiness check ----------------------------------------------------------------------

_retry = None        # the last setup: {"root", "purpose", "then"} (Retry)
_last_run = None     # (root key, needs keys, finished ok at) of the last setup job


def ensure_ready(root=None, purpose: str = "setup", then=None, *, ask: bool = True) -> str:
    """Make `purpose` possible: "play", "sceneview", "new", "open", "load", "claude", "codex" or "setup".

    Returns READY when nothing is missing (go on now; `then` is not called), STARTED when a setup job
    runs (or one was running) and ASKING while the consent dialog is open; then(ok, message) follows
    when it ends. Anything else is a message saying why it cannot happen now (offline, declined), to
    report as is."""
    global _asking, _trusting
    root = pathlib.Path(root) if root is not None else None
    if JOB.running():
        JOB.then(_after_job(root, purpose, then))
        return STARTED
    if _asking is not None and time.monotonic() - _asking.get("since", 0.0) > ASK_TIMEOUT:
        _asking = None
    if _asking is not None:
        if then is not None:
            _asking["then"].append(then)
        return ASKING
    if _trusting is not None and time.monotonic() - _trusting["since"] > ASK_TIMEOUT:
        _trusting = None
    if _trusting is not None:
        _trusting["then"].append(_after_trust(root, purpose, then))
        return ASKING
    needs = check(root, purpose)
    essential = ESSENTIAL.get(purpose, set())
    if root is not None and not trust.is_trusted(root):
        code = [n for n in needs if n.key in PROJECT_CODE]
        if code or purpose in RUNS_PROJECT:
            if ask and can_ask_trust() and not said_not_now(root):
                ask_trust(root, _after_trust(root, purpose, then))
                return ASKING
            needs = [n for n in needs if n.key not in PROJECT_CODE]  # the shared setup and the links go on
            if purpose in RUNS_PROJECT or any(n.key in essential for n in code):
                return untrusted_message()
    if not needs:
        return READY
    downloads = [n for n in needs if n.download]
    if downloads and not (consented() and online()):
        wanted = [n for n in downloads if n.key in essential]
        if ask and _can_ask() and (_consent is None or (wanted and purpose not in QUIET)):
            _ask(root, purpose, then)
            return ASKING
        needs = [n for n in needs if not n.download]  # declined, offline or nobody to ask: local work only
        if wanted:
            return _cannot(purpose, wanted)
    if not needs:
        return READY
    key = (_key(root, purpose)[0], tuple(n.key for n in needs))
    if _last_run is not None and _last_run[:2] == key and time.monotonic() - _last_run[2] < RERUN_GUARD:
        missing = ", ".join(NOUNS.get(n.key, n.text) for n in needs)
        return f"GameStudio's setup finished, but this is still missing: {missing}. See {user_dir() / SETUP_LOG}"
    _retry_state(root, purpose, then)
    _run(root, purpose, needs, then)
    return STARTED


def _cannot(purpose: str, wanted: list[Need]) -> str:
    actions = {"play": "Play", "sceneview": "The Scene view", "claude": "Claude Code", "codex": "Codex"}
    action = actions.get(purpose, "This")
    what = _summary(wanted)
    if consented() and not online():
        return f"{action} needs {what}, but {OFFLINE}. Turn online access on, then try again"
    if not _can_ask() and not consented():
        return (f"{action} needs {what}, which GameStudio downloads once it may: Blender runs without a window "
                f"here, so set {AUTO_SETUP_ENV}=1 or turn on Set Up Automatically in GameStudio's preferences")
    return f"{action} needs GameStudio's one-time setup ({what}): GameStudio > Set Up GameStudio..."


def _retry_state(root, purpose, then) -> None:
    global _retry
    _retry = {"root": root, "purpose": purpose, "then": then}


def _after_job(root, purpose: str, then):
    """A continuation for an action that came while a job ran: check again once it ends (another
    project, or what that job did not cover)."""
    then = then or (lambda _ok, _message: None)

    def resume(ok, message):
        if not ok:
            then(False, message)
            return
        invalidate()
        state = ensure_ready(root, purpose, then)
        if state == READY:
            then(True, "")
        elif state not in (STARTED, ASKING):
            then(False, state)
    return resume


def check_later(root, purpose: str = "load", delay: float = 0.3) -> None:
    """ensure_ready() from a timer: after a file loads, so loading never waits and never shows a
    dialog in the middle of it."""
    key = _key(root, purpose)
    if key in _later:
        return
    _later.add(key)

    def run():
        _later.discard(key)
        try:
            state = ensure_ready(root, purpose)
            if state not in (READY, STARTED, ASKING):
                log.info("%s", state)
        except Exception:  # noqa: BLE001 - never break loading a file
            log.exception("Checking GameStudio's setup failed")
        return None

    bpy.app.timers.register(run, first_interval=delay, persistent=True)


_later: set = set()


# --- The job ----------------------------------------------------------------------------------


class _Job:
    def __init__(self):
        self.state = ""            # "", "running", "done", "failed"
        self.title = ""
        self.message = ""
        self.progress = None       # 0..1 while a download knows its size
        self.root = None           # the project being set up, if any
        self.purpose = ""
        self.downloads = False     # the job downloads something
        self.log_path = None
        self.step = 0              # the step running (1-based) of `steps`
        self.steps = 0
        self.warnings = []         # what did not work but did not stop the job (the component list)
        self.started = 0.0
        self.finished = 0.0
        self.callbacks = []
        self._on_done = None
        self._cancel = threading.Event()

    def running(self) -> bool:
        return self.state == "running"

    def status_text(self) -> str:
        if self.progress is not None and self.running():
            return f"{self.message} {int(self.progress * 100)}%"
        return self.message

    def progress_text(self) -> str:
        """The status bar's and the menu's line: the title, which step of how many, what it does."""
        if not self.running():
            return self.message
        steps = f" {self.step}/{self.steps}" if self.steps > 1 and self.step else ""
        return f"{self.title}{steps}: {self.status_text()}"

    def then(self, callback) -> None:
        """Call callback(ok, message) when the running job ends."""
        self.callbacks.append(callback)

    # Worker thread ------------------------------------------------------------------------

    def set(self, message: str, progress: float | None = None) -> None:
        self.message, self.progress = message, progress

    def _popen(self, argv, cwd, out, stdout):
        env = toolchain.child_env(dict(os.environ, FORCE_COLOR="0", NO_COLOR="1"))
        kwargs = {"creationflags": _NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
        out.write(f"# {' '.join(map(str, argv))}  (cwd {cwd})\n")
        out.flush()
        try:
            return subprocess.Popen([str(a) for a in argv], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=subprocess.STDOUT, **kwargs)
        except OSError as exc:
            raise SetupError(f"Could not run {argv[0]}: {exc}") from exc

    def _check_exit(self, proc, log_path: pathlib.Path, label: str) -> None:
        if proc.returncode == 0:
            return
        if _network_failure(log_path):
            raise SetupError(f"{label} failed: the package server (registry.npmjs.org) could not be reached. "
                             "Check the internet connection, then Retry")
        last = _last_error_line(log_path)
        raise SetupError(f"{label} failed (exit code {proc.returncode}){': ' + last if last else ''}. "
                         f"See {log_path}")

    def run(self, argv: list[str], cwd: pathlib.Path, log_path: pathlib.Path, label: str) -> None:
        """Run a command, its output appended to log_path, showing `label` and the elapsed time."""
        self.log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8", errors="replace") as out:
            proc = self._popen(argv, cwd, out, out)
            begun = time.monotonic()
            while proc.poll() is None:
                if self._cancel.is_set():
                    from .core.devserver import kill_tree

                    kill_tree(proc.pid)
                    raise SetupError("Cancelled")
                self.set(f"{label}... {int(time.monotonic() - begun)} s")
                time.sleep(0.25)
        self._check_exit(proc, log_path, label)

    def run_lines(self, argv: list[str], cwd: pathlib.Path, log_path: pathlib.Path, label: str, on_line) -> None:
        """Run a command that reports on its output, one line at a time: on_line(line) for each (the
        lines go to log_path too)."""
        self.log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8", errors="replace") as out:
            proc = self._popen(argv, cwd, out, subprocess.PIPE)
            self.set(f"{label}...")
            lines = []

            def read():
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    out.write(line + "\n")
                    out.flush()
                    lines.append(line)

            reader = threading.Thread(target=read, daemon=True, name="gamestudio-setup-output")
            reader.start()
            seen = 0
            while proc.poll() is None or reader.is_alive():
                if self._cancel.is_set():
                    from .core.devserver import kill_tree

                    kill_tree(proc.pid)
                    raise SetupError("Cancelled")
                while seen < len(lines):
                    on_line(lines[seen])
                    seen += 1
                time.sleep(0.1)
            while seen < len(lines):
                on_line(lines[seen])
                seen += 1
        self._check_exit(proc, log_path, label)

    def note(self, text: str) -> None:
        """A line in <user folder>/setup.log (any thread: no bpy here)."""
        path = _setup_log()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8", errors="replace") as out:
                out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")
        except OSError:
            pass

    # Main thread --------------------------------------------------------------------------

    def start(self, title: str, steps, on_done=None, root=None, purpose: str = "", downloads: bool = False) -> bool:
        if self.running():
            return False
        callbacks = []
        self.__init__()
        self.state, self.title, self.message, self.started = "running", title, f"{title}...", time.time()
        self.root, self._on_done, self.purpose, self.downloads = root, on_done, purpose, downloads
        self.callbacks = callbacks
        steps = list(steps)
        self.steps = len(steps)
        self.note(f"{title}: {self.steps} step(s){f' for {root}' if root is not None else ''}")

        def work():
            try:
                for number, step in enumerate(steps, 1):
                    if self._cancel.is_set():
                        raise SetupError("Cancelled")
                    self.step = number
                    step(self)
                ok, message = True, f"{title}: done"
            except SetupError as exc:
                ok, message = False, str(exc)
            except Exception as exc:  # report, never leave the job running
                log.exception("%s failed", title)
                ok, message = False, f"{title} failed: {exc}"
            if ok and self.warnings:
                message += f" ({'; '.join(self.warnings)})"
            self.note(message)
            self.message, self.progress, self.finished = message, None, time.time()
            if not ok and self.log_path is None:
                self.log_path = _setup_log()
            self.state = "done" if ok else "failed"

        threading.Thread(target=work, daemon=True, name="gamestudio-setup").start()
        if not bpy.app.timers.is_registered(_tick):
            bpy.app.timers.register(_tick, first_interval=0.2, persistent=True)
        tag_redraw()
        return True

    def cancel(self) -> None:
        self._cancel.set()


JOB = _Job()


def _setup_log() -> pathlib.Path | None:
    folder = toolchain.user_dir()
    return folder / SETUP_LOG if folder is not None else None


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ERROR = re.compile(r"(?<![a-z])error|ERR!", re.IGNORECASE)  # "npm error", "[TSCONFIG_ERROR]"
_NETWORK = re.compile(r"\b(ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENETUNREACH|EHOSTUNREACH)\b|"
                      r"request to https?://\S+ failed")


def _last_error_line(log_path: pathlib.Path) -> str:
    """The most telling line of a failed command: its first error line, else its last line."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [_ANSI.sub("", line).strip() for line in text.splitlines() if not line.startswith("#")]
    lines = [line for line in lines if line]
    errors = [line for line in lines if _ERROR.search(line)]
    return (errors[0] if errors else lines[-1] if lines else "")[:200]


def _network_failure(log_path: pathlib.Path) -> bool:
    """npm failed because it could not reach the registry (offline, DNS, a proxy)."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    tail = text[text.rfind("\n# "):] if "\n# " in text else text  # the last command only
    return bool(_NETWORK.search(tail))


def _tick():
    if JOB.running():
        _redraw()
        return 0.25
    callback, JOB._on_done = JOB._on_done, None
    callbacks, JOB.callbacks = JOB.callbacks, []
    ok = JOB.state == "done"
    if JOB.state == "failed":
        log.error(JOB.message)
    elif ok:
        log.info(JOB.message)
    for finish in ([callback] if callback is not None else []) + callbacks:
        try:
            finish(ok, JOB.message)
        except Exception as exc:  # report, never crash the timer
            log.exception("Finishing '%s' failed", JOB.title)
            JOB.state, JOB.message = "failed", f"{JOB.title} failed: {exc}"
    _redraw()
    return None


def _redraw() -> None:
    tag_redraw(area_types=("VIEW_3D", "PROPERTIES", "TOPBAR", "STATUSBAR", "PREFERENCES"), region_type=None)


def wait(timeout: float = 600.0) -> bool:
    """Run the job to the end on this thread (tests and scripts without an event loop)."""
    deadline = time.monotonic() + timeout
    while JOB.running() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not JOB.running():
        _tick()
    return JOB.state == "done"


# --- Steps (worker thread) ------------------------------------------------------------------


def _step_node(folder: pathlib.Path, allowed: bool):
    def step(job):
        if toolchain.node_status(__file__)["ok"]:
            return
        if not allowed:
            raise SetupError(f"{OFFLINE} (Node.js)")
        try:
            nodejs.install(folder, progress=job.set, cancel=job._cancel)
        except nodejs.NodeInstallError as exc:
            raise SetupError(str(exc)) from exc
        toolchain.activate(__file__)
        job.note(f"Node.js {nodejs.installed(folder).get('version', '')} in {folder / toolchain.PORTABLE_NODE}")
    return step


def _npm(command: str) -> list[str]:
    npm = toolchain.npm_argv(command, __file__)
    if npm is None:
        if command != "npm":
            raise SetupError(f"Could not find '{command}', the npm command in GameStudio's preferences")
        raise SetupError("Node.js is not set up: its download did not finish")
    return npm


def _step_shared_runtime(command: str, allowed: bool):
    def step(job):
        if not toolchain.is_extension_bundle(__file__):
            return
        template = template_dependencies()
        if toolchain.shared_runtime_state(template, __file__) == "":
            return
        job.set("Preparing the game packages")
        try:
            needs_install = toolchain.sync_shared_runtime(template, __file__)
        except OSError as exc:
            raise SetupError(f"Could not prepare the game packages in {toolchain.shared_runtime_dir()}: "
                             f"{exc}") from exc
        if needs_install:
            if not allowed:
                raise SetupError(f"{OFFLINE} (the game packages)")
            home = toolchain.shared_runtime_dir()
            first = not toolchain.shared_runtime_installed_once()
            job.run([*_npm(command), *RUNTIME_INSTALL], home, home / "npm-install.log",
                    "Downloading the game packages (first time only)" if first else "Updating the game packages")
            missing = toolchain.missing_runtime_dependencies(template, __file__)
            if missing:
                raise SetupError(f"The game packages are still incomplete after the download ({', '.join(missing)}). "
                                 f"See {home / 'npm-install.log'}")
            if toolchain.prune_npm_cache():
                job.note("Emptied GameStudio's npm cache")
        toolchain.mark_shared_runtime_installed(__file__)
        job.note(f"The game packages are ready in {toolchain.shared_runtime_dir()}")
    return step


_BROWSER_STAGES = {"download": "Downloading the game browser", "extract": "Unpacking the game browser",
                   "verify": "Checking the game browser"}


def _browser_line(job, line: str, found: dict) -> None:
    """One line of `gamestudio-browser install` (@gamestudio/devserver bin/gamestudio-browser.js; one JSON
    object a line): {"stage"}, {"progress", "bytes", "total"}, {"path", ...} at the end, or
    {"error", "offline"?} when it failed."""
    try:
        event = json.loads(line)
    except ValueError:
        return
    if not isinstance(event, dict):
        return
    if isinstance(event.get("error"), str):
        found["error"], found["offline"] = event["error"], bool(event.get("offline"))
        return
    if isinstance(event.get("path"), str) and event["path"]:
        found["path"] = event["path"]
        return
    fraction, done, total = event.get("progress"), event.get("bytes"), event.get("total")
    if isinstance(fraction, (int, float)):
        size = (f" ({done / 1e6:.0f} of {total / 1e6:.0f} MB)"
                if isinstance(done, (int, float)) and isinstance(total, (int, float)) and total else "")
        job.set(f"Downloading the game browser{size}", max(0.0, min(1.0, float(fraction))))
    elif event.get("stage") in _BROWSER_STAGES:
        job.set(_BROWSER_STAGES[event["stage"]], 0.0 if event["stage"] == "download" else None)
    elif isinstance(event.get("message"), str):
        job.set(event["message"][:80])


# Chrome Headless Shell's download for each platform (MB; gamestudio-browser `path` says it exactly).
BROWSER_DOWNLOAD_MB = {"win-x64": 125, "win-arm64": 125, "darwin-arm64": 102, "darwin-x64": 108, "linux-x64": 124,
                       "linux-arm64": 125}


def browser_size_mb() -> int:
    return BROWSER_DOWNLOAD_MB.get(nodejs.platform_key() or "", BROWSER_MB)


def _step_browser(allowed: bool, required: bool):
    def step(job):
        if find_browser() is not None:
            return
        script = browser_installer()
        if script is None:
            return  # this GameStudio has no installer: Play uses a window, which needs no browser of ours
        if not allowed:
            if required:
                raise SetupError(f"{OFFLINE} (the game browser)")
            job.warnings.append("the game browser was not downloaded: online access is off")
            return
        node = toolchain.node_executable(__file__)
        if not node:
            raise SetupError("Node.js is not set up: its download did not finish")
        found = {}
        folder = toolchain.browser_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SetupError(f"GameStudio cannot write the game browser to {folder}: {exc.strerror or exc}") from exc
        try:
            job.run_lines([node, script, "install", "--dir", folder], script.parent, folder / "install.log",
                          "Downloading the game browser", lambda line: _browser_line(job, line, found))
        except SetupError:
            if found.get("error"):  # its own sentence says it best ("Check the internet connection ...")
                raise SetupError(found["error"]) from None
            raise
        invalidate()
        if find_browser() is None:
            raise SetupError(f"The game browser was downloaded, but GameStudio cannot find it in {folder}. "
                             f"See {folder / 'install.log'}")
        job.note(f"The game browser: {find_browser()}")
    return step


def _step_project(root: pathlib.Path, command: str, allowed: bool, build_registry: bool):
    def step(job):
        log_path = project.gs_dir(root) / "setup.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        shared = toolchain.sdk(__file__)
        job.set("Linking the project to the game packages")
        repointed = toolchain.repoint_specs(root, shared)
        foreign = toolchain.foreign_links(root, shared)
        linked = toolchain.link_project(root, shared, relink=foreign)
        with open(log_path, "w", encoding="utf-8") as out:
            out.write(f"# linked from the shared packages: {', '.join(linked['linked']) or '-'}\n")
            if foreign:
                out.write(f"# linked again (they led into another GameStudio install): {', '.join(foreign)}\n")
            if repointed:
                out.write(f"# package.json now points at this GameStudio's packages: {', '.join(repointed)}\n")
        missing = toolchain.missing_dependencies(root)
        if missing and not trust.is_trusted(root):  # npm install runs the packages' install scripts
            job.note(f"Did not download the packages of {root}, which is not trusted: {', '.join(missing)}")
        elif missing:
            if not allowed:
                raise SetupError(f"{OFFLINE} (the project's packages: {', '.join(missing)})")
            job.run([*_npm(command), *NPM_INSTALL], root, log_path, "Downloading the project's packages")
            toolchain.prune_npm_cache()
        if build_registry:
            _registry(job, root, command, log_path, strict=True)
    return step


def _registry(job, root: pathlib.Path, command: str, log_path: pathlib.Path, strict: bool) -> None:
    if "registry" not in (toolchain.read_manifest(root).get("scripts") or {}):
        return
    if not trust.is_trusted(root):  # the project's own script
        if strict:
            raise SetupError(trust.ASK)
        job.note(f"Did not read the components of {root}, which is not trusted")
        return
    try:
        job.run([*_npm(command), "run", "registry"], root, log_path, "Reading the components")
    except SetupError as exc:
        if strict or str(exc) == "Cancelled":
            raise
        job.warnings.append("the component list could not be read: see the project's .gamestudio/setup.log")
        log.warning("%s", exc)


def _step_registry(root: pathlib.Path, command: str):
    def step(job):
        log_path = project.gs_dir(root) / "setup.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _registry(job, root, command, log_path, strict=False)
    return step


# --- Starting jobs (main thread) ----------------------------------------------------------------


def _finished(root, purpose: str, downloads: bool, keys: tuple, on_done=None):
    """What runs when a setup job ends, before the actions waiting for it."""
    def done(ok, message):
        global _last_run
        invalidate()
        if ok:
            _known.clear()  # what was missing is set up now: menus stop offering the setup until a check says otherwise
        _last_run = (_key(root, purpose)[0], keys, time.monotonic()) if ok else None
        if ok and root is not None and project.project_root() == pathlib.Path(root):
            registry.reload_registry(root)
        if on_done is not None:
            on_done(ok, message)  # may say more in JOB.message (New Project: how to resume)
        if not ok and message != "Cancelled" and (downloads or purpose not in QUIET):
            show_failure(JOB.message or message)
        if bpy.app.timers.is_registered(_first_check) is False and not bpy.app.background:
            bpy.app.timers.register(_first_check, first_interval=0.1, persistent=True)
    return done


def _run(root, purpose: str, needs: list[Need], then) -> None:
    allowed = online()
    command = npm_command()
    keys = {n.key for n in needs}
    steps = []
    if "node" in keys:
        steps.append(_step_node(user_dir(), allowed))
    if keys & {"runtime", "refresh"}:
        steps.append(_step_shared_runtime(command, allowed))
    if "browser" in keys:
        steps.append(_step_browser(allowed, required="browser" in ESSENTIAL.get(purpose, ())))
    if root is not None and keys & {"packages", "links"}:
        steps.append(_step_project(pathlib.Path(root), command, allowed, False))
    if root is not None and "registry" in keys:
        steps.append(_step_registry(pathlib.Path(root), command))
    downloads = any(n.download for n in needs)
    JOB.start(SETUP_TITLE, steps, on_done=_finished(root, purpose, downloads, tuple(n.key for n in needs)),
              root=root, purpose=purpose, downloads=downloads)
    if then is not None:
        JOB.then(then)
    log.info("%s: %s", SETUP_TITLE, _summary(needs))


def start(title: str, root=None, *, node: bool = True, build_registry: bool = False, on_done=None,
          extra_steps=(), browser: bool = False) -> str:
    """Start a full setup job: Node.js, the shared packages, the game browser (`browser`, unless the
    user said no) and, with `root`, the project's packages. Returns "" when it started, else why not
    (an untrusted project: its packages and components run its code)."""
    if JOB.running():
        return f"Wait for '{JOB.title}' to finish"
    if root is not None and not trust.is_trusted(root):
        return untrusted_message()
    allowed = online()
    command = npm_command()
    steps = []
    if node and command == "npm":
        steps.append(_step_node(user_dir(), allowed))
    steps.append(_step_shared_runtime(command, allowed))
    if browser and consented():
        steps.append(_step_browser(allowed, required=False))
    if root is not None:
        steps.append(_step_project(pathlib.Path(root), command, allowed, build_registry))
    steps.extend(extra_steps)
    _missing_cache.update(root=None, time=0.0)
    invalidate()
    JOB.start(title, steps, on_done=_finished(root, "new", True, (), on_done), root=root, purpose="new",
              downloads=True)
    return ""


def setup_project(root, on_done=None, title="Setting up the project's packages") -> str:
    """Make the project playable: link or install its packages, then read its components (the
    project's registry is reloaded when it is open)."""
    return start(title, pathlib.Path(root), build_registry=True, on_done=on_done)


_missing_cache = {"root": None, "time": 0.0, "names": []}


def missing_packages(root) -> list[str]:
    """The project's packages Node cannot find, cached briefly: draw code asks on every redraw."""
    now = time.monotonic()
    if _missing_cache["root"] != root or now - _missing_cache["time"] > 2.0:
        names = toolchain.missing_dependencies(root) if root is not None else []
        _missing_cache.update(root=root, time=now, names=names)
    return _missing_cache["names"]


def retry() -> str:
    """Run the last setup again (Retry after a failure)."""
    global _last_run
    _last_run = None
    invalidate()
    if _retry is None:
        return ensure_ready(project.project_root(), "setup")
    return ensure_ready(_retry["root"], _retry["purpose"], _retry["then"])


# --- Messages -----------------------------------------------------------------------------------


def notify(message: str, level: str = "WARNING") -> None:
    """Report in the status bar and the Info log, from a timer or a continuation."""
    try:
        from .editors import game_stream

        game_stream.notify(message, level)
    except Exception:  # noqa: BLE001 - no game stream module: the log has it
        (log.warning if level != "INFO" else log.info)(message)


def show_failure(message: str) -> None:
    """A popup saying what failed, with Retry and Show Log."""
    if bpy.app.background:
        return

    def show():
        window = any_window()
        if window is None:
            return None

        def draw(menu, _context):
            from .ui import wrap_label

            layout = menu.layout
            wrap_label(layout, message, width=70)
            row = layout.row()
            row.operator("gamestudio.setup_retry", icon="FILE_REFRESH")
            if JOB.log_path is not None and pathlib.Path(JOB.log_path).is_file():
                row.operator("gamestudio.open_path", text="Show Log", icon="TEXT").filepath = str(JOB.log_path)

        try:
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.context.window_manager.popup_menu(draw, title="GameStudio's setup did not finish", icon="ERROR")
        except RuntimeError as exc:
            log.warning("Could not show the setup failure: %s", exc)
        return None

    bpy.app.timers.register(show, first_interval=0.1)


def status_item() -> tuple[str, str, bool] | None:
    """(text, icon, alert) for the status bar while a job runs and a while after one failed: then the
    reason, which draw_statusbar_actions() follows with Retry and Show Log."""
    if JOB.running():
        return JOB.progress_text(), "SORTTIME", False
    if _failure_shown():
        reason = JOB.message if len(JOB.message) <= 140 else JOB.message[:139] + "…"
        return f"{JOB.title} failed: {reason}", "ERROR", True
    return None


def _failure_shown() -> bool:
    return JOB.state == "failed" and time.time() - JOB.finished < SHOW_FAILURE_FOR and JOB.message != "Cancelled"


def draw_statusbar_actions(layout) -> None:
    """Retry and Show Log next to a failed setup in the status bar."""
    if not _failure_shown():
        return
    row = layout.row(align=True)
    row.alert = False
    row.operator("gamestudio.setup_retry", text="Retry", icon="FILE_REFRESH")
    if JOB.log_path is not None:
        row.operator("gamestudio.open_path", text="Show Log", icon="TEXT").filepath = str(JOB.log_path)


def setting_up(root=None) -> bool:
    """GameStudio's setup runs (or waits for the user's answer): what it provides is on its way."""
    return JOB.running() or _asking is not None


def folder_label() -> str:
    """Where the setup puts things, for the user (the home folder as ~)."""
    try:
        from .claude_code import display_path

        return display_path(str(user_dir()))
    except Exception:  # noqa: BLE001
        return str(user_dir())


# --- UI pieces ----------------------------------------------------------------------------------


def draw_job(layout) -> None:
    """The running or last job: progress, result and its log."""
    if not JOB.state:
        return
    from .ui import wrap_label

    box = layout.box()
    if JOB.running():
        row = box.row()
        row.label(text=JOB.title, icon="SORTTIME")
        row.operator("gamestudio.setup_cancel", text="", icon="X", emboss=False)
        if JOB.progress is not None:
            box.progress(factor=JOB.progress, text=JOB.status_text())
        else:
            box.label(text=JOB.status_text(), icon="BLANK1")
        if JOB.steps > 1 and JOB.step:
            box.label(text=f"Step {JOB.step} of {JOB.steps}", icon="BLANK1")
        return
    icon = "CHECKMARK" if JOB.state == "done" else "ERROR"
    wrap_label(box, JOB.message, icon=icon)
    row = box.row()
    if JOB.state == "failed":
        row.operator("gamestudio.setup_retry", icon="FILE_REFRESH")
    if JOB.log_path is not None and pathlib.Path(JOB.log_path).is_file():
        row.operator("gamestudio.open_path", text="Show Log", icon="TEXT").filepath = str(JOB.log_path)


def draw_node_status(layout, compact: bool = False) -> None:
    """The first-run setup in the launcher and the preferences: which Node.js projects use, or what
    GameStudio sets up the first time (and a button that does it now)."""
    if JOB.running():
        return
    needs = cached_needs(None)
    if needs:
        col = layout.column(align=True)
        downloads = [n for n in needs if n.download]
        col.label(text="GameStudio sets itself up the first time you open or play a game" if downloads else
                  "GameStudio updates its game packages the next time you open or play a game", icon="INFO")
        if downloads:
            row = col.row()
            row.active = False
            names = ", ".join(SHORT_NAMES.get(n.key, n.text) for n in downloads)
            row.label(text=f"About {sum(n.size_mb for n in downloads)} MB, once: {names}", icon="BLANK1")
        col.operator("gamestudio.setup", text="Set Up Now", icon="IMPORT")
        return
    if compact:
        return
    status = toolchain.node_status(__file__)
    if status["ok"]:
        where = {"bundled": "bundled with GameStudio", "system": "system", "portable": "set up by GameStudio"}
        version = f"Node.js {status['version']}".strip() if status["version"] else "Node.js"
        layout.label(text=f"{version} ({where[status['source']]})", icon="CHECKMARK")


def draw_menu_status(layout, root=None) -> None:
    """The GameStudio menu's setup line: progress, or what failed with Retry, or Set Up. Trust This
    Project first while the project is not trusted."""
    if root is not None and not trust.is_trusted(root):
        layout.separator()
        layout.operator("gamestudio.trust_project", text="Trust This Project...", icon="LOCKED")
    if JOB.running():
        layout.separator()
        layout.label(text=JOB.progress_text()[:100], icon="SORTTIME")
        layout.operator("gamestudio.setup_cancel", text="Cancel Setup", icon="X")
        return
    if JOB.state == "failed" and JOB.message != "Cancelled":
        layout.separator()
        layout.label(text=JOB.message[:100], icon="ERROR")
        layout.operator("gamestudio.setup_retry", icon="FILE_REFRESH")
        if JOB.log_path is not None:
            layout.operator("gamestudio.open_path", text="Show Setup Log", icon="TEXT").filepath = str(JOB.log_path)
        return
    if cached_needs(root):
        layout.separator()
        layout.operator("gamestudio.setup", text="Set Up GameStudio...", icon="IMPORT")
    elif JOB.state == "done" and time.time() - JOB.finished < SHOW_FAILURE_FOR:
        layout.separator()
        layout.label(text=JOB.message[:100], icon="CHECKMARK")


def draw_missing_packages(layout, root) -> None:
    """A hint and the button that sets the project's packages up, when some are missing."""
    if root is None or JOB.running() or not missing_packages(root):
        return
    col = layout.column(align=True)
    col.label(text="The project's packages are not set up yet", icon="ERROR")
    col.operator("gamestudio.setup", text="Set Up GameStudio...", icon="IMPORT")


def draw_consent(layout, needs: list[Need]) -> None:
    """What the setup downloads, about how big, and where it goes."""
    downloads = [n for n in needs if n.download]
    col = layout.column(align=True)
    col.label(text="GameStudio needs to set itself up (one time):")
    for need in downloads:
        split = col.split(factor=0.8)
        split.label(text=need.text[0].upper() + need.text[1:], icon="IMPORT")
        split.label(text=f"about {need.size_mb} MB" if need.size_mb else "")
    local = [n for n in needs if not n.download]
    if local:
        col.label(text="Then, without downloading, it " + " and ".join(n.text for n in local) + ".", icon="BLANK1")
    layout.separator()
    hint = layout.column(align=True)
    hint.label(text=f"They go in {folder_label()}", icon="FILE_FOLDER")
    row = hint.row()
    row.active = False
    row.label(text="Nothing is installed system-wide; you see the progress in the status bar", icon="BLANK1")
    if not online():
        layout.label(text="Blender's online access is off: Set Up turns it on (Preferences > System > Network)",
                     icon="ERROR")


# --- Operators ----------------------------------------------------------------------------------


class GAMESTUDIO_OT_setup(bpy.types.Operator):
    bl_idname = "gamestudio.setup"
    bl_label = "Set Up GameStudio"
    bl_description = ("Download and prepare what game projects need, once: Node.js, the game packages and, when "
                      "there is no Chrome or Edge, a game browser. Everything goes in GameStudio's own folder")

    always: bpy.props.BoolProperty(name="Set up without asking next time", default=False, options={"SKIP_SAVE"},
                                   description="Remember the answer: GameStudio downloads what it needs when it "
                                               "needs it (Set Up Automatically in GameStudio's preferences)")

    @classmethod
    def poll(cls, context):
        if JOB.running():
            cls.poll_message_set(f"Wait for '{JOB.title}' to finish")
            return False
        return True

    @staticmethod
    def _pending():
        global _asking
        if _asking is None:  # the menu item, not a waiting action
            _asking = {"root": project.project_root(), "purpose": "setup", "then": []}
        return _asking

    def invoke(self, context, event):
        pending = self._pending()
        invalidate()
        needs = check(pending["root"], pending["purpose"])
        pending["needs"] = needs
        if not any(n.download for n in needs):
            return self.execute(context)
        scale = context.preferences.system.ui_scale or 1.0
        confirm = "Set Up" if online() else "Allow Online Access and Set Up"
        return context.window_manager.invoke_props_dialog(self, width=int(600 * scale), title="Set Up GameStudio",
                                                          confirm_text=confirm)

    def draw(self, context):
        pending = _asking or {}
        needs = pending.get("needs")
        if needs is None:
            needs = cached_needs(pending.get("root"))
        draw_consent(self.layout, needs)
        self.layout.prop(self, "always")

    def execute(self, context):
        self._pending()
        if self.always:
            from .prefs import get_prefs

            prefs = get_prefs()
            if hasattr(prefs, "bl_rna"):
                prefs.auto_setup = True
        if not online():
            _allow_online(context)
            if not online():
                self.report({"ERROR"}, f"{OFFLINE}: Blender was started offline (--offline-mode)")
                _answer(False)
                return {"CANCELLED"}
        _answer(True)
        if JOB.running():
            self.report({"INFO"}, "Setting up GameStudio: see the status bar")
        elif JOB.state == "failed":
            self.report({"ERROR"}, JOB.message)
        return {"FINISHED"}

    def cancel(self, context):
        _answer(False)


def _allow_online(context) -> None:
    """Blender's own Allow Online Access (the user chose "Allow Online Access and Set Up")."""
    try:
        if hasattr(bpy.ops.extensions, "userpref_allow_online"):
            bpy.ops.extensions.userpref_allow_online()
            return
    except (AttributeError, RuntimeError) as exc:
        log.debug("extensions.userpref_allow_online: %s", exc)
    try:
        context.preferences.system.use_online_access = True
    except (AttributeError, TypeError) as exc:
        log.warning("Could not turn on online access: %s", exc)


class GAMESTUDIO_OT_setup_retry(bpy.types.Operator):
    bl_idname = "gamestudio.setup_retry"
    bl_label = "Retry"
    bl_description = "Run GameStudio's setup again: the download or step that failed, and what follows it"

    @classmethod
    def poll(cls, context):
        return not JOB.running()

    def execute(self, context):
        state = retry()
        if state == READY:
            self.report({"INFO"}, "GameStudio is set up")
        elif state not in (STARTED, ASKING):
            self.report({"ERROR"}, state)
            return {"CANCELLED"}
        return {"FINISHED"}


class GAMESTUDIO_OT_install_node(bpy.types.Operator):
    bl_idname = "gamestudio.install_node"
    bl_label = "Set Up Node.js for GameStudio"
    bl_description = ("Download the Node.js LTS release GameStudio pins, check its SHA-256 and unpack it into "
                      "GameStudio's own folder (nothing is installed system-wide)")

    force: bpy.props.BoolProperty(name="Reinstall", default=False, options={"SKIP_SAVE"},
                                  description="Set up the portable Node.js even when the system has one")

    @classmethod
    def poll(cls, context):
        if JOB.running():
            cls.poll_message_set(f"Wait for '{JOB.title}' to finish")
            return False
        return True

    def execute(self, context):
        status = toolchain.node_status(__file__)
        if status["ok"] and not self.force:
            self.report({"INFO"}, f"Node.js {status['version']} is ready ({status['path']})")
            return {"CANCELLED"}
        if not online():
            self.report({"ERROR"}, OFFLINE)
            return {"CANCELLED"}
        folder = user_dir()
        entry = nodejs.release()
        if entry is None:
            self.report({"ERROR"}, "Node.js has no release for this platform")
            return {"CANCELLED"}

        def step(job):
            try:
                nodejs.install(folder, progress=job.set, cancel=job._cancel)
            except nodejs.NodeInstallError as exc:
                raise SetupError(str(exc)) from exc
            toolchain.activate(__file__)

        JOB.start("Setting up Node.js", [step], on_done=_finished(None, "setup", True, ("node",)), downloads=True)
        self.report({"INFO"}, f"Downloading Node.js {entry['version']} (about {entry['size_mb']} MB)...")
        return {"FINISHED"}


class GAMESTUDIO_OT_setup_project(bpy.types.Operator):
    bl_idname = "gamestudio.setup_project"
    bl_label = "Set Up Project Packages"
    bl_description = ("Link the project's packages to GameStudio's shared ones, download what is missing "
                      "and read its components")

    @classmethod
    def poll(cls, context):
        if JOB.running():
            cls.poll_message_set(f"Wait for '{JOB.title}' to finish")
            return False
        if project.project_root() is None:
            cls.poll_message_set("Open a GameStudio project first")
            return False
        return True

    def execute(self, context):
        problem = setup_project(project.project_root())
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        self.report({"INFO"}, "Setting up the project's packages...")
        return {"FINISHED"}


class GAMESTUDIO_OT_setup_cancel(bpy.types.Operator):
    bl_idname = "gamestudio.setup_cancel"
    bl_label = "Cancel"
    bl_description = "Stop the download or installation that is running"

    @classmethod
    def poll(cls, context):
        return JOB.running()

    def execute(self, context):
        JOB.cancel()
        return {"FINISHED"}


def draw_trust(layout, root) -> None:
    """The trust question: the folder, what trusting it lets GameStudio do, and whom to trust."""
    from .ui import wrap_label

    layout.label(text=str(root), icon="FILE_FOLDER")
    layout.separator()
    wrap_label(layout, "Trusting this project lets GameStudio run its scripts and packages: its npm scripts, its "
                       "tools and the game's dev server. Trust only projects from people you trust.", width=80)
    col = layout.column()
    col.active = False
    wrap_label(col, "Not now: Cancel. The level opens either way, and GameStudio > Trust This Project trusts it "
                    "later.", width=80)


class GAMESTUDIO_OT_trust_project(bpy.types.Operator):
    bl_idname = "gamestudio.trust_project"
    bl_label = "Trust This Project"
    bl_description = ("Let GameStudio run this project's scripts and packages: its npm scripts, its tools and the "
                      "game's dev server. Trust only projects from people you trust")

    @staticmethod
    def _root():
        return _trusting["root"] if _trusting is not None else project.project_root()

    @classmethod
    def poll(cls, context):
        if cls._root() is None:
            cls.poll_message_set("Open a GameStudio project first")
            return False
        return True

    def invoke(self, context, event):
        scale = context.preferences.system.ui_scale or 1.0
        return context.window_manager.invoke_props_dialog(self, width=int(560 * scale), title="Trust This Project?",
                                                          confirm_text="Trust", cancel_default=True)

    def draw(self, context):
        draw_trust(self.layout, self._root())

    def execute(self, context):
        root = self._root()
        if _trusting is not None:  # GameStudio asked: what waited for the answer goes on
            answer_trust(True)
            self.report({"INFO"}, f"GameStudio trusts {root}")
            return {"FINISHED"}
        trust.trust(root)
        _not_now.discard(trust.key(root))
        invalidate()
        state = ensure_ready(root, "open")  # what waited for it: the project's packages, its component list
        if state in (STARTED, ASKING):
            self.report({"INFO"}, f"GameStudio trusts {root}. Setting it up (see the status bar)...")
        elif state != READY:
            self.report({"WARNING"}, state)
        else:
            self.report({"INFO"}, f"GameStudio trusts {root}")
        return {"FINISHED"}

    def cancel(self, context):
        if _trusting is not None:
            answer_trust(False)


_CLASSES = (GAMESTUDIO_OT_setup, GAMESTUDIO_OT_setup_retry, GAMESTUDIO_OT_install_node, GAMESTUDIO_OT_setup_project,
            GAMESTUDIO_OT_setup_cancel, GAMESTUDIO_OT_trust_project)


def _first_check():
    """What the computer lacks, for the launcher and the menus (no project yet)."""
    try:
        check(None, "setup")
    except Exception:  # noqa: BLE001 - the launcher then shows nothing
        log.exception("Checking GameStudio's setup failed")
    return None


def register():
    toolchain.set_user_dir(user_dir())
    node = toolchain.activate(__file__)
    if node is not None:
        log.info("Using the Node.js in %s", node)
    trust.refresh()  # Blender's Auto Run Python Scripts, for checks on other threads
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    if not bpy.app.background:
        bpy.app.timers.register(_first_check, first_interval=1.0, persistent=True)


def unregister():
    global _asking, _trusting
    JOB.cancel()
    _asking = _trusting = None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if bpy.app.timers.is_registered(_first_check):
        bpy.app.timers.unregister(_first_check)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
