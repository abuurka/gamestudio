// SPDX-License-Identifier: GPL-3.0-or-later
// gs_gameview_probe: a minimal live-link client (docs/SPEC.md §5). The probe connects to the
// dev server's hub as an editor, finds the game running in its runtime host, and reads
// entity positions with state.request, to prove that synthetic input moved the player.

#pragma once

#include <array>
#include <string>
#include <vector>
#include <string_view>

#include "json.hh"

namespace gs::probe {

namespace json = gs::gameview::json;

class LinkClient {
 public:
  LinkClient() = default;
  ~LinkClient();
  LinkClient(const LinkClient &) = delete;
  LinkClient &operator=(const LinkClient &) = delete;

  /** Connects to 127.0.0.1:<port>, sends hello as an editor and waits for welcome. */
  bool connect(uint16_t port, const std::string &token, std::string &error);
  /** Waits until a game peer whose client string contains `marker` is connected. */
  bool find_game(std::string_view marker, int timeout_ms, std::string &peer, std::string &error);
  /** World position of the first entity named `name` with component `component`. */
  bool position(const std::string &peer, std::string_view component, std::string_view name,
                std::array<double, 3> &out, std::string &error);
  /** Reads (and drops) messages for `ms`, keeping the game's `stats` reports. */
  void listen(int ms);
  /** Median of the fps values the game reported in `stats` (0 if none). */
  double game_fps() const;

 private:
  bool send(const std::string &line);
  bool read(json::Value &out, int timeout_ms);
  bool scan_peers(const json::Value &list, std::string_view marker, std::string &peer);

  uintptr_t socket_ = ~uintptr_t(0);
  json::Value welcome_;
  std::string game_peer_;
  std::vector<double> game_fps_;
  std::string buffer_;
  int next_id_ = 1;
};

}  // namespace gs::probe
