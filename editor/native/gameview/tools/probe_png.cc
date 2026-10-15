// SPDX-License-Identifier: GPL-3.0-or-later

#include "probe_png.hh"

#include <array>
#include <fstream>
#include <set>
#include <vector>

#include "probe_devserver.hh"  // utf8_path

namespace gs::probe {

namespace {

uint32_t crc32(const uint8_t *data, size_t size, uint32_t crc = 0)
{
  static const std::array<uint32_t, 256> table = [] {
    std::array<uint32_t, 256> t{};
    for (uint32_t i = 0; i < 256; i++) {
      uint32_t c = i;
      for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
      t[i] = c;
    }
    return t;
  }();
  crc = ~crc;
  for (size_t i = 0; i < size; i++) crc = table[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
  return ~crc;
}

void put32(std::vector<uint8_t> &out, uint32_t v)
{
  out.push_back(uint8_t(v >> 24));
  out.push_back(uint8_t(v >> 16));
  out.push_back(uint8_t(v >> 8));
  out.push_back(uint8_t(v));
}

void chunk(std::ofstream &file, const char type[4], const std::vector<uint8_t> &data)
{
  std::vector<uint8_t> body(type, type + 4);
  body.insert(body.end(), data.begin(), data.end());
  std::vector<uint8_t> head;
  put32(head, uint32_t(data.size()));
  std::vector<uint8_t> tail;
  put32(tail, crc32(body.data(), body.size()));
  file.write(reinterpret_cast<const char *>(head.data()), 4);
  file.write(reinterpret_cast<const char *>(body.data()), std::streamsize(body.size()));
  file.write(reinterpret_cast<const char *>(tail.data()), 4);
}

void rgb_at(const uint8_t *p, bool bgra, uint8_t rgb[3])
{
  rgb[0] = bgra ? p[2] : p[0];
  rgb[1] = p[1];
  rgb[2] = bgra ? p[0] : p[2];
}

}  // namespace

bool write_png(const std::string &path, const uint8_t *pixels, int width, int height, int pitch, bool bgra,
               std::string &error)
{
  // Raw scanlines: filter byte 0 + RGB.
  std::vector<uint8_t> raw;
  raw.reserve(size_t(height) * (size_t(width) * 3 + 1));
  for (int y = 0; y < height; y++) {
    raw.push_back(0);
    const uint8_t *row = pixels + size_t(y) * size_t(pitch);
    for (int x = 0; x < width; x++) {
      uint8_t rgb[3];
      rgb_at(row + size_t(x) * 4, bgra, rgb);
      raw.insert(raw.end(), rgb, rgb + 3);
    }
  }
  // zlib stream of stored deflate blocks.
  std::vector<uint8_t> z = {0x78, 0x01};
  for (size_t pos = 0; pos < raw.size();) {
    const size_t n = std::min<size_t>(65535, raw.size() - pos);
    z.push_back(pos + n == raw.size() ? 1 : 0);
    z.push_back(uint8_t(n));
    z.push_back(uint8_t(n >> 8));
    z.push_back(uint8_t(~n));
    z.push_back(uint8_t(~n >> 8));
    z.insert(z.end(), raw.begin() + std::ptrdiff_t(pos), raw.begin() + std::ptrdiff_t(pos + n));
    pos += n;
  }
  uint32_t a = 1, b = 0;
  for (const uint8_t byte : raw) {
    a = (a + byte) % 65521;
    b = (b + a) % 65521;
  }
  put32(z, (b << 16) | a);

  std::ofstream file(utf8_path(path), std::ios::binary);
  if (!file) {
    error = "cannot write " + path;
    return false;
  }
  static const uint8_t signature[] = {0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};
  file.write(reinterpret_cast<const char *>(signature), sizeof(signature));
  std::vector<uint8_t> ihdr;
  put32(ihdr, uint32_t(width));
  put32(ihdr, uint32_t(height));
  ihdr.insert(ihdr.end(), {8, 2, 0, 0, 0});  // 8-bit RGB, no interlace
  chunk(file, "IHDR", ihdr);
  chunk(file, "IDAT", z);
  chunk(file, "IEND", {});
  return bool(file);
}

ImageSummary summarize(const uint8_t *pixels, int width, int height, int pitch, bool bgra)
{
  ImageSummary s;
  double sum = 0;
  size_t lit = 0, count = 0;
  for (int y = 0; y < height; y += 4) {
    const uint8_t *row = pixels + size_t(y) * size_t(pitch);
    for (int x = 0; x < width; x += 4) {
      uint8_t rgb[3];
      rgb_at(row + size_t(x) * 4, bgra, rgb);
      const double luma = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
      sum += luma;
      lit += luma > 24;
      count++;
    }
  }
  s.mean_luma = count ? sum / double(count) : 0;
  s.lit_fraction = count ? double(lit) / double(count) : 0;
  std::set<int> colors;
  for (int gy = 0; gy < 8; gy++) {
    for (int gx = 0; gx < 8; gx++) {
      const int x = (2 * gx + 1) * width / 16, y = (2 * gy + 1) * height / 16;
      uint8_t rgb[3];
      rgb_at(pixels + size_t(y) * size_t(pitch) + size_t(x) * 4, bgra, rgb);
      colors.insert((rgb[0] >> 4) << 8 | (rgb[1] >> 4) << 4 | (rgb[2] >> 4));
    }
  }
  s.distinct_colors = int(colors.size());
  return s;
}

}  // namespace gs::probe
