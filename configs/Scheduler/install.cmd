@echo off
REM Register the weekly calendar refresh from calendar_refresh.xml.
REM Re-run after editing that file; /F replaces the task rather than duplicating it.
REM   install.cmd            -> Monday 08:00
REM   install.cmd 07:30      -> Monday 07:30
setlocal
set "AT=%~1"
if "%AT%"=="" set "AT=08:00"
pushd "%~dp0..\.."
python tools\refresh_calendar.py --install-task --at %AT%
popd
endlocal
