// SPDX-License-Identifier: GPL-3.0-or-later
// Win32 includes and small helpers shared by the Windows backend.

#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#  define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#  define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <string>
#include <string_view>

namespace gs::gameview::win {

std::wstring widen(std::string_view utf8);
std::string narrow(std::wstring_view wide);

/** "<what>: <system message> (0x%08X)" for a Win32 or HRESULT/Winsock error code. */
std::string error_text(std::string_view what, unsigned long code);

/** Closes a handle that may be null or INVALID_HANDLE_VALUE, and nulls it. */
void close_handle(HANDLE &handle) noexcept;

}  // namespace gs::gameview::win
