# Blog Writer

Full-stack AI-powered blog generation web app. Enter a **GitHub repository** or **webpage URL**, and the app will analyze it, generate a rich technical blog post with GPT-4o, then let you edit, preview, and export. Now with **LinkedIn publishing** built in.

## Features

- **URL Analysis** — auto-detects GitHub repos vs. webpages; extracts structure, README, key files, or page content
- **AI Blog Generation** — streams a complete MDX blog post with Mermaid diagrams, frontmatter, and key observations
- **Split-Pane Editor** — Monaco Editor (dark theme) alongside a live Markdown preview
- **AI Editing** — 8 quick-action prompts (improve flow, add examples, shorten, etc.) plus free-form custom prompts, all streamed
- **Export** — download as **Markdown**, **HTML**, **PDF**, **DOCX**, or **MDX**
- **Publish to GitHub** — opens a PR on your portfolio repo
- **LinkedIn Publishing** — AI-composed, insights-driven LinkedIn posts via OAuth (feed posts or long-form)
- **Cloud Persistence** — drafts and LinkedIn sessions saved to Azure Cosmos DB (NoSQL)
- **Agent Mode** — standalone Azure AI Agent Framework agent for automated analyze → generate → publish pipelines

## Architecture

```text
┌──────────────────────────────────────────────────────────────────┐
│                      React SPA (Vite + Tailwind)                 │
│  ┌──────────┐  ┌──────────────────┐  ┌────────────────────────┐ │
│  │ Home      │  │ Editor (Monaco + │  │ AI Edit / Export /     │ │
│  │ (URL in)  │  │ Preview split)   │  │ Publish / LinkedIn     │ │
│  └─────┬─────┘  └────────┬─────────┘  └───────────┬────────────┘│
└────────┼─────────────────┼─────────────────────────┼─────────────┘
         │   /api/generate  │  /api/blogs CRUD        │  /api/edit
         │                  │                          │  /api/linkedin
         ▼                  ▼                          ▼
┌──────────────────────────────────────────────────────────────────┐
│                      FastAPI Backend (SSE)                        │
│  ┌──────────────┐  ┌────────────┐  ┌──────────┐  ┌───────────┐ │
│  │ Blog Service  │  │ AI Editor  │  │ Export   │  │ Cosmos DB │ │
│  │  (GPT-4o)     │  │ (GPT-4o)  │  │ Service  │  │ Client    │ │
│  └──────┬────────┘  └────────────┘  └──────────┘  └───────────┘ │
│         │                                                        │
│  ┌──────┴──────────────────────────────────────────┐             │
│  │ Tools: GitHub Analyzer │ Webpage Analyzer │       │             │
│  │        Blog Publisher  │ LinkedIn Publisher│       │             │
│  └─────────────────────────────────────────────────┘             │
│  ┌─────────────────────────────────────────────────┐             │
│  │ LinkedIn Service (AI-composed posts via GPT-4o) │             │
│  └─────────────────────────────────────────────────┘             │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│              Agent Mode (Azure AI Agent Framework)                │
│  app/agent.py — standalone analyze → generate → publish pipeline │
└──────────────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | React 19, Vite 6, TypeScript 5.8, Tailwind CSS 4 |
| Editor | Monaco Editor (`@monaco-editor/react`) |
| Preview | `react-markdown` + `remark-gfm` + `rehype-highlight` |
| State | Zustand 5 |
| Backend | Python 3.11, FastAPI, `sse-starlette` |
| AI | Azure OpenAI GPT-4o via `openai` SDK + `DefaultAzureCredential` |
| Agent | Azure AI Agent Framework (`agent_framework`) |
| Database | Azure Cosmos DB NoSQL |
| Export | `markdown`, `weasyprint` (PDF), `python-docx` (DOCX) |
| Infra | Terraform (Azure Container Apps, ACR, Log Analytics) |

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** (for frontend build)
- Azure AI Foundry project with a **GPT-4o** deployment
- Azure Cosmos DB account (or emulator)
- GitHub PAT with `repo` scope (for publishing)
- LinkedIn OAuth app (optional, for LinkedIn publishing)

## Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description | Default |
| --- | --- | --- |
| `PROJECT_ENDPOINT` | Azure AI Foundry / OpenAI endpoint | — |
| `PROJECT_API_KEY` | API key (optional; falls back to `DefaultAzureCredential`) | — |
| `MODEL_DEPLOYMENT_NAME` | Deployed model name | `gpt-4o` |
| `API_VERSION` | Azure OpenAI API version | `2024-12-01-preview` |
| `COSMOS_ENDPOINT` | Cosmos DB account endpoint | — |
| `COSMOS_KEY` | Cosmos DB key (optional; falls back to `DefaultAzureCredential`) | — |
| `COSMOS_DATABASE` | Cosmos DB database name | `blog-writer` |
| `GITHUB_TOKEN` | GitHub PAT with `repo` scope | — |
| `GITHUB_REPO` | Target repo (`owner/repo`) | `jatmadan/portfolio` |
| `LINKEDIN_CLIENT_ID` | LinkedIn OAuth app client ID | — |
| `LINKEDIN_CLIENT_SECRET` | LinkedIn OAuth app client secret | — |
| `LINKEDIN_REDIRECT_URI` | LinkedIn OAuth redirect URI | `http://localhost:8080/api/linkedin/oauth/callback` |
| `LINKEDIN_SCOPES` | LinkedIn API scopes | `r_liteprofile w_member_social` |
| `LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `PORT` | Backend listen port | `8080` |

## Quick Start

Run the setup script to install dependencies, run tests, and build the frontend:

```bash
# Linux / macOS
./setup.sh

# Windows
setup.bat
```

## Local Development

### Backend

```bash
python -m venv .venv
.venv\Scripts\activate   # or source .venv/bin/activate
pip install -r backend/requirements.txt
python -m backend.main
```

The API starts at `http://localhost:8080`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Vite dev server runs at `http://localhost:5173` and proxies `/api` to the backend.

### Run Tests

```bash
pip install pytest httpx
python -m pytest backend/tests/ -v
```

## Docker

Build and run the complete app as a single container:

```bash
docker build -f Dockerfile.webapp -t blog-writer .
docker run -p 8080:8080 --env-file .env blog-writer
```

Open `http://localhost:8080`.

## Infrastructure (Terraform)

Deploy to Azure Container Apps with the Terraform module in `infra/terraform/`. See [infra/terraform/README.md](infra/terraform/README.md) for full instructions.

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
terraform init
terraform apply
```

## API Reference

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Health check |
| `POST` | `/api/generate` | Generate blog (non-streaming) |
| `POST` | `/api/generate/stream` | Generate blog (SSE) |
| `POST` | `/api/edit` | AI edit (non-streaming) |
| `POST` | `/api/edit/stream` | AI edit (SSE) |
| `GET` | `/api/blogs` | List saved drafts |
| `GET` | `/api/blogs/{id}` | Get a draft |
| `POST` | `/api/blogs` | Create a draft |
| `PUT` | `/api/blogs/{id}` | Update a draft |
| `DELETE` | `/api/blogs/{id}` | Delete a draft |
| `POST` | `/api/export` | Export content (md/html/pdf/docx/mdx) |
| `POST` | `/api/publish` | Open GitHub PR |
| `GET` | `/api/linkedin/oauth/start` | Start LinkedIn OAuth flow |
| `POST` | `/api/linkedin/oauth/callback` | LinkedIn OAuth callback |
| `GET` | `/api/linkedin/status` | LinkedIn connection status |
| `DELETE` | `/api/linkedin/disconnect` | Disconnect LinkedIn session |
| `POST` | `/api/linkedin/compose` | AI-compose a LinkedIn post from blog content |
| `POST` | `/api/linkedin/publish` | Publish post to LinkedIn |

## Project Structure

```text
blog-writer/
├── app/                            # Agent mode (Azure AI Agent Framework)
│   ├── agent.py                    # BlogWriterAgent: analyze → generate → publish
│   ├── main.py                     # Agent entrypoint
│   ├── prompts/
│   │   └── system_prompt.md        # Agent system prompt
│   └── tools/
│       ├── github_analyzer.py      # GitHub repo analysis (agent)
│       ├── webpage_analyzer.py     # Webpage extraction (agent)
│       └── blog_publisher.py       # GitHub PR creation (agent)
├── backend/                        # FastAPI web backend
│   ├── main.py                     # FastAPI app entrypoint
│   ├── requirements.txt            # Python dependencies
│   ├── db/
│   │   └── cosmos_client.py        # Cosmos DB CRUD + LinkedIn sessions
│   ├── routers/
│   │   ├── generate.py             # /api/generate endpoints
│   │   ├── edit.py                 # /api/edit endpoints
│   │   ├── blogs.py                # /api/blogs CRUD
│   │   ├── export.py               # /api/export
│   │   ├── publish.py              # /api/publish
│   │   └── linkedin.py             # /api/linkedin (OAuth + compose + publish)
│   ├── services/
│   │   ├── blog_service.py         # URL analysis + GPT-4o generation
│   │   ├── ai_editor.py            # AI-powered content editing
│   │   ├── export_service.py       # MD / HTML / PDF / DOCX / MDX export
│   │   └── linkedin_service.py     # AI-composed LinkedIn posts
│   ├── tools/
│   │   ├── github_analyzer.py      # GitHub repo analysis
│   │   ├── webpage_analyzer.py     # Webpage content extraction
│   │   ├── blog_publisher.py       # GitHub PR creation
│   │   └── linkedin_publisher.py   # LinkedIn OAuth + UGC publishing
│   ├── prompts/
│   │   ├── system_prompt.md        # Blog generation system prompt
│   │   ├── editor_prompt.md        # AI editor system prompt
│   │   └── linkedin_post_prompt.md # LinkedIn post composition prompt
│   └── tests/
│       ├── test_blog_service.py
│       ├── test_export_service.py
│       └── test_routers.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
│       ├── main.tsx                # React Router setup
│       ├── types.ts                # Shared TypeScript types
│       ├── pages/
│       │   ├── Home.tsx            # URL input + draft list
│       │   └── Editor.tsx          # Split-pane editor page
│       ├── components/
│       │   ├── MonacoEditor.tsx
│       │   ├── MarkdownPreview.tsx
│       │   ├── AIEditPanel.tsx
│       │   └── ExportDropdown.tsx
│       ├── services/
│       │   └── api.ts              # API client + SSE helpers
│       └── store/
│           └── blogStore.ts        # Zustand state
├── infra/
│   └── terraform/                  # Azure Container Apps IaC
│       ├── main.tf
│       ├── variables.tf
│       ├── outputs.tf
│       └── terraform.tfvars.example
├── agent.yaml                      # Azure AI Agent Framework manifest
├── Dockerfile                      # Agent container build
├── Dockerfile.webapp               # Web app multi-stage Docker build
├── requirements.txt                # Agent Python dependencies
├── setup.sh                        # Dev setup script (Linux/macOS)
├── setup.bat                       # Dev setup script (Windows)
├── .env.example                    # Environment variable template
└── README.md
```
