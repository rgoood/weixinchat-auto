@echo off
rem ============================================
rem  微信个人号 AI 自动回复 一键启动
rem  前提：PC 微信 4.x 已登录（窗口不要最小化到托盘）
rem  停止：在本窗口按 Ctrl+C
rem ============================================
cd /d "%~dp0"
.venv\Scripts\python.exe -X utf8 wechat_ai_reply.py
echo.
echo ----------------------------------------
echo 程序已退出。如报错请截图保存上面的信息。
pause
