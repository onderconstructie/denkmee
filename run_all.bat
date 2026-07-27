@echo off
REM Dubbelklik dit bestand om de DATA bij te werken: ophalen + tagging + bouwen.
REM Publiceren naar de website doe je daarna met deploy.bat.
cd /d "%~dp0"
python run_all.py
echo.
echo  ============================================
echo   Data bijgewerkt. Bekijk eventueel dist\index.html,
echo   en dubbelklik daarna deploy.bat om te publiceren.
echo  ============================================
echo.
pause
