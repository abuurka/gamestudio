# 11: Release engineering for a GameStudio Blender fork

*Research date: 2026-10-09. Target: Blender 5.2.2 LTS (`v5.2.2`, commit `d13f752e`). Claims tagged **[V]** were checked against the local source clone at `F:\Development-games\blender-upstream` or measured with git against upstream tags. Claims tagged **(unverified)** are estimates, secondary reports or proposals. Everything else comes from the official docs and release notes listed in Sources. Nothing was built.*

---

## 1. TL;DR

- **Base the fork on 5.2 LTS, supported until July 2028.** `main` is now 5.4 alpha and 5.3 is in beta until Nov 4, 2026 **[V]**. A February 2026 proposal moves LTS releases to November. The next LTS would then be **5.5 LTS on Nov 9, 2027**, and the next API-breaking release **6.0 in 2028**, followed by 6.2 LTS **(proposal; adoption unverified)**.
- **Upstream churn is very high.** Each 4-month release changes **4,900–6,400 of the ~8,500 files** under `source/` and `intern/`; the 4.5→5.2 LTS hop touched **8,361** **[V]**. Typical hook files (`rna_space.cc`, `DNA_space_types.h`, `creator.cc`, `space_view3d.py`) change in every release **[V]**, so expect most core patches to need conflict fixes.
- **VS 2019 cannot build 5.2.** `platform_win32.cmake` stops with a fatal error below MSVC 19.44.35216 ("MSVC 2022 17.14.14 or newer") **[V]**. On this machine, `make.bat` auto-detection tries VS 2019 first and would pick the old VS 2019 Community 16.5, so **always pass `2022`**.
- **Toolchain:** VS 2022 17.14.x with the *Desktop development with C++* workload, CMake ≥ 3.21 (4.2 is fine), git with git-lfs, and Ninja plus sccache. You don't need a system Python, because the libs ship Python 3.13 **[V]**.
- **The precompiled Windows libs are 6.98 GB checked out, in 1,276 LFS files** **[V]**. Git LFS also keeps a second copy, so plan on about 14 GB. LLVM alone is 4.3 GB.
- **Use a self-hosted runner for Windows CI.** Standard GitHub runners have 14 GB of SSD, a 6 h job limit and a 10 GB free cache. That is too tight for 7 GB of libs plus a build, although UPBGE does build weekly on `windows-latest`. A 16-core Windows larger runner costs **$0.082/min**.
- **Repo structure:** keep almost all code in an **out-of-tree module plus a Python extension**. Hook it in with a **small, ordered patch queue** applied to a clean upstream tag. Run a weekly "canary" that applies the queue to upstream `main` so breakage shows up months early.
- **Signing:** Azure Artifact Signing costs about **$9.99/month**, but individuals must be in the USA or Canada. Otherwise an OV certificate costs **$150–300/yr**. EV no longer bypasses SmartScreen. macOS needs a Developer ID, the hardened runtime and `notarytool`.
- **Effort per LTS upgrade:** about **1–2 weeks** with a minimal-hook design, against **1–2+ months** for a deep fork that edits core files **(unverified estimate)**.

---

## 2. Blender's release cadence and LTS policy

### 2.1 Versions and support (as of 2026-10-09)

| Version | Released | Type | Supported until | Status |
|---|---|---|---|---|
| 4.2 | Jul 16, 2024 | LTS | ~Jul 2026 | Final update 4.2.23 (Jul 2026), end of life |
| 4.5 | Jul 15, 2025 | LTS | ~Jul 2027 | 4.5.14 (Sep 15, 2026), last release with Intel macOS |
| 5.0 | Nov 18, 2025 | Regular, API-breaking | until 5.1 | — |
| 5.1 | Mar 17, 2026 | Regular (new libs, Python 3.13) | until 5.2 | — |
| **5.2** | **Jul 14, 2026** | **LTS** | **Jul 2028** | 5.2.2 (Sep 15, 2026), current stable |
| 5.3 | Nov 2026 | Regular | — | Beta until Nov 4, 2026 |
| 5.4 | May 11, 2027 (proposed) | "New libs" | — | Alpha on `main` [V] |
| 5.5 | Nov 9, 2027 (proposed) | LTS | ~2029 (unverified) | — |
| 6.0 / 6.1 / 6.2 | 2028 (proposed) | Breaking / regular / LTS | — | 6.0 alpha starts Jun 30, 2027 (proposed) |

The official policy is three releases a year, one of them an LTS with "critical fixes throughout a 2-year time span" and "no new features, API changes or improvements." The Feb 6, 2026 devtalk proposal "Evolutions to Blender Release Cycle" keeps the slots but gives them roles: **March** = library/VFX-platform update, **July** = regular, **November** = LTS. 2027 gets only 5.4 and 5.5 LTS, with long betas. Compatibility breaks go only into the x.0 release every other year, and smaller breaks must be deprecated in the preceding LTS. **(Proposal. 5.4 alpha started on the proposed date, but I found no formal adoption notice.)**

### 2.2 Branches and tags on projects.blender.org [V]

- **`main`** carries the next feature release (now `BLENDER_VERSION 504`, `alpha`).
- **`blender-vX.Y-release`** is branched at beta, takes only fixes, and lives two years for an LTS (branches exist from 2.93 through 5.3).
- **Tags** `vX.Y.Z` are cut on the release branch (`v4.2.0`…`v4.2.23`, `v5.2.0`…`v5.2.2`).
- **Library repos** (`lib-windows_x64`, `lib-windows_arm64`, `lib-linux_x64`, `lib-macos_arm64`) are submodules under `lib/` with `update = none` and matching branch names. Each source commit pins an exact lib commit: `v5.2.2` pins `lib/windows_x64` at `60d6e96b` (Jun 29, 2026). There is no `lib/macos_x64`.

### 2.3 Where the API breaks fall

Breaking changes cluster in the **x.0 release**: 4.0, 5.0 and, if the proposal holds, 6.0. Removals are announced one or two releases earlier. Python API release notes:

| Release | Breaking changes / removals (approx.) | Deprecations | Notable for GameStudio |
|---|---|---|---|
| 4.3 | ~5 breaking, ~37 legacy EEVEE props removed | 0 | Grease Pencil API rewritten. `AttributeGroup` split |
| 4.4 | ~11 | 10, all removed in 5.0 | Slotted Actions. `Sequence`→`Strip` (~37 types) |
| 4.5 LTS | ~10, mostly behaviour changes | 4 | `GPUShader(vert, frag)` deprecated |
| **5.0** | **~60 across ~20 areas** | 4 (2 removed in 6.0) | `bpy.props` storage split from IDProperties. BGL removed. Legacy Action API removed. 15 bundled modules made private (e.g. `rna_info`) |
| 5.1 | ~7 (~20 identifiers) | `template_list(columns)`, 9 VSE props (removed in 6.0) | Python 3.13 |
| 5.2 LTS | ~6–7 (+18 relocated paint props) | 0 | Geometry Nodes modifier inputs moved to real RNA properties |

### 2.4 Measured code churn between releases [V]

Measured with `git diff --shortstat --no-renames` between upstream release tags. Without rename detection, a moved file counts twice.

| Span | Releases | Files changed, whole repo | Files changed in `source/`+`intern/` (~8.4–8.7k files) | Lines in `source/`+`intern/` | Python layer* | `makesdna` files |
|---|---|---|---|---|---|---|
| 4.2.0 → 4.5.0 | 3 | 19,587† | 8,882 | +757k / −676k | 537 | 108 |
| 4.5.0 → 5.0.0 | 1 | 8,330 | 5,289 | +236k / −195k | 409 | 83 |
| 5.0.0 → 5.1.0 | 1 | 8,533 | 6,375 | +382k / −334k | 421 | 143 |
| 5.1.0 → 5.2.0 | 1 | 9,674 | 4,899 | +270k / −161k | 261 | 97 |
| 4.5.0 → 5.2.0 | 3 | 16,444 | 8,361 | +804k / −605k | 535 | 147 |

\* `source/blender/python`, `makesrna`, `scripts/modules` and `scripts/startup/bl_ui`. † Inflated because test data moved into the main repo: `tests/` grew from 246 to 7,235 files.

**What this means:** a 4-month release touches most of the C/C++ tree, much of it probably mechanical refactoring **(inference)**. A fork that edits many core files should expect conflicts in most of them at each upgrade.

---

## 3. Building Blender 5.2 on Windows

### 3.1 Requirements

| Item | Requirement | Source |
|---|---|---|
| Compiler | **MSVC ≥ 19.44.35216 (VS 2022 17.14.14)**. Official builds use 17.14.21. VS 2026 (v18) is also detected | `platform_win32.cmake` [V]. Handbook table |
| VS 2019 | **Not supported for 5.2.** `make.bat` still accepts `2019`/`2019b`, but CMake stops with a fatal error | [V] |
| Workload | "Desktop development with C++". `vswhere` requires `Microsoft.VisualStudio.Component.VC.Tools.x86.x64` | `detect_msvc_vswhere.cmd` [V] |
| Windows SDK | No pinned version. `_WIN32_WINNT=0x603`. The SDK installed with the workload is fine | [V] |
| Clang (optional) | clang-cl ≥ 20.1.0. The buildbot `pipeline_config.yaml` sets `msvc: 2022, use_clang_cl: true`, which suggests official x64 builds use clang-cl **(inference)** | [V] / handbook |
| CMake | ≥ 3.21. 3.28.2 disables unity builds. VS 2026 plus a bundled CRT needs CMake ≥ 4.2 | `CMakeLists.txt` [V] |
| git + git-lfs | Required. `make update` runs `git lfs install --skip-repo` and pulls the lib submodule | `make_update.py` [V] |
| Python | None needed. The libs ship Python 3.13 (`lib\windows_x64\python\313`) | `find_dependencies.cmd` [V] |
| Shell | `cmd.exe` (not PowerShell). No spaces in the path | Handbook. `check_spaces_in_path.cmd` |
| Standard | C++20 | `CMakeLists.txt` [V] |

**On this machine [V]:** VS 2022 Community 17.14.14 has **no** C++ tools. VS 2019 BuildTools 16.11 and VS 2019 Community 16.5 have them but are too old. Windows SDKs go up to 10.0.19041, and `ninja`, `sccache` and `clang-cl` are not on PATH. `autodetect_msvc.cmd` tries 2019 before 2022, so a bare `make` would select VS 2019 and fail.

### 3.2 Precompiled libraries

`make update` enables the `lib/windows_x64` submodule from `projects.blender.org/blender/lib-windows_x64.git`, branch `blender-v5.2-release`, and runs `git lfs pull` **[V]**. The libraries are built by Blender's dependency builder (`make deps`) on a clean VM with ≥ 16 GB RAM and 200 GB of disk. You don't need to rebuild them unless you change a dependency.

Checkout at `v5.2.2`, measured by summing LFS pointer sizes **[V]**:

| Total | Files | Largest folders |
|---|---|---|
| **6.98 GB** | 19,077 (1,276 in LFS) | llvm 4,349 MB · dpcpp 425 · usd 295 · python 276 · osl 261 · eigen 194 · MaterialX 193 · manifold 128 · alembic 91 · abseil 77 |

The LFS store under `.git/modules/lib/windows_x64/lfs` holds a second copy, so plan on **about 14 GB per lib version**. Gitea reports the whole `lib-windows_x64` repo, with all history, at 48 GB, and `lib-linux_x64` at 25 GB. The main repo's own LFS files at `v5.2.0` add about **0.8 GB**, almost all of it test data **[V]**.

### 3.3 `make.bat` options that matter [V]

| Option | Effect |
|---|---|
| *(none)* | CMake defaults: most features on, no GPU kernel binaries |
| `full` | `blender_full.cmake`: everything except CUDA, HIP and oneAPI kernels |
| `release` | `blender_release.cmake`: identical to blender.org. Needs the CUDA, OptiX and HIP SDKs (buildbot: CUDA 12.9.1, OptiX 8.0, HIP 7.1). GPU kernels take hours |
| `lite` | Turns off about 60 features (Cycles, FFmpeg, USD, Alembic, OpenVDB, Draco, and others). Fastest build for UI work |
| `developer` | Asserts, `WITH_GTESTS`, Python safety checks, `compile_commands.json`. Can be combined with the targets above |
| `ninja` | Ninja generator instead of MSBuild (which `make.bat` runs with `/maxcpucount:2`, i.e. two projects at a time) |
| `sccache` | `WITH_WINDOWS_SCCACHE`, **Ninja only**. Switches debug info to `/Z7` |
| `2022` / `2022b` / `2026` | Pick the VS version, or Build Tools with `b` |
| `debug`, `builddir X`, `nobuild`, `test`, `packagename X`, `with_tests`, `clang`, `asan` | As named |

`WITH_UNITY_BUILD` is on by default. Each Ninja build directory gets a `rebuild.cmd` that calls vcvars and runs `ninja install` **[V]**.

### 3.4 Build time and disk

| Measure | Value |
|---|---|
| `make full ninja 2019`, clean, reported by a Blender Windows developer (4.4-era code) | 5 min 50 s on SSD, 7 min 10 s on HDD (Dec 2024, CPU not stated) |
| Ninja vs MSBuild (2018 commit) | Clean build 801 s vs 868 s. **Incremental 15 s vs 43 s** |
| RAM rule of thumb (devtalk) | 1–2 GB per parallel job. A 16-core/32-thread machine with 32 GB stayed under 20 GB |
| **Estimate for this PC (16 cores, 32 GB), `full ninja`, cold** | **~15–30 min. Warm sccache: ~3–8 min (unverified)** |
| Single `.cc` change + relink | ~30–90 s, mostly linking `blender.exe` and its PDB **(unverified)** |
| Disk per Release build directory, with PDBs | ~15–25 GB. Debug: 40–60 GB **(unverified)** |
| Installed `bin/` | ~0.9 GB (stock 5.2.2 install measured in report 06) |

**Budget for this machine:** ~1 GB source + 14 GB libs + ~25 GB per Release build directory + 30–50 GB sccache, so **~80–100 GB** for one version plus a "next upstream" tree. F: has 287 GB free.

### 3.5 macOS and Linux

| | macOS | Linux |
|---|---|---|
| Hardware | **Apple Silicon only** since 5.0. No Intel libs [V] | x86_64 (arm64 libs are not in `.gitmodules`) |
| Compiler | Xcode ≥ 16.3. Official builds use 26.1.1 | GCC ≥ 14.0 (official 14.2.1) or Clang ≥ 17 [V] |
| Deployment target | `OSX_MIN_DEPLOYMENT_TARGET 11.2` in CMake [V]. Officially supported: macOS 13+ (unverified, secondary) | Libs built on Rocky Linux 8 for VFX Reference Platform compatibility |
| Tools | `brew install cmake git git-lfs` | `install_linux_packages.py`, git-lfs, ninja. `make ccache ninja` |
| Libs | `lib/macos_arm64`, 12 GB repo | `lib/linux_x64`, 25 GB repo |

---

## 4. CI for a fork

### 4.1 GitHub Actions against self-hosted runners

| Option | Specs | Fits Blender? | Cost per clean Windows build |
|---|---|---|---|
| Standard runner, public repo | 4 vCPU, 16 GB, **14 GB SSD**, 6 h limit | Marginal. 14 GB of libs plus build is above the documented disk size, but UPBGE builds `blender_release.cmake` weekly on `windows-latest` | Free |
| Standard runner, private repo | 2 vCPU, 8 GB, 14 GB | No. Too little RAM and disk | $0.010/min |
| Larger runner, Windows 16-core | 64 GB, 600 GB SSD | Yes. About 20–40 min cold **(unverified)** | $0.082/min, so **~$2–3.50 per build**. Needs a card on file. Plan minutes can't be used |
| Self-hosted (this PC or a dedicated box) | Your hardware | Best. The lib checkout, build directory and sccache persist, so builds are incremental | Hardware and power. GitHub postponed its proposed $0.002/min fee |

The Actions cache is 10 GB free per repo (paid beyond that since Nov 2025), too small for 7 GB of libs plus sccache, so cache only sccache or keep libs on a persistent runner. Pulling 7 GB from projects.blender.org on every run is slow and unfriendly to Blender's infrastructure; mirror the pinned lib commit. Never attach a self-hosted runner to a *public* repo, because fork PRs could run code on it.

### 4.2 Blender's own buildbot

Blender's buildbot lives in `infrastructure/blender-devops`: the buildbot master and worker code, daily, patch and release builds, signing and deployment.

- **What gets built.** `main` and active release branches are built on every commit. Nightlies are **signed on Windows and macOS** and published at builder.blender.org/download.
- **PR builds** run via `@blender-bot` and only committers can trigger them.
- **Configuration in the source tree [V]:**
  - `build_files/config/pipeline_config.yaml` holds the toolchain versions: GCC 14.2.1, Xcode 26.1.1, CUDA 12.9.1, OptiX 8.0.0, HIP 7.1/7.2, MSVC 2022 with clang-cl.
  - `build_files/buildbot/config/blender_{windows,linux,macos}.cmake` is the compile-step config.
  - `release/darwin/buildbot` and `release/windows/msix` feed the packaging steps.

### 4.3 Prior art: UPBGE

UPBGE, the BGE-restoring fork, reports `BLENDER_VERSION 503` / `UPBGE_VERSION 53` alpha on `master`. It continuously merges Blender `main` and trails it by one version **[V]**. Its `weekly-automatic-builds.yml` clones with `GIT_LFS_SKIP_SMUDGE=1`, runs `make.bat update`, configures `blender_release.cmake -DWITH_WINDOWS_RELEASE_PDB=OFF`, builds `install` on `windows-latest` and `ubuntu-24.04`, and publishes zipped `build/bin` as a GitHub pre-release. Standard runners are therefore *possible* for weekly builds, but not fast enough for per-commit CI.

### 4.4 Recommended pipeline

| Trigger | Runner | Job |
|---|---|---|
| Every push | Self-hosted Windows (this PC, as a service) | Incremental `ninja install`, then contract tests (§6.2) |
| Nightly | Self-hosted | Clean `full` build with sccache, all tests, package a ZIP and MSI, sign |
| Weekly "canary" | Self-hosted, or a 16-core larger runner | Apply the patch queue to upstream `main` and the newest release branch. Report patch failures and build errors |
| Tag `gs-v*` | Self-hosted, plus a macOS runner later | Release build, sign, notarize, publish |
| Optional | `ubuntu-24.04` standard runner | Linux compile check with ccache, to catch MSVC-only code |

### 4.5 Developer incremental workflow

From an "x64 Native Tools" prompt in the build directory: `ninja bf_editor_space_view3d` (or `bf_python`, `bf_windowmanager`, or the GameStudio module's target) compiles one library **[V target names]**, `ninja blender` relinks, and `ninja install` copies DLLs and `scripts/` (needed on Windows, or Blender won't start). **Python UI changes need no rebuild:** point `BLENDER_SYSTEM_SCRIPTS` (`--env-system-scripts`) at the source add-on folder **[V flag exists]** and hot-reload. Configure once with `make developer ninja sccache 2022`, then use `rebuild.cmd`. Turn `WITH_UNITY_BUILD` off only when editing headers. A Dev Drive and Defender exclusions reportedly help **(unverified magnitude)**.

---

## 5. Packaging and distribution

### 5.1 What CPack produces [V `packaging.cmake`]

| Platform | Mechanism | Output |
|---|---|---|
| Windows | `PACKAGE` target, CPack. NSIS settings (`/SOLID lzma`) and a WiX template (`release/windows/installer_wix`) | MSI (WiX v3 template, upgrade GUID derived from "Blender Foundation/Blender 5.2") and ZIP. Generators are chosen by the buildbot **(unverified)** |
| Windows Store | `release/windows/msix/AppxManifest.xml.template`, identity `BlenderFoundation.Blender[PACKAGETYPE]` | MSIX, signed by the Store |
| macOS | CPack `DragNDrop` plus `package_archive` | DMG and ZIP |
| Linux | `package_archive` → `build_archive.py`. RPM if `rpmbuild` exists | `tar.xz` |
| Source | `make source_archive` | Source tarball. Useful for GPL compliance |

**What Blender's own installer does:** the MSI UpgradeCode is a UUID derived from the `major.minor` install directory, so patch releases (5.2.0→5.2.2) upgrade in place while 5.1 and 5.2 install **side by side**. It adds per-version shortcuts to `blender-launcher.exe` and `.blend` associations. Config lives per version in `%APPDATA%\Blender Foundation\Blender\5.2` (`~/.config/blender/5.2` on Linux) **[V]**. There is **no built-in auto-updater**; the Microsoft Store, Steam and Snap update themselves.

### 5.2 Branding touchpoints [V]

| What | Where |
|---|---|
| Project and package name | `project(Blender)` in `CMakeLists.txt`. `build_files/cmake/packaging.cmake`: vendor, contact, install directory, registry key, `BLENDER_NAMESPACE_GUID`, shortcut names |
| Windows exe metadata and icons | `release/windows/icons/winblender.rc` (CompanyName, ProductName, OriginalFilename) plus `winblender.ico` and `winblenderfile.ico`. Manifests in `release/windows/manifest` |
| Executable names | `source/creator/CMakeLists.txt`: `blender`, `blender-launcher`. `OUTPUT_NAME Blender` on macOS |
| Installer | WiX template, banner and dialog bitmaps. MSIX manifest and assets |
| macOS identity | `release/darwin/Blender.app/Contents/Info.plist` (`org.blenderfoundation.blender`), `.icon` file, entitlements (`allow-jit`, `disable-library-validation`, …) |
| Linux | `release/freedesktop/blender.desktop`, `org.blender.Blender.metainfo.xml`, icons |
| User config directory | `GHOST_SystemPathsWin32.cc` ("Blender Foundation\Blender"), `GHOST_SystemPathsUnix.cc`, macOS equivalent. Change these so the fork doesn't share preferences or extensions with stock Blender |
| Window title and splash | `wm_window.cc`, `wm_splash_screen.cc`, `release/datafiles` splash |

**Keep `BLENDER_VERSION` unchanged** and add a separate `GAMESTUDIO_VERSION`, as UPBGE does, so extensions keyed on `blender_version_min` still resolve. The name and logo "are not part of the GNU GPL" and the logo must not be used as your own, so rename and re-icon everything user-visible and keep a "based on Blender" credit.

### 5.3 Windows code signing

| Option | Cost | Notes |
|---|---|---|
| **Azure Artifact Signing** (formerly Trusted Signing) | ~$9.99/month | Organizations: USA, Canada, EU, UK. **Individuals: USA and Canada only.** Integrates with CI and needs no token |
| OV certificate (DigiCert, Sectigo, Certum, …) | $150–300/yr | Works worldwide. Since June 2023 the key must sit on a hardware token or cloud HSM |
| EV certificate | $400+/yr | **No longer bypasses SmartScreen** (removed in 2024). Not worth it for an app |
| SignPath Foundation | Free for qualifying OSS | A GPL fork may qualify **(unverified)** |
| Microsoft Store (MSIX) | Free | The Store re-signs the package, so users see no SmartScreen warnings |

With every option except the Store, reputation builds up over releases signed with the same identity. Sign `blender.exe`, `blender-launcher.exe`, your own DLLs and the MSI with `signtool` plus an RFC 3161 timestamp.

### 5.4 macOS notarization

You need an Apple Developer Program membership ($99/yr) and a Developer ID Application certificate. The steps:

1. Run `codesign --options runtime --timestamp --entitlements` over **every** Mach-O in the bundle. Blender's `entitlements.plist` already lists `allow-jit`, `allow-unsigned-executable-memory` and `disable-library-validation` **[V]**.
2. Run `xcrun notarytool submit --wait`.
3. Run `xcrun stapler staple` on the DMG.

Plan a Mac (Apple Silicon) build host. GitHub's macOS runners cost $0.062/min and have only 7 GB of RAM.

### 5.5 Auto-update options

| Option | Platforms | Fit |
|---|---|---|
| **Velopack** | Win, macOS, Linux | C/C++ API (`Velopack.h`), delta packages. Good for a small **launcher/hub** |
| WinSparkle + Sparkle 2 | Win / macOS | Appcast feed with EdDSA-signed updates, mature. Two integrations to maintain |
| MSIX + `.appinstaller` / Store | Windows | Auto-updates built in. MSIX sandbox quirks for a Python-heavy app **(unverified)** |
| Steam | All | Free updates and distribution. Blender itself ships there |
| Custom hub | All | Side-by-side engine versions per project, like Unity Hub. Matches Blender's per-version config directories |

---

## 6. Version swapping: structuring the fork

### 6.1 Structures compared

| Structure | How it works | Pros | Cons |
|---|---|---|---|
| **A. Long-lived branch, rebased per release** | `gs/main` = upstream tag + N commits; `git rebase --onto v5.5.0 v5.2.2` | Plain git. `git range-diff` shows what changed | Rewrites history (confuses CI). Conflicts resolved commit by commit; `rerere` helps |
| **A′. Continuous merge (UPBGE)** | Merge `main` weekly | Small, frequent conflicts. Always current | Delta buried in merge history. Inherits alpha instability. Never ends |
| **B. Patch queue plus script** | `patches/series` of `git format-patch` files; `apply.py` runs `git am -3` on a clean tag; edit on a throwaway branch and re-export (ungoogled-chromium, Brave, CEF) | Explicit, reviewable delta with one purpose per patch. Easy canary against `main` | Tooling to build. Refreshing patches is a chore |
| **C. Subtree or submodule** | *Submodule*: points at a commit in your Blender fork. *Subtree*: upstream vendored into a subfolder | Submodules suit pulling the *GameStudio module* **into** Blender | A submodule still needs a modified Blender (A or B in disguise). A 20k-file subtree is a slow 3-way merge |
| **D. Out-of-tree module plus minimal hook** | GameStudio C++ in `module/`, built as `bf_gamestudio`. Upstream gets ~5–15 small patches: `add_subdirectory`, a link entry in `source/creator`, registration calls, space/DNA enums if needed | Upgrades = small patches plus compile errors in *your* code | The module still uses unstable internal APIs (BKE, DNA, WM). New editors need DNA/RNA edits in hot files |

**Hook files change every release [V]** (lines added/removed per release):

| File | 4.5→5.0 | 5.0→5.1 | 5.1→5.2 |
|---|---|---|---|
| `makesrna/intern/rna_space.cc` | +460/−353 | +421/−249 | +258/−33 |
| `makesdna/DNA_space_types.h` | +47/−15 | +568/−553 | +111/−101 |
| `makesdna/DNA_space_enums.h` | +27/−5 | +141/−137 | +140/−82 |
| `editors/space_api/spacetypes.cc` | +0/−3 | +26/−22 | +2/−7 |
| `source/creator/CMakeLists.txt` | +19/−5 | +77/−34 | +75/−70 |
| `source/creator/creator.cc` | +64/−20 | +91/−72 | +13/−1 |
| `wm_init_exit.cc` | +20/−14 | +40/−37 | +36/−10 |
| `scripts/startup/bl_ui/space_view3d.py` | +157/−124 | +147/−84 | +188/−121 |
| `GHOST_SystemPathsWin32.cc`, `packaging.cmake`, `winblender.rc` | small | 0 | small or 0 |

Branding files are stable; editor-core hooks are not, so keep hunks there tiny. Put UI in Python (sidebar panels, an app template via `bl_app_template`/`bl_app_override` for startup file, splash and UI trimming, a bundled extension). Use C++ only for what Python cannot do: a new editor type, viewport embedding, fast data paths.

### 6.2 Detecting what broke

| Tier | Check | Tooling |
|---|---|---|
| 1. Patch | Does the patch queue apply? | `git am -3`. Fail fast and list the failing patches |
| 2. Build | Windows `full ninja`, plus a Linux compile | sccache/ccache. `-DWITH_STRICT_BUILD_OPTIONS=ON` so missing dependencies fail |
| 3. Upstream tests | Relevant upstream tests via ctest: `bl_pyapi_*`, `bl_load_addons`, `bl_keymap_validate`, `bl_blendfile_*`, `io_gltf_*` | `make test`. Needs about 0.8 GB of LFS test files |
| 4. Python API contract | (a) Dump the RNA API with `tests/python/rna_info_dump.py` ("used for generating API diffs between releases") on the old and new build, diff the dumps, and intersect with an AST scan of the identifiers the add-on uses. (b) Headless tests: `blender -b --factory-startup --python-exit-code 1 --python tests/contract/run.py` exercising every GameStudio operator, PropertyGroup and override flag, plus the glTF export round trip | Your own tests. API change log at docs.blender.org |
| 5. Data compatibility | Open fixture `.blend` files saved by the *previous* GameStudio version, check component data, re-export, and compare the manifest hash with the golden one | Golden files in the repo |
| 6. UI smoke | Start the GUI, then draw each GameStudio panel and editor, run the Play/Stop operators via `context.temp_override`, take screenshots and compare them with a tolerance. Fail on any Python traceback | `tests/python/ui_screenshot_tests.py` pattern. `--enable-event-simulate` and `ui_simulate/run.py`. `WITH_UI_TESTS` is still experimental and headless only on Linux/Wayland [V] |

### 6.3 Effort per LTS→LTS upgrade (e.g. 5.2 → 5.5) (unverified estimates)

Fixed costs apply under every structure:

- Lib download and clean build: about 2 machine-hours.
- Test triage: 1–3 days.
- Branding and packaging check: 0.5 day.
- Release: 0.5 day.

| Structure | Small delta (≤15 core patches, ≤1.5k core lines) | Large delta (100+ core files, 20k+ lines) |
|---|---|---|
| A. Rebased branch | 3–6 dev-days | 4–10 weeks |
| A′. Continuous merge | 0.5–2 days/month, ongoing (~10–25 days/yr) | 2–4 days/month, ongoing |
| B. Patch queue | 3–5 dev-days, plus 3–5 days of one-time tooling | 4–8 weeks |
| C. Subtree/submodule | As A, plus 1–2 days of overhead | Worst case |
| **D. Out-of-tree + minimal hook (+B for the hooks)** | **1–2 days for hooks + 2–8 days of module API fixes** | Not applicable by design |
| Extension only, no fork (baseline) | 1–3 days of Python API fixes | — |

The 5.x→6.x hop in 2028 will cost more under every structure, because the x.0 release is where Blender allows breaking changes. Budget about 2× **(unverified)**.

---

## 7. Recommendations for the GameStudio fork

1. **Keep the fork thin.** Everything that Python, an extension or an app template can do stays out of the C++ delta. That covers panels, operators, the startup file, splash, keymaps and the glTF exporter hooks. Fork only for what report 04 showed Python cannot do, such as a new editor type. Each core patch must justify itself, and the target is **≤ 15 patches**.
2. **Repo layout: structure D with a B-style patch queue.**
   ```
   gamestudio-editor/
     upstream.lock        # tag v5.2.2, commit d13f752e, lib/windows_x64 60d6e96b
     patches/series       # ordered, grouped: build/, branding/, hooks/
     module/              # bf_gamestudio C++ (blender_add_lib), linked via one hook
     extension/           # bundled Python extension (UI, operators)
     branding/            # .ico/.rc, Info.plist, WiX, desktop files, splash
     tools/apply.py, export.py, upgrade.py, canary.py
     tests/contract/, tests/ui/, tests/fixtures/*.blend
   ```
   - `apply.py` makes a worktree from a local upstream mirror at the locked tag, runs `git am -3`, links `module/`, and overlays `branding/`.
   - Developers commit on that worktree's branch, and `export.py` turns the commits back into patches with `git format-patch`.
   - Enable `git rerere`.
3. **Release identity.** Use `GameStudio Editor 0.x (Blender 5.2.2)`, keep `BLENDER_VERSION` as it is, give the app its own config directory name, its own WiX upgrade GUID namespace and its own bundle ID, and tag releases `gs-v0.x.y`.
4. **Upgrade plan.**
   - Stay on 5.2 LTS and take its patch releases. Each one is a lib-pin and tag bump, about half a day.
   - Start the canary now: weekly against `blender-v5.3-release`, monthly against `main` (5.4 alpha), and weekly against 5.4/5.5 once their betas start (Feb and Jun 2027, proposed).
   - Move to **5.5 LTS** in Nov 2027–Jan 2028, while 5.2 is still supported until Jul 2028.
   - Skip 5.3 and 5.4 for releases.
5. **CI.** Register this PC as a self-hosted runner on a *private* repo, persist `lib/` and the build directory, and set `SCCACHE_DIR=F:\sccache`, `SCCACHE_CACHE_SIZE=50G`. Add an `ubuntu-24.04` compile check. Add a 16-core larger runner (~$3 per build) only if the PC is often busy.
6. **Hosting.** A GitHub repo carrying Blender history runs into LFS quotas (10 GiB storage and bandwidth on Free/Pro). The patch-queue layout avoids this, because your repo never contains Blender history. If you do push a full fork, rely on `make_update.py`'s **`lfs-fallback` remote** to projects.blender.org [V] rather than uploading upstream LFS objects (e.g. `lfs.allowincompletepush`; **verify with your git-lfs version**).
7. **Distribution.** Ship a ZIP and a rebranded MSI, side by side per minor version. Sign with Azure Artifact Signing if you qualify, otherwise an OV certificate on a cloud HSM. Add a small "GameStudio Hub" launcher, updated through Velopack, that installs editor versions side by side and pins one per project. Do macOS once a Mac build host exists. Publish `make source_archive` output with every release to meet the GPL.
8. **GPU kernels.** Ship `full` builds (no CUDA, HIP or oneAPI kernels) until users need GPU Cycles. If the fork never touches `intern/cycles`, reusing kernel binaries from the official build of the same version could save hours **(unverified; test carefully)**.

### 7.1 Exact toolchain to install on this machine

| # | Install / action | Why |
|---|---|---|
| 1 | **Update VS 2022 Community to the latest 17.14.x** (17.14.14 is installed, which is the exact minimum; official builds use 17.14.21). Then **Modify → "Desktop development with C++"**, which adds MSVC v143 x64/x86, the Windows 11 SDK (10.0.26100) and C++ CMake tools for Windows. Command-line equivalent (run `setup.exe update --installPath …` first; **verify flags**): `"C:\Program Files (x86)\Microsoft Visual Studio\Installer\setup.exe" modify --installPath "C:\Program Files\Microsoft Visual Studio\2022\Community" --add Microsoft.VisualStudio.Workload.NativeDesktop --includeRecommended` | Required compiler. Check with `cl` in an x64 Native Tools prompt that it reports **≥ 19.44.35216** |
| 2 | **Leave the VS 2019 and VS 2017 installs alone, but always pass `2022`** to `make.bat` | Auto-detection would pick VS 2019 16.5 and fail |
| 3 | **Ninja**: `winget install Ninja-build.Ninja` (also bundled with VS CMake tools) | `make ... ninja`. Incremental builds about 3× faster than MSBuild |
| 4 | **sccache**: `winget install Mozilla.sccache` (**verify package ID**) or the release binary from GitHub. Set `SCCACHE_DIR=F:\sccache` and `SCCACHE_CACHE_SIZE=50G` | `make ... sccache`, Ninja only |
| 5 | Keep **CMake 4.2**, **git 2.55** and **git-lfs 3.7**. Run `git lfs install` once | ≥ 3.21 is required. CMake 4.2 is also fine for VS 2026 |
| 6 | No Python install needed for the build | The libs ship Python 3.13. System Python 3.12 is only used for repo tooling |
| 7 | Optional: **LLVM 20.1.x** (clang-cl) | To match the official clang-cl builds (inference) |
| 8 | Optional for packaging: **WiX Toolset 3.14** (MSI) and **NSIS 3** (`.exe` installer) | CPack Windows generators **(versions unverified)** |
| 9 | Optional: a Windows **Dev Drive** on F: for `build_*` and `sccache`, plus Defender exclusions | Faster I/O **(unverified magnitude)** |

**First build** (later, from `cmd.exe` in `F:\Development-games\blender-upstream`; not run here):

```
make update                              :: libs (~7 GB, +7 GB LFS store) and LFS assets
make full ninja sccache 2022             :: -> F:\Development-games\build_windows_Full_x64_vc17_Release
make developer ninja sccache 2022 builddir build_dev   :: day-to-day build with asserts and gtests
```

---

## 8. Sources

**Local, verified [V]:** `F:\Development-games\blender-upstream` at `v5.2.2`: `make.bat`, `build_files/windows/*.cmd`, `build_files/cmake/platform/{platform_win32,platform_apple_xcode}.cmake`, `build_files/cmake/{packaging,macros}.cmake`, `CMakeLists.txt`, `build_files/cmake/config/*.cmake`, `build_files/buildbot/*`, `build_files/config/pipeline_config.yaml`, `build_files/utils/make_update.py`, `.gitmodules`, `release/{windows,darwin,freedesktop}/*`, `source/creator/CMakeLists.txt`, `tests/python/{rna_info_dump.py,ui_screenshot_tests.py,ui_simulate/run.py}`, `BKE_blender_version.h`.

**Measured [V]:** churn via `git diff` between tags `v4.2.0`, `v4.5.0`, `v5.0.0`, `v5.1.0` and `v5.2.0`, fetched at depth 1 into a scratch repo. Lib sizes from the LFS pointers of `lib-windows_x64` at `60d6e96b`. Branches and tags via `git ls-remote`. Repo sizes via the projects.blender.org API. Installed VS versions via `vswhere`.

**Web:**
- Release cycle: https://developer.blender.org/docs/handbook/release_process/release_cycle/
- Release notes index (5.3 beta, 5.4 alpha): https://developer.blender.org/docs/release_notes/ and https://developer.blender.org/docs/release_notes/5.3/
- Python API release notes: https://developer.blender.org/docs/release_notes/4.3/python_api/ · /4.4/python_api/ · /4.5/python_api/ · /5.0/python_api/ · /5.1/python_api/ · /5.2/python_api/
- LTS page: https://www.blender.org/download/lts/
- Releases list: https://www.blender.org/download/releases/
- Release-cycle proposal (Feb 6, 2026): https://devtalk.blender.org/t/evolutions-to-blender-release-cycle/44253
- Building Blender: https://developer.blender.org/docs/handbook/building_blender/ · /windows/ · /linux/ · /mac/ · /options/
- Unity builds: https://developer.blender.org/docs/handbook/tooling/unity_builds/
- Windows dependency builder: https://developer.blender.org/docs/handbook/release_process/build/windows/
- Buildbot: https://developer.blender.org/docs/handbook/tooling/buildbot/ and https://projects.blender.org/infrastructure/blender-devops
- Build hardware thread (build times, RAM): https://devtalk.blender.org/t/what-are-the-specifications-that-you-recommend-on-a-computer-to-compile-blender/38116
- Intel macOS removal: https://devtalk.blender.org/t/deprecation-and-removal-of-macos-intel-builds-in-blender-5-0/38835
- Logo and trademark: https://www.blender.org/about/logo/
- UPBGE weekly CI: https://github.com/UPBGE/upbge/blob/master/.github/workflows/weekly-automatic-builds.yml
- GitHub runners: https://docs.github.com/en/actions/reference/runners/github-hosted-runners · https://docs.github.com/en/actions/reference/runners/larger-runners · https://docs.github.com/en/actions/reference/limits · https://docs.github.com/en/billing/reference/actions-runner-pricing
- GitHub Actions cache above 10 GB: https://github.blog/changelog/2025-11-20-github-actions-cache-size-can-now-exceed-10-gb-per-repository/
- Self-hosted runner fee postponed: https://www.theregister.com/2025/12/17/github_charge_dev_own_hardware/
- Git LFS billing: https://docs.github.com/en/billing/concepts/product-billing/git-lfs
- Windows code signing options: https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options
- Velopack for C++: https://docs.velopack.io/getting-started/cpp
- WinSparkle: https://github.com/vslavik/winsparkle/releases
- Notarization troubleshooting: https://developer.apple.com/tutorials/data/documentation/security/resolving-common-notarization-issues.md
- Patch-queue prior art: Qt WebEngine rebase https://wiki.qt.io/QtWebEngine/Rebase_on_New_Chromium · Chromium OpenXR src_overrides https://chromium.googlesource.com/chromium/src/+/da6f458c81d41f786bd1733668d49586ddd6c919/third_party/openxr/src_overrides/README.md · ungoogled-chromium design https://gogs.librecmc.org/RISCI_ATOM/ungoogled-chromium/src/master/docs/design.md
