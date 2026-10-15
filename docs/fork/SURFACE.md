# GameStudio surface protocol (v1): frames and input for the docked Game View

This document is MIT-licensed and engine-agnostic. It defines how a game running in its own process shows its frames inside an editor area and receives input from it. The editor is GameStudio Editor (Blender-based); the producer today is the Electron **runtime host** (`@gamestudio/host`). A future native runtime (Bevy, Godot, a C++ engine) would be another producer of the same protocol. Background: [ARCHITECTURE §4](ARCHITECTURE.md) and [research/09](../research/09-embedding-game-view.md).

| Part | Path | Language | License |
|---|---|---|---|
| Runtime host (producer) | `runtime/packages/host/` | JavaScript (Electron 44) | MIT |
| Surface consumer library | `editor/native/gameview/` (`gs_gameview`) | C++20 | GPL-3.0-or-later |
| This specification | `docs/fork/SURFACE.md` | | MIT |

The live link ([SPEC.md §5](SPEC.md)) carries scene edits and game state. This protocol carries **pixels, input and view control**. The two are independent: a game in the host still connects to the dev server's hub as usual.

---

## 1. Model

```
 Editor (consumer, listens)                                Runtime host (producer, connects)
 ┌──────────────────────────────────┐   TCP 127.0.0.1    ┌─────────────────────────────────────────┐
 │ Game View region                 │   NDJSON           │ hidden offscreen BrowserWindow          │
 │  worker: DuplicateHandle ─ open ─┼◄── surface.frame ──┤  paint → shared D3D11 texture (ntHandle) │
 │  copy → staging → map → memcpy   │─── surface.release►│  texture.release()                      │
 │  main: newest frame → GPU upload │─── input.* ───────►│ preload → synthetic DOM events          │
 │                                  │◄── cursor.grab ────┤  requestPointerLock() interception      │
 └──────────────────────────────────┘                    └─────────────────────────────────────────┘
```

- **Docked mode** (`--mode docked`): the host renders the game offscreen into GPU-shared textures and announces each one. The editor copies the pixels and draws them in an editor region, so the editor's own menus and popups draw over the game. Windows only in v1.
- **Window mode** (`--mode window`): the host shows an ordinary window. No frames flow; the link only ties the host's lifetime to the editor and reports state. This is also the detached view and the desktop shipping shell.

---

## 2. Transport

- **TCP on loopback.** The editor binds `127.0.0.1` on an ephemeral port and accepts exactly one connection per launch. The host connects to it.
- **NDJSON:** one UTF-8 JSON object per line, `\n`-terminated, like the live link. Lines are at most 1 MiB.
- **Envelope:** every message is an object with a string `t` (its type). Requests that expect an answer carry `id`; answers carry `re`.
- **Ordering:** TCP keeps each direction in order. Several statements below depend on that.
- **Unknown message types and unknown fields are ignored**, never treated as errors (§8).

---

## 3. Lifecycle

### 3.1 Launch

The editor starts the host itself, so it owns the host's process handle (needed for handle passing, §6.1):

```
electron.exe <host dir> -- --surface 127.0.0.1:<port> --token <hex> --url <game url> --mode docked
             [--width 1280 --height 720 --dpr 1 --fps 60 --max-in-flight 3 --adapter-luid <16 hex>]
```

A packaged host has no `<host dir>`: `game.exe -- --surface …`.

| Option | Meaning | Default |
|---|---|---|
| `--surface` | The editor's listener: `127.0.0.1:<port>` (loopback only) | none (no link) |
| `--token` | 32–128 hex digits, random per launch (§7) | required with `--surface` |
| `--url` | The game: a loopback `http(s)` URL or a `file:` URL (§7) | required |
| `--mode` | `docked` or `window` | `window` |
| `--width`, `--height` | Initial texture size in pixels | 1280 × 720 |
| `--dpr` | `window.devicePixelRatio` the page sees | 1 |
| `--fps` | Offscreen frame rate, 1–240 | 60 |
| `--max-in-flight` | Frames the editor may hold before the host revokes the oldest (§5) | 3 |
| `--adapter-luid` | Pin Chromium to this GPU, `HighPart << 32 \| LowPart` as 16 hex digits (§6.1) | Chromium's choice |

**The `--` is required.** Electron refuses to start (exit code −1, no output) when a URL-like argument is followed by any other argument, unless `--` comes first. This is its CVE-2018-1000006 guard. `--` also stops Chromium from reading the host's options as Chromium switches. Chromium switches for the host go before `--`.

**Environment:** the launcher must remove `ELECTRON_RUN_AS_NODE` from the child's environment. Editors started from VS Code or another Electron app inherit it, and it turns `electron.exe` into plain Node.

### 3.2 Handshake

1. The host connects and sends `hello`. **It sends nothing else before the editor's answer.**
2. The editor checks the token, protocol and capabilities, then answers `welcome`, or `error` and closes.
3. The editor follows the `welcome` with a `surface.configure` carrying the real size, then starts clock sync (§4.4).
4. The host then reports `surface.state` and, in docked mode, starts sending frames.

```json
{"t":"hello","role":"host","protocol":1,"token":"9f…","pid":1234,"client":"gamestudio-host 0.1.0 (electron 44.4.4)",
 "mode":"docked","handles":["d3d11-nt"],"formats":["bgra8"],"maxInFlight":3,
 "adapterLuid":"00000000000113b3","gpu":{"vendorId":4318,"deviceId":10118,"description":"NVIDIA GeForce RTX 4070"}}
{"t":"welcome","protocol":1}
{"t":"surface.configure","width":1280,"height":720,"dpr":1}
```

| `hello` field | Meaning |
|---|---|
| `role` | Always `"host"` |
| `protocol` | 1 (§8) |
| `token` | The `--token` value |
| `pid` | The host's process id (informational; the editor already holds the handle) |
| `client` | Free-form producer description |
| `mode` | `"docked"` or `"window"` |
| `handles` | Handle kinds the producer can send (§6). v1: `["d3d11-nt"]` on Windows |
| `formats` | Pixel formats it can send. v1: `["bgra8"]` |
| `maxInFlight` | Its revoke threshold (§5) |
| `adapterLuid` | Optional. The GPU the producer is pinned to. When present, the editor **must** open frames on that adapter |
| `gpu` | Optional. The producer's active GPU (`vendorId`, `deviceId`, …), so an editor can pick a matching adapter when no LUID is given |

The Electron host also ends the page's user agent with `GameStudioHost/<version> (pid <pid>)`, the same pid as in `hello`. An editor uses it to find the game it docked among the live link's game peers (a browser tab on the same dev server is another peer).

Error codes in `error {code, msg}`: `auth` (bad token, or the first message was not a hello), `protocol` (version, role, malformed line or no usable handle kind), `gpu` (the editor cannot open frames), `configure` (an invalid `surface.configure`).

Timeouts (editor side): the host has `connect_timeout` (30 s by default) to connect and complete the handshake, and 10 s from connecting to send its hello. The host waits 5 s for `welcome`.

### 3.3 Lifetime

- **The host exits when the link closes**, whatever the reason. So an editor closes its Game View by closing the socket. Then, after a grace period of about 2 s, it closes the job (below).
  - The host stops producing frames, releases every texture, destroys its windows and calls `app.exit(0)`. Its JavaScript is finished within about 15 ms.
  - The OS process itself took about 0.2 s to go away on a quiet machine. With the CPU saturated by a parallel C++ build, it took 3–15 s, and a window-less Electron app was slow too. That is Electron/OS teardown, and the reason for the job.
  - The editor never waits for that on its UI thread. GameStudio Editor closes the link, hands the process handle to a background thread that waits up to 2 s, and then closes the job; Stop and Restart return in about 20 ms.
- **When the host exits, crashes or fails** (the editor's state becomes Closed or Failed), the editor terminates the job at once, so no Chromium child process (GPU, renderer, network) outlives the host, and a host that hangs after a failed handshake does not linger.
- **Job object (Windows).** The editor starts the host suspended, assigns it to a job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, then resumes it. Every Chromium child process (GPU, renderer, utility) inherits the job, and nested jobs are fine for Chromium's sandbox. If the editor crashes, the kernel closes the job handle and the whole tree dies. `gs_gameview::launch_host()` does exactly this.
- **The game page crashes:** the host stays up and reports `surface.state {state:"crashed", reason, exitCode}`. The editor shows "Game stopped (crashed) – Restart". Restart means closing the link and launching again.
- **The host ends the link on purpose** (the user closed the detached game window, or the page closed itself): it sends `bye {reason}` first, then closes the socket. The editor shows "Game stopped".
- **The host process dies** (a crash, or it was killed): the editor sees the socket close without a `bye`, or the process handle signal, and shows "Game stopped (crashed)" with the exit code when it has one. A killed Electron process can take several seconds to signal (GPU teardown), so the editor waits at most 1 s for the code after a link closes without `bye` (3 s after a `bye`). Older hosts send no `bye`; for them an exit code of 0 still means a clean exit.
- macOS and Linux have no job objects. The future backends use the closed socket, plus `PR_SET_PDEATHSIG` on Linux and a parent-death watcher (kqueue `NOTE_EXIT`) on macOS.

```
editor:  Created ─start()→ Waiting ─hello ok→ Connected ─socket closed / host exit 0→ Closed
                              │                    └─ error / host crash ────────────→ Failed
                              └─ timeout / host exited before hello ─────────────────→ Failed
host page (surface.state):  loading → running ⇄ loading (reloads) ; → crashed | error
```

---

## 4. Message catalogue

### 4.1 Frames (docked mode)

| Direction | `t` | Payload | Meaning |
|---|---|---|---|
| host → editor | `surface.frame` | `{ frameId, handle, width, height, format, ts }` | A finished frame is ready. `frameId` increases by 1 per frame sent, starting at 1. `handle` is the OS handle value **in the host process**, as a decimal string (it is a 64-bit value; §6). `width`/`height` are the texture's pixels. `format` is `"bgra8"` in v1. `ts` is the host's monotonic clock in ms when the frame was handed over (§4.4). |
| editor → host | `surface.release` | `{ frameId }` | The editor is done with the frame's texture. Send it as soon as the GPU copy has finished, or immediately for frames you skip. Unknown ids are ignored. |
| host → editor | `surface.revoke` | `{ frameIds: [..] }` | The host has already released these frames (§5). Do not open them, and do not release them. A revoke is sent **before** the `surface.frame` that caused it. |

Pixels are 8-bit, **sRGB-encoded**, straight alpha (the page background is normally opaque), with rows top-to-bottom. `bgra8` means the byte order is B, G, R, A. An OpenGL or Vulkan consumer uploads the data as RGBA8 and swizzles `bgra`.

### 4.2 View control

| Direction | `t` | Payload | Meaning |
|---|---|---|---|
| editor → host | `surface.configure` | `{ width, height, dpr, fps? }` | Resize the game. `width`×`height` are texture pixels (16–16384), and `dpr` is the device pixel ratio the page sees (0.25–8). The CSS viewport becomes `width/dpr` × `height/dpr`. Frames of the new size follow within a frame or two (measured: about 85 ms to the first 1080p frame). The host may clamp to the screen size (Chromium limits window sizes); frames always state their real size. |
| editor → host | `pause` | `{ paused: bool }` | `true` pauses a docked game (for example while its view is hidden, or on the user's request), and `false` resumes it. Paused, the host sends no frames, and the page behaves like a background tab: `document.visibilityState` is `"hidden"` (with a `visibilitychange` event), and `requestAnimationFrame` callbacks are held until the resume, so a game driven by animation frames stops simulating. Timers, network and the live link keep running, so the editor can still query and edit a paused game. A page that reloads while paused starts paused. |
| editor → host | `detach` | `{}` | Move the game into a normal window. The host revokes all frames in flight, opens the window (the page reloads, and the live link restores the scene) and reports `mode:"window"`. The link stays up. |
| editor → host | `devtools` | `{ open?: bool }` | Open Chromium DevTools for the game page in a window of their own, or focus them if already open (`open` defaults to `true`); `open:false` closes them. Works for the offscreen page and for the detached window. Console output and breakpoints work as in a browser, including in TypeScript through the dev server's source maps. A producer without DevTools ignores it. |
| host → editor | `surface.state` | `{ state, mode, paused, devtools, msg?, reason?, exitCode? }` | Page state: `loading`, `running`, `crashed` or `error`. `mode`: `docked` or `window`. `devtools`: DevTools are open for the page. Sent after the handshake and on every change. |
| host → editor | `error` | `{ code, msg }` | A non-fatal complaint, e.g. `configure` |
| host → editor | `bye` | `{ reason }` | The host is about to close the link on purpose; `reason` is for people (e.g. "the game window was closed"). Without it, a closed link means the host crashed (§3.3). Never sent before the `welcome`. |

### 4.3 Input

Input is captured by the editor (click to capture; a release chord; losing window focus or clicking outside the view releases). GameStudio Editor releases on Shift+Esc or Shift+F1, and on Esc alone unless the game holds pointer lock: then Esc belongs to the game, which in a browser would end pointer lock with it. The editor translates its own events into **engine-level DOM vocabulary**: no editor key enums or structures cross the link (§9). The host turns them into synthetic DOM events in the page's main world, so the game's ordinary listeners see them.

| Direction | `t` | Payload |
|---|---|---|
| editor → host | `input.key` | `{ type: "down"\|"up", code, key, repeat, modifiers }` |
| editor → host | `input.pointer` | `{ type: "move"\|"down"\|"up"\|"wheel", x, y, button, buttons, dx?, dy?, deltaX?, deltaY?, modifiers }` |
| editor → host | `focus` | `{ focused: bool }` |
| host → editor | `cursor.grab` | `{ grab: bool }` |

- **`code`** is the layout-independent DOM `KeyboardEvent.code` (`"KeyW"`, `"Space"`, `"ShiftLeft"`, `"ArrowUp"`). **`key`** is the DOM `KeyboardEvent.key` for the user's layout (`"w"`, `" "`, `"Shift"`). Games should read `code` for controls. The host also fills in the legacy `keyCode`/`which`, and sends `keypress` for printable keys.
- **`modifiers`** lists the modifiers held after the event: any of `"Shift"`, `"Control"`, `"Alt"`, `"Meta"`.
- **`x`, `y`** are game pixels: texture pixels with the origin at the top left. The host divides them by `devicePixelRatio` to get CSS `clientX`/`clientY`. `dx`/`dy` are relative motion in game pixels; they become `movementX`/`movementY`, and while the cursor is grabbed they are the only useful pointer data.
- **`button`** is the DOM button that changed: 0 left, 1 middle, 2 right, 3 back, 4 forward. **`buttons`** is the DOM bitmask held after the event: 1 left, 2 right, 4 middle.
- **`deltaX`/`deltaY`** are in CSS pixels, with +Y meaning scroll down. Send about 100 per wheel notch.
- **Event mapping:** `move` → `pointermove` + `mousemove`. `down` → focus the target, then `pointerdown` + `mousedown`. `up` → `pointerup` + `mouseup` + `click` (left) or `contextmenu` (right). `wheel` → `wheel`. Pointer events go to `document.elementFromPoint(x, y)` (usually the canvas), or to the locked element. Key events go to the focused element, falling back to the canvas, and bubble to `window`.
- **`focus`:** `true` dispatches `focus` on `window` and focuses the canvas. `false` ends any emulated pointer lock and dispatches `blur`; the GameStudio engine clears held keys on `blur`. The editor should still send `up` for every key it reported `down`.
- **Pointer lock** does not work in offscreen rendering, so the host emulates it:
  - `Element.requestPointerLock()` succeeds at once, `document.pointerLockElement` reports the element, `pointerlockchange` fires, and the host sends `cursor.grab {grab:true}`.
  - `exitPointerLock()`, or `focus {focused:false}`, ends it, fires `pointerlockchange` and sends `cursor.grab {grab:false}`.
  - On `grab:true` the editor hides and wraps its own cursor (Blender: `WM_cursor_grab_enable`) and sends relative motion in `dx`/`dy`. Motion is continuous across the start of a grab; when a grab ends the cursor jumps back, and no motion is reported across that jump.
- **Text input / IME** is not in v1. Synthetic key events do not insert text into `<input>` fields.
- **Gamepads** are read by the producer itself, not forwarded. The planned route is SDL3 in the host, gated on focus.

### 4.4 Clock sync

| Direction | `t` | Payload |
|---|---|---|
| editor → host | `ping` | `{ id, ts }`. `ts` is the editor's clock (informational) |
| host → editor | `pong` | `{ re, ts }`. `ts` is the host's clock: the same clock as `surface.frame.ts` |

The editor sends a few pings right after the handshake (50 ms apart), then one per second. From the sample with the smallest round trip among the last 16, it computes `offset = host_ts − (sent + received)/2`, NTP-style. A frame's production time in editor time is then `ts − offset`. Over loopback the round trip is well under 1 ms. `ts` marks when Chromium handed the frame to the host; Chromium's own render-to-paint latency is not included.

---

## 5. Flow control

- **The producer holds every frame it announces** until `surface.release`. Electron's shared-texture pool is small (about 10), and Chromium stops painting when it runs out.
- **Bounded frames in flight.** The host holds at most `maxInFlight` (default 3) unreleased frames. When a new frame would exceed that, it releases the **oldest** itself, sends `surface.revoke` for it, then sends the new frame. A slow editor therefore drops frames; it never starves the pool or stalls the game.
- **Editor rules** (what `gs_gameview` does):
  1. Read every complete line available. Of the frames in that batch, open only the newest; send `surface.release` for the others without opening them.
  2. Open the handle, start the GPU copy, and send `surface.release` **as soon as the copy has finished on the GPU**, before the CPU readback.
  3. Keep up to N copies in flight (a staging ring, N = 3). When the ring is full, keep only the newest pending frame.
  4. A revoked frame that has not been opened is dropped. A revoked frame whose copy is already running finishes, but may show a torn image, and is not released.
  5. Present the newest complete frame. Never queue frames for display.
- **Revoke race.** A frame can be revoked while the editor is duplicating its handle. This only happens if the editor stalled for `maxInFlight` frame intervals (50 ms at 60 fps). The host closes the handle on release, so the value might even name another object by then. The editor therefore validates every opened texture against the message (size, 8-bit RGBA family, one mip, no MSAA) and drops mismatches.
- **No keyed mutex or fence.** Electron's `bgra` shared textures have no keyed mutex. The contract is that Chromium has finished writing when `paint` fires, and the producer must not reuse the texture before `surface.release` or its own revoke. A future native producer should use a keyed mutex or a shared fence and say so in `hello` (§8).

---

## 6. Handle passing per OS

### 6.1 Windows (v1): `d3d11-nt`

- **What is passed:** the numeric value of a D3D11 **NT handle**, local to the producer process, to a 2D BGRA8 texture. Electron provides it as `OffscreenSharedTexture.textureInfo.handle.ntHandle`, an 8-byte little-endian buffer.
- **How the editor gets it:**
  1. `DuplicateHandle(hostProcess, (HANDLE)value, GetCurrentProcess(), &local, 0, FALSE, DUPLICATE_SAME_ACCESS)`. `hostProcess` is the handle the editor got from `CreateProcess`, which needs `PROCESS_DUP_HANDLE`. **Never** pass `DUPLICATE_CLOSE_SOURCE`, and never duplicate from a process the editor did not start.
  2. `ID3D11Device1::OpenSharedResource1(local, IID_ID3D11Texture2D, …)` on a D3D11 device **on the same adapter** as the producer.
  3. Close `local` at once (the opened resource keeps its own reference), then `CopyResource` into a staging texture. Signal an `ID3D11Fence` and wait on its event, then `Map`.
- **Adapter matching.** `OpenSharedResource1` fails on a different adapter, and Vulkan import needs the same device as well. Electron cannot report its adapter's LUID, so the **editor chooses**:
  1. It passes `--adapter-luid` (normally the adapter driving its own window or the primary monitor).
  2. The host turns it into Chromium's `--use-adapter-luid=<high>,<low>` and echoes it in `hello.adapterLuid`.
  3. The consumer opens frames on the adapter named in the hello. Failing that, it uses the adapter matching `hello.gpu` vendor/device ids, then its own fallback, then the adapter driving the primary monitor.
- **Zero-copy consumers (v1.5)** import the same handle into the editor's renderer directly: Vulkan `VK_EXTERNAL_MEMORY_HANDLE_TYPE_D3D11_TEXTURE_BIT`, or OpenGL `WGL_NV_DX_interop2`. They need no protocol change.

### 6.2 macOS (future): `iosurface-mach`

- Electron provides `handle.ioSurface`, an `IOSurfaceRef` that is local to the process.
- The host would send `IOSurfaceCreateMachPort(surface)` over a Mach channel. The NDJSON link cannot carry Mach ports, so the two sides set up a bootstrap-registered service or an XPC connection whose name is passed in `welcome`.
- The editor calls `IOSurfaceLookupFromMachPort`, then `newTextureWithDescriptor:iosurface:plane:`. Blender already wraps foreign `MTLTexture`s.
- `surface.frame` would carry `{ handle: "<port index>" }`, or a per-frame message on the Mach channel.

### 6.3 Linux (future): `dmabuf`

- Electron provides `handle.nativePixmap`: `planes[] { fd, stride, offset, size }` plus a DRM `modifier`.
- File descriptors cannot travel over TCP. The link moves to a **Unix domain socket**, and each `surface.frame` is sent with its plane fds as `SCM_RIGHTS` ancillary data. The JSON adds `planes: [{stride, offset, size}]`, `modifier` and `fourcc`.
- The editor imports with `VK_EXT_external_memory_dma_buf` + `VK_EXT_image_drm_format_modifier`, or with `EGL_EXT_image_dma_buf_import`, and closes the fds after the copy.
- Electron 44's shared-texture OSR on Linux is still fragile (electron#52618 on the NVIDIA driver). A CPU fallback (`shm`, a shared-memory ring) is the likely first Linux backend.

`hello.handles` advertises the kinds a producer can send. The editor refuses a docked host that offers none it can open.

---

## 7. Security

- **Loopback only.** The editor binds `127.0.0.1`, with `SO_EXCLUSIVEADDRUSE` on Windows. The host refuses `--surface` values that are not loopback.
- **Token.** Each launch gets a fresh 128-bit random token from the OS CSPRNG. It is passed on the host's command line and must be the first thing the host says. The editor compares it in constant time, rejects a connection without a valid hello within 10 s, and stops listening once the real host is in. The command line is readable by other processes of the same user; that is the same trust boundary as the live link's `session.json` (SPEC §5.1).
- **Handles only flow one way, from a known process.** The editor duplicates handles only out of the process it launched, never one named by a message. It never closes handles in the host, and it validates each opened texture (§5). The host accepts no handles at all.
- **Origin.** `--url` must be a loopback `http(s)` URL or a `file:` URL. The page may navigate only within its own origin (Vite full reloads are fine), and `window.open` is denied. The page runs with `sandbox: true`, `contextIsolation: true` and no Node integration. The preload exposes exactly one function to the page (`__gamestudioHost.cursorGrab`), and that function can only request a cursor grab, which the editor is free to ignore.
- **Input is validated** in the host's main process (types, ranges, string lengths, known modifiers) before it reaches the page. Unknown message types are dropped, so there is no generic "eval" channel. `devtools` does give whoever sits at the DevTools window full control of the page, so only the authenticated editor may ask for it, and it opens on the local desktop only.

---

## 8. Versioning

- `hello.protocol` and `welcome.protocol` carry the version, **1** for this document. Each side refuses a different version with `error {code:"protocol"}` and closes.
- **Additive changes** keep the version: new optional fields, new message types, new `handles` kinds or `formats` that are announced in `hello`. Receivers ignore what they do not know.
- **Breaking changes** bump the version: changed meanings, a field that becomes required, a new transport.
- Capabilities are negotiated by the lists in `hello`, not by version numbers. A future producer with a keyed mutex would add, for example, `"sync":["keyed-mutex"]`, and only editors that understand it would use it.

---

## 9. The GPL boundary

The editor is GPL-3.0-or-later. The runtime, the host and this protocol are MIT ([ARCHITECTURE §9](ARCHITECTURE.md#9-licensing-and-naming)). The FSF's test looks at the mechanism and at the **semantics** of the communication. Sharing complex data structures through shared memory counts as close to linking. So this protocol is held to these rules:

- **Payloads are flat images** (a GPU texture of 8-bit pixels with the documented header: size, format, frame id and timestamp), **plus the documented control messages** above. Nothing else crosses.
- **No Blender structures cross.** That means no DNA or RNA data, no Blender IDs, enums, keymaps, event structs, pointers or memory layouts. The editor converts its input into DOM vocabulary (`code`, `key`, buttons, pixels) before sending.
- **The producer knows nothing about Blender.** Any editor could drive it, and the editor works without it (the Game View simply shows nothing).
- Shared-texture frames are the only part with a medium (rather than low) legal risk (`research/08` §3.3). Keep them flat images, and have counsel review before a commercial release.

---

## 10. Reference implementations and measurements

- **Producer:** `runtime/packages/host/src/` contains:
  - `main.js`: link, handshake, modes;
  - `frame-pump.js`: §5;
  - `views.js`: windows and the origin lock;
  - `preload.cjs`: input, pointer lock and pause;
  - `input-messages.js`: validation.

  Unit tests are in `test/host.test.js` (`npm test`).
- **Consumer:** `editor/native/gameview/`. The public API is `include/gs_gameview/gameview.hh`. The Windows backend is in `src/win/`; the protocol code in `src/protocol.cc` is portable.
- **End-to-end probe:** `npm run gameview:probe` builds `gs_gameview_probe` and runs it as a fake editor against the demo game. It measures the pipeline, drives WASD, resizes, pauses and checks that the host exits when the link closes. Options: `--detach-test`, `--window`, `--url`, `--seconds` and others (`--help`).

Measured on 2026-10-09 on an RTX 4070 with Electron 44.4.4 and 60 fps requested. Every run had a full Blender build saturating the CPU in the background. Figures are per frame, as ranges over five runs: p50 range, then the worst p95.

| Worker-thread step | 1280×720 | 1920×1080 |
|---|---|---|
| DuplicateHandle + OpenSharedResource1 + copy submit | 0.24–0.45 ms, p95 ≤ 0.67 | 0.25–0.45 ms, p95 ≤ 0.64 |
| GPU copy, from submit until the fence fires | 1.0–1.1 ms, p95 ≤ 3.1 | 2.0–3.7 ms, p95 ≤ 6.6 |
| `Map` | ≤ 0.03 ms | ≤ 0.04 ms |
| `memcpy` into the triple buffer | 0.30–0.50 ms, p95 ≤ 0.60 | 0.6–1.1 ms, p95 ≤ 1.3 |
| Host `ts` → frame acquirable on the main thread | 1.8–2.5 ms, p95 ≤ 5.7 | 3.2–5.6 ms, p95 ≤ 10.8 |
| Main-thread cost of `acquire_latest()` | O(1), no copy | O(1), no copy |

The game's own frame rate is delivered: 60 fps from live-link `stats` against 57–61 fps acquired, with no frames revoked and only 0–1 skipped per run. After a resize, the first frame at the new size arrived 82–108 ms later. The main thread still has to upload each frame (`GPU_texture_update_sub`), which this probe does not measure.

- **In the editor:** `python tools/e2e/game_view_play_e2e.py` plays the demo docked in GameStudio Editor. It simulates input (`--enable-event-simulate`) and checks capture, keys, mouse, wheel, pointer lock, release, resize, two views, pause while hidden or minimized, detach, crashes, Restart, start/stop cycles without leaked processes, handles or memory, DevTools, and quitting with a game running. Its `cpu` mode measures the editor.

Measured on 2026-10-10 with the same machine and settings, with other GPU work running alongside, for a 1311×578 game at 60 fps:

| Editor (Blender main thread and process) | Value |
|---|---|
| `GPU_texture_update_sub` per frame (main thread) | p50 0.33–0.66 ms, p99 ≤ 2.8 ms |
| Editor process CPU, game docked and visible | 20–36 % of one core (Blender's own redraw of one region at 60 Hz costs about 16 %) |
| Editor process CPU, game hidden (auto-paused) or stopped | 2–4 % of one core |
| Regions redrawn per game frame | only the Game View's main region |
| Stop or Restart, time on the main thread | 0.1–0.3 ms (teardown runs on a background thread) |

Uploading straight from the mapped staging texture, without the worker's `memcpy`, saved about 1 ms of latency. It also made the main-thread upload 2–3× slower (p99 up to 7.7 ms, spikes to 31 ms), because GPU-written memory is cold in the CPU cache. The worker therefore keeps its copy.
