# SPDX-License-Identifier: GPL-3.0-or-later
# GameStudio *development* build: loaded after blender_full.cmake and gamestudio.cmake.
#
# Turns off large subsystems that game development in the editor never uses, so clean
# builds and upgrades compile far less. Release builds (`gsfork build --config full`)
# keep everything. Kept on purpose: OpenSubdiv and exact booleans (modeling), IK solvers
# (rigging), audio, and FBX/OBJ/glTF import-export.

foreach(_gs_off
    WITH_CYCLES WITH_CYCLES_OSL WITH_CYCLES_EMBREE WITH_LLVM
    WITH_USD WITH_HYDRA WITH_MATERIALX
    WITH_ALEMBIC WITH_OPENVDB WITH_NANOVDB
    WITH_CODEC_FFMPEG WITH_FREESTYLE WITH_LIBMV
    WITH_MOD_FLUID WITH_MOD_OCEANSIM WITH_OPENIMAGEDENOISE
    WITH_XR_OPENXR WITH_POTRACE WITH_HARU WITH_INPUT_NDOF
    WITH_BLENDER_THUMBNAILER WITH_INTERNATIONAL
    # Build info (commit hash and date) changes on every build and forces a relink.
    WITH_BUILDINFO)
  set(${_gs_off} OFF CACHE BOOL "" FORCE)
endforeach()
