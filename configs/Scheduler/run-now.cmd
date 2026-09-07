@echo off
REM Refresh immediately, without waiting for Monday. Same script the task runs.
pushd "%~dp0..\.."
python tools\refresh_calendar.py
popd
