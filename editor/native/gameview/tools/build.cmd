@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
rem Builds gs_gameview and gs_gameview_probe with MSVC and Ninja (Release).
rem
rem   build.cmd [build-dir]        default build dir: %TEMP%\gs-gameview-build
rem
rem Finds Visual Studio 2022 with vswhere and sets up the x64 environment itself.
setlocal
set "SRC=%~dp0.."
set "BUILD=%~1"
if "%BUILD%"=="" set "BUILD=%TEMP%\gs-gameview-build"

if defined VCToolsInstallDir goto have_vs
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo error: vswhere.exe not found. Install Visual Studio 2022 with "Desktop development with C++".
  exit /b 1
)
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS=%%i"
if not defined VS (
  echo error: no Visual Studio installation with the x64 C++ tools was found.
  exit /b 1
)
rem vcvarsall looks vswhere up on PATH.
set "PATH=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer;%PATH%"
call "%VS%\VC\Auxiliary\Build\vcvarsall.bat" x64 >nul || exit /b 1
:have_vs

where ninja >nul 2>nul || set "PATH=%VS%\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
where cmake >nul 2>nul || set "PATH=%VS%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;%PATH%"

cmake -S "%SRC%" -B "%BUILD%" -G Ninja -DCMAKE_BUILD_TYPE=Release || exit /b 1
cmake --build "%BUILD%" || exit /b 1
echo built: %BUILD%\gs_gameview_probe.exe
