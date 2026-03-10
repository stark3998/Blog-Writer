@echo off
REM Blog Writer Agent - Setup & Development Script (Windows)
REM Initializes backend and frontend, runs tests, and starts the dev environment

setlocal enabledelayedexpansion

echo.
echo ==========================================
echo Blog Writer Agent - Setup Script (Windows)
echo ==========================================
echo.

REM Check prerequisites
echo Checking prerequisites...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11+ not found
    exit /b 1
)

node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js 18+ not found
    exit /b 1
)

echo [OK] Prerequisites found
echo.

REM Create Python virtual environment
echo Creating Python virtual environment...
if exist ".venv" (
    echo  Virtual environment already exists
) else (
    python -m venv .venv
    echo [OK] Virtual environment created
)

REM Activate virtual environment
call .venv\Scripts\activate.bat
echo [OK] Virtual environment activated
echo.

REM Install backend dependencies
echo Installing backend dependencies...
pip install -q -r backend\requirements.txt pytest httpx
if errorlevel 1 (
    echo [ERROR] Failed to install Python dependencies
    exit /b 1
)
echo [OK] Dependencies installed
echo.

REM Run backend tests
echo Running backend tests...
python -m pytest backend\tests\ -q
if errorlevel 1 (
    echo [ERROR] Tests failed
    exit /b 1
)
echo [OK] All tests passed
echo.

REM Install frontend dependencies
echo Installing frontend dependencies...
cd frontend
call npm install --silent
if errorlevel 1 (
    echo [ERROR] Failed to install frontend dependencies
    exit /b 1
)
echo [OK] Frontend dependencies installed
echo.

REM Build frontend
echo Building frontend...
call npm run build >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Frontend build failed
    exit /b 1
)
echo [OK] Frontend built successfully
cd ..
echo.

REM Create .env if it doesn't exist
echo Checking environment configuration...
if not exist ".env" (
    echo Creating .env from .env.example...
    copy .env.example .env >nul
    echo [WARNING] Please populate .env with your Azure credentials:
    echo            - PROJECT_ENDPOINT
    echo            - GITHUB_TOKEN
    echo            - COSMOS_ENDPOINT
) else (
    echo [OK] .env file exists
)

REM Summary
echo.
echo ==========================================
echo Setup Complete!
echo ==========================================
echo.
echo Next steps:
echo.
echo  1. Update .env with your Azure credentials:
echo     - PROJECT_ENDPOINT: Your Azure OpenAI service endpoint
echo     - GITHUB_TOKEN: Your GitHub PAT with 'repo' scope
echo     - COSMOS_ENDPOINT: Your Cosmos DB endpoint
echo.
echo  2. Start backend (port 8080):
echo     python -m backend.main
echo.
echo  3. In another terminal, start frontend dev server (port 5173):
echo     cd frontend ^&^& npm run dev
echo.
echo  4. Open http://localhost:5173
echo.
echo Or for Docker:
echo     docker build -f Dockerfile.webapp -t blog-writer .
echo     docker run -p 8080:8080 --env-file .env blog-writer
echo.
