@echo off
REM Register the command bot. Long-polls, so /status answers in about a second.
setlocal
pushd "%~dp0..\.."
set PROJ=%CD%
for /f "delims=" %%P in ('where pythonw') do set PYEXE=%%P& goto :got
:got
if "%PYEXE%"=="" for /f "delims=" %%P in ('where python') do set PYEXE=%%P& goto :got2
:got2
powershell -NoProfile -Command ^
  "(Get-Content '%~dp0telegram_bot.xml' -Raw)" ^
  " -replace 'PYTHONW_EXE','%PYEXE%'" ^
  " -replace 'PROJECT_DIR','%PROJ%'" ^
  " -replace 'TASK_USER','%USERDOMAIN%\%USERNAME%'" ^
  " | Set-Content '%TEMP%\dnfx_tgbot.xml' -Encoding Unicode"
schtasks /Create /TN "DiaNurFx Telegram Bot" /XML "%TEMP%\dnfx_tgbot.xml" /F
del "%TEMP%\dnfx_tgbot.xml"
schtasks /Run /TN "DiaNurFx Telegram Bot"
popd
endlocal
