// SPDX-License-Identifier: GPL-3.0-or-later

#include "win_util.hh"

#include <bcrypt.h>

#include <cstdio>

#include "../surface_impl.hh"

namespace gs::gameview {

namespace win {

std::wstring widen(std::string_view utf8)
{
  if (utf8.empty()) {
    return {};
  }
  const int n = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), int(utf8.size()), nullptr, 0);
  std::wstring out(size_t(n), L'\0');
  MultiByteToWideChar(CP_UTF8, 0, utf8.data(), int(utf8.size()), out.data(), n);
  return out;
}

std::string narrow(std::wstring_view wide)
{
  if (wide.empty()) {
    return {};
  }
  const int n = WideCharToMultiByte(CP_UTF8, 0, wide.data(), int(wide.size()), nullptr, 0, nullptr, nullptr);
  std::string out(size_t(n), '\0');
  WideCharToMultiByte(CP_UTF8, 0, wide.data(), int(wide.size()), out.data(), n, nullptr, nullptr);
  return out;
}

std::string error_text(std::string_view what, unsigned long code)
{
  wchar_t *buffer = nullptr;
  const DWORD n = FormatMessageW(
      FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, nullptr, code,
      0, reinterpret_cast<wchar_t *>(&buffer), 0, nullptr);
  std::string message = n ? narrow(std::wstring_view(buffer, n)) : std::string("unknown error");
  if (buffer) {
    LocalFree(buffer);
  }
  while (!message.empty() && (message.back() == '\n' || message.back() == '\r' || message.back() == ' ' ||
                              message.back() == '.'))
  {
    message.pop_back();
  }
  char hex[16];
  std::snprintf(hex, sizeof(hex), "0x%08lX", code);
  return std::string(what) + ": " + message + " (" + hex + ")";
}

void close_handle(HANDLE &handle) noexcept
{
  if (handle && handle != INVALID_HANDLE_VALUE) {
    CloseHandle(handle);
  }
  handle = nullptr;
}

}  // namespace win

bool secure_random(uint8_t *out, size_t size) noexcept
{
  return BCryptGenRandom(nullptr, out, ULONG(size), BCRYPT_USE_SYSTEM_PREFERRED_RNG) == 0;
}

}  // namespace gs::gameview
