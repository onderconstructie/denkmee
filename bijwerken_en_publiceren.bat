@echo off
setlocal
cd /d "%~dp0"
title Denk mee met Mechelen - bijwerken en publiceren
REM Dubbelklik dit bestand om ALLES in een beweging te doen: data ophalen + taggen +
REM bouwen (run_all.py) en meteen publiceren (commit + push). Stopt bij elke fout,
REM zodat een mislukte run nooit half werk live zet. Wil je de stappen los, gebruik
REM dan run_all.bat en deploy.bat zoals voorheen.
echo.
echo  ==================================================
echo    Denk mee met Mechelen  -  bijwerken en publiceren
echo  ==================================================
echo.
REM De API-sleutel is nodig om nieuwe stukken samen te vatten. In een gewone
REM gebruikerssessie staat hij klaar; zo niet, halen we hem uit het register.
if not defined ANTHROPIC_API_KEY (
  for /f "tokens=2,*" %%a in ('reg query HKCU\Environment /v ANTHROPIC_API_KEY 2^>nul ^| find "ANTHROPIC_API_KEY"') do set "ANTHROPIC_API_KEY=%%b"
)
if not defined ANTHROPIC_API_KEY (
  echo  GESTOPT: geen ANTHROPIC_API_KEY gevonden. Zonder sleutel blijven nieuwe
  echo  stukken zonder samenvatting, dus er wordt NIETS gepubliceerd.
  pause
  exit /b 1
)
echo  [1/4] Data bijwerken en site bouwen (run_all.py)...
python run_all.py
if errorlevel 1 (
  echo.
  echo  GESTOPT: de pijplijn gaf een fout. Er wordt NIETS gepubliceerd.
  pause
  exit /b 1
)
echo.
echo  [2/4] Wijzigingen verzamelen...
git add -A
git diff --cached --quiet
if not errorlevel 1 (
  echo.
  echo  Niets gewijzigd - er is niets te publiceren.
  pause
  exit /b 0
)
echo  [3/4] Vastleggen...
git commit -m "Bijwerking %date%"
if errorlevel 1 (
  echo.
  echo  GESTOPT bij het vastleggen (commit).
  pause
  exit /b 1
)
echo.
echo  [4/4] Pushen naar GitHub (start de publicatie)...
git push -u origin main
if errorlevel 1 (
  echo.
  echo  GESTOPT bij het pushen. Controleer je internet of je GitHub-login.
  pause
  exit /b 1
)
echo.
echo  ==================================================
echo    Klaar! Over enkele minuten staat het live
echo    op https://denkmee.asgaupaust.be
echo  ==================================================
echo.
pause
exit /b 0
