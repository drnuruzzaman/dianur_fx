@echo off
REM Register the signal alerter. Polls every minute; which cells it watches is
REM configs\signals.yaml, read fresh on every poll -- edit that, not this.
setlocal
pushd "%~dp0..\.."
set PROJ=%CD%
for /f "delims=" %%P in ('where python') do set PYEXE=%%P& goto :got
:got
powershell -NoProfile -Command ^
  "(Get-Content '%~dp0signal_alert.xml' -Raw)" ^
  " -replace 'PYTHON_EXE','%PYEXE%'" ^
  " -replace 'PROJECT_DIR','%PROJ%'" ^
  " -replace 'TASK_USER','%USERDOMAIN%\%USERNAME%'" ^
  " | Set-Content '%TEMP%\dnfx_signal_alert.xml' -Encoding Unicode"
schtasks /Create /TN "DiaNurFx Signal Alert" /XML "%TEMP%\dnfx_signal_alert.xml" /F
del "%TEMP%\dnfx_signal_alert.xml"
popd
endlocal
