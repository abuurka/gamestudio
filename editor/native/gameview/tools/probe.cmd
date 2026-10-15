@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
rem Builds gs_gameview_probe and runs it against the demo game (npm run gameview:probe).
rem
rem   probe.cmd [probe options]     e.g.  probe.cmd --seconds 20   or   probe.cmd --detach-test
rem
rem Build dir: %GS_GAMEVIEW_BUILD% or %TEMP%\gs-gameview-build.
setlocal
set "BUILD=%GS_GAMEVIEW_BUILD%"
if "%BUILD%"=="" set "BUILD=%TEMP%\gs-gameview-build"
set "REPO=%~dp0..\..\..\.."

rem Electron 44 downloads its binary on first use, not at npm install.
pushd "%REPO%"
node -e "require('electron')" >nul || (popd & echo error: could not install Electron; run  npx install-electron  in %REPO% & exit /b 1)
popd

call "%~dp0build.cmd" "%BUILD%" || exit /b 1
"%BUILD%\gs_gameview_probe.exe" %*
