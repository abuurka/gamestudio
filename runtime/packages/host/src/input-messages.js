// @ts-check
// SPDX-License-Identifier: MIT
// Validates editor → host input messages (docs/fork/SURFACE.md §4.3) before they reach the
// page. The preload only ever sees the clean shape returned here.

const KEY_TYPES = new Set(['down', 'up']);
const POINTER_TYPES = new Set(['move', 'down', 'up', 'wheel']);
const MODIFIERS = new Set(['Shift', 'Control', 'Alt', 'Meta']);
const MAX_COORD = 1e6;

/**
 * @typedef {{ t: 'input.key', type: 'down' | 'up', code: string, key: string, repeat: boolean, modifiers: string[] }} KeyInput
 * @typedef {{ t: 'input.pointer', type: 'move' | 'down' | 'up' | 'wheel', x: number | null, y: number | null,
 *             button: number, buttons: number, dx: number, dy: number, deltaX: number, deltaY: number, modifiers: string[] }} PointerInput
 * @typedef {{ t: 'focus', focused: boolean }} FocusInput
 * @typedef {KeyInput | PointerInput | FocusInput} PageInput
 */

/**
 * Returns a clean copy of an input message, or null when it is malformed.
 * @param {Record<string, unknown>} message
 * @returns {PageInput | null}
 */
export function sanitizeInput(message) {
  switch (message.t) {
    case 'input.key': {
      const { type, code, key } = message;
      if (typeof type !== 'string' || !KEY_TYPES.has(type)) return null;
      if (typeof code !== 'string' || code.length === 0 || code.length > 32) return null;
      return {
        t: 'input.key',
        type: /** @type {'down' | 'up'} */ (type),
        code,
        key: typeof key === 'string' && key.length <= 32 ? key : '',
        repeat: message.repeat === true,
        modifiers: modifiers(message.modifiers),
      };
    }
    case 'input.pointer': {
      const { type } = message;
      if (typeof type !== 'string' || !POINTER_TYPES.has(type)) return null;
      return {
        t: 'input.pointer',
        type: /** @type {PointerInput['type']} */ (type),
        x: coord(message.x),
        y: coord(message.y),
        button: integer(message.button, 0, 4),
        buttons: integer(message.buttons, 0, 31),
        dx: finite(message.dx),
        dy: finite(message.dy),
        deltaX: finite(message.deltaX),
        deltaY: finite(message.deltaY),
        modifiers: modifiers(message.modifiers),
      };
    }
    case 'focus':
      return { t: 'focus', focused: message.focused === true };
    default:
      return null;
  }
}

/** @param {unknown} value */
function modifiers(value) {
  return Array.isArray(value) ? value.filter((m) => typeof m === 'string' && MODIFIERS.has(m)) : [];
}

/** @param {unknown} value */
function coord(value) {
  return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= MAX_COORD ? value : null;
}

/** @param {unknown} value */
function finite(value) {
  return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= MAX_COORD ? value : 0;
}

/** @param {unknown} value @param {number} min @param {number} max */
function integer(value, min, max) {
  return typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max ? value : 0;
}
