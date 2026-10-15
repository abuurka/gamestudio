// @ts-check
// SPDX-License-Identifier: MIT
// The game stream's wire format: the stream host (bin/gamestudio-stream.js) and Blender
// (blender/gamestudio/core/stream.py) on one TCP connection to 127.0.0.1.
//
//   startup   the host prints one JSON line to stdout, {"port": N, "token": "..."}, then listens
//   message   u32 big-endian payload length, u8 type, payload (the length leaves out the type byte)
//   'J'       Blender -> host: a JSON command (below). The first one must be hello with the token.
//   'S'       host -> Blender: status JSON {status: 'starting' | 'ready' | 'error' | 'stopped',
//             message, fps, mode, audio, renderer, gpu, url, agent?, ...}, on changes and once a
//             second (agent: the mark at the end of the page's user agent)
//   'F'       host -> Blender: a frame, a 24-byte header and the pixels:
//               u32 width, u32 height, u32 frame number, u8 format, 3 bytes reserved,
//               f64 when the browser drew it (ms since the epoch, the host's clock)
//             format 1 is a JPEG file (the browser's screencast, passed through), format 0 raw
//             RGBA8 with rows top to bottom.
//
// Commands: {type: 'hello', token, formats?: ['jpeg', 'rgba']}, {type: 'size', width, height, scale?},
// {type: 'quality', quality?, max_fps?}, {type: 'mouse', kind: 'move' | 'down' | 'up' | 'wheel',
// x, y, button?, dx?, dy?, shift?, ctrl?, alt?} (x, y in frame pixels from the top left; button 0
// left, 1 middle, 2 right; dx, dy wheel pixels), {type: 'key', kind: 'down' | 'up', code, key, shift?,
// ctrl?, alt?, repeat?} (DOM KeyboardEvent.code and .key), {type: 'text', text}, {type: 'release'}
// (lift every held key and button), {type: 'pause', suspend?} (no frames; suspend: also hide the
// page once it has loaded, so it renders nothing: `suspended` in the status), {type: 'resume'},
// {type: 'navigate', url}
// (load the game at this http(s) address; a host started without one waits for it), {type: 'stop'}.

export const MESSAGE_HEADER = 5;
export const FRAME_HEADER = 24;
/** Larger messages are a protocol error (an 8K RGBA frame is 133 MB; JPEG frames are far smaller). */
export const MAX_MESSAGE = 64 * 1024 * 1024;
export const TYPE = Object.freeze({ JSON: 'J', STATUS: 'S', FRAME: 'F' });
export const FORMAT = Object.freeze({ RGBA: 0, JPEG: 1 });

/**
 * @param {string} type one character
 * @param {Uint8Array} payload
 * @returns {Buffer}
 */
export function encodeMessage(type, payload) {
  const header = Buffer.allocUnsafe(MESSAGE_HEADER);
  header.writeUInt32BE(payload.length, 0);
  header.writeUInt8(type.charCodeAt(0), 4);
  return Buffer.concat([header, payload]);
}

/** @param {string} type @param {unknown} value */
export function encodeJson(type, value) {
  return encodeMessage(type, Buffer.from(JSON.stringify(value), 'utf8'));
}

/**
 * The message and frame headers of an 'F' message; the pixels follow (written separately, so a
 * frame is never copied).
 * @param {{ width: number, height: number, frame: number, format: number, timestamp: number }} frame
 * @param {number} pixelBytes
 */
export function frameHeader({ width, height, frame, format, timestamp }, pixelBytes) {
  const out = Buffer.alloc(MESSAGE_HEADER + FRAME_HEADER);
  out.writeUInt32BE(FRAME_HEADER + pixelBytes, 0);
  out.writeUInt8(TYPE.FRAME.charCodeAt(0), 4);
  out.writeUInt32BE(width >>> 0, 5);
  out.writeUInt32BE(height >>> 0, 9);
  out.writeUInt32BE(frame >>> 0, 13);
  out.writeUInt8(format, 17);
  out.writeDoubleBE(timestamp, 21);
  return out;
}

/** @param {{ width: number, height: number, frame: number, format: number, timestamp: number }} frame @param {Uint8Array} pixels */
export function encodeFrame(frame, pixels) {
  return Buffer.concat([frameHeader(frame, pixels.length), pixels]);
}

/**
 * @param {Buffer} payload the payload of an 'F' message
 * @returns {{ width: number, height: number, frame: number, format: number, timestamp: number, pixels: Buffer }}
 */
export function decodeFrame(payload) {
  if (payload.length < FRAME_HEADER) throw new Error(`Frame message too short (${payload.length} bytes)`);
  return {
    width: payload.readUInt32BE(0),
    height: payload.readUInt32BE(4),
    frame: payload.readUInt32BE(8),
    format: payload.readUInt8(12),
    timestamp: payload.readDoubleBE(16),
    pixels: payload.subarray(FRAME_HEADER),
  };
}

/**
 * Splits a byte stream into messages. Chunks are kept as they arrive and joined once per message,
 * so a large frame in many TCP chunks is copied once.
 */
export class MessageReader {
  constructor(maxMessage = MAX_MESSAGE) {
    this.maxMessage = maxMessage;
    /** @type {Buffer[]} */
    this.chunks = [];
    this.length = 0;
  }

  /**
   * @param {Buffer} chunk
   * @returns {{ type: string, payload: Buffer }[]}
   */
  push(chunk) {
    this.chunks.push(chunk);
    this.length += chunk.length;
    /** @type {{ type: string, payload: Buffer }[]} */
    const out = [];
    for (;;) {
      if (this.length < MESSAGE_HEADER) return out;
      const head = this.#peek(MESSAGE_HEADER);
      const size = head.readUInt32BE(0);
      if (size > this.maxMessage) throw new Error(`Stream message too large (${size} bytes)`);
      if (this.length < MESSAGE_HEADER + size) return out;
      const whole = this.#take(MESSAGE_HEADER + size);
      out.push({ type: String.fromCharCode(whole[4]), payload: whole.subarray(MESSAGE_HEADER) });
    }
  }

  /** @param {number} n */
  #peek(n) {
    if (this.chunks[0].length >= n) return this.chunks[0];
    const joined = Buffer.concat(this.chunks);
    this.chunks = [joined];
    return joined;
  }

  /** @param {number} n */
  #take(n) {
    let first = this.chunks[0];
    if (first.length < n) {
      first = Buffer.concat(this.chunks);
      this.chunks = [first];
    }
    const taken = first.subarray(0, n);
    if (first.length === n) this.chunks.shift();
    else this.chunks[0] = first.subarray(n);
    this.length -= n;
    return taken;
  }
}

/**
 * Width and height of a JPEG from its SOF marker, or null.
 * @param {Uint8Array} jpeg
 * @returns {[number, number] | null}
 */
export function jpegSize(jpeg) {
  if (jpeg.length < 4 || jpeg[0] !== 0xff || jpeg[1] !== 0xd8) return null;
  let i = 2;
  while (i + 9 < jpeg.length) {
    if (jpeg[i] !== 0xff) return null;
    const marker = jpeg[i + 1];
    if (marker === 0xff) {
      i++;
      continue;
    }
    const length = (jpeg[i + 2] << 8) | jpeg[i + 3];
    // SOF0..SOF15 except DHT (C4), JPG (C8) and DAC (CC).
    if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
      return [(jpeg[i + 7] << 8) | jpeg[i + 8], (jpeg[i + 5] << 8) | jpeg[i + 6]];
    }
    i += 2 + length;
  }
  return null;
}

// --- Input -------------------------------------------------------------------------------------

/** CDP modifier bits. */
const ALT = 1;
const CTRL = 2;
const SHIFT = 8;

/** @param {{ shift?: boolean, ctrl?: boolean, alt?: boolean }} cmd */
export function modifiers(cmd) {
  return (cmd.alt ? ALT : 0) | (cmd.ctrl ? CTRL : 0) | (cmd.shift ? SHIFT : 0);
}

/** Windows virtual key codes of DOM codes (Chromium needs them for Enter, arrows, Backspace, ...). */
const NAMED_KEYS = /** @type {Record<string, number>} */ ({
  Backspace: 8, Tab: 9, Enter: 13, NumpadEnter: 13, ShiftLeft: 16, ShiftRight: 16, ControlLeft: 17,
  ControlRight: 17, AltLeft: 18, AltRight: 18, Pause: 19, CapsLock: 20, Escape: 27, Space: 32,
  PageUp: 33, PageDown: 34, End: 35, Home: 36, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39,
  ArrowDown: 40, PrintScreen: 44, Insert: 45, Delete: 46, MetaLeft: 91, MetaRight: 92,
  ContextMenu: 93, NumpadMultiply: 106, NumpadAdd: 107, NumpadSubtract: 109, NumpadDecimal: 110,
  NumpadDivide: 111, NumLock: 144, ScrollLock: 145, Semicolon: 186, Equal: 187, Comma: 188,
  Minus: 189, Period: 190, Slash: 191, Backquote: 192, BracketLeft: 219, Backslash: 220,
  BracketRight: 221, Quote: 222, IntlBackslash: 226,
});

/** @param {string} code DOM KeyboardEvent.code */
export function virtualKeyCode(code) {
  if (/^Key[A-Z]$/.test(code)) return code.charCodeAt(3);
  if (/^Digit[0-9]$/.test(code)) return code.charCodeAt(5);
  if (/^Numpad[0-9]$/.test(code)) return 96 + Number(code[6]);
  const f = /^F([1-9]|1[0-9]|2[0-4])$/.exec(code);
  if (f) return 111 + Number(f[1]);
  return NAMED_KEYS[code] ?? 0;
}

/** @param {string} code */
function keyLocation(code) {
  if (code.startsWith('Numpad')) return 3;
  if (/^(Shift|Control|Alt|Meta)Left$/.test(code)) return 1;
  if (/^(Shift|Control|Alt|Meta)Right$/.test(code)) return 2;
  return 0;
}

/**
 * Input.dispatchKeyEvent parameters for a key command, or null when it is not one.
 * @param {any} cmd
 */
export function keyEventParams(cmd) {
  if (!cmd || (cmd.kind !== 'down' && cmd.kind !== 'up') || typeof cmd.code !== 'string') return null;
  const code = cmd.code;
  const key = typeof cmd.key === 'string' && cmd.key ? cmd.key : code;
  const vk = virtualKeyCode(code);
  /** @type {Record<string, unknown>} */
  const params = {
    type: 'keyUp', modifiers: modifiers(cmd), code, key, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk,
    location: keyLocation(code),
  };
  if (cmd.kind === 'down') {
    // Printable keys carry text, so they also type into focused inputs (keypress, input events).
    const text = key === 'Enter' ? '\r' : [...key].length === 1 && !cmd.ctrl && !cmd.alt ? key : '';
    params.type = text ? 'keyDown' : 'rawKeyDown';
    if (text) params.text = params.unmodifiedText = text;
    if (cmd.repeat) params.autoRepeat = true;
  }
  return params;
}

const BUTTONS = /** @type {const} */ (['left', 'middle', 'right']);
/** DOM MouseEvent.buttons bits of buttons 0 (left), 1 (middle), 2 (right). */
const BUTTON_BITS = [1, 4, 2];
const MOUSE_TYPES = /** @type {Record<string, string>} */ ({ move: 'mouseMoved', down: 'mousePressed', up: 'mouseReleased', wheel: 'mouseWheel' });

/**
 * Input.dispatchMouseEvent parameters for a mouse command, or null when it is not one. `state.buttons`
 * (DOM bits of the held buttons) is updated; `scaleX`/`scaleY` turn frame pixels into CSS pixels.
 * @param {any} cmd
 * @param {{ buttons: number }} state
 * @param {number} scaleX
 * @param {number} scaleY
 */
export function mouseEventParams(cmd, state, scaleX = 1, scaleY = 1) {
  const type = MOUSE_TYPES[cmd?.kind];
  if (!type || !Number.isFinite(cmd.x) || !Number.isFinite(cmd.y)) return null;
  const index = Number.isInteger(cmd.button) && cmd.button >= 0 && cmd.button <= 2 ? cmd.button : 0;
  if (cmd.kind === 'down') state.buttons |= BUTTON_BITS[index];
  if (cmd.kind === 'up') state.buttons &= ~BUTTON_BITS[index];
  const held = BUTTON_BITS.findIndex((bit) => (state.buttons & bit) !== 0);
  /** @type {Record<string, unknown>} */
  const params = {
    type, x: cmd.x * scaleX, y: cmd.y * scaleY, modifiers: modifiers(cmd), buttons: state.buttons,
    button: cmd.kind === 'down' || cmd.kind === 'up' ? BUTTONS[index] : held >= 0 ? BUTTONS[held] : 'none',
  };
  if (cmd.kind === 'down' || cmd.kind === 'up') params.clickCount = 1;
  if (cmd.kind === 'wheel') {
    params.deltaX = Number.isFinite(cmd.dx) ? cmd.dx : 0;
    params.deltaY = Number.isFinite(cmd.dy) ? cmd.dy : 0;
  }
  return params;
}

/**
 * Lets frames through at no more than `maxFps` on average: a frame passes when it is due, and the
 * next is due one interval later (so a 75 Hz source capped at 60 sends 60, not 37.5).
 */
export class RateLimiter {
  /** @param {number} maxFps 0: no limit */
  constructor(maxFps = 0) {
    this.interval = 0;
    this.due = 0;
    this.set(maxFps);
  }

  /** @param {number} maxFps */
  set(maxFps) {
    this.interval = maxFps > 0 ? 1000 / maxFps : 0;
    this.due = 0;
  }

  /** @param {number} now ms */
  allow(now) {
    if (!this.interval) return true;
    if (now < this.due) return false;
    this.due = Math.max(this.due, now - this.interval) + this.interval;
    return true;
  }
}
