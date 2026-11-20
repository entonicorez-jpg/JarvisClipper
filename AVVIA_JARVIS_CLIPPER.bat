@echo off
chcp 65001 >nul
title JARVIS CLIPPER
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run

echo.
echo  === Prima installazione di Jarvis Clipper (solo la prima volta, qualche minuto) ===
echo.
set PY=
where py >nul 2>nul && (py -3.12 -c "1" >nul 2>nul && set PY=py -3.12)
if not defined PY where py >nul 2>nul && (py -3.11 -c "1" >nul 2>nul && set PY=py -3.11)
if not defined PY where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo  Python non trovato. Lo installo con winget...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo  Chiudi questa finestra e riapri AVVIA_JARVIS_CLIPPER.bat
  pause
  exit /b
)
%PY% -m venv .venv
if errorlevel 1 ( echo Errore nella creazione dell'ambiente Python & pause & exit /b )
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 ( echo Errore installazione pacchetti & pause & exit /b )

:run
".venv\Scripts\python.exe" -m pip install -q -U yt-dlp >nul 2>nul
".venv\Scripts\python.exe" app.py
pause
