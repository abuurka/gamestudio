// SPDX-License-Identifier: GPL-3.0-or-later
// gs_gameview_probe: save a BGRA/RGBA frame as an RGB PNG (uncompressed deflate, no zlib),
// and summarize it so the probe can tell a rendered scene from a black frame.

#pragma once

#include <cstdint>
#include <string>

namespace gs::probe {

struct ImageSummary {
  double mean_luma = 0;      ///< 0..255
  double lit_fraction = 0;   ///< Pixels with luma > 24.
  int distinct_colors = 0;   ///< Among a 64-sample grid, quantized to 4 bits per channel.
};

/** `bgra`: true for B,G,R,A byte order, false for R,G,B,A. Rows top-down, `pitch` bytes apart. */
bool write_png(const std::string &path, const uint8_t *pixels, int width, int height, int pitch, bool bgra,
               std::string &error);

ImageSummary summarize(const uint8_t *pixels, int width, int height, int pitch, bool bgra);

}  // namespace gs::probe
