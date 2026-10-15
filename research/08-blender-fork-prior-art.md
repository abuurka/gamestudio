# 08: How forks stay in sync with upstream, and what it means for a Blender game-IDE fork

*Date: 2026-10-09. Base: Blender 5.2.2 LTS (tagged 2026-09-14). The repository measurements in this report come from shallow git fetches of the public repositories, done today: commit histories, merge commits, and file-level diffs between each fork and the exact upstream commit it last merged. Claims taken only from secondary sources are marked **(unverified)**. My own reasoning is marked **(inference)**. This is not legal advice.*

---

## TL;DR

- **No living Blender fork keeps its changes small.** Against the Blender commit each last merged:
  - Bforartists, a "UI-only" fork, modifies **464 upstream C/C++ files (+21k/−5k lines) and 110 Python UI files (+31k/−8k)**.
  - UPBGE modifies **238 upstream C/C++ files**.
  - Goo Engine modifies **159 upstream C/C++ files, 16 of them DNA headers**, and carries its own copy of the deleted legacy EEVEE.
- **The two healthy forks pay a constant tax by merging `main`.**
  - Bforartists merges weekly (17 merges, May–Sep 2026, each of about 75–490 upstream commits). It releases 1–2 weeks after Blender, with Blender *alpha* code inside: Bforartists 5.2.0 reports `BLENDER_VERSION 503 alpha`.
  - UPBGE merged `b/main` **123 times between Apr 1 and Oct 8, 2026**, all by one developer.
- **Rebasing once per release breaks when upstream rewrites a subsystem.** EEVEE Next kept Goo Engine on Blender 4.1 for a year. Its 4.4 port came about 9 months after Blender 4.4, and it is now five releases behind.
- **Freezing, or simply falling behind, kills forks.** Fluid Designer stopped in 2018. Mechanical Blender's repo says "THIS REPO IS DEAD". Cursor says rebasing "isn't in our short-term plans" while it sits about 20 VS Code minor versions behind.
- **Forks that stay current use a pinned upstream, a patch queue and an overlay.** VSCodium keeps 70 patches, LibreWolf 65 and Electron 142 (each with a written justification). Brave keeps 1,285 patches plus 1,625 `chromium_src` overrides, under the rule that patches must be "trivial changes, and not nested logic changes". Floorp keeps about 30 patches and hot-reloads its features as TypeScript, which is GameStudio's own model.
- **Legal.** A fork needs its own name ("give it a new name and create a unique brand"). Saying it is "based on Blender's open source code" is allowed without permission; using the logo is not. Every binary needs its Corresponding Source. The runtime stays outside the GPL only at arm's length: "Using shared memory to communicate with complex data structures is pretty much equivalent to dynamic linking" (FSF).
- **Recommendation:**
  - **Distribution first, thin fork second.** Use an app template and add-ons wherever Python reaches. Keep a pinned-tag patch queue plus an overlay directory for what only C can do.
  - **Hard budget:** ≤20 patches, ≤1,000 changed lines and ≤30 files in upstream code, and no DNA struct changes.
  - **Cadence:** rebase once per Blender release, starting when the release branch opens, plus a weekly canary build against `main`.

---

## 1. Blender forks

### 1.1 Overview

| Fork | What it changes | How it tracks upstream | Lag behind Blender releases | Team / activity | Status (Oct 2026) |
|---|---|---|---|---|---|
| **Bforartists** | UI and UX: keymap, menus, colored icons, toolbar editor, defaults, bundled add-ons | **Weekly merge** of `blender/main` through `merge-weekNN` PRs | Releases 7, 7, 8 and 16 days after Blender 4.5, 5.0, 5.1 and 5.2. Releases include Blender alpha code | About 4–5 active committers. Merges are done by "Andres Stephens" and the PRs are merged by "Draise", likely the same person **(unverified)** | Alive. 807 stars. Last push 2026-10-07 |
| **UPBGE** | Restores the game engine (`source/gameengine`), adds a logic editor in C, draw/GPU/RNA hooks, and a `Game_Engine` app template | **Near-daily merge** of `b/main` | 0.30: 1 day after Blender 3.0. 0.36: 4 days after 3.6. 0.44: about 7 weeks after 4.4. 0.50: about 7 weeks after 5.0. **No tagged stable release between Aug 2023 and May 2025** | One developer (`youle31`) does nearly everything | Alive. 1.8k stars |
| **Goo Engine** | NPR rendering (a vendored legacy EEVEE renamed `gooengine`), shading nodes, light groups, some UI | **Per-release branches** (`goo-engine-v4.4-release`) with features re-applied as squashed per-concern commits | Stuck on 4.1 until late 2024. Goo 4.4 shipped Dec 12, 2025, about 9 months after Blender 4.4. Still on 4.4 in Sept 2026 | One active developer in 2025–26 | Alive but 5 releases behind. Binaries are Patreon-only |
| **Fluid Designer** (Microvellum) | Cabinet and interior design: a modified UI and a parametric library on Blender 2.7x | Ported by hand to 2.7x releases | n/a | Company-funded | **Dead since Jan 2018** |
| **Mechanical Blender** | CAD tools: base points, snapping to the cursor, dimensions | "Periodically updated with bf-blender trunk" | n/a | One developer | Dormant. The GitHub repo says "THIS REPO IS DEAD". Last push 2023-10 |
| **Range Engine** | A BGE fork (from UPBGE) focused on game performance | **Deliberate freeze.** 2.0 restarted on UPBGE 0.3 | Doesn't track | Small team on Patreon | Alive, intentionally frozen **(unverified details)** |
| **NVIDIA Omniverse Blender** | USD and MDL branch builds | Alpha branches (3.0 to 4.2) | n/a | NVIDIA | Ended. Features were **upstreamed** and future work moves to add-ons **(unverified)** |

### 1.2 Bforartists: what a "UI-only" fork really costs

**Method.** I took the merge commit `e35eb5f` ("Merge remote-tracking branch 'blender/main' into merge-week39", 2026-09-28) and diffed it against its second parent `1b20a88`, which is the exact Blender `main` commit merged. The result is Bforartists' entire delta on top of Blender at that moment.

| Area | Files differing | Lines (+/−) | Notes |
|---|---|---|---|
| `release/datafiles` (icons, themes) | 2,209 | mostly binary | Colored icon set |
| `scripts/addons_core` (bundled add-ons) | 1,851 | — | Bundled extensions |
| **`source/blender` (C/C++)** | **469** (464 modified, 5 added) | **+21,110 / −4,696** | editors: 238 files, 18.4k lines. makesrna: 63 files, 3.6k lines. modifiers: 59 files, 1.9k lines. windowmanager: 22. nodes: 33. makesdna: 12 |
| **`scripts/startup` (Python UI)** | **110** (84 in `bl_ui`, 18 in `bl_operators`, 7 app templates) | **+31,035 / −8,317** | In-place edits to Blender's own UI files |
| `scripts/presets` (keymaps) | 25 | +22,963 | Own keymap |
| `tests` | 169 | +119 / −122 | Small fixes |
| Total | **5,217** | | |

Findings:
- **Most of the change by line count is Python, but the C footprint is very wide.** Every weekly merge has to get through 464 modified upstream C/C++ files. Bforartists also added a whole C editor (`editors/space_toolbar/`), because Python can't add editor types (report 04).
- **The weekly rhythm is steady.** Each `merge-weekNN` PR was merged 0–3 days after its upstream merge commit, usually with 0–5 follow-up commits.
- **Releases are cut from a branch that tracks `main`.** The v5.2.0 tag has `BLENDER_VERSION 503`, `BLENDER_VERSION_CYCLE alpha` and `BFORARTISTS_VERSION 52`. Its release notes say it is "part of the latest from Blender 5.2.0 and 5.3.0 to the date".
  - One good pattern: Blender's version macros are left exactly as upstream has them, and Bforartists adds its own beside them, so add-on version checks and `.blend` versioning keep working.
- **What burns them is regressions after merges.** Examples are issue branches `6618-outliner---recent-merge-issues` and `6610-ui---themes-out-of-sync`.
  - The 2.8 rewrite cost a long reset: Bforartists 1.0 shipped in 2018 on 2.7x, and 2.0.0 in April 2020 on 2.83 alpha **(the size of the gap is my inference)**.
  - The team: "We are unfortunately already more than busy to keep up to date with just Blender."

### 1.3 UPBGE: the cost of a deep engine fork

The same method on UPBGE's latest `b/main` merge (`618c637`, 2026-10-08):

| Area | Files | Lines (+/−) |
|---|---|---|
| `source/gameengine` (new, out-of-tree in practice) | 493 added | +112,727 |
| **`source/blender` (upstream-owned)** | **316 (238 modified, 78 added)** | **+48,053 / −190** |
| `intern/` (moto, decklink …) and `extern/` (Recast, Bullet) | 124 | +36,722 |
| `scripts/startup` | 18 (including the `Game_Engine` app template, `properties_game.py`, `space_logic.py`) | +1,755 |

Inside `source/blender` the changes are: draw (43 files, 9.7k lines), python (21 files, 7.8k), editors (49 files, 7.7k, including a new `space_logic` editor), makesrna (29 files, 7.1k), blenkernel (52 files, 6.5k), gpu (40 files, 5.5k) and **makesdna (26 files, 2.0k)**.

What this shows:
- **Good pattern.** UPBGE keeps the engine itself as **pure additions in its own directory**: 493 files and 0 deleted lines.
- **Bad pattern.** The engine reaches into Blender through 238 modified files across draw, GPU, RNA and DNA. Keeping up took 123 `b/main` merges between Apr 1 and Oct 8, 2026 (about 4.6 a week). All of them, and 159 of the 162 other first-parent commits, are by `youle31`.
- **The DNA edits mean UPBGE-specific data doesn't round-trip through stock Blender (inference).**
- **The tagged-release gap** between 0.36.1 (Aug 2023) and 0.44 (May 2025) spans Blender 4.0–4.3, including EEVEE Next in 4.2 **(the cause is my inference)**.

### 1.4 Goo Engine: rebasing once per release, and how it breaks

Goo Engine's branch structure is `goo-engine-v3.6-release` through `goo-engine-v4.4-release`, plus `-old` variants for 4.2 and 4.3, which suggests ports were redone.
- History is linear: 0 merges on v4.3 and 2 on v4.4.
- Goo work is re-applied as squashed per-concern commits:
  - "GOOENGINE: Shaders", "GOOENGINE: Operators" and "GOOENGINE: UI Compatibility fixes" on 2025-09-11;
  - "Restore gooengine settings and defaults lost in merge" and "Restore UI panels" on 2025-09-29.
- The v4.4 branch is based on Blender 4.4.3 (April 2025).

Its delta against 4.4.3:
- 136 new files in `draw/engines/gooengine/`, which is **legacy EEVEE** (`eevee_bloom.cc`, `eevee_shadows_cascade.cc` …) kept alive after upstream deleted it;
- 159 modified upstream C/C++ files;
- 16 modified DNA headers (`DNA_light_types.h`, `DNA_material_types.h`, `DNA_scene_types.h`, `DNA_view3d_types.h` …);
- `source/blender` +49.7k lines in total.

Lessons:
- **Rebasing once per release is fine until upstream rewrites the subsystem your patches hook into.** Goo stayed on Blender 4.1 because a 4.2 version was "difficult to get working right now" (CG Channel, Dec 2024). Its fix was to vendor the old renderer, which grows the fork without bound.
- **The way out was upstreaming.** DillonGoo joined Blender's NPR project, with Goo's lead developer working alongside EEVEE developer Miguel Pozo. That is the "add hooks upstream" path.

### 1.5 Abandoned and adjacent cases

- **Fluid Designer** (Microvellum, a heavily modified UI on Blender 2.7x). Andrew Peel, June 2018: development "officially stopped in January 2018. … Nothing really went wrong. Microvellum wanted to focus all of the development resources on the manufacturing and engineering modules." Peel's later PyClone/Home Builder was described as an add-on on 2.83–2.93, though another summary calls it a custom build **(sources disagree)**. **Lesson:** a sponsored fork dies when the sponsor's priorities change, and the successor fell back to the add-on model.
- **Mechanical Blender.** It promised periodic syncs with trunk. Its repo now says "THIS REPO IS DEAD AND WILL NOT UPDATED ANYNORE" (last push 2023-10).
- **NVIDIA Omniverse.** NVIDIA shipped custom USD "Blender Alpha" branches from 3.0 to 4.2 (last build May 2025), then reportedly stopped, with the features in Blender LTS and future work in add-ons **(unverified: the docs URL now redirects)**. **Lesson:** a fork used as a staging branch, upstreamed, then retired.
- **Bonsai** (formerly the BlenderBIM add-on). It is not a fork, but it is the naming precedent: after the Foundation tightened use of "Blender" in project names in 2024, it renamed with v0.8.0 (Sept 2024). In April 2025, blender.org said: "we recommend third parties to avoid using the name 'Blender', especially not as the beginning of the name."

### 1.6 App templates and Python-first customization

Blender's app templates (`startup.blend`, `userpref.blend`, a `splash.png` of 1000×500, and `__init__.py` with `register`/`unregister`) exist "to support switching to a customized configuration" and let people "build their own applications on top of Blender". They can replace parts of the UI and define menus, keymaps, tools and a template-specific add-on path. Limits:
- Only themes, add-ons, keymaps and viewport lighting are honoured from template preferences.
- Default changes have to go through handlers such as `load_factory_startup_post`.

How the forks use them:

| Fork | App template? | Where the customization lives |
|---|---|---|
| UPBGE | **Yes.** It ships `bl_app_templates_system/Game_Engine/` (`__init__.py` + `startup.blend`) | Mostly C/C++ |
| Bforartists | Edits Blender's bundled templates' `startup.blend` files and adds an `All` template | **Edits Blender's own `bl_ui/*.py` in place** (84 files), plus 464 C/C++ files |
| Goo Engine | No | 43 modified Python UI files plus C/C++ |
| Fluid Designer's successor | Reportedly an app template, then an add-on | Python |

**No current fork keeps its UI changes as out-of-tree Python overrides** (re-registering or appending to Blender's UI classes from an add-on or template). Bforartists has most of its line changes in Python but still edits upstream files in place, so it gets merge conflicts anyway. This is the cheapest gap available for GameStudio to use.

---

## 2. Forks of other big projects

### 2.1 Overview

| Project | Upstream | Mechanism | Size of change | Cadence and lag | Team |
|---|---|---|---|---|---|
| **Brave** | Chromium | `patches/` + **`chromium_src/` compile-time overrides** + code in `src/brave` | 1,285 `.patch` files. 2,083 `chromium_src` files (1,625 `.cc/.h/.mm`) | Each Chromium major is shipped 1–3 days after Chrome. Chromium went to **two-week majors** with Chrome 153 (Sept 8, 2026): Brave 1.95/Cr153 on Sept 11, 1.96/Cr154 on Sept 24, 1.97/Cr155 on Oct 7. "Migration" (rebase) starts about a week before each release | A dedicated "rebase team" |
| **Electron** | Chromium, Node, V8 … | Patch series with a **`.patches` order file** and `git-import-patches`/`git-export-patches` tools | 207 patches, 142 of them against Chromium | Tracks Chromium majors | A large community |
| **VSCodium** | VS Code | **Pinned upstream** (`upstream/stable.json`: tag 1.135.0 + commit) + 70 patches named by concern + a **`product.json` overlay** merged with `jq -s '.[0] * .[1]'` | 70 patches | VS Code 1.135.0 was tagged Aug 25. VSCodium adopted it Sept 8 (14 days). VS Code ships **weekly** since 1.111 (Mar 2026). VSCodium skips some of these, and upstream is at 1.141.0 | Small volunteer team |
| **Cursor** | VS Code | A deep fork | Unknown | Reports "VS Code Extension API 1.105.1". Upstream was 1.125.0 on Jun 30, 2026. Staff (Jun 27, 2026): "updating the VS Code base isn't in our short-term plans right now" | Well funded |
| **Windsurf** | VS Code | A deep fork, using Open VSX | Unknown | Base version lag not found **(unverified)** | Company |
| **Microsoft Edge** | Chromium | Internal patch set, upstream by default | Unknown | Tracks Chromium majors. Reportedly aligned to the two-week cadence from 152 **(unverified)** | Very large |
| **Vivaldi** | Chromium | Branch per Chromium update. **UI written in web tech (React) on top of Chromium** | Unknown | 2018: integration "can take several weeks", cut from 3–4 weeks to under 2 | About 20 developers (2018 estimate) |
| **LibreWolf** | Firefox | Patches on the upstream **tarball**, with a `make check` target for new upstream releases | 65 patches (e.g. `disable-data-reporting-at-compile-time.patch`) | Its FAQ says within about 3 days **(unverified)**. A community measurement found 1.7 days on average for security fixes **(unverified)** | Volunteers |
| **Floorp** (v12, "Noraneko") | Firefox | A pinned `Floorp-Runtime` (lock file: `trackingRef nora-0.2.0`, `daily-1142`) + about 32 small patches + **features loaded at runtime** from `browser-features/` (SolidJS/TS, found by glob; a Vite dev server on `localhost:5181` in development, `chrome://noraneko/content/core.js` in production) | 36 common chrome features, 24 window actors | Tracks its runtime by lock-file bumps | Small |

### 2.2 What each one teaches

- **Brave's three-tier rule.**
  1. Write code in `src/brave` first.
  2. Next, use `chromium_src` overrides: "The content of this directory is prioritized over upstream files during compilation."
  3. Patch upstream only "when other options are exhausted".

  Specific tricks:
  - "Header patches should use preprocessor defines when possible." A `BRAVE_BROWSER_H` macro placed as "the last thing in `public`" injects members with a one-line patch.
  - Rename an upstream function with `#define` so the override can still call the original.
  - Never copy whole upstream files: "This will lead to newer Chromium rebases over time using old stale code." Goo Engine's vendored EEVEE is exactly this failure.
- **Electron's justification policy.** "Every patch in Electron is a maintenance burden. When upstream code changes, patches can break—sometimes without even a patch conflict or a compilation error." Every patch's commit message has to give one of three reasons:
  1. it is temporary or being upstreamed, with a link;
  2. it is needed to compile and can't be done by subclassing;
  3. it is fundamentally incompatible with upstream.

  Also: "We should aim to upstream changes whenever we can, and avoid indefinite-lifetime patches." The order file is used instead of `001-` prefixes because it "reduces conflicts related to patch ordering".
- **VSCodium's mechanics.** When `git apply` fails, `dev/update_patches.sh` stops for a human to fix the `*.rej` files, then rewrites the patch. Configuration goes through a JSON overlay, not patches.
- **Cursor and Windsurf: the cost of deep divergence.** Around September 2025, Cursor was still on VS Code 1.99.3 (from March 2025) while upstream was at 1.104 **(forum, unverified)**. By June 2026 the gap was about 20 minor versions, and extensions whose `engines.vscode` asks for more than 1.105.1 refuse to install. The visible cost is lag, extension breakage and slower security fixes (an advocacy blog cites CVE-2024-49050). Neither company publishes its engineering cost.
- **Vivaldi and Floorp: "put the product above the engine".** Vivaldi's UI is a React app running on top of Chromium. Floorp v12 moved almost all features into runtime-loaded TypeScript and kept its Firefox changes to a pinned runtime with about 30 patches. This is the closest match to GameStudio: features as hot-reloaded scripts, with the engine underneath barely touched.
- **Edge: upstream by default.** "Our default position will be to contribute to the Chromium project." Edge landed "over 275 commits" in its first four months (April 2019) and declined to fork, to avoid splitting the community.

### 2.3 Catalogue of techniques

| Technique | Used by | What it buys | Blender equivalent for GameStudio |
|---|---|---|---|
| **Pinned upstream** (tag + SHA in a file) | VSCodium, Floorp, LibreWolf | Reproducible builds and an explicit "upgrade" event | `upstream.json` holding `v5.2.2` / `d13f752e3b` |
| **Patch queue, one concern per patch, named by concern** | VSCodium, Electron, LibreWolf, Brave | An auditable, countable delta. A failed patch names the feature that broke | `patches/0007-wm-game-editor-hook.patch` |
| **Order file instead of numeric prefixes** | Electron | Fewer ordering conflicts | `patches/series` |
| **Overlay or override directory** compiled ahead of upstream | Brave `chromium_src`, VSCodium `product.json`, Floorp `browser-features/` | New code lives in new files, which can't conflict | `source/blender/gamestudio/` (own CMake target), app template, bundled extensions |
| **Hook patches**: one-line calls or macros into overlay code | Brave (`BRAVE_*` defines), UPBGE (in spirit) | Upstream edits stay a few lines | `ED_spacetype_gamestudio();` in `ED_spacetypes_init()` |
| **Vendor-version macro beside upstream's** | Bforartists (`BFORARTISTS_VERSION`) | Add-on and `.blend` version checks keep working | `GAMESTUDIO_VERSION`, with `BLENDER_VERSION` untouched |
| **Patch justification + upstream link** | Electron | Patches have a lifetime and a reason to be deleted | A header on each patch (see §5.3) |
| **Automated upstream detection and CI rebase** | VSCodium (cron), LibreWolf (`make check`), Brave (CI labels, rebase branches) | Breakage is found within days, not at release time | A nightly "apply + build + headless tests" job against the release branch, and weekly against `main` |
| **Upstreaming the hook, then deleting the patch** | Edge, Electron, Goo (NPR), NVIDIA (USD) | The patch count goes *down* over time | Python-registerable editors, app-template branding, a GPU external-texture API |
| **Product layer in a scripting language above the engine** | Vivaldi (React), Floorp (TS) | Most product work never touches engine code | GameStudio's add-on and app template in Python, with the runtime in TS |
| **Weekly or continuous merge of upstream `main`** | Bforartists, UPBGE | Small, frequent conflicts, but a constant tax and alpha code in releases | Not recommended for one developer (§4) |
| **Freeze** | Range 2.0, and Cursor in practice | No merge cost; debt grows | Not acceptable: GameStudio is pitched on "current Blender" |

---

## 3. Legal and policy constraints for a Blender fork

### 3.1 Name and logo

From blender.org's Trademark Policy (undated page, fetched today), Logo page and FAQ:
- **Forks:** "Same goes for forks of the Blender software, give it a new name and create a unique brand that way. The latter is also enforced by the GNU GPL, which explicitly excludes brand names from the freedom." GPLv3 §7(e) allows "Declining to grant rights under trademark law".
- **Allowed without permission:** "Use the Blender wordmark to explain that your software is based on Blender's open source code, or is compatible with Blender's software."
- **Not allowed:** "Don't use the Blender trademark (name or logo) in the name of your business, product, service, app, domain name". Also: no display more prominent than your own name, no taglines or slogans, no implied endorsement.
- **Logo:** "The logo and the brand name 'Blender' are not part of the GNU GPL", and "Do not use the Blender logo as your own logo."
- **FAQ:** "Yes, you are free to name Blender whatever you like, and repackage and distribute it under your own branding", provided you "still deliver or publish it under the GPL conditions". You may not change copyright notices or claim authorship. Splash screens and icons are GPL material.

**For GameStudio:** "GameStudio — based on Blender's open source code" is fine. "Blender GameStudio", "GameStudio for Blender" as a product name, a `blender-*` domain, or the Blender logo on our splash or icon are not. Keep Blender's credits and copyright notices, and add our own.

### 3.2 GPL obligations for the fork itself

- **Every binary needs its Corresponding Source:** "all the source code needed to generate, install, and … run the object code and to modify the work, including scripts to control those activities" (GPLv3 §1).
  - For us: the pinned upstream tag, the patch series, the overlay, the build scripts and the bundled add-ons, at a tag matching each release. §6(d) allows offering it "from a designated place", such as GitHub tags.
  - Linking to blender.org's sources is enough for stock Blender but not for a fork.
  - For Blender's precompiled `lib/` dependencies, the practical approach is to point to the upstream library repositories for the same Blender version **(unverified)**.
- **Bundled add-ons and scripts are part of the program.** Scripts that use the Python API "must be shared under the GPL if you distribute or publish them" (Blender FAQ). Everything we bundle that imports `bpy` must be GPL-compatible; the GameStudio add-on already is (GPL-3.0-or-later). Extensions that users install themselves are not our distribution.
- **Internal use is free.** The GPL "does not require you to release your modified version"; only distribution triggers the duty.
- **Shipping the MIT runtime in the same installer is fine.** An "aggregate" of "separate and independent works … not combined with it such as to form a larger program" does not extend the GPL to the other parts.

### 3.3 Does a separate-process runtime stay outside the GPL when the fork embeds it?

FSF GPL FAQ (page updated 2026-01-13):
- "We believe that a proper criterion depends both on the mechanism of communication (exec, pipes, rpc, function calls within a shared address space, etc.) and the semantics of the communication (what kinds of information are interchanged)."
- "pipes, sockets and command-line arguments are communication mechanisms normally used between two separate programs … But if the semantics of the communication are intimate enough, exchanging complex internal data structures, that too could be a basis to consider the two parts as combined."
- "Using shared memory to communicate with complex data structures is pretty much equivalent to dynamic linking."
- On interpreters: if the interpreter is "extended to provide 'bindings' to other facilities … the interpreted program is effectively linked to the facilities it uses".
- GPLv3 §1 also pulls into the Corresponding Source any "dynamically linked subprograms that the work is specifically designed to require, such as by intimate data communication".

Blender's own FAQ is stricter in wording. It grants "full freedom" only to software that operates outside Blender, uses no Blender code or API calls, produces data for Blender, and executes Blender.

| How the fork shows the game | Mechanism | Semantics | Risk that the runtime becomes part of the GPL work |
|---|---|---|---|
| A separate window beside Blender (today) | Separate process, sockets | glTF + JSON manifest + NDJSON deltas, all documented | **Low** |
| The runtime's native window reparented into a Blender area (an OS-level child window) | Separate process. The OS window handle is the only shared thing | Pixels drawn by the runtime itself | **Low (inference)** |
| Frames streamed as pixel buffers over a socket or shared memory | Shared memory | A flat image with a documented header, not Blender structures | **Low to medium (inference)**. Keep the payload "just an image" |
| A GPU texture shared by handle (D3D/Vulkan external memory) and drawn by the fork | Shared GPU memory | Still an image | **Low to medium (inference)**. The FAQ's trigger is "complex data structures", which a texture is not |
| The runtime reads Blender's DNA or scene graph from shared memory | Shared memory | Blender's internal data structures | **High** |
| The runtime loaded as a DLL or plugin inside the fork, or game JS run in an in-process webview with bindings into Blender | Same address space or bindings | Function calls and data structures | **High.** A combined work |

**Rules that keep the runtime MIT:**
- The fork must be a complete editor without the runtime, so it is not "specifically designed to require" it (GPLv3 §1).
- The runtime must run without the fork, on the web or in Electron, and must never depend on Blender internals.
- The protocol must be public and generic: glTF, JSON, NDJSON and pixel frames.

---

## 4. Synthesis: fork-maintenance strategies, ranked for a small team

Ranked by expected cost and risk for **1 developer + AI assistants**, best first.

| # | Strategy | Real examples | Cost per Blender release (3 per year) | Ongoing cost | Typical lag | Main failure mode |
|---|---|---|---|---|---|---|
| 1 | **Distribution, not fork**: official Blender binary + GameStudio app template + bundled extensions + launcher, rebranded only where allowed | Blender's own system templates, NVIDIA's return to add-ons, Fluid Designer's successor, GameStudio today | Python API fixes only (0.5–2 days) **(inference)** | About 0 | Days | Can't add editors, change C UI, rename the window, or zero-copy embed the runtime |
| 2 | **Thin fork: pinned tag + small patch queue + overlay directory** | VSCodium (70), LibreWolf (65), Floorp (about 30), Electron (142 on Chromium), Brave with a large team | Linear in patches × upstream churn in the files they touch. For ≤20 one-line hooks, 1–3 days **(inference)** | Nightly CI | 1–2 weeks | Patch creep. Patches that drift into logic changes |
| 3 | **Continuous merge of `main`** | Bforartists (weekly), UPBGE (near-daily) | Spread across the year | **Constant.** UPBGE: about 4.6 merges a week by one person. Bforartists: one weekly merge PR | 1–2 weeks (Bforartists) to months (UPBGE stable) | The delta becomes invisible and unbounded (464 or 238 upstream C files). Releases contain upstream alpha code |
| 4 | **Re-port squashed features per release** | Goo Engine | Low in quiet releases. **Months** when upstream rewrites a subsystem | Low | About 9 months per port. Now 5 releases behind | Vendoring removed upstream code (legacy EEVEE). Falling behind for good |
| 5 | **Freeze and diverge** | Range 2.0, Fluid Designer, Mechanical Blender, Cursor in effect | 0 | Growing debt: features, security, the extension ecosystem | Grows without limit | Abandonment or a forced rewrite |

**Why #3 is wrong for us even though it works for Bforartists and UPBGE:**
- It needs a merger every week for life. UPBGE's merger is a single point of failure.
- It ships alpha upstream.
- It hides *what is ours*: you can't count a merged delta, so you can't budget it.
- An AI assistant is much better at re-applying a 30-line, well-described patch than at resolving a 300-commit merge across 464 files.

---

## 5. Implications for the GameStudio fork

### 5.1 Strategy: "distribution first, thin fork second"

1. **Every feature defaults to Python**, in the GameStudio add-on or the GameStudio **app template**: startup layout, keymaps, theme, splash, workspaces, panels, menus and tool settings.
   - Customize Blender's built-in UI by **appending to or re-registering its classes from the template or add-on**, never by editing `scripts/startup/bl_ui/*.py`. This is our `chromium_src`.
   - It goes further than Bforartists (84 edited `bl_ui` files) and avoids its merge regressions.
2. **C/C++ only for what Python can't do:**
   - a dedicated **Game editor** space type (Python can't add editors);
   - **zero-copy display** of the runtime's frames (if we want it);
   - **product identity**: app name, window title, user config directory, file associations, about box;
   - the few performance hooks the add-on can't reach.
3. **All new C/C++ goes in an overlay**: `source/blender/gamestudio/`, its own CMake library, wired in through one-line hook patches. No copies of upstream files, ever. Goo's vendored EEVEE is the cautionary example.
4. **Track Blender *releases*, not `main`.** Rebase the queue when `blender-vX.Y-release` branches (Beta, about 6 weeks before release) and ship ≤14 days after the upstream tag. The **LTS (5.2 now) is the supported base**; non-LTS releases are optional when we're busy.
5. **Upstream our hooks.** Each hook Blender accepts deletes a patch. Priority targets:
   - Python-registerable editor or space types. These are long requested; a DevTalk answer says "custom (Python defined) editors are not supported currently".
   - App-template-level control of the app name, window title and about text.
   - A `gpu` module API for importing external (shared-handle) textures.

   Goo (NPR), NVIDIA (USD) and Edge show this route works. Acceptance is not guaranteed, so plan as if every hook stays ours.

### 5.2 Patch budget (hard limits, enforced in CI)

| Metric | Budget | For comparison |
|---|---|---|
| Patches in the series | **≤ 20** | VSCodium 70, LibreWolf 65, Floorp about 30, Electron 142 |
| Changed lines inside upstream-owned files (excluding new overlay files) | **≤ 1,000** | Bforartists about 26k C plus 39k Python. UPBGE about 48k |
| Distinct upstream files touched | **≤ 30** | Modified upstream C/C++ files: Bforartists 464, UPBGE 238, Goo 159 |
| DNA struct or member changes (`makesdna/DNA_*_types.h`) | **0, with one budgeted exception**: a Game editor needs a space-type enum value and a layout-only `SpaceGame` struct. Test what stock Blender does with that area in a saved file **(unverified)** | UPBGE 26 files. Goo 16. Bforartists 12 |
| Edits to `scripts/startup/bl_ui/*.py` | **0.** Use template and add-on overrides | Bforartists 84 files |
| Patches containing non-trivial logic (more than about 15 lines in one upstream hunk) | **≤ 3**, each with an upstreaming issue filed | Brave: "trivial changes, and not nested logic changes" |
| Time to rebase onto a new release branch | **≤ 2 working days.** If it is exceeded twice in a row, freeze new patches and pay down debt | Bforartists' weekly merge PRs: 0–3 days each, 52 times a year |

**Why no DNA changes:** `.blend` files made in GameStudio must open in stock Blender with the add-on installed. Keep all game data in ID properties and add-on `PropertyGroup`s (report 04). This keeps users free to leave and is the best possible hedge for the fork.

### 5.3 Rules that keep upgrades cheap

1. **Pin upstream explicitly.** `upstream.json` = `{ "tag": "v5.2.2", "commit": "d13f752e3b…" }`. An upgrade is a one-line PR plus patch fixes, never an untracked merge.
2. **One concern per patch, named by concern, with a header.** Example: `0004-wm-gamestudio-window-title.patch`. The header carries `Why:`, `Hook-for:` (the overlay function it calls), `Upstream:` (issue/PR link or "not upstreamable: reason", in Electron's three categories), `Test:` (the headless test that proves it works) and `Remove-when:`. The header is also what lets an AI assistant **re-implement** the intent when the context has moved, instead of guessing at conflict markers.
3. **Hooks, not edits.** A patch should add one call or `#include` into overlay code, or one macro at the end of a struct or class (Brave's `BRAVE_*` trick). No reformatting, no renaming of upstream symbols, no moving upstream files, no changing upstream defaults in place. Defaults belong in the template's `startup.blend` and `userpref.blend`.
4. **Leave `BLENDER_VERSION` alone.** Add `GAMESTUDIO_VERSION`, as Bforartists does, so extensions' `blender_version_min` checks and `.blend` versioning behave exactly as on stock Blender.
5. **Use a separate identity at runtime.** Use our own app name, user config directory (so we never overwrite a user's Blender preferences) and file-association IDs. Keep the `.blend` format and extension, since that is truthful compatibility. *(Recommendation. I didn't verify how Bforartists or UPBGE handle config directories.)*
6. **Tooling borrowed from Electron and VSCodium:**
   - `tools/fork/apply.py`: `git am --3way` the series onto the pinned tag;
   - `export.py`: commits to patch files, plus the `series` order file;
   - `budget.py`: fails CI when a §5.2 limit is exceeded;
   - `rebase.py <new-tag>`: applies, builds and runs `npm run test:blender` and `e2e`, and stops on the first failing patch with the patch header and the upstream diff of the touched files ready for the AI assistant.
7. **CI cadence:**
   - **nightly**, apply and build against the current LTS branch (catches 5.2.x point releases);
   - **weekly canary** against `main`, apply and compile only, giving about 17 weeks of warning before each release (Blender `main` is in alpha for 17 weeks);
   - **when a release branch opens**, a full rebase PR.
8. **An AI assistant may propose a resolution, never merge it.** A rebased patch lands only when its `Test:` passes and the budget check is green. That is the same "proposal → human Apply" rule the in-editor assistant follows (report 07).
9. **Keep the GPL boundary in the design, not just the license file** (§3.3). The runtime never links Blender code, never reads Blender structures, and talks only through the documented protocol and image frames. The fork must work as a full editor with no runtime installed.
10. **Release hygiene:**
    - Every binary gets a matching source tag containing the upstream pin, the patches, the overlay, the build scripts and the bundled add-ons.
    - The product is named "GameStudio", with "based on Blender's open source code" in text and no Blender logo as our mark.
    - The MIT runtime ships in the same installer as a separate program (an aggregate).

### 5.4 What to build first

1. **Before writing any C, a v0 "distribution".** Package official Blender 5.2.2 with the GameStudio app template, our splash and startup layout, and the add-on preinstalled, launched with `--app-template GameStudio` (and the per-app config directory if possible without patching). This costs nothing to maintain and checks how far Python gets us.
2. **v1 thin fork, about 5 patches:**
   - build hook for the overlay library;
   - app name, window title and config directory;
   - Game editor space registration;
   - runtime frame display;
   - about box and credits.
3. **Measure the first real rebase (5.2 to 5.3) against the 2-day budget** before adding any further patches.

---

## Sources

**Blender forks (repos and measurements, fetched 2026-10-09)**
- https://github.com/Bforartists/Bforartists (README, branches, `merge-weekNN` history, merge `e35eb5f` vs `1b20a88`)
- https://github.com/Bforartists/Bforartists/releases/tag/v5.2.0
- https://raw.githubusercontent.com/Bforartists/Bforartists/v5.2.0/source/blender/blenkernel/BKE_blender_version.h
- https://github.com/blender/blender (release tags v3.0.0–v5.2.2, dates)
- https://www.bforartists.de/bforartists-2-alpha-0-8-0-released/
- https://www.cgchannel.com/?p=114611 (Bforartists 2, April 2020; via search summary)
- https://devtalk.blender.org/t/introduction-and-questions-how-to-come-from-bforartists-fork-to-feature-development-in-blender/2084 (via search summary)
- https://github.com/UPBGE/upbge (master history, merge `618c637` vs `134b607`, tags v0.30–v0.50)
- https://github.com/UPBGE/upbge/wiki/Release-notes-version-0.44
- https://github.com/dillongoostudios/goo-engine (branches, `goo-engine-v4.4-release` vs Blender 4.4.3)
- https://www.cgchannel.com/2024/12/check-out-blenders-experimental-non-photorealistic-rendering-build/
- https://www.patreon.com/posts/145563519 (Goo Engine 4.4, Dec 2025; via search summary, unverified)
- https://www.blender3darchitect.com/?p=58226 (Fluid Designer interview, June 2018)
- https://www.blendernation.com/2014/03/12/fluid-designer-modifying-blenders-ui/
- https://www.blender3darchitect.com/?p=64300 (PyClone / Home Builder)
- https://github.com/mauge123/mechanical-blender
- https://www.blendernation.com/2017/01/16/mechanical-blender-fork-adds-cad-tools-blender/
- https://www.patreon.com/rangeengine/posts/range-dev-update-146325731 (via search summary)
- https://developer.nvidia.com/blog/introducing-omniverse-usd-support-for-blender/ and https://docs.omniverse.nvidia.com/connect/latest/blender/manual.html (via search summary)
- https://www.blendernation.com/2024/09/01/bonsai-previously-blenderbim-add-on-v0-8-0-adds-blender-4-2-and-much-more/
- https://community.osarch.org/discussion/comment/21369
- https://docs.blender.org/manual/en/latest/advanced/app_templates.html
- https://developer.blender.org/docs/handbook/release_process/release_cycle/
- https://devtalk.blender.org/t/is-it-possible-to-create-a-new-editor-area-with-python-api/17605

**Other forks**
- https://github.com/brave/brave-core/blob/master/docs/patching_and_chromium_src.md (and brave-core master tree: `patches/`, `chromium_src/`)
- https://github.com/brave/brave-browser/wiki/Chromium-rebases
- https://github.com/brave/brave-browser/wiki/Brave-Release-Schedule
- https://developer.chrome.com/blog/chrome-two-week-release
- https://chromeunboxed.com/chrome-153-launches-today-googles-accelerated-2-week-release-cadence-begins/
- https://github.com/electron/electron/blob/main/docs/development/patches.md (and `patches/` tree)
- https://github.com/VSCodium/vscodium (`docs/howto-build.md`, `prepare_vscode.sh`, `upstream/stable.json`, `patches/`, commit history)
- https://github.com/microsoft/vscode (tags 1.130.0–1.141.0)
- https://forest.watch.impress.co.jp/docs/news/2091953.html (VS Code weekly stable from 1.111)
- https://forum.cursor.com/t/request-upgrade-vs-code-extension-api-base-beyond-1-105-1/164443
- https://forum.cursor.com/t/why-cursor-is-still-stuck-on-vscode-base-105-its-like-5-months-old-base/164193/3
- https://forum.cursor.com/t/cursor-still-on-vscode-1-99-3-update-plans/135066 (via search summary)
- https://forum.cursor.com/t/are-you-sure-cursor-2-1-really-running-vs-code-1-105/144171 (via search summary)
- https://blog.kilo.ai/p/vs-code-forks-are-facing-a-grim
- https://docs.windsurf.com/windsurf/recommended-plugins (via search summary)
- https://blogs.windows.com/msedgedev/2019/04/08/microsoft-edge-preview-channel-details/
- https://github.com/MicrosoftEdge/WebView2Announcements/issues/137 (via search summary)
- https://vivaldi.com/?p=29166 (Chromium integration interview, Sept 2018)
- https://www.thurrott.com/?p=287859 (Vivaldi React UI; via search summary)
- https://codeberg.org/librewolf/source (`patches/`, Makefile)
- https://factually.co/fact-checks/technology/librewolf-security-patch-timing-after-firefox-updates-75ede6
- https://discuss.privacyguides.net/t/librewolf-browser-firefox-fork/148/103
- https://github.com/Floorp-Projects/Floorp (`docs/development/architecture-overview.mdx`, `floorp-runtime.lock.json`, `tools/patches/`)

**Legal and policy**
- https://www.blender.org/about/trademark-policy/
- https://www.blender.org/about/logo/
- https://www.blender.org/?p=94341 ("Reinforcing the Blender Trademark Guidelines", Apr 18, 2025)
- https://www.blender.org/about/license/
- https://www.blender.org/support/faq/
- https://www.gnu.org/licenses/gpl-faq.html (#MereAggregation, #GPLPlugins, #NFUseGPLPlugins, #IfInterpreterIsGPL, #GPLRequireSourcePostedPublic; updated 2026-01-13)
- https://www.gnu.org/licenses/gpl-3.0.html (§1 Corresponding Source, §5 aggregate, §6(d), §7(e))
