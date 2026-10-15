// SPDX-License-Identifier: MIT
// Docked-mode preload (docs/SURFACE.md §4.3). An offscreen page gets no OS input, so the
// editor's input.* messages arrive here over IPC and become synthetic DOM events, dispatched
// in the page's own (main) world so the game sees ordinary KeyboardEvent / PointerEvent /
// MouseEvent / WheelEvent objects. Pointer lock is unsupported offscreen, so
// requestPointerLock() is emulated and reported to the editor as cursor.grab. While the
// editor pauses the game, the page is hidden and requestAnimationFrame callbacks are held.
//
// Sandboxed preload: CommonJS, and only `electron` may be required.
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('__gamestudioHost', {
  cursorGrab: (grab) => ipcRenderer.send('gs:cursor-grab', grab === true),
});
contextBridge.executeInMainWorld({ func: installSurfaceInput, args: [ipcRenderer.sendSync('gs:paused') === true] });

const toMainWorld = (message) =>
  contextBridge.executeInMainWorld({
    func: (m) => window[Symbol.for('gamestudio.surface.input')]?.(m),
    args: [message],
  });
ipcRenderer.on('gs:input', (_event, message) => toMainWorld(message));
ipcRenderer.on('gs:pause', (_event, paused) => toMainWorld({ t: 'pause', paused: paused === true }));

/**
 * Runs in the page's main world. It is serialized by executeInMainWorld, so it must not
 * use anything from this file's scope.
 * @param {boolean} startPaused
 */
function installSurfaceInput(startPaused) {
  const KEY = Symbol.for('gamestudio.surface.input');
  if (window[KEY]) return;
  const bridge = window.__gamestudioHost;

  // --- Pause ---------------------------------------------------------------------------------
  // The editor pauses a docked game it does not show. Like a background tab, the page is then
  // hidden and gets no animation frames; callbacks requested meanwhile run after the resume.
  let paused = false;
  const nativeRequest = window.requestAnimationFrame.bind(window);
  const nativeCancel = window.cancelAnimationFrame.bind(window);
  const held = new Map(); // id -> callback, requested while paused
  const cancelled = new Set(); // held ids cancelled after they were handed back to Chromium
  let nextHeldId = 1e9; // far above Chromium's own (sequential) ids
  window.requestAnimationFrame = function requestAnimationFrame(callback) {
    if (!paused) return nativeRequest(callback);
    const id = nextHeldId++;
    held.set(id, callback);
    return id;
  };
  window.cancelAnimationFrame = function cancelAnimationFrame(id) {
    if (held.delete(id)) return;
    if (id >= 1e9) cancelled.add(id);
    else nativeCancel(id);
  };
  const visibility = Object.getOwnPropertyDescriptor(Document.prototype, 'visibilityState');
  const hidden = Object.getOwnPropertyDescriptor(Document.prototype, 'hidden');
  Object.defineProperty(Document.prototype, 'visibilityState', {
    configurable: true,
    get() {
      return paused ? 'hidden' : visibility.get.call(this);
    },
  });
  Object.defineProperty(Document.prototype, 'hidden', {
    configurable: true,
    get() {
      return paused || hidden.get.call(this);
    },
  });
  function setPaused(value) {
    if (value === paused) return;
    paused = value;
    if (!paused) {
      for (const [id, callback] of held) {
        nativeRequest((time) => {
          if (!cancelled.delete(id)) callback(time);
        });
      }
      held.clear();
    }
    document.dispatchEvent(new Event('visibilitychange'));
  }
  setPaused(startPaused);

  // --- Pointer lock emulation ------------------------------------------------------------
  let locked = null;
  const lockChanged = () => document.dispatchEvent(new Event('pointerlockchange', { bubbles: true }));
  const unlock = () => {
    if (!locked) return;
    locked = null;
    bridge?.cursorGrab(false);
    setTimeout(lockChanged, 0);
  };
  Element.prototype.requestPointerLock = function requestPointerLock() {
    if (locked !== this) {
      locked = this;
      bridge?.cursorGrab(true);
      setTimeout(lockChanged, 0);
    }
    return Promise.resolve();
  };
  Document.prototype.exitPointerLock = function exitPointerLock() {
    unlock();
  };
  Object.defineProperty(Document.prototype, 'pointerLockElement', {
    configurable: true,
    get() {
      return locked && locked.isConnected ? locked : null;
    },
  });

  // --- Helpers ---------------------------------------------------------------------------
  const canvas = () => document.querySelector('canvas');
  const modifierInit = (list) => ({
    shiftKey: list.includes('Shift'),
    ctrlKey: list.includes('Control'),
    altKey: list.includes('Alt'),
    metaKey: list.includes('Meta'),
  });
  const LEGACY_CODES = {
    Backspace: 8, Tab: 9, Enter: 13, NumpadEnter: 13, ShiftLeft: 16, ShiftRight: 16, ControlLeft: 17,
    ControlRight: 17, AltLeft: 18, AltRight: 18, Pause: 19, CapsLock: 20, Escape: 27, Space: 32,
    PageUp: 33, PageDown: 34, End: 35, Home: 36, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39,
    ArrowDown: 40, Insert: 45, Delete: 46, MetaLeft: 91, MetaRight: 92, Semicolon: 186, Equal: 187,
    Comma: 188, Minus: 189, Period: 190, Slash: 191, Backquote: 192, BracketLeft: 219, Backslash: 220,
    BracketRight: 221, Quote: 222,
  };
  const legacyKeyCode = (code) => {
    let m;
    if ((m = /^Key([A-Z])$/.exec(code))) return m[1].charCodeAt(0);
    if ((m = /^Digit([0-9])$/.exec(code))) return 48 + Number(m[1]);
    if ((m = /^Numpad([0-9])$/.exec(code))) return 96 + Number(m[1]);
    if ((m = /^F([0-9]{1,2})$/.exec(code))) return 111 + Number(m[1]);
    return LEGACY_CODES[code] ?? 0;
  };
  const keyLocation = (code) => {
    const side = /^(?:Shift|Control|Alt|Meta)(Left|Right)$/.exec(code);
    if (side) return side[1] === 'Left' ? 1 : 2;
    return /^Numpad/.test(code) ? 3 : 0;
  };
  const withLegacy = (event, keyCode, charCode = 0) => {
    Object.defineProperty(event, 'keyCode', { value: keyCode });
    Object.defineProperty(event, 'which', { value: charCode || keyCode });
    Object.defineProperty(event, 'charCode', { value: charCode });
    return event;
  };

  // --- Keyboard ----------------------------------------------------------------------------
  function key(m) {
    const active = document.activeElement;
    const target = active && active !== document.body ? active : (canvas() ?? document.body ?? document);
    const init = {
      key: m.key, code: m.code, repeat: m.repeat, location: keyLocation(m.code),
      bubbles: true, cancelable: true, composed: true, view: window, ...modifierInit(m.modifiers),
    };
    const keyCode = legacyKeyCode(m.code);
    const type = m.type === 'down' ? 'keydown' : 'keyup';
    const proceed = target.dispatchEvent(withLegacy(new KeyboardEvent(type, init), keyCode));
    if (type === 'keydown' && proceed && m.key.length === 1 && !init.ctrlKey && !init.metaKey) {
      const charCode = m.key.charCodeAt(0);
      target.dispatchEvent(withLegacy(new KeyboardEvent('keypress', init), charCode, charCode));
    }
  }

  // --- Pointer -----------------------------------------------------------------------------
  // Coordinates arrive in game (texture) pixels; DOM events use CSS pixels.
  let lastX = 0;
  let lastY = 0;
  function pointer(m) {
    const ratio = window.devicePixelRatio || 1;
    if (m.x !== null) lastX = m.x / ratio;
    if (m.y !== null) lastY = m.y / ratio;
    const target = locked ?? document.elementFromPoint(lastX, lastY) ?? canvas() ?? document.body;
    if (!target) return;
    const mouse = {
      bubbles: true, cancelable: true, composed: true, view: window,
      clientX: lastX, clientY: lastY, screenX: lastX, screenY: lastY,
      button: m.button, buttons: m.buttons, movementX: m.dx / ratio, movementY: m.dy / ratio,
      ...modifierInit(m.modifiers),
    };
    const ptr = { ...mouse, pointerId: 1, pointerType: 'mouse', isPrimary: true, width: 1, height: 1, pressure: m.buttons ? 0.5 : 0 };
    switch (m.type) {
      case 'move':
        target.dispatchEvent(new PointerEvent('pointermove', { ...ptr, button: -1 }));
        target.dispatchEvent(new MouseEvent('mousemove', { ...mouse, button: 0 }));
        break;
      case 'down':
        if (target instanceof HTMLElement && target !== document.activeElement) target.focus({ preventScroll: true });
        if (target.dispatchEvent(new PointerEvent('pointerdown', ptr))) target.dispatchEvent(new MouseEvent('mousedown', mouse));
        break;
      case 'up':
        target.dispatchEvent(new PointerEvent('pointerup', ptr));
        target.dispatchEvent(new MouseEvent('mouseup', mouse));
        if (m.button === 0) target.dispatchEvent(new PointerEvent('click', { ...ptr, detail: 1 }));
        if (m.button === 2) target.dispatchEvent(new PointerEvent('contextmenu', ptr));
        break;
      case 'wheel':
        target.dispatchEvent(new WheelEvent('wheel', { ...mouse, deltaX: m.deltaX, deltaY: m.deltaY, deltaMode: 0 }));
        break;
    }
  }

  // --- Focus -------------------------------------------------------------------------------
  function focus(m) {
    if (m.focused) {
      window.dispatchEvent(new FocusEvent('focus'));
      const c = canvas();
      if (c && document.activeElement !== c) c.focus({ preventScroll: true });
    } else {
      unlock(); // Browsers end pointer lock when the window loses focus.
      window.dispatchEvent(new FocusEvent('blur'));
    }
  }

  Object.defineProperty(window, KEY, {
    value: (m) => {
      try {
        if (m.t === 'input.key') key(m);
        else if (m.t === 'input.pointer') pointer(m);
        else if (m.t === 'focus') focus(m);
        else if (m.t === 'pause') setPaused(m.paused === true);
      } catch (error) {
        console.error('[gamestudio-host] input dispatch failed:', error);
      }
    },
  });
}
