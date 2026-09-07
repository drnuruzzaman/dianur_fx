@echo off
REM Remove the weekly calendar refresh. The data already fetched is untouched.
schtasks /Delete /TN "DiaNurFx calendar refresh" /F
