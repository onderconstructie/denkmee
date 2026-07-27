@echo off
setlocal
cd /d "%~dp0"
title Denk mee met Mechelen - publiceren

echo.
echo  ============================================
echo    Denk mee met Mechelen  -  publiceren
echo  ============================================
echo.

echo  [1/4] dist\index.html bouwen...
python build.py
if errorlevel 1 goto buildfout
echo.

echo  [2/4] Wijzigingen verzamelen...
git add -A
git diff --cached --quiet
if not errorlevel 1 goto nietswijzig

set "msg=Bijwerking %date%"
echo.
set /p "usermsg=  [3/4] Korte beschrijving (Enter = standaard): "
if not "%usermsg%"=="" set "msg=%usermsg%"
git commit -m "%msg%"
if errorlevel 1 goto commitfout
echo.

echo  [4/4] Pushen naar GitHub (start de publicatie)...
git push -u origin main
if errorlevel 1 goto pushfout

echo.
echo  ============================================
echo    Klaar! Over enkele minuten staat het live
echo    op https://denkmee.asgaupaust.be
echo  ============================================
echo.
pause
exit /b 0

:buildfout
echo.
echo  FOUT bij het bouwen. Er wordt NIETS gepubliceerd.
echo  Werkt "python build.py" handmatig in deze map?
echo.
pause
exit /b 1

:nietswijzig
echo.
echo  Niets gewijzigd - er is niets te publiceren.
echo.
pause
exit /b 0

:commitfout
echo.
echo  FOUT bij het vastleggen (commit).
echo.
pause
exit /b 1

:pushfout
echo.
echo  FOUT bij het pushen. Controleer je internet of je GitHub-login.
echo.
pause
exit /b 1
