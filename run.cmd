@echo off
rem Claude FinOps Command Center (Windows). Same as: python run.py [--rebuild|--stop]
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 "%~dp0run.py" %*) else (python "%~dp0run.py" %*)
