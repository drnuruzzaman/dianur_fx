@echo off
REM Register the Rayo Scalper, this project's only signal source. Runs every 5
REM minutes; which cells it watches is configslerts.json (scalper.watch), read
REM fresh on every run -- edit that, not this.
setlocal
pushd "%~dp0..\.."
set PROJ=%CD%
REM PYTHONW, NOT PYTHON. The console build opens a window on every
REM run, and this task polls once a MINUTE. pythonw has no console at
REM all; tools\_run_quiet.py points its output at logs\ so nothing is
REM lost in exchange. Falls back to python.exe if pythonw is missing --
REM a visible task still beats a task that will not register.
for /f "delims=" %%P in ('where pythonw') do set PYEXE=%%P& goto :got
:got
if "%PYEXE%"=="" for /f "delims=" %%P in ('where python') do set PYEXE=%%P& goto :got2
:got2
powershell -NoProfile -Command ^
  "(Get-Content '%~dp0rayo_scalper.xml' -Raw)" ^
  " -replace 'PYTHONW_EXE','%PYEXE%'" ^
  " -replace 'PROJECT_DIR','%PROJ%'" ^
  " -replace 'TASK_USER','%USERDOMAIN%\%USERNAME%'" ^
  " | Set-Content '%TEMP%\dnfx_rayo_scalper.xml' -Encoding Unicode"
schtasks /Create /TN "DiaNurFx-Rayo-Scalper" /XML "%TEMP%\dnfx_rayo_scalper.xml" /F
del "%TEMP%\dnfx_rayo_scalper.xml"
popd
endlocal
