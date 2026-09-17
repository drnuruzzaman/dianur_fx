@echo off
REM Register the live-ticket scorer. Runs every 5 minutes (:02, :07, ...), reads
REM data\scalper_journal.jsonl, writes data\scalper_scored.jsonl. Sends nothing
REM to anybody -- it is a ledger, not an alerter.
setlocal
pushd "%~dp0..\.."
set PROJ=%CD%
for /f "delims=" %%P in ('where pythonw') do set PYEXE=%%P& goto :got
:got
if "%PYEXE%"=="" for /f "delims=" %%P in ('where python') do set PYEXE=%%P& goto :got2
:got2
powershell -NoProfile -Command ^
  "(Get-Content '%~dp0score_scalper.xml' -Raw)" ^
  " -replace 'PYTHONW_EXE','%PYEXE%'" ^
  " -replace 'PROJECT_DIR','%PROJ%'" ^
  " -replace 'TASK_USER','%USERDOMAIN%\%USERNAME%'" ^
  " | Set-Content '%TEMP%\dnfx_score_scalper.xml' -Encoding Unicode"
schtasks /Create /TN "DiaNurFx-Score-Scalper" /XML "%TEMP%\dnfx_score_scalper.xml" /F
del "%TEMP%\dnfx_score_scalper.xml"
popd
endlocal
