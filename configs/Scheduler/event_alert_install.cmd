@echo off
REM Register the release-alert task. Polls every minute; the tool is idempotent.
REM   event_alert_install.cmd            10 minutes of warning (default)
REM   event_alert_install.cmd 30         30 minutes
setlocal
set LEAD=%1
if "%LEAD%"=="" set LEAD=10
pushd "%~dp0..\.."
set PROJ=%CD%
for /f "delims=" %%P in ('where python') do set PYEXE=%%P& goto :got
:got
powershell -NoProfile -Command ^
  "(Get-Content '%~dp0event_alert.xml' -Raw)" ^
  " -replace 'PYTHON_EXE','%PYEXE%'" ^
  " -replace 'PROJECT_DIR','%PROJ%'" ^
  " -replace 'TASK_USER','%USERDOMAIN%\%USERNAME%'" ^
  " -replace '--lead 10','--lead %LEAD%'" ^
  " | Set-Content '%TEMP%\dnfx_event_alert.xml' -Encoding Unicode"
schtasks /Create /TN "DiaNurFx Release Alert" /XML "%TEMP%\dnfx_event_alert.xml" /F
del "%TEMP%\dnfx_event_alert.xml"
popd
endlocal
