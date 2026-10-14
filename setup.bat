@echo off
setlocal
cd /d "%~dp0"

echo === Online Dekanat: dependency setup ===
echo This installs the project with the local Tesseract OCR backend.
echo.

if not defined PIP_INDEX_URL set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
if not defined TESSDATA_SOURCE_URL set "TESSDATA_SOURCE_URL=https://gitee.com/dalaomai/tessdata_fast/raw/main"

if exist ".venv\Scripts\python.exe" (
    set "VENV_PY=%CD%\.venv\Scripts\python.exe"
    goto install_python_packages
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv .venv
    if errorlevel 1 goto python_setup_failed
    set "VENV_PY=%CD%\.venv\Scripts\python.exe"
    goto install_python_packages
)

where python >nul 2>nul
if errorlevel 1 goto python_not_found
python -m venv .venv
if errorlevel 1 goto python_setup_failed
set "VENV_PY=%CD%\.venv\Scripts\python.exe"

:install_python_packages
call "%VENV_PY%" -c "import sys; sys.exit(sys.version_info < (3, 11))"
if errorlevel 1 goto python_too_old

echo [1/3] Upgrading pip...
call "%VENV_PY%" -m pip install --upgrade pip --index-url "%PIP_INDEX_URL%"
if errorlevel 1 goto pip_upgrade_failed

echo.
echo [2/3] Installing the project and Tesseract adapter...
call "%VENV_PY%" -m pip install --index-url "%PIP_INDEX_URL%" -e ".[tesseract]"
if errorlevel 1 goto packages_failed

echo.
echo [3/3] Setting up Tesseract and English/Russian language data...
where tesseract >nul 2>nul
if not errorlevel 1 goto configure_language_data

where winget >nul 2>nul
if errorlevel 1 goto tesseract_unavailable

winget install --id UB-Mannheim.TesseractOCR -e --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto tesseract_install_failed

:configure_language_data
set "TESSERACT_DIR="
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "TESSERACT_DIR=%ProgramFiles%\Tesseract-OCR"
if not defined TESSERACT_DIR if defined ProgramFiles(x86) if exist "%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe" set "TESSERACT_DIR=%ProgramFiles(x86)%\Tesseract-OCR"
if not defined TESSERACT_DIR (
    where tesseract >nul 2>nul
    if errorlevel 1 goto tesseract_unavailable
)

set "TESSDATA_DIR=%LOCALAPPDATA%\Tesseract-OCR\tessdata"
if not exist "%TESSDATA_DIR%" mkdir "%TESSDATA_DIR%"
if errorlevel 1 goto tessdata_directory_failed

where curl.exe >nul 2>nul
if errorlevel 1 goto curl_not_found

for %%L in (eng rus) do (
    if not exist "%TESSDATA_DIR%\%%L.traineddata" (
        echo Downloading %%L language data...
        curl.exe --fail --location --silent --show-error --output "%TESSDATA_DIR%\%%L.traineddata" "%TESSDATA_SOURCE_URL%/%%L.traineddata"
        if errorlevel 1 goto language_download_failed
    )
)

setx TESSDATA_PREFIX "%TESSDATA_DIR%" >nul
if errorlevel 1 goto environment_setup_failed
set "TESSDATA_PREFIX=%TESSDATA_DIR%"

if defined TESSERACT_DIR (
    powershell.exe -NoProfile -Command "$d = '%TESSERACT_DIR%'; $p = [Environment]::GetEnvironmentVariable('Path', 'User'); if (($p -split ';') -notcontains $d) { $n = if ([string]::IsNullOrWhiteSpace($p)) { $d } else { $p + ';' + $d }; [Environment]::SetEnvironmentVariable('Path', $n, 'User') }"
    if errorlevel 1 goto environment_setup_failed
    set "PATH=%TESSERACT_DIR%;%PATH%"
)

echo.
echo Setup completed successfully.
echo Restart terminals and VS Code so the TESSDATA_PREFIX setting is picked up.
echo Activate the environment with: .venv\Scripts\activate
echo Start the app with: .venv\Scripts\online-dekanat.exe serve --host 127.0.0.1 --port 8000
echo Tesseract runs locally; optional PaddleOCR and EasyOCR models download on first use.
pause
exit /b 0

:python_too_old
echo ERROR: Python 3.11 or later is required. Install Python and run this file again.
goto failed

:python_not_found
echo ERROR: Python 3.11 or later was not found. Install Python and run this file again.
goto failed

:python_setup_failed
echo ERROR: Could not create the .venv environment.
goto failed

:pip_upgrade_failed
echo ERROR: Could not upgrade pip.
goto failed

:packages_failed
echo ERROR: Python dependency installation failed.
goto failed

:tesseract_unavailable
echo ERROR: Tesseract and winget were not found. Install Tesseract OCR and run this file again.
goto failed

:tesseract_install_failed
echo ERROR: Tesseract installation failed.
goto failed

:tessdata_directory_failed
echo ERROR: Could not create the user tessdata directory.
goto failed

:curl_not_found
echo ERROR: curl.exe was not found; it is required to download OCR language data.
goto failed

:language_download_failed
echo ERROR: Could not download Tesseract language data.
goto failed

:environment_setup_failed
echo ERROR: Could not save the TESSDATA_PREFIX user environment variable.
goto failed

:failed
pause
exit /b 1
