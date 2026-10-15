# SPDX-License-Identifier: GPL-3.0-or-later
# GameStudio Editor build options. gsfork loads this after Blender's own
# build_files/cmake/config/blender_full.cmake, so only differences belong here.

# Upstream's unit tests are not part of our build. Our contract tests run against the binary.
set(WITH_GTESTS OFF CACHE BOOL "" FORCE)

# Seam S03: our own Windows app ID, so GameStudio gets its own taskbar entry and never
# takes over Blender's file registrations ("gamestudio.editor.5.2", "GameStudio Editor 5.2").
set(BLENDER_WIN_APPID_PREFIX "gamestudio.editor" CACHE STRING "" FORCE)
set(BLENDER_WIN_APPID_FRIENDLY_PREFIX "GameStudio Editor" CACHE STRING "" FORCE)

# Cycles stays on: game projects use it to bake lightmaps and textures.
# GPU kernels (CUDA/HIP/oneAPI) stay off, which blender_full.cmake already does.
