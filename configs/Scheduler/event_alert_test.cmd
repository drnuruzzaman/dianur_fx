@echo off
REM Prove the wiring without waiting for a release.
pushd "%~dp0..\.."
python tools\event_alert.py --test
popd
