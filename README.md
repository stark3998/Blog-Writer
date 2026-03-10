# Blog Writer

Full-stack AI-powered blog generation web app. Enter a **GitHub repository** or **webpage URL**, and the app will analyze it, generate a rich technical blog post with GPT-4o, then let you edit, preview, and export.

## Features

- **URL Analysis** — auto-detects GitHub repos vs. webpages; extracts structure, README, key files, or page content
- **AI Blog Generation** — streams a complete MDX blog post with Mermaid diagrams, frontmatter, and key observations
- **Split-Pane Editor** — Monaco Editor (dark theme) alongside a live Markdown preview
- **AI Editing** — 8 quick-action prompts (improve flow, add examples, shorten, etc.) plus free-form custom prompts, all streamed
- **Export** — download as **Markdown**, **HTML**, **PDF**, **DOCX**, or **MDX**
- **Publish to GitHub** — opens a PR on your portfolio repo
- **Cloud Persistence** — drafts saved to Azure Cosmos DB (NoSQL)

## Architecture

```text
┌──────────────────────────────────────────────────────────────────┐
│                      React SPA (Vite + Tailwind)                 │
│  ┌──────────┐  ┌──────────────────┐  ┌────────────────────────┐ │
│  │ Home      │  │ Editor (Monaco + │  │ AI Edit Panel /        │ │
│  │ (URL in)  │  │ Preview split)   │  │ Export / Publish       │ │
│  └─────┬─────┘  └────────┬─────────┘  └───────────┬────────────┘│
└────────┼─────────────────┼─────────────────────────┼─────────────┘
         │   /api/generate  │  /api/blogs CRUD        │  /api/edit
         ▼                  ▼                          ▼
┌──────────────────────────────────────────────────────────────────┐
│                      FastAPI Backend (SSE)                        │
│  ┌──────────────┐  ┌────────────┐  ┌──────────┐  ┌───────────┐ │
│  │ Blog Service  │  │ AI Editor  │  │ Export   │  │ Cosmos DB │ │
│  │  (GPT-4o)     │  │ (GPT-4o)  │  │ Service  │  │ Client    │ │
│  └──────┬────────┘  └────────────┘  └──────────┘  └───────────┘ │
│         │                                                        │
│  ┌──────┴──────────────────────────────────────────┐             │
│  │ Tools: GitHub Analyzer │ Webpage Analyzer │ Publisher │       │
│  └─────────────────────────────────────────────────┘             │
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
| Database | Azure Cosmos DB NoSQL |
| Export | `markdown`, `weasyprint` (PDF), `python-docx` (DOCX) |

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** (for frontend build)
- Azure AI Foundry project with a **GPT-4o** deployment
- Azure Cosmos DB account (or emulator)
- GitHub PAT with `repo` scope (for publishing)

## Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description | Default |
| --- | --- | --- |
| `PROJECT_ENDPOINT` | Azure AI Foundry / OpenAI endpoint | — |
| `MODEL_DEPLOYMENT_NAME` | Deployed model name | `gpt-4o` |
| `GITHUB_TOKEN` | GitHub PAT with `repo` scope | — |
| `GITHUB_REPO` | Target repo (`owner/repo`) | `jatmadan/portfolio` |
| `COSMOS_ENDPOINT` | Cosmos DB account endpoint | — |
| `COSMOS_DATABASE` | Cosmos DB database name | `blog-writer` |
| `PORT` | Backend listen port | `8080` |

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

## Project Structure

```text
blog-writer-agent/
├── backend/
│   ├── main.py                  # FastAPI app entrypoint
│   ├── requirements.txt         # Python dependencies
│   ├── routers/
│   │   ├── generate.py          # /api/generate endpoints
│   │   ├── edit.py              # /api/edit endpoints
│   │   ├── blogs.py             # /api/blogs CRUD
│   │   ├── export.py            # /api/export
│   │   └── publish.py           # /api/publish
│   ├── services/
│   │   ├── blog_service.py      # URL analysis + GPT-4o generation
│   │   ├── ai_editor.py         # AI-powered content editing
│   │   ├── export_service.py    # MD / HTML / PDF / DOCX / MDX export
│   │   └── cosmos_client.py     # Cosmos DB CRUD
│   ├── tools/
│   │   ├── github_analyzer.py   # GitHub repo analysis
│   │   ├── webpage_analyzer.py  # Webpage content extraction
│   │   └── blog_publisher.py    # GitHub PR creation
│   ├── prompts/
│   │   ├── system_prompt.md     # Blog generation system prompt
│   │   └── editor_prompt.md     # AI editor system prompt
│   └── tests/
│       ├── test_blog_service.py
│       ├── test_export_service.py
│       └── test_routers.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
│       ├── main.tsx             # React Router setup
│       ├── types.ts             # Shared TypeScript types
│       ├── pages/
│       │   ├── Home.tsx         # URL input + draft list
│       │   └── Editor.tsx       # Split-pane editor page
│       ├── components/
│       │   ├── MonacoEditor.tsx
│       │   ├── MarkdownPreview.tsx
│       │   ├── AIEditPanel.tsx
│       │   └── ExportDropdown.tsx
│       ├── services/
│       │   └── api.ts           # API client + SSE helpers
│       └── store/
│           └── blogStore.ts     # Zustand state
├── Dockerfile.webapp            # Multi-stage Docker build
├── .env.example                 # Environment variable template
└── README.md
```
