@echo off
REM Register the release-alert task. Polls every minute; the tool is idempotent.
REM   event_alert_install.cmd            10 minutes of warning (default)
REM   event_alert_install.cmd 30         30 minutes
setlocal
set LEAD=%1
if "%LEAD%"=="" set LEAD=10
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
  "(Get-Content '%~dp0event_alert.xml' -Raw)" ^
  " -replace 'PYTHONW_EXE','%PYEXE%'" ^
  " -replace 'PROJECT_DIR','%PROJ%'" ^
  " -replace 'TASK_USER','%USERDOMAIN%\%USERNAME%'" ^
  " -replace '--lead 10','--lead %LEAD%'" ^
  " | Set-Content '%TEMP%\dnfx_event_alert.xml' -Encoding Unicode"
schtasks /Create /TN "Financial News Release Alert" /XML "%TEMP%\dnfx_event_alert.xml" /F
del "%TEMP%\dnfx_event_alert.xml"
popd
endlocal
