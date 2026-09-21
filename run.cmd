@echo off
rem Claude FinOps Command Center (Windows). Same as: python run.py [--rebuild|--stop|--share]
cd /d "%~dp0"
where py >nul 2>nul && (py -3 run.py %*) || (python run.py %*)
