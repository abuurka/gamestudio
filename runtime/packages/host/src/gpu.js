// @ts-check
// SPDX-License-Identifier: MIT
// Which GPU Chromium renders on, for the hello message (docs/fork/SURFACE.md §3.2).
// Electron does not expose the adapter LUID, so the editor chooses the adapter, passes
// its LUID with --adapter-luid, and main.js pins Chromium to it (--use-adapter-luid).
// The vendor/device ids reported here let the editor double-check that choice.

const GPU_INFO_TIMEOUT_MS = 3000;

/**
 * @typedef {{ vendorId: number, deviceId: number, revision?: number, subSysId?: number, description?: string }} GpuDescription
 */

/**
 * @param {Electron.App} app
 * @returns {Promise<GpuDescription | null>} the active GPU, or null if Chromium did not say
 */
export async function describeActiveGpu(app) {
  try {
    const info = /** @type {{ gpuDevice?: Array<Record<string, unknown>> }} */ (
      await Promise.race([
        app.getGPUInfo('basic'),
        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), GPU_INFO_TIMEOUT_MS)),
      ])
    );
    const devices = info.gpuDevice ?? [];
    const device = devices.find((d) => d.active === true) ?? devices[0];
    if (!device || typeof device.vendorId !== 'number' || typeof device.deviceId !== 'number') return null;
    /** @type {GpuDescription} */
    const result = { vendorId: device.vendorId, deviceId: device.deviceId };
    if (typeof device.revision === 'number') result.revision = device.revision;
    if (typeof device.subSysId === 'number') result.subSysId = device.subSysId >>> 0;
    if (typeof device.deviceString === 'string') result.description = device.deviceString;
    return result;
  } catch {
    return null;
  }
}
