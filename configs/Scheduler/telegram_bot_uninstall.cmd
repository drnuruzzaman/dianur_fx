@echo off
schtasks /End /TN "DiaNurFx Telegram Bot" 2>nul
schtasks /Delete /TN "DiaNurFx Telegram Bot" /F
