# 09: Embedding an out-of-process Game View (and web panels) in the Blender fork

*Research date: 2026-10-09. Question: how should a separate game process's output appear inside a Blender editor area? The game must stay out of process for crash isolation and to keep the GPL/MIT boundary. **[V]** means verified on this date, either by reading the source at the cited path or by running a spike on the dev machine (Windows 11, RTX 4070). **(unverified)** marks an inference, an estimate or a claim without a primary source.*

---

## 1. TL;DR

- **Blender draws every menu, popup and tooltip inside its own window surface.** `wm_draw.cc` draws each region into its own frame-buffer, draws "menus into their own frame-buffer", and then blends "floating regions (menus)" on top [V]. Only Preferences, new windows and OS dialogs are separate OS windows. Any native OS surface placed over an editor area therefore **covers Blender's popups**. That includes a child HWND, an X11 transient window, an NSView or `CALayerHost`, a Wayland subsurface, and a WebView2 child or visual. This "airspace" problem decides the design.
- **Godot shows the cost.** Godot 4.4–4.7 embeds the game process with four platform implementations:
  - an owned borderless popup window kept over the Game tab on Windows and X11;
  - a private CoreAnimation remote layer on macOS (4.5);
  - a Wayland protocol proxy (4.6, experimental).

  Godot **refuses to embed in single-window mode** because popups would be drawn beneath the game [V]. Blender is permanently in single-window mode.
- **Unity's Game view and Unreal's PIE run in-process** and render into editor textures. Their out-of-process modes (Multiplayer Play Mode virtual players, Standalone, multi-process PIE) open separate windows and are not embedded.
- **Recommended technique: (B).** The game renders offscreen into a GPU-shared texture, and Blender draws it as an ordinary textured quad in a region. Blender popups, DPI, resize, docking and maximize then work for free, and the process boundary stays intact.
- **The producer side already exists.** Electron's `webPreferences.offscreen = { useSharedTexture: true }` (Electron 33.2+, experimental) hands the main process a D3D11 NT handle on Windows, an IOSurface on macOS, or DMA-BUF planes on Linux.
  - **Spike [V]:** Electron 44.4.4 (Chromium 152) delivered **240 fps at 1920×1080** for both WebGL2 and WebGPU content. Frames were BGRA8 sRGB, and the main process used about 4–6% of one core.
  - The CPU-bitmap OSR mode peaked at **105 fps** and used 1.2 cores.
  - CEF offers the same thing through `OnAcceleratedPaint` (CEF 124+).
- **The consumer side is the real work.** Blender has **no texture-import path** today. It only *exports*: Vulkan pixel buffers for Cycles' CUDA/HIP interop, and an OpenGL→D3D11 copy for OpenXR through `WGL_NV_DX_interop2` [V]. The fork needs one importer per GPU backend.
- **v1:** a zero-copy producer, plus a GPU-backend-agnostic **CPU bridge** inside Blender.
  1. A helper thread opens the D3D11 handle on its own device and copies the frame to a staging texture.
  2. The main thread calls `GPU_texture_update_sub(UBYTE)`.

  This works unchanged on OpenGL (5.2) and Vulkan (5.3). **v1.5 / v2:** zero-copy importers behind the same interface.
- **Wrap it in a `GameViewHost` with pluggable `FrameImporter` backends,** and always keep a CPU fallback. Blender's own OpenXR Vulkan binding already uses this pattern: it picks among CPU, Win32-handle, FD and render-graph transfer modes at runtime [V].
- **Input:** forward GHOST events over the link into the engine's own input layer, not as synthesized DOM events. Pointer lock is **unsupported in both Electron and CEF OSR**, so the Game View implements capture itself (§5.5).
- **Panels:** keep the inspector and project browser as **native Blender UI**. If the AI chat moves into an editor area, it must use the same offscreen-texture path, never a child webview (§5.6).

---

## 2. The constraint in Blender terms

The system has two processes:

- **Blender fork (GPL).** It owns the window, the regions, the event loop and the GPU context: OpenGL in 5.2 on Windows, Vulkan from 5.3 on Windows and Linux (5.3 is in beta until 2026-11-04), and Metal on macOS.
- **Runtime host (MIT).** Electron runs Three.js and Rapier. The two processes exchange only handles and messages.

How Blender draws [V]:

- `GHOST_WindowWin32` creates the main window as `WS_OVERLAPPEDWINDOW`, without `WS_CLIPCHILDREN`.
- `wm_draw.cc` renders every area region and every menu region into offscreen frame-buffers, then composites them into the window's single swapchain.

A Game View must therefore satisfy three requirements:

1. Blender's UI can draw on top of it: menus, pie menus, tooltips, drag previews, region-overlap sidebars and the Game View's own header.
2. It follows region geometry: split, join, swap, Ctrl+Space maximize, fullscreen area, multiple windows, and HiDPI.
3. It takes part in Blender's focus and keymap system.

---

## 3. How other editors do it

### 3.1 Godot 4.4–4.7: the closest precedent

"The game always runs in a separate process, no matter the embedding mode used" (docs). There are three modes: **Floating window (default)**, Main window, and Disabled.

| Platform | Mechanism (read from `godotengine/godot` master) | Focus / input | Documented limits |
|---|---|---|---|
| **Windows** (4.4, PR #99010, Hilderin, merged 2024-12-20) | The editor passes its HWND with `--wid`. The game creates its main window as a **`WS_POPUP` owned by the editor HWND**, not with `WS_CHILD`/`SetParent`. It drops `WS_EX_APPWINDOW`, so it is hidden from the taskbar and Alt-Tab. The editor finds the window with `EnumWindows` by PID and `SetWindowPos`es it over the Game tab's screen rectangle. A **0.1 s timer** polls the rectangle, because resize notifications "are not enough". The game calls `AttachThreadInput(…, FALSE)` to avoid "input lags when resizing or moving the main window". It closes itself if its parent disappears [V]. | The OS delivers input straight to the game. The editor polls the foreground PID and auto-focuses the game when the mouse hovers over it, except when a popup, menu, exclusive window or text field is active. It works around activation bugs with `AllowSetForegroundWindow` and a temporary-window trick [V]. | "In single-window mode, all popups and tooltips are displayed beneath the embedded game", so the editor **disables embedding there** (`EMBED_NOT_AVAILABLE_SINGLE_WINDOW_MODE`) [V]. Clicks right after focusing can be lost. OS shortcuts can move the window. OBS cannot capture it. Window-mode changes are unsupported. |
| **X11** (4.4) | `XSetTransientForHint` and removal from the taskbar/pager, then `XMoveResizeWindow`. The rectangle is clipped to the screens because "X11 does not allow moving the window entirely outside the screen boundaries". The window is unmapped when smaller than 100 px [V]. | `XSetInputFocus`. | Moving the editor off-screen resizes the game. There is a KDE re-map quirk. |
| **macOS** (4.5, PR #105884, Stuart Carnie, May 2025) | The game uses a special "embedded" DisplayServer **with no window**. It renders into a `CAMetalLayer` attached to a `CAContext`, created with the private `contextWithCGSConnection:`, and sends `contextId` over the debugger link. The editor adds a private `CALayerHost` with that id to its layer tree [V]. Chromium uses the same remote-layer API between its GPU and browser processes (`ui/base/cocoa/remote_layer_api.h`) [V]. | The editor **serializes InputEvents**, converting pixels to points, and sends them as `embed:event`. Cursor shape, mouse mode, IME text and focus are proxied as messages. Joypad events are *not* forwarded: the game reads pads itself. A "Release Mouse" shortcut (Alt+Esc) is provided [V]. | Uses private API. Godot's blog says it is "more complex, but is also more robust as it doesn't rely on window management tricks". |
| **Wayland** (4.6, PR #107435, deralmas, merged 2025-11-20, experimental) | The editor runs a **Wayland protocol proxy**. The game connects to it, and the proxy rewrites the game's `xdg_toplevel` into a `wl_subsurface` of the editor surface, emulating "just enough of a toplevel" [V]. | Input goes through the real compositor. | Every protocol must be registered in the proxy. Tablets are unsupported. A compositor disconnect can take down the editor. Disabled in single-window mode because the subsurface "covers popups". |

**Lessons:**

1. Even Godot, whose editor can turn popups into native OS windows, needed four platform implementations and many focus heuristics, and it still defaults to a floating window.
2. Its macOS design (no game window, compositor-shared frames, input serialized over the debug link) is the closest template for Blender.
3. Godot embeds only the first run instance.

### 3.2 Unity

- **Game view.** The Game view is in-process: it is "rendered from the Cameras in your application", and the Device Simulator is also an editor view.
- **Multiplayer Play Mode 3.0** (Unity 6.3 LTS+). Virtual players are separate Editor instances, each in its own window, with at most four. They are **not embedded**.
- **Player embedding.** The Windows player accepts `-parentHWND <HWND>`, which creates the player as a child window, and `-parentHWND delayed`, which lets the host call `SetParent` later. Unity supports route (A) for apps that host the player, but not for its own Game view.
- **Out-of-process Play Mode.** None exists in 6.x or the 7.0 alpha. Unity is removing domain reload instead (research/01).

### 3.3 Unreal

- **PIE** runs in-process (Selected Viewport or New Editor Window) on a duplicated world. Clicking captures the mouse, and **Shift+F1** returns it to the editor. The "Game Gets Mouse Control" setting controls the default.
- **Separate processes.** Standalone Game and multi-process PIE ("Run Under One Process" off) are separate processes with their own windows (`EPlaySessionDestinationType` InProcess / NewProcess / Launcher). I found no feature that embeds another process's frames (absence not proven).
- **Other.** The WebBrowser plugin renders CEF offscreen into Slate textures; whether it uses shared textures is **(unverified)**. Pixel Streaming 2 streams the editor *out*, the opposite direction.

### 3.4 Other tools that composite another process's pixels

- **Chromium on Windows.** The GPU process creates a disabled, input-transparent `WS_CHILDWINDOW` (`WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOREDIRECTIONBITMAP`) on a dedicated "Window owner thread". The browser then calls `::SetParent(child, parent)` (`ui/gl/child_window_win.cc`, `ui/gfx/win/rendering_window_manager.cc`) [V].
  - This works only because all of Chromium's UI is drawn by that same GPU process, so nothing else competes for the airspace.
  - Raymond Chen: cross-process parenting is "technically legal", but it implicitly attaches input queues, which is like "juggling chainsaws".
- **OBS.** Game capture hooks `Present` and copies the back buffer into a shared D3D11 texture, with a staging/shared-memory fallback. The OBS browser source uses CEF `OnAcceleratedPaint` on all three OSes.
- **Spout (Windows) and Syphon (macOS).** Spout bridges DX11 shared textures into GL with `WGL_NV_DX_interop`. Its CPU fallback uses staging textures plus a PBO; Spout's code comments give about 6 ms for a 4K staging copy and 17–30 ms for a 4K PBO upload. Syphon uses IOSurface.
- **Qt `createWindowContainer`.** A foreign window "will stack on top of the widget hierarchy as an opaque box". This is the same airspace rule, documented.
- **JetBrains IDEs.** They use JCEF in **offscreen mode by default**. Known OSR gaps: HTML drag and drop (JBR-7399) and IME problems.
- **WPF and WebView2.** Microsoft's own fix for the airspace problem, `WebView2CompositionControl` (SDK 1.0.2957.106, Jan 2025), copies frames with Windows.Graphics.Capture into a WPF `Image`. Microsoft's answer is capture-to-texture.

---

## 4. Technique options

### (A) Native window embedding

**Mechanics:**

| OS | Mechanism | Notes |
|---|---|---|
| Win32 | `SetParent` + `WS_CHILD` (Chromium), or an owned `WS_POPUP` kept over the region (Godot) | Mixed-DPI hosting needs `SetThreadDpiHostingBehavior` (Win10 1803+). |
| X11 | `XReparentWindow`, or a transient window | |
| macOS | Not possible | Another process's `NSWindow` cannot be reparented, and `addChildWindow:` is in-process only. The only route is the private `CAContext`/`CALayerHost`. |
| Wayland | No cross-client embedding | Needs a nested compositor or proxy (Godot, GTK4 Casilda). |

**Airspace mitigations for Blender:**

1. **Hide while overlapped.** On each redraw, if any region in `screen->regionbase` (menus, popups, tooltips) intersects the Game View, hide the game window and draw its last frame in the region. Getting that frame needs path (B) or (C) anyway. Hover tooltips would make the game flicker, and dragging over it breaks.
2. **`SetWindowRgn` holes** (Win32 only). Behaviour with GL and flip-model presentation is **(unverified)**.
3. **Underlay.** Put the game below a transparent editor surface: Wayland `place_below`, CoreAnimation sublayer order, or DirectComposition.
   - DComp puts content presented through DXGI/GL at the *bottom* layer, below child windows and DComp trees.
   - So Blender would have to present through a DComp composition swapchain, which is a large GHOST change.

**Advantages:** zero added latency and full native input. **Verdict:** it fights Blender's single-surface UI on every platform.

### (B) Offscreen render plus a GPU-shared texture

**Producers:**

| Producer | API and version | Handle per OS | Sync and lifetime |
|---|---|---|---|
| **Electron** | `offscreen: { useSharedTexture, sharedTexturePixelFormat: 'argb'\|'rgbaf16'\|'nv12', deviceScaleFactor }`, all experimental. The `paint` event receives `details.texture` (`OffscreenSharedTexture`: `textureInfo` + `release()`) in the **main process only**. History: PR #42953 (reitowo, merged 2024-08-23) shipped in 34.0, backported to 33.2.0. The 240 fps cap was lifted in shared mode (#45669). The unified `textureInfo.handle` arrived in 39 (#47315, breaking). The default `deviceScaleFactor` became 1 in 42. | Win: `ntHandle`, a D3D11 NT handle **local to the Electron main process**. "rgba/bgra/rgbaf16 … don't have a keyed mutex" [V, typings]. macOS: `ioSurface` (process-local). Linux: `nativePixmap` with `planes[]` (fd, stride, offset, size) and `modifier`. | The pool holds 10 frames, and every frame may be a different texture. The consumer opens the handle, copies, and calls `release()` promptly, or frames are dropped. Popups arrive as separate `widgetType: 'popup'` frames. |
| **CEF** | `windowless_rendering_enabled` + `shared_texture_enabled`. `OnAcceleratedPaint(browser, type, dirtyRects, const CefAcceleratedPaintInfo&)`, reworked by reito: CEF 125, backported to **124** on 2024-04-27. `external_begin_frame_enabled` lets the host drive frames. | Win: `shared_texture_handle`, opened with `OpenSharedResource1`. macOS: `shared_texture_io_surface`. Linux: `planes[4]` + `modifier`. | The handle is valid **only during the callback**, so copy inside it. There is no fence; CEF patches out the keyed mutex. |
| **Future native runtime** (Rust/C++) | Owns its swapchain-like ring of shared textures | Win: NT handles from `IDXGIResource1::CreateSharedHandle`. macOS: IOSurface. Linux: DMA-BUF. | Keyed mutex, or a shared fence the runtime controls. |

**Spike [V].** This ran Electron 44.4.4 / Chromium 152 with a hidden offscreen `BrowserWindow`, a full-screen procedural shader, release-on-receipt, and 6 s measured after a 1.5 s warm-up. The scripts are in the scratchpad, not the project.

| Mode | Content | Size | Requested fps | Delivered `paint` fps | Page rAF | Paint interval p50 / p95 | Main-process CPU |
|---|---|---|---|---|---|---|---|
| shared texture | WebGL2 (ANGLE D3D11) | 1920×1080 | 240 | **240.1** | 239.9 | 4.1 / 6.1 ms | 37 ms/s (3.7% core) |
| shared texture | WebGPU (`bgra8unorm`) | 1920×1080 | 240 | **239.9** | 239.9 | 4.0 / 5.8 ms | 63 ms/s |
| shared texture | WebGL2 | 1920×1080 | 60 | 59.9 | 60.0 | 16.5 / 27.0 ms | 26 ms/s |
| shared texture | WebGL2 | 2560×1392 (a 4K request, clamped to the screen) | 240 | 240.0 | 239.8 | 4.0 / 6.5 ms | 60 ms/s |
| CPU bitmap | WebGL2 | 1920×1080 | 240 | **105.5** | 242.3 | 7.9 / 19.4 ms | 1,176 ms/s (incl. 2.7 ms `toBitmap()` per frame) |
| CPU bitmap | WebGL2 | 1920×1080 | 60 | 60.0 | 62.5 | 16.3 / 26.7 ms | 603 ms/s |

What the first frame reported:

- `pixelFormat: 'bgra'` and `colorSpace {bt709, srgb, rgb, full}`.
- `handle` has only `ntHandle` (8 bytes).
- `metadata` carries `captureUpdateRect`, `regionCaptureRect`, `sourceSize` and `frameCount`.
- `release()` took 0.02 ms.

At 60 fps the p95 interval of 27 ms shows **delivery jitter**: frames sometimes arrive in pairs. The consumer should always present the newest frame and drop the rest.

The shader is trivial, so these numbers measure the transport, not game rendering. Rendering-to-paint latency inside Chromium was **not measured**. It is plausibly 0.5–1 frame **(unverified)**.

**Cross-process hand-off.** The handles are process-local:

- **Windows:** Blender, which spawned the runtime and holds its process handle, calls `DuplicateHandle`.
- **macOS:** `IOSurfaceCreateMachPort` over a Mach/XPC channel.
- **Linux:** fd passing with `SCM_RIGHTS`.
- **Adapters must match.** Vulkan requires a matching `deviceUUID`/`driverUUID` for D3D handles, and Overwolf's ow-electron reports a `gpuAdapterMismatch` failure. Pass the LUID both ways and pin Chromium to Blender's adapter.

**Consumers (Blender side):**

| Blender backend | Import API | Blender today | Caveats |
|---|---|---|---|
| OpenGL (5.2 default, Windows) | `WGL_NV_DX_interop2`: `OpenSharedResource1` on a D3D11 device Blender owns → `wglDXRegisterObjectNV(READ_ONLY)` → lock / sample / unlock. Alternative: `GL_EXT_memory_object_win32` (`GL_HANDLE_TYPE_D3D11_IMAGE_EXT`) + `GL_EXT_semaphore_win32`. | `GHOST_ContextD3D.cc` already uses `wglDXOpenDeviceNV`/`RegisterObjectNV`/`Lock`/`Unlock` for OpenXR (GL→D3D11) [V]. Its unregister is skipped under `#if 0` because "wglDXUnregisterObjectNV() causes an access violation on AMD". | Intel says it does **not support** `EXT_memory_object_win32` (KB 000103065, 2026), so `NV_DX_interop2` is the portable GL choice. Intel's history with it is patchy **(unverified, current status)**. |
| Vulkan (5.3 default, Windows/Linux) | `VK_KHR_external_memory_win32` with `VK_EXTERNAL_MEMORY_HANDLE_TYPE_D3D11_TEXTURE_BIT` and a dedicated allocation. Sync with `VK_KHR_win32_keyed_mutex`, or a D3D11 shared fence imported as a timeline semaphore (`D3D11_FENCE_BIT`). Linux: `VK_EXT_external_memory_dma_buf` + `VK_EXT_image_drm_format_modifier`. | Requests `VK_KHR_external_memory_win32/_fd` (plus semaphores and the keyed mutex under OpenXR). **Exports** pixel buffers (`vk_pixel_buffer.cc`, `OPAQUE_WIN32`/`OPAQUE_FD`, since 4.5) and images (`VKTexture::export_memory`). **No import.** The protected `VKTexture::init_swapchain(VkImage, …)` shows how to wrap a foreign `VkImage`. | DMA-BUF extensions are not requested yet. Submission (`vk_device_submission.cc`) needs a keyed-mutex `pNext` or real timeline wait values. Per-vendor support for `D3D11_TEXTURE_BIT` must be queried at runtime. |
| Metal (macOS) | `newTextureWithDescriptor:iosurface:plane:` | `MTLTexture(name, format, type, id<MTLTexture>)` with `MTL_TEXTURE_MODE_EXTERNAL` already wraps foreign textures. This is the closest backend to ready. | Electron's IOSurface is process-local, so it must be passed as a Mach port. |

**Synchronization.** Electron and CEF RGBA/BGRA frames have no keyed mutex. Chromium has finished writing when `paint` fires, and the contract is "copy, then release". The robust design has three parts:

1. A helper process or thread does `CopyResource` from the pooled texture into a **ring of three shared textures** owned by our side. These are created with `SHARED_NTHANDLE | SHARED_KEYEDMUTEX`, or paired with an `ID3D11Device5::CreateFence` shared fence.
2. The ring's handles are duplicated into Blender **once per resize**.
3. Each frame sends only `{slot, frameId}`.

Spout uses the same pattern: a mutex-guarded shared texture plus a frame counter.

### (C) CPU frame copy

- **C1, all-CPU** (Electron bitmap `paint` → shared memory → upload). Measured above: it cannot reach high frame rates at 1080p and burns about 0.6–1.2 cores in the Electron main process.
- **C2, the "CPU bridge"** (GPU texture shared, CPU copy only inside Blender):
  - a Blender-side thread opens the frame on its own D3D11 device, copies it to a staging texture (`CopyResource`) and `Map`s it;
  - the main thread calls `GPU_texture_update_sub(tex, GPU_DATA_UBYTE, …)` and fixes the channel order with `GPU_texture_swizzle_set(tex, "bgra")` (both exist in `GPU_texture.hh` [V]).
- **Cost of C2.** It is BGRA8, with no float conversion: research/04's 9–23 ms per 1080p frame came from Python's FLOAT-only path.
  - Native C++ upload of 8.3 MB should be roughly 1–3 ms.
  - Readback adds about one frame of latency if pipelined.
  - Spout's figures, scaled down from 4K, suggest up to about 5 ms at 1080p on bad paths.
  - research/06 measured 0.23 ms for a 720p `memcpy`.
  - Total: **an estimated 2–6 ms per 1080p frame, partly on Blender's main thread (unverified; spike 1 below measures it).**
- **Advantage:** it works on every Blender GPU backend through public GPU-module calls. Blender's own `GHOST_XrGraphicsBindingVulkanD3D` does exactly this kind of CPU readback for OpenXR [V].

### (D) Web panels: native webviews or offscreen

| Option | Status | Airspace |
|---|---|---|
| WebView2 windowed | A child HWND | Covers Blender's popups |
| WebView2 visual hosting (`CreateCoreWebView2CompositionController`, `put_RootVisualTarget`, `SendMouseInput`/`SendPointerInput`, `CursorChanged`, `DragEnter…`, `get_AutomationProvider`) | The host forwards input | DComp visuals always composite **above** content presented through GL/DXGI on the same HWND (`CreateTargetForHwnd` layering), so popups are still covered. **No offscreen texture API**: WebView2Feedback #547 has been open since 2020-10-20. |
| WKWebView | An NSView over Blender's Metal view | Covers popups. Snapshot APIs are asynchronous CPU images. |
| WebKitGTK / WPE | WebKitGTK is a GtkWidget (Blender has no GTK). **WPE** WPEPlatform hands `WPEBuffer`s (DMA-BUF or SHM) to the host; 2.54 (2026-09-16) is the "stable WPEPlatform". | WPE is offscreen, so it avoids the problem |
| CEF / Electron OSR | The same pipeline as (B) | None. But input, IME (CEF has an `Ime*` API; Electron OSR only has `insertText`), cursor, drag-drop, popups (`PET_POPUP`/`widgetType: popup`) and accessibility must be re-plumbed. |

### Comparison

| | A: child/owned window | A + hide-on-popup | **B zero-copy** | **B + CPU bridge (C2)** | C1 all-CPU | D: native webview | D: OSR web panel |
|---|---|---|---|---|---|---|---|
| Blender popups on top | No | Mostly (game hidden or frozen) | **Yes** | **Yes** | Yes | No | Yes |
| Added latency vs. a standalone window | ~0 | ~0 | ~1 frame (unverified) | ~1–2 frames (unverified) | 2+ frames | n/a | n/a |
| Max FPS shown | Game's own | Game's own | Blender's redraw rate | Blender's redraw rate | ~105 at 1080p (measured) | — | — |
| Blender GPU-backend code | None | None (snapshot needs B/C) | One importer per backend | **None** (public GPU API) | None | None | Same as B |
| Per-OS work | 4 implementations (Godot) | Same + snapshot | Handle passing + importer | Handle passing + staging | Shared memory | 3 webviews | Shared with B |
| Crash isolation | Yes, but input queues can couple | Yes | Yes | Yes | Yes | Yes | Yes |
| Complexity | Medium, heuristic-heavy | High | High | **Medium** | Low | Low | High |

---

## 5. Recommendation for the GameStudio fork

### 5.1 Decision

| Phase | What | Why |
|---|---|---|
| **v1 (Windows)** | **(B) producer + (C2) CPU bridge consumer.** The runtime host gains a "docked" mode: a hidden Electron `BrowserWindow` with `offscreen: { useSharedTexture: true }` and `backgroundThrottling: false`. Blender gets a new **Game View** editor (SpaceType) that draws the latest frame. Keep today's separate window as **Detach**, which is Godot's default floating mode. | It has no GPU-backend code, so it survives the 5.2 GL → 5.3 Vulkan switch unchanged. The producer is already measured at 240 fps. Popups and docking are correct by construction. |
| **v1.5** | Zero-copy importers on Windows: **Vulkan first** (`D3D11_TEXTURE_BIT` + keyed mutex or shared fence, since it is the default from 5.3), then **GL** through `WGL_NV_DX_interop2`, reusing the `GHOST_ContextD3D` plumbing. Fall back automatically to C2 if import or a probe frame fails. | This removes the main-thread upload and a frame of latency. |
| **v2** | macOS (IOSurface → `MTLTexture`; the wrapper exists) and Linux (DMA-BUF; add the extensions). | Electron's Linux shared-texture OSR is still fragile: issues #52618 (`texture` always null on the NVIDIA proprietary driver), #53271 and #54553 (44.x). Use C1 there until it settles. |
| **Not recommended** | (A) for the docked view. Native webviews inside editor areas. | Airspace, plus four platform implementations, plus focus heuristics. |

**GPL boundary.** All importer code lives in the GPL fork. The runtime host and the protocol spec stay MIT. Exchanging OS handles and small control messages is arm's-length communication, but have counsel confirm, as research/04 already notes **(unverified)**.

### 5.2 Abstraction that survives backend switches and Blender upgrades

```cpp
// GPL side, source/blender/editors/space_gameview/ + gpu/ seam
struct ExternalFrameDesc {            // arrives over IPC, per resize
  HandleKind kind;                    // D3D11_NT, IOSURFACE_MACH, DMABUF, SHM
  int width, height; PixelFormat fmt; // BGRA8 for v1 (later RGBA16F for HDR)
  ColorSpace cs; uint64_t adapter_luid; SyncKind sync;  // KEYED_MUTEX / FENCE / NONE
  Vector<NativeHandle> ring;          // already duplicated into Blender
};
class FrameImporter {                 // one per (OS x GPU backend x HandleKind)
 public:
  static std::unique_ptr<FrameImporter> create(const ExternalFrameDesc &, GPUBackendType);
  virtual gpu::Texture *acquire(int slot, uint64_t frame_id) = 0;  // main thread, in draw()
  virtual void release(int slot) = 0;
};
// Backends: CpuBridgeD3D11 (Win, any backend) · ShmCpu (all OSes) · VkD3D11Import ·
//           GlNvDxInterop · MtlIOSurface · VkDmaBuf
class GameViewHost {                  // per Game View editor; owns process + link
  void launch_or_attach(); void configure(int w, int h, float dpi_scale);
  void post_input(const GameInputEvent &); gpu::Texture *latest_frame();
  State state();                      // starting / running / paused / crashed / detached
};
```

- **The protocol is MIT** and lives in `docs/SPEC.md`: `surface.configure`, `surface.frame {slot, frameId, ts}`, `surface.release`, `input.*`, `focus`, `cursor.grab/shape`, `detach`. It is engine-agnostic, so a future native runtime is just another producer.
- **Importer selection:** `GPU_backend_get_type()`, then capability probes, then C2. This is the same idea as `GHOST_XrGraphicsBindingVulkan::choseDataTransferMode()`.
- **Keep the merge tax small.** The SpaceType sits in its own folder. The GPU seam is one function family, `GPU_texture_create_from_external(...)`, implemented in `gl_/vk_/mtl_texture`. That function family is plausibly upstreamable, which would remove most of the fork delta **(unverified, worth asking Blender's GPU module)**.

### 5.3 Minimal native seam inside Blender

| Need | Where | Exists today? |
|---|---|---|
| Native window handle | `GHOST_Window::getOSWindow()` (internal base class): HWND / `NSWindow*` / X11 Window. **Not** on `GHOST_IWindow`, not in WM, not in Python. | Partly. Needed only for (A), monitor and DPI queries, and DComp. |
| Adapter identity (LUID) | GL: via the D3D11 device in `GHOST_ContextD3D`. Vulkan: `VkPhysicalDeviceIDProperties.deviceLUID`. | Needs a small `GPU_` query. |
| Region geometry and DPI | `ARegion::winrct/winx/winy` (framebuffer pixels); `U.pixelsize`, `UI_SCALE_FAC`; `WM_window_native_pixel_size`, `WM_window_dpi_get_scale`; `GHOST_Window::getNativePixelSize()` for Retina | Yes |
| A new editor | `SpaceType`/`ARegionType` registered with `BKE_spacetype_register`. Template: **`space_project`** (commit 563b073381, PR #140924, 2026-06-05), which touched DNA, RNA, `spacetypes.cc`, `screen*.cc`, `wm_draw.cc`, theme `resources.cc` and `bl_ui`. Python cannot register space types, which is one real reason to fork. | Template exists |
| Draw an external texture | `ARegionType::draw`: `immBindBuiltinProgram(GPU_SHADER_3D_IMAGE)` and a letterboxed quad, with no OCIO because the frames are display-referred sRGB | Yes, for textures Blender creates |
| Import an external texture | `GPU_texture_create_from_external` for GL (NV_DX_interop2 name), Vulkan (`VkImage` wrap, like `init_swapchain`) and Metal (`MTL_TEXTURE_MODE_EXTERNAL`) | **No.** This is the main new code. |
| Wake on new frame | Today the main loop polls GHOST and sleeps up to 5 ms when idle, clamped to the next timer (`wm_window_events_process`) [V]. Add a thread-safe wake (a GHOST user event / `PostMessage`) plus `ED_region_tag_redraw`. | Partly (timers) |
| Input routing | Space keymap + a "captured" modal handler; `WM_cursor_grab_enable` with `GHOST_kGrabWrap`/`GHOST_kGrabHide` [V]; window activate/deactivate events | Yes |
| Child-process lifetime | Windows Job Object (`KILL_ON_JOB_CLOSE`), so a crashed Blender takes the runtime with it | New, small |

### 5.4 Expected latency and FPS (v1 on this machine)

| Stage | Budget |
|---|---|
| Game render → Electron `paint` (Chromium capture + mojo + Node event) | ~0.5–1 frame **(unverified)**. Jitter seen at p95. |
| Hand-off to Blender (IPC message, handle already open) | < 0.2 ms (unverified) |
| C2: staging copy + `Map` on the helper thread | ~1–3 ms, pipelined one frame **(unverified)** |
| Main-thread `GPU_texture_update_sub` 1080p BGRA8 | ~1–3 ms **(unverified)**. Removed in v1.5. |
| Blender redraw + vsynced swap + DWM | ≤ 1 display frame. Blender is vsynced, and this machine shows 75 Hz (research/04). |

- **FPS.** The Game View refreshes at Blender's redraw rate, capped at the display rate, while the game simulates at its own rate.
- **Expected added latency** over a standalone game window: about +1–2 display frames for v1 (13–27 ms at 75 Hz), and about +1 frame for zero-copy.
- **Risk.** A busy Blender main thread (depsgraph evaluation, Python timers, the live-link pump) stalls the *view* but not the game.
- **Exit test for v1:** 1080p at display refresh with < 3 ms main-thread cost per frame on both the GL and Vulkan backends.

### 5.5 Input

- **Focus model.** Unreal-style. Hovering shows a hint. Clicking **captures** the input: all keys and mouse go to the game except a release chord (Shift+F1 by default, plus Esc only if the game doesn't use it). Losing window activation releases capture and sends `focus out`, so the game clears held keys. Blender shortcuts work whenever the view is not captured.
- **Transport.** Translate GHOST events to engine-level events (DOM `code` names, buttons, wheel, modifiers, coordinates in game pixels) and send them over the link into the engine's input layer.
  - Don't use `webContents.sendInputEvent`. It derives `code` from a US layout, has no IME, and would tie us to Chromium.
  - Keep `sendInputEvent` only for web panels.
- **Pointer lock.** `requestPointerLock()` fails in OSR: Electron returns `kUnsupportedOptions` and CEF returns `kPermissionDenied`. Instead:
  1. The engine's pointer-lock call becomes a `cursor.grab` request.
  2. Blender calls `WM_cursor_grab_enable` (wrap/hide) and sends relative deltas.
  3. Release restores the cursor.
- **Gamepad.** Don't depend on Chromium's Gamepad API: its exposure is gated on page visibility, and background behaviour is **(unverified)**. Read pads in the runtime-host process with SDL3 background events, gated by the Game View's capture/focus state. On macOS, `GCController.shouldMonitorBackgroundEvents` (macOS 11.3+) is needed because the editor, not the game, is the frontmost app. Godot likewise leaves pads to the game.
- **Cursor shape and IME.** The game sends `cursor.shape` and Blender sets it. IME for in-game text fields is deferred; GHOST already raises `GHOST_kEventImeComposition*` events.

### 5.6 Web panels or native Blender UI?

| Panel | Recommendation | Reasoning |
|---|---|---|
| **Inspector** (components) | **Native Blender UI** | It must integrate with RNA, undo, drivers and keyframes, library overrides (`LIBRARY_OVERRIDABLE`), copy-paste, theme, keymaps and accessibility. Our schema-generated `PropertyGroup`s already give us this (research/04). A web inspector would duplicate all of it across a process boundary. |
| **Project browser** | **Native** (Asset Browser / File Browser plus the new Project space) | Blender already supports drag and drop into the viewport there. |
| **AI chat** | **v1: native panel** (today's Assistant tab) **or a detached Electron window.** v2, if rich rendering is needed (markdown, code diffs, streaming): a **Web Panel editor through the same `FrameImporter` pipeline**, using CEF OSR because it has IME APIs. | This is the only panel that gains much from HTML. Child webviews are ruled out by the airspace problem on all three OSes. |

**The VS Code model** (webviews are iframes inside one Chromium, with `postMessage`, `getState`/`setState`, `retainContextWhenHidden` and per-webview origins) works because VS Code's whole UI is web content composited by one viz compositor. A native GPU UI that hosts web content must turn that content into textures, which is what JetBrains (JCEF OSR by default) and Microsoft's WPF control do.

Treat web panels as a v2 feature that reuses the Game View pipeline, not as a separate technology.

### 5.7 Spikes, in order

1. **C2 cost (2–3 days).** A small C++ consumer that `DuplicateHandle`s the Electron frame, copies it to staging, and uploads it to GL and Vulkan.
   - Measure per-frame cost.
   - Measure end-to-end latency by encoding the frame number as pixels and comparing timestamps.
2. **Game View SpaceType in a 5.2 fork (1 week).** Draw C2 frames, resize, run on both `--gpu-backend opengl` and `vulkan`, and handle popups over the view and Ctrl+Space.
3. **Vulkan `D3D11_TEXTURE_BIT` import** on the RTX 4070, plus one AMD and one Intel machine.
4. **Input capture.** Grab, relative deltas, the release chord, and focus-loss handling.

---

## 6. Sources

**Godot**
- Godot docs, Game embedding (4.7): https://docs.godotengine.org/en/stable/tutorials/editor/game_embedding.html
- PRs: https://github.com/godotengine/godot/pull/99010 · https://github.com/godotengine/godot/pull/105884 · https://github.com/godotengine/godot/pull/107435
- Godot 4.5 dev 4 article (2025-05-13): https://godotengine.org/article/dev-snapshot-godot-4-5-dev-4/
- Source read [V], all under `github.com/godotengine/godot/blob/master/`:
  - `platform/windows/display_server_windows.cpp` (`embed_process`, `_get_window_style`, `_create_window`)
  - `platform/linuxbsd/x11/display_server_x11.cpp`
  - `platform/macos/display_server_macos.mm`, `display_server_macos_embedded.mm`, `editor/embedded_process_macos.mm`
  - `platform/linuxbsd/wayland/wayland_embedder.cpp`, `display_server_wayland.cpp`
  - `editor/run/embedded_process.cpp`, `editor/run/game_view_plugin.cpp`
- LibGodot surface proposal: https://github.com/godotengine/godot-proposals/issues/14435

**Chromium and Windows**
- Chromium source [V], under `chromium.googlesource.com/chromium/src/+/refs/heads/main/`: `ui/gl/child_window_win.cc`, `ui/gfx/win/rendering_window_manager.cc`, `ui/base/cocoa/remote_layer_api.h`
- Raymond Chen, cross-process parent/child windows: https://devblogs.microsoft.com/oldnewthing/20130412-00/?p=4683
- `SetThreadDpiHostingBehavior`: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpihostingbehavior

**Electron**
- Offscreen rendering tutorial: https://www.electronjs.org/docs/latest/tutorial/offscreen-rendering
- Structures: https://www.electronjs.org/docs/latest/api/structures/offscreen-shared-texture · https://www.electronjs.org/docs/latest/api/structures/shared-texture-handle
- `sharedTexture` module: https://www.electronjs.org/docs/latest/api/shared-texture
- Breaking changes: https://www.electronjs.org/docs/latest/breaking-changes
- OSR README: https://github.com/electron/electron/blob/main/shell/browser/osr/README.md
- PRs: https://github.com/electron/electron/pull/42953 · /44511 · /45669 · /47315 · /49683
- Issues: https://github.com/electron/electron/issues/52618 · /53271 · /54553
- Electron 44.4.4 `electron.d.ts` typings, read locally [V]

**CEF and Chromium-based consumers**
- `cef_render_handler.h`: https://github.com/chromiumembedded/cef/blob/master/include/cef_render_handler.h
- CEF issues: https://github.com/chromiumembedded/cef/issues/2575 · https://github.com/chromiumembedded/cef/issues/4027
- OBS browser source client: https://github.com/obsproject/obs-browser/blob/master/browser-client.cpp
- OBS D3D11 game-capture hook: https://github.com/obsproject/obs-studio/blob/master/plugins/win-capture/graphics-hook/d3d11-capture.cpp
- Overwolf shared-texture rendering: https://dev.overwolf.com/ow-electron/reference/examples/overlay/shared-texture-rendering

**GPU interop specs**
- Khronos: https://registry.khronos.org/OpenGL/extensions/NV/WGL_NV_DX_interop2.txt · https://registry.khronos.org/OpenGL/extensions/EXT/EXT_external_objects_win32.txt · https://registry.khronos.org/OpenGL/extensions/EXT/EXT_win32_keyed_mutex.txt
- Vulkan: https://docs.vulkan.org/refpages/latest/refpages/source/VkExternalMemoryHandleTypeFlagBits.html · https://docs.vulkan.org/refpages/latest/refpages/source/VK_KHR_win32_keyed_mutex.html · https://docs.vulkan.org/refpages/latest/refpages/source/VkExternalSemaphoreHandleTypeFlagBits.html
- Intel KB 000103065: https://www.intel.com/content/www/us/en/support/articles/000103065.html
- D3D11 shared fences: https://learn.microsoft.com/en-us/windows/win32/api/d3d11_4/nf-d3d11_4-id3d11device5-createfence
- Metal IOSurface textures: https://developer.apple.com/documentation/metal/mtldevice/maketexture(descriptor:iosurface:plane:)
- Spout2: https://github.com/leadedge/Spout2 · Syphon: https://github.com/Syphon/Syphon-Framework

**Blender**
- Source [V], under `projects.blender.org/blender/blender` (main, with 5.2 checked where noted):
  - `source/blender/windowmanager/intern/wm_draw.cc`, `wm_window.cc`
  - `intern/ghost/intern/GHOST_WindowWin32.cc`, `GHOST_ContextD3D.cc`, `GHOST_XrGraphicsBinding.cc`, `GHOST_XrGraphicsBindingD3D.cc`, `GHOST_XrGraphicsBindingVulkan.cc`
  - `intern/ghost/GHOST_Types.hh`, `GHOST_IWindow.hh`
  - `source/blender/gpu/GPU_texture.hh`, `gpu/vulkan/vk_pixel_buffer.cc`
  - `source/blender/editors/space_project/space_project.cc`
- Blender 5.3 release notes (Vulkan default): https://developer.blender.org/docs/release_notes/5.3/eevee/

**Webviews**
- WebView2 windowed vs. visual hosting: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/windowed-vs-visual-hosting
- `ICoreWebView2CompositionController`: https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2compositioncontroller
- WebView2 offscreen rendering request: https://github.com/MicrosoftEdge/WebView2Feedback/issues/547
- `WebView2CompositionControl`: https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.wpf.webview2compositioncontrol
- DComp layering: https://learn.microsoft.com/en-us/windows/win32/api/dcomp/nf-dcomp-idcompositiondevice-createtargetforhwnd
- WPE: https://wpewebkit.org/release/ · https://blogs.igalia.com/llepage/the-wpe-platform-api/
- `WKWebView.takeSnapshot`: https://developer.apple.com/documentation/webkit/wkwebview/takesnapshot(with:completionhandler:)

**Other editors and tools**
- VS Code webviews: https://code.visualstudio.com/api/extension-guides/webview
- JetBrains JCEF: https://plugins.jetbrains.com/docs/intellij/embedded-browser-jcef.html
- Qt `createWindowContainer`: https://doc.qt.io/qt-6/qwidget.html#createWindowContainer
- GTK Casilda 1.0: https://blogs.gnome.org/gtk/2025/09/15/casilda-1-0-released/

**Unity**
- Game view: https://docs.unity3d.com/Manual/GameView.html
- Multiplayer Play Mode 3.0: https://docs.unity3d.com/Packages/com.unity.multiplayer.playmode@3.0/manual/index.html
- Player command-line arguments: https://docs.unity3d.com/Manual/PlayerCommandLineArguments.html

**Unreal**
- Playing and simulating: https://dev.epicgames.com/documentation/en-us/unreal-engine/playing-and-simulating-in-unreal-engine
- Play in Editor settings: https://dev.epicgames.com/documentation/en-us/unreal-engine/play-in-editor-settings-in-unreal-engine
- `EPlaySessionDestinationType`: https://dev.epicgames.com/documentation/en-us/unreal-engine/API/Editor/UnrealEd/EPlaySessionDestinationType

**Apple**
- `GCController.shouldMonitorBackgroundEvents`: https://developer.apple.com/documentation/gamecontroller/gccontroller/shouldmonitorbackgroundevents

**Earlier GameStudio research**
- `research/04-blender-internals.md` (Python upload cost, vsync, `gpu` API) and `research/06-runtime-options.md` (shared-memory copy cost, Electron choice)
