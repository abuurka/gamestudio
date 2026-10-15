# SPDX-License-Identifier: GPL-3.0-or-later
#
# gs_gameview: editor side of the docked Game View (docs/fork/SURFACE.md).
# Blender-independent C++20. Use standalone (tools/build.cmd) or from a parent project:
#
#   add_subdirectory(gameview)
#   target_link_libraries(<target> PRIVATE gs_gameview)
#
# Targets: gs_gameview (static library), gs_gameview_probe (test program, Windows,
# built by default only when this directory is the top-level project).

cmake_minimum_required(VERSION 3.21)
project(gs_gameview LANGUAGES CXX)

option(GS_GAMEVIEW_BUILD_PROBE "Build the gs_gameview_probe test program" ${PROJECT_IS_TOP_LEVEL})

add_library(gs_gameview STATIC
  include/gs_gameview/gameview.hh
  src/clock_sync.hh
  src/json.cc
  src/json.hh
  src/protocol.cc
  src/protocol.hh
  src/surface.cc
  src/surface_impl.hh
  src/triple_buffer.hh
)
target_include_directories(gs_gameview PUBLIC include PRIVATE src)
target_compile_features(gs_gameview PUBLIC cxx_std_20)

if(WIN32)
  target_sources(gs_gameview PRIVATE
    src/win/d3d11_bridge.cc
    src/win/d3d11_bridge.hh
    src/win/process_win.cc
    src/win/win_util.cc
    src/win/win_util.hh
    src/win/worker_win.cc
  )
  target_compile_definitions(gs_gameview PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX UNICODE _UNICODE)
  target_link_libraries(gs_gameview PRIVATE d3d11 dxgi ws2_32 bcrypt)
else()
  target_sources(gs_gameview PRIVATE src/stub/platform_stub.cc)
endif()

if(MSVC)
  target_compile_options(gs_gameview PRIVATE /W4 /permissive- /utf-8)
else()
  target_compile_options(gs_gameview PRIVATE -Wall -Wextra)
endif()

if(GS_GAMEVIEW_BUILD_PROBE AND WIN32)
  add_executable(gs_gameview_probe
    tools/probe.cc
    tools/probe_devserver.cc
    tools/probe_devserver.hh
    tools/probe_link.cc
    tools/probe_link.hh
    tools/probe_png.cc
    tools/probe_png.hh
  )
  # The probe reuses the library's internal JSON parser.
  target_include_directories(gs_gameview_probe PRIVATE src)
  target_link_libraries(gs_gameview_probe PRIVATE gs_gameview ws2_32 dxgi)
  target_compile_definitions(gs_gameview_probe PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX UNICODE _UNICODE
    GS_REPO_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/../../..")
  if(MSVC)
    target_compile_options(gs_gameview_probe PRIVATE /W4 /permissive- /utf-8)
  endif()
endif()
