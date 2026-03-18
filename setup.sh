#!/bin/bash
# Blog Writer Agent - Setup & Development Script
# Initializes backend and frontend, runs tests, and starts the dev environment

set -e

echo "=========================================="
echo "Blog Writer Agent - Setup Script"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check prerequisites
echo -n "Checking prerequisites..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗${NC} Python 3.11+ not found"
    exit 1
fi

if ! command -v node &> /dev/null; then
    echo -e "${RED}✗${NC} Node.js 18+ not found"
    exit 1
fi
echo -e "${GREEN}✓${NC}"

# Create Python virtual environment
echo "Creating Python virtual environment..."
if [ -d ".venv" ]; then
    echo "  Virtual environment already exists"
else
    python3 -m venv .venv
    echo -e "${GREEN}✓${NC} Virtual environment created"
fi

# Activate virtual environment
source .venv/bin/activate || . .venv/Scripts/activate
echo -e "${GREEN}✓${NC} Virtual environment activated"

# Install backend dependencies
echo "Installing backend dependencies..."
pip install -q -r backend/requirements.txt pytest httpx
echo -e "${GREEN}✓${NC} Dependencies installed"

# Run backend tests
echo ""
echo "Running backend tests..."
python -m pytest backend/tests/ -q
echo -e "${GREEN}✓${NC} All tests passed"

# Install frontend dependencies
echo ""
echo "Installing frontend dependencies..."
cd frontend
npm install --silent
echo -e "${GREEN}✓${NC} Frontend dependencies installed"

# Build frontend
echo "Building frontend..."
npm run build --silent > /dev/null 2>&1
echo -e "${GREEN}✓${NC} Frontend built successfully"
cd ..

# Create .env if it doesn't exist
echo ""
echo "Checking environment configuration..."
if [ ! -f ".env" ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo -e "${YELLOW}⚠${NC}  Please populate .env with your credentials:"
    echo "      - PROJECT_ENDPOINT          (Azure OpenAI)"
    echo "      - COSMOS_ENDPOINT           (Cosmos DB)"
    echo "      - GITHUB_TOKEN              (GitHub PAT)"
    echo "      - LINKEDIN_CLIENT_ID/SECRET (LinkedIn OAuth)"
    echo "      - TWITTER_CLIENT_ID         (Twitter/X OAuth 2.0)"
    echo "      - MEDIUM integration token  (via Medium Settings > Security)"
else
    echo -e "${GREEN}✓${NC} .env file exists"
fi

# Summary
echo ""
echo "=========================================="
echo -e "${GREEN}Setup Complete!${NC}"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "  1. Update .env with your credentials:"
echo "     - PROJECT_ENDPOINT:    Azure OpenAI / AI Foundry endpoint"
echo "     - COSMOS_ENDPOINT:     Cosmos DB endpoint"
echo "     - GITHUB_TOKEN:        GitHub PAT with 'repo' scope"
echo "     - LINKEDIN_CLIENT_ID:  LinkedIn OAuth app client ID"
echo "     - LINKEDIN_CLIENT_SECRET: LinkedIn OAuth app secret"
echo "     - TWITTER_CLIENT_ID:   Twitter/X OAuth 2.0 app client ID"
echo "     - ENTRA_CLIENT_ID:     (Optional) Entra ID app client ID for auth"
echo "     - ENTRA_TENANT_ID:     (Optional) Entra ID tenant ID for auth"
echo "     - Medium: uses integration tokens (no env var needed at startup)"
echo ""
echo "  2. Start backend (port 8080):"
echo "     python -m backend.main"
echo ""
echo "  3. In another terminal, start frontend dev server (port 5173):"
echo "     cd frontend && npm run dev"
echo ""
echo "  4. Open http://localhost:5173"
echo ""
echo "Or run both together with:"
echo "     npm run dev:all"
echo ""
