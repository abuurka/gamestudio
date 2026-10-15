// SPDX-License-Identifier: GPL-3.0-or-later
// gs_gameview_probe: find or start the game's Vite dev server (docs/SPEC.md §5.1).

#pragma once

#include "gs_gameview/gameview.hh"

#include <cstdint>
#include <filesystem>
#include <string>

namespace gs::probe {

/** UTF-8 std::string <-> std::filesystem::path (C++20 made u8path/u8string use char8_t). */
inline std::filesystem::path utf8_path(const std::string &s)
{
  return std::filesystem::path(std::u8string(reinterpret_cast<const char8_t *>(s.data()), s.size()));
}
inline std::string path_utf8(const std::filesystem::path &p)
{
  const std::u8string s = p.u8string();
  return std::string(reinterpret_cast<const char *>(s.data()), s.size());
}

/** Contents of <project>/.gamestudio/session.json. */
struct DevSession {
  uint32_t pid = 0;
  std::string url;
  uint16_t editor_port = 0;  ///< Live-link hub, TCP NDJSON.
  std::string token;
};

bool read_session(const std::string &project, DevSession &out, std::string &error);

/** A session whose server process is alive and whose URL accepts connections. */
bool find_live_session(const std::string &project, DevSession &out);

/**
 * Starts `npx vite --port <free> --strictPort` in `project` (in a kill-on-close job) and
 * waits until it has written a fresh session.json and accepts connections.
 */
bool start_vite(const std::string &project, const std::string &log_file, gameview::HostProcess &process,
                DevSession &session, std::string &error);

/** Removes session.json if it still holds `token` (the server was killed, not stopped). */
void remove_session_if_ours(const std::string &project, const std::string &token);

/** TCP connect test to a http://host:port/ URL (tries IPv6 and IPv4 for localhost). */
bool url_reachable(const std::string &url, int timeout_ms);

}  // namespace gs::probe
