@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo [키즈노트 준비물·일정 정리 비서]를 시작합니다...
start "" "http://localhost:8080"
python app.py
pause
