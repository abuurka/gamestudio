# Releasing GameStudio Editor

*The release checklist for GameStudio Editor, a thin distribution of Blender 5.2 LTS. Background: [ARCHITECTURE.md](ARCHITECTURE.md) §5 (repository and `gsfork`), §7 (upgrades) and §9 (licensing and naming), and [research/11](../research/11-fork-release-engineering.md) §4–§7.*

A release is the git tag `gs-v<version>` plus five files, published together on one release page:

| File | What it is |
|---|---|
| `GameStudio-Editor-<ver>-blender<bl>-windows-x64-setup.exe` | **What users download.** A per-user installer (no admin rights) of the tree below. See §6 |
| `GameStudio-Editor-<ver>-blender<bl>-windows-x64.zip` | The same editor, portable. One top folder, `GameStudio-Editor-<ver>/`: Blender's install tree, the IDE shell, the runtime with its own Node.js and packages, `GameStudio.cmd`, `NOTICE.md` |
| `GameStudio-Editor-<ver>-blender<bl>-source.tar.gz` | GPL Corresponding Source, our side: `editor/` (patches, native, shell, branding, build, installer, tools, tests, lock), root build scripts, CI workflows, `runtime/`, `examples/`, `UPSTREAM.txt`, `HOW_TO_BUILD.md`, `COPYING` |
| `GameStudio-Editor-<ver>-blender<bl>-upstream-source.tar.gz` | GPL Corresponding Source, Blender's side: the unmodified upstream tree at the locked commit |
| `SHA256SUMS.txt` | SHA-256 of the four other files |

`gsfork package --installer` writes all five into `dist/`. The code is `editor/tools/gsfork_package.py` and `editor/installer/`; `editor/tests/tools/` tests it (`test_package.py`, `test_bundle.py`).

---

## 1. Checklist

Copy this into the release issue and tick it off.

### Version
- [ ] Pick the version. Bump **`gamestudio_version`** in `editor/upstream.lock.json`. It names the archives, becomes `GAMESTUDIO_VERSION` in the build and is reported by `host.info()`.
- [ ] Set the same version in `blender/gamestudio/blender_manifest.toml` (`version`).
- [ ] **Blender patch release** (e.g. 5.2.2 → 5.2.3, about half a day): change `blender.tag`, `blender.commit` and `libs.windows_x64.commit` together.
  - Commit: `git ls-remote https://projects.blender.org/blender/blender.git "refs/tags/v5.2.3^{}"`.
  - Library commit: `git -C <mirror> fetch --depth 1 origin tag v5.2.3`, then `git -C <mirror> ls-tree v5.2.3 lib/windows_x64`. The third column is the commit.
  - `gsfork upgrade <tag>` will automate this (ARCHITECTURE §7). Until it exists, edit the three fields by hand.
- [ ] **New LTS** (5.2 → 5.5): follow [ARCHITECTURE.md §7, "The upgrade procedure"](ARCHITECTURE.md#7-the-upgrade-procedure-new-blender-lts). It is not repeated here. Then continue with this checklist.

### Build and test with `gsfork`
Run these from the repository root. The machine paths come from `editor/gsfork.local.json`.

- [ ] `python editor/tools/gsfork.py status`: the lock, the paths, and the seam budget (≤ 15 patches, ~250 lines).
- [ ] `python editor/tools/gsfork.py sync`: the mirror is at the locked commit, with its LFS files and `lib/windows_x64`. **Packaging needs the LFS files**, because the upstream source archive is built from them.
- [ ] `python editor/tools/gsfork.py apply`: every patch applies, and there are **no stale-overlay warnings**. If there are, re-derive the overlay files, then run `gsfork overlay-lock`.
- [ ] `python editor/tools/gsfork.py build`: a clean or incremental Release build, installed to `<build>/bin`.
- [ ] If the shell's generated data changed, run `python editor/tools/gsfork.py template` (and `branding`), then commit the results.
- [ ] `python editor/tools/gsfork.py test`: the add-on, shell and contract suites against the fork build.
- [ ] `python editor/tools/gsfork.py test --stock "<stock Blender 5.2>\blender.exe"`: the shell still degrades gracefully (G3).
- [ ] `npm ci && npm run typecheck && npm test`, plus `npm run smoke` on a machine with Edge. **`npm ci` matters for packaging:** the bundled `node_modules` is copied from the repository's, and packaging refuses a tree that differs from `package-lock.json` or lacks Electron's binary.
- [ ] `python -m unittest discover -s editor/tests/tools`.
- [ ] Node.js: `editor/installer/node.lock.json` pins a current **LTS** release (even major; `https://nodejs.org/dist/index.json`, `"lts"` not false). To bump it, change `version`, `url` and `sha256` together; the hash comes from that release's `SHASUMS256.txt`.
- [ ] Inno Setup 6.5 or newer is installed (`winget install JRSoftware.InnoSetup`, per-user is fine).
- [ ] The nightly `editor` workflow and the latest `canary` run are green. Open canary issues are understood.

### Sign (before packaging)
- [ ] Sign the binaries **in `<build>/bin`, after the final build and before `gsfork package`**, because the zip is made from that folder. Any later `ninja install` copies unsigned files back. See §3.
- [ ] `signtool verify /pa /all <build>\bin\blender.exe` (and the launcher).

### Package and verify
- [ ] `python editor/tools/gsfork.py package --installer --verify` (add `--verify-play` on a machine with a desktop session and a GPU). It downloads the pinned Node.js once (into `../gs-downloads`, checked against its SHA-256; `--node-zip` uses a local copy), writes the zip, the installer, both source archives and `SHA256SUMS.txt`, then checks the zip:
  - it extracts the zip and starts its `blender.exe --background --factory-startup` with **no** development variables (`BLENDER_SYSTEM_SCRIPTS`, `BLENDER_SYSTEM_EXTENSIONS`, `GAMESTUDIO_*` are removed);
  - **run 1**, with a scratch `BLENDER_USER_RESOURCES`: the `GameStudio` app template is active, and both the template and the `bl_ext.system.gamestudio` extension load **from inside the extracted folder**. `bpy.utils.user_resource('CONFIG')` is the scratch folder;
  - **run 2**, with no `BLENDER_USER_RESOURCES` and every user folder redirected to scratch, so nothing is written: `bpy.utils.resource_path('USER')` is the built-in default and contains `GameStudio` (seam S02), not `Blender Foundation`;
  - the real Blender profile (`%APPDATA%\Blender Foundation\Blender`) is fingerprinted before and after, and must be unchanged.
- [ ] Then it checks the installer (`verify_installer()`), end to end:
  - **install** with `/VERYSILENT` into a scratch folder: the files, the "GameStudio" Start-menu shortcut and the HKCU uninstall entry exist. It refuses to run if GameStudio is already installed for this user, because it would replace that install;
  - **the editor**, headless with a scratch `BLENDER_USER_RESOURCES` and no development variables: the GameStudio template, the `gamestudio` extension and the workspaces load from the install;
  - **New Project offline**, with every Node.js, npm and Git removed from `PATH`, `npm_config_offline=true` and an unreachable registry: the project's packages are linked, its dev server starts on the bundled Node.js and serves the game, `npm run build` writes the web build, the runtime host is the bundled Electron, and the bundled example opens as a linked copy;
  - with `--verify-play`, the same new project in the installed editor **with its UI**: Game View, Play, frames arrive (`tools/e2e/blender_game_view.py`);
  - **uninstall** with `/VERYSILENT`: the app folder, the shortcut, the uninstall entry and the caches (npm, editor) are gone; the project and `%APPDATA%\GameStudio` stay. `%APPDATA%\Blender Foundation`, `%LOCALAPPDATA%\Blender Foundation` and the `.blend` registration are compared before and after.
- [ ] The log says **"Releasable."** It says "NOT releasable" when:
  - the upstream archive is missing;
  - a build input (`upstream.lock.json`, `patches/`, `native/`, `build/`, `branding/overlay/`) is newer than `blender.exe`, so the source would not match the binary. Re-run `apply` and `build`;
  - `bin/` holds a program no install rule lists;
  - Blender's `license/` folder or the GPL text for `COPYING` is missing;
  - a bundled npm package has a licence that is not in `REVIEWED_LICENSES` (review it first);
  - verification failed.
- [ ] Spot-check the installer by hand on a clean Windows 10/11 user account without Node.js: run the setup, start **GameStudio** from the Start menu, **New Project**, **Play**. Then uninstall it from Settings > Apps.
- [ ] `sha256sum -c SHA256SUMS.txt` in `dist/` (Git Bash), or compare with `Get-FileHash`.

### Publish
- [ ] Tag the repository commit `gs-v<ver>` and push the tag.
- [ ] Create the GitHub release `gs-v<ver>`. Attach **all five files**, never the installer or the zip alone. The release notes should say "based on Blender's open source code" and link to `NOTICE.md` and the source archives.
- [ ] The GPL items in §2 are done.
- [ ] The trademark items in §4 are done.

### After the release
- [ ] Keep every published release's source archives online for as long as its zip is downloadable (§2). Never delete only the source assets.
- [ ] Bump `gamestudio_version` to the next development version.

---

## 2. GPL source publication

**Decision: every binary release ships the full Corresponding Source from the same release page. That means our source archive *and* a complete copy of the upstream Blender sources at the locked commit. A pointer to projects.blender.org alone is not enough.** `gsfork package` implements this. It builds the upstream archive with `git archive` from the local mirror and fails if any non-test Git LFS file is missing.

Why a pointer is not enough for us:

| Source | What it says | Consequence |
|---|---|---|
| GPLv3 §1 (text in `license/spdx/GPL-3.0-or-later.txt`) | Corresponding Source is "all the source code needed to generate, install, and (for an executable work) run the object code and to modify the work, including scripts to control those activities." | For a modified Blender, that is upstream Blender **plus** `editor/patches`, `native`, `shell`, `branding`, `build` and the build tools (`gsfork.py`, `gsfork_package.py`). It is all in the source archive. |
| GPLv3 §6(d) | The source may be on "a different server (operated by you or a third party)" with "clear directions next to the object code", **but** "Regardless of what server hosts the Corresponding Source, you remain obligated to ensure that it is available for as long as needed to satisfy these requirements." | A pointer to projects.blender.org makes our compliance depend on a server we don't control keeping that tag and its LFS objects. Shipping our own copy removes that dependency. |
| FSF GPL FAQ, [SourceAndBinaryOnDifferentSites](https://www.gnu.org/licenses/gpl-faq.html#SourceAndBinaryOnDifferentSites) | Allowed by 6(d), "However, you must provide clear instructions people can follow to obtain the source, and you must take care to make sure that the source remains available for as long as you distribute the object code." | The same point. `NOTICE.md` in the zip gives the directions. |
| FSF GPL FAQ, [AnonFTPAndSendSources](https://www.gnu.org/licenses/gpl-faq.html#AnonFTPAndSendSources) | "the source should be just as easy to access as the object code" and "The sources you provide must correspond exactly to the binaries." | Both archives sit next to the zip and are pinned to the exact commit, with checksums. |
| Blender FAQ, ["Can I distribute Blender freely?"](https://www.blender.org/support/faq/) | "for sharing official releases it's sufficient to provide information that forwards to the sources at blender.org." | This applies to **official, unmodified** releases only. |
| Blender FAQ, "Can I change Blender and give it to my co-workers or employees?" | You may use a changed Blender internally without publishing it. Once you share it outside, you must make all the source code available under the GNU GPL. | GameStudio Editor is a changed version, so the full source is required. Also in `research/08` §3.2: "Linking to blender.org's sources is enough for stock Blender but not for a fork." |

**What the upstream archive contains.** It is `git archive <locked commit>` of the mirror, run with `core.autocrlf=false` so files keep their LF endings and the patches apply. Every Git LFS pointer (icons, splash, fonts, `.blend` data and so on) is replaced by its object from the mirror's LFS store, and each object is checked against its SHA-256. `tests/files` is left out, as Blender's own `make source_archive` does. The archive is unmodified; `UPSTREAM.txt` records the repository, tag, commit, library commit and the archive's SHA-256.

**Precompiled libraries (`lib/windows_x64`).** These are Blender's builds of generally available free libraries (Python, OpenImageIO, OpenVDB, and others), used unmodified. Their licences are in the install's `license/license.md`, and `UPSTREAM.txt` pins the library commit. The scripts that build them, `build_files/build_environment/` with the exact URLs and checksums in `cmake/versions.cmake`, are inside the upstream archive. Blender's own `make source_archive_complete` can also produce a `-with-libraries` source tarball. **Open question for counsel before any commercial release:** should we publish that tarball too? This matters most for LGPL-licensed libraries listed in `license/license.md` (for example libheif), which carry their own source obligations.

**The installer.** The setup `.exe` is one more form of the same object code, published on the same release page as the source archives (GPLv3 §6(d), "equivalent access ... through the same place"). Its first page shows `NOTICE.md`, which says where the source is; the installed `NOTICE.md` says it again. Its own sources (`editor/installer/gamestudio.iss`, `node.lock.json`, and the packaging code) are in the source archive, so the installer can be rebuilt from it.

**Bundled third-party programs** (`gamestudio/node`, `gamestudio/node_modules`) are separate programs in the same aggregate (GPLv3 §5), each under its own licence. None is GPL. They ship unmodified, with their licence files in place, and `gamestudio/THIRD-PARTY-NOTICES.md` lists each one with its version, licence and source. Node.js is MIT (its `LICENSE` also carries the notices of V8, OpenSSL, ICU and the rest); npm is Artistic-2.0, which allows redistributing the standard version. `lightningcss` (Vite's CSS engine, a native binary) is MPL-2.0: distributing it unmodified needs its source to be available and recipients told where, which the notices file does. Packaging fails the "Releasable" check if a package's licence is not on the reviewed list.

**How long to keep it.** Keep it for as long as the binaries are offered. If a build is ever shipped on physical media without the source, GPLv3 §6(b) requires a written offer valid for at least three years. Avoid that by always shipping the source next to the binary.

**Internal builds** (for yourself, not distributed) carry no source obligation. Nightly CI artifacts given to testers outside the project count as distribution, so the nightly uploads both source archives with the zip.

---

## 3. Code signing

Unsigned downloads trigger SmartScreen warnings. Reputation builds up over releases signed with the same identity. The costs below come from research/11 §5.3, checked 2026-10-09.

| Option | Cost | Availability and notes |
|---|---|---|
| **Azure Artifact Signing** (formerly Trusted Signing) | ~**$9.99/month** | Organizations: USA, Canada, EU, UK. **Individuals: USA and Canada only.** Cloud keys, no token, CI-friendly. |
| **OV certificate** (DigiCert, Sectigo, Certum, …) | **$150–300/year** | Available worldwide. Since June 2023 the key must live on a hardware token or a cloud HSM. This is the fallback if Azure is not available to you. |
| EV certificate | $400+/year | **No longer bypasses SmartScreen** (removed in 2024). Not worth it for this app. |
| SignPath Foundation | Free for qualifying open-source projects | A GPL project may qualify (unverified). |
| Microsoft Store (MSIX) | Free | The Store re-signs the package, so users see no SmartScreen warning. Needs an MSIX package, which is not built yet. |

**What to sign:** `blender.exe`, `blender-launcher.exe`, any DLLs we build, and the setup `.exe` with its uninstaller. Inno Setup signs both while compiling when `ISCC` gets a sign tool (`/Ssigntool=...` plus `SignTool=signtool` in `gamestudio.iss`); wire that in with the signing automation, because signing the setup afterwards changes its SHA-256. Always use SHA-256 and an RFC 3161 timestamp, so signatures outlive the certificate:

```
signtool sign /fd SHA256 /td SHA256 /tr <timestamp URL> <signing options> blender.exe blender-launcher.exe
signtool verify /pa /all blender.exe
```

- With **Azure Artifact Signing**, the signing options are `/dlib <Azure.CodeSigning.Dlib.dll> /dmdf metadata.json` and the timestamp URL is `http://timestamp.acs.microsoft.com`.
- With an **OV certificate**, use `/sha1 <thumbprint>` and your CA's timestamp URL.
- `GameStudio.cmd` cannot carry an Authenticode signature (batch files can't), which is why the signed `blender-launcher.exe` does the real work.
- When signing is automated, run it only in a job tied to a protected `release` environment with required reviewers, never in the push or nightly jobs.
- macOS (later) needs a Developer ID ($99/year), the hardened runtime and `notarytool` (research/11 §5.4).

---

## 4. Trademark and naming checklist

From ARCHITECTURE §9 and research/08 §3.1, which quote blender.org's trademark policy and FAQ.

- [ ] The product name is **"GameStudio"** / **"GameStudio Editor"**. "Blender" does not appear in the product, company, app or domain name: no "Blender GameStudio", no "GameStudio for Blender", no `blender-*` domain.
- [ ] Blender is mentioned only as **"based on Blender's open source code"**, never more prominently than our own name, and without slogans or any hint of endorsement. Check `NOTICE.md`, the release notes, the website and the README.
- [ ] **No Blender logo** in our icon, splash or installer art. Check `editor/branding/generated/` and the overlay (`gsfork branding` renders our own mark).
- [ ] The window title and the About box say GameStudio (seam S03). The `.exe` file properties say "GameStudio Editor" (overlay `winblender.rc`).
- [ ] Blender's **credits and copyright notices are kept**: `copyright.txt`, `readme.html` and `license/` in the zip, and the credits in the About box. Ours are added, never substituted.
- [ ] Our own config folders (`%APPDATA%\GameStudio\Editor\<X.Y>`, seam S02) and app ID (`gamestudio.editor.*`, S03), so we never take over Blender's profile or file associations. `--verify` checks the profile folders and, for the installer, the `.blend` registration.
- [ ] The installer registers no file types. Its wizard images, setup icon and shortcut icon are ours (`editor/branding/generated/`: `gamestudio.ico`, `icon_256.png`, and the cube cropped from `splash.png`).
- [ ] Known exception, tracked: the executable is still called `blender.exe`, because that is upstream's install layout. Users never see that name in normal use: the Start-menu shortcut is called "GameStudio" and starts `blender-launcher.exe` (no console window) with our icon; it carries the app ID the editor's windows set (`gamestudio.editor.<X.Y>`), so the taskbar groups the windows under it with its name and icon; and the `.exe` file properties say "GameStudio Editor". Only Task Manager's Details tab and the install folder show `blender.exe`. Renaming the executable would need a seam in `source/creator/CMakeLists.txt` (and the launcher's hard-coded child name).

---

## 5. CI

| Workflow | Runner | Trigger | Does |
|---|---|---|---|
| `.github/workflows/editor.yml` | self-hosted `[self-hosted, windows, gamestudio-build]` | push to `main`, nightly, manual | `gsfork sync/apply/build/test`, optional `test --stock`, packaging unit tests, `npm ci`, typecheck, vitest. **Nightly:** `gsfork package --verify`, then uploads the zip, both source archives and `SHA256SUMS.txt` (kept 3 days) |
| `.github/workflows/canary.yml` | self-hosted | weekly (Mon) and manual | `gsfork canary blender-v5.3-release` and `gsfork canary main`. On failure it opens a `canary` issue, or comments on the open one |
| `.github/workflows/runtime.yml` | `ubuntu-latest` | push and pull requests touching the runtime | Node 22, `npm ci`, typecheck, vitest. The Edge smoke test is skipped there |

**Runner security** (research/11 §4.1):
- **Never attach the self-hosted runner to a public repository.** Fork pull requests would run arbitrary code on the build machine.
- Keep the repository private. Keep "Run workflows from fork pull requests" off.
- Run the runner service as a dedicated non-admin account, in a runner group limited to this repository.

**Folders:** CI uses its own folders under the repository variable `GS_CI_ROOT` (default `F:\gs-ci`), never a developer's mirror or build. The canary and the editor build share one concurrency group, because they share that mirror.

**Wiring `gsfork package` into gsfork:** `gsfork.py` dispatches `package` to `gsfork_package.package(paths(), lock(), REPO / "dist", verify=...)`. Without that, `python editor/tools/gsfork_package.py --verify` does the same.

---

## 6. The installer and the bundled runtime

**What a user gets.** One download, `...-windows-x64-setup.exe`. It installs per user without admin rights into `%LOCALAPPDATA%\Programs\GameStudio` (Inno Setup, `PrivilegesRequired=lowest`), adds a **GameStudio** Start-menu shortcut (a desktop one is optional) and an uninstall entry in Settings > Apps, and registers no file types. Everything the editor needs is inside; it works offline after installation.

```
%LOCALAPPDATA%\Programs\GameStudio\
  blender.exe, blender-launcher.exe, *.dll, license\, ...   Blender's install tree (the editor)
  5.2\scripts\startup\bl_app_templates_system\GameStudio\   the app template
  5.2\extensions\system\gamestudio\                         the system extension
  GameStudio.ico, GameStudio.cmd, NOTICE.md
  gamestudio\
    bundle.json                 marks this layout for the shell (core/toolchain.py)
    node\                       portable Node.js LTS (node.lock.json), with npm and its LICENSE
    node_modules\               one shared, prebuilt package tree (Electron, Vite, three, TypeScript, ...)
    runtime\packages\           @gamestudio/engine, devserver, host
    examples\demo-game\
    THIRD-PARTY-NOTICES.md, README.txt, package.json
  uninstall\unins000.exe
```

**How the shell finds things** (`core/toolchain.py`, no environment variables needed):
- the install: each parent folder of the extension, and `<parent>/gamestudio` (where `bundle.json` is);
- **Node.js and npm:** a non-default *npm command* preference first, as given; then the bundled `node.exe` running the bundled `npm-cli.js`; then the system's `npm`. In an install, the extension also puts the bundled Node.js first on its own `PATH`, so every child process (dev server, web build, registry) and `shutil.which` finds it;
- **the engine packages:** `GAMESTUDIO_RUNTIME` first, then `runtime/packages` beside a parent folder;
- **Electron and the runtime host:** `GAMESTUDIO_ELECTRON` / `GAMESTUDIO_HOST_DIR` first, then the project and its parents, then the development checkout, then the install's `gamestudio/`.

**How projects use the packages: links into the install.** New Project writes the same `package.json` as before (`@gamestudio/*` as `file:` paths into the install, everything else pinned). It does not run `npm install`. It creates `node_modules` with one directory junction per dependency (junctions need no admin rights or Developer Mode) pointing into the shared tree, plus npm-style `.bin` launchers, then runs `npm run registry`. Node resolves each package from its real path, so the engine, the game and Vite share one copy of three. Why this design:

| Option | Offline | Disk per project | Notes |
|---|---|---|---|
| `npm install` with `file:` links (before) | no | ~150 MB | Needs the registry for every third-party package; writes the engine's dependencies into the install |
| An offline npm cache in the install | yes | ~150 MB | Every project unpacks its own copy of Vite, TypeScript and three; npm writes into the cache |
| **Links into one shared tree (chosen)** | **yes** | **a few KB, ~10 MB after Vite's cache and a web build** | No npm work at all; Electron is never in a project. A later `npm install` (online) simply replaces the links with real packages |

- The linker only adds links and replaces links whose target is gone. It never touches a real folder, so packages a user installed with npm win. It links a package only if the version in the shared tree satisfies the project's range; otherwise New Project falls back to `npm install`.
- Opening a project re-links whatever Node cannot resolve. A cloned project, or one made before GameStudio moved, plays without `npm install`.
- A development checkout works the same way, with the repository's `runtime/packages` and `node_modules` as the shared tree. Projects inside the repository (`examples/demo-game`) resolve through the workspace and are left alone.
- An example inside an install is copied to `Documents\GameStudio\Examples\<name>` on first open (its `@gamestudio/*` entries pointed at the install) and linked, so nothing writes into the install folder. Later opens reuse that copy.
- The bundled npm uses `%LOCALAPPDATA%\GameStudio\npm-cache` and never checks for updates.

**How the bundle is made.** `gsfork package --bundle` (implied by `--installer`) copies, from the repository's own `npm ci` tree, exactly the production dependencies of `runtime/packages/*` and every dependency of the project templates and examples (42 packages for 0.1.0, nested folders included). It checks each against `package-lock.json`, each template range against the installed version, and Electron's binary. Development-only packages (Vitest, Puppeteer) stay out. Node.js comes from the zip pinned in `node.lock.json`.

**Uninstall** removes the app folder (including files it wrote under `5.2\` and `gamestudio\`), the shortcuts, the uninstall entry and the caches in `%LOCALAPPDATA%\GameStudio` (npm's, and the editor's shader and asset-index cache). It never removes game projects. It keeps the settings in `%APPDATA%\GameStudio` unless the user says yes to the question (silent: `unins000.exe /VERYSILENT /REMOVEPROFILE`).

**Upgrades** install over the previous version (same `AppId`), and replace `gamestudio\node`, `node_modules` and `runtime` whole. Projects keep working, because their links point at the same paths.

### Follow-ups
- **Assistant packages** still install into the extension's `assistant/_vendor` inside the install. That works for a per-user install (and uninstall removes them) but would not work under `Program Files`; move `_vendor` to a user folder before any per-machine package.
- **Executable name.** See §4: renaming `blender.exe` needs a seam.
- **MSI / Microsoft Store.** Not built. The Store (MSIX) would also remove SmartScreen warnings (§3).
- **No signing automation yet.** See §3.
- **Node.js 26** becomes LTS at the end of October 2026; bump `node.lock.json` in the next release after that.
- **The canary checks patch application only.** It does not yet build against `main`, which needs a second library checkout.
- **Long paths:** `blender.exe` is not long-path aware, so the zip should be extracted near a drive root (`C:\GS\`). `gsfork package` warns when the deepest path gets close to Windows' 260-character limit.
