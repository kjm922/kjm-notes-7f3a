@echo off
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=%USERPROFILE%\anaconda3\python.exe
"%PY%" -c "import curl_cffi" 2>nul || "%PY%" -m pip install -q curl_cffi
"%PY%" -c "import playwright" 2>nul || "%PY%" -m pip install -q playwright
"%PY%" hotdeal.py
pause
