// @ts-check
// SPDX-License-Identifier: MIT
// Docked-frame flow control (docs/fork/SURFACE.md §5).
//
// Each Electron `paint` texture becomes one `surface.frame` message. The texture stays
// alive until the editor answers `surface.release`. At most `maxInFlight` frames are held:
// when a new frame would exceed that, the oldest is released here and announced with
// `surface.revoke`, so a slow editor drops frames instead of starving Chromium's pool.
// This module has no Electron dependency so it can be unit-tested.

/**
 * The parts of Electron's OffscreenSharedTexture this module uses.
 * @typedef {object} SharedTexture
 * @property {{
 *   widgetType: string,
 *   pixelFormat: string,
 *   codedSize: { width: number, height: number },
 *   handle: { ntHandle?: Buffer }
 * }} textureInfo
 * @property {() => void} release
 */

/** Electron pixel formats → SURFACE.md format names. */
const FORMATS = /** @type {Record<string, string>} */ ({ bgra: 'bgra8', rgba: 'rgba8', rgbaf16: 'rgba16f' });

export class FramePump {
  /**
   * @param {object} options
   * @param {number} options.maxInFlight
   * @param {(message: Record<string, unknown>) => void} options.send
   * @param {() => number} [options.now] monotonic milliseconds (the `ts` clock)
   */
  constructor({ maxInFlight, send, now = () => performance.now() }) {
    this.maxInFlight = Math.max(1, maxInFlight);
    this.send = send;
    this.now = now;
    /** False until the handshake completes and while paused: paints are released at once. */
    this.enabled = false;
    /** @type {Map<number, SharedTexture>} insertion order = frame order */
    this.inFlight = new Map();
    this.nextFrameId = 1;
    this.stats = { sent: 0, released: 0, revoked: 0, skipped: 0 };
  }

  /**
   * Handles one painted texture: sends it to the editor, or releases it at once.
   * @param {SharedTexture} texture
   * @returns {number} the frame id, or 0 when the texture was not sent
   */
  onPaint(texture) {
    const ts = this.now();
    const info = texture.textureInfo;
    const handle = info?.handle?.ntHandle;
    const format = FORMATS[info?.pixelFormat];
    if (!this.enabled || info?.widgetType !== 'frame' || !handle || handle.length < 8 || !format) {
      // Popups (<select> dropdowns) arrive as separate widgets; v1 does not composite them.
      safeRelease(texture);
      this.stats.skipped++;
      return 0;
    }

    const frameId = this.nextFrameId++;
    this.inFlight.set(frameId, texture);
    /** @type {number[]} */
    const revoked = [];
    for (const [oldId, oldTexture] of this.inFlight) {
      if (this.inFlight.size <= this.maxInFlight) break;
      this.inFlight.delete(oldId);
      safeRelease(oldTexture);
      revoked.push(oldId);
    }
    if (revoked.length > 0) {
      this.stats.revoked += revoked.length;
      this.send({ t: 'surface.revoke', frameIds: revoked });
    }
    this.send({
      t: 'surface.frame',
      frameId,
      handle: handle.readBigUInt64LE(0).toString(),
      width: info.codedSize.width,
      height: info.codedSize.height,
      format,
      ts: Math.round(ts * 1000) / 1000,
    });
    this.stats.sent++;
    return frameId;
  }

  /**
   * The editor is done with a frame. Unknown ids (already revoked) are ignored.
   * @param {unknown} frameId
   * @returns {boolean} true when a texture was released
   */
  release(frameId) {
    if (typeof frameId !== 'number') return false;
    const texture = this.inFlight.get(frameId);
    if (!texture) return false;
    this.inFlight.delete(frameId);
    safeRelease(texture);
    this.stats.released++;
    return true;
  }

  /** Releases everything still held, silently (the link is gone). */
  releaseAll() {
    for (const texture of this.inFlight.values()) safeRelease(texture);
    this.inFlight.clear();
  }

  /** Releases everything still held and tells the editor (the docked view is going away). */
  revokeAll() {
    const ids = [...this.inFlight.keys()];
    this.releaseAll();
    if (ids.length > 0) {
      this.stats.revoked += ids.length;
      this.send({ t: 'surface.revoke', frameIds: ids });
    }
  }
}

/** @param {SharedTexture} texture */
function safeRelease(texture) {
  try {
    texture.release();
  } catch {
    // Already released by Electron (e.g. the window closed).
  }
}
