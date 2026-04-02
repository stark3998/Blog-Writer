# Blog Writer

Full-stack AI-powered blog generation and content management platform. Enter a **GitHub repository** or **webpage URL**, and the app analyzes it, generates a rich technical blog post with GPT-4o, then lets you edit, preview, publish, and distribute across multiple platforms. Features an automated RSS-to-blog pipeline, content scheduling, SEO analysis, collaborative editing, and multi-platform social distribution.

---

## Features

### Content Generation & Editing
- **URL Analysis** — auto-detects GitHub repos vs. webpages; extracts structure, README, key files, or page content
- **AI Blog Generation** — streams a complete MDX blog post with Mermaid diagrams, frontmatter, hero images (DALL-E), and key observations
- **Split-Pane Editor** — Monaco Editor alongside a live Markdown preview (editor-only, split, preview-only modes)
- **AI Editing** — 8 quick-action prompts (improve flow, add examples, shorten, etc.) plus free-form custom prompts, all streamed via SSE
- **Version History** — track and restore previous versions of your drafts
- **Content Templates** — reusable blog post templates by category (tutorial, how-to, review, comparison, news, opinion, case-study) with built-in and user-created templates
- **Voice Profiles** — define writing voice/tone profiles (professional, casual, technical, conversational, academic, witty) for AI-generated content
- **Bulk Import** — import content from Markdown files, URLs (auto-scraped), or WordPress XML exports

### Publishing & Distribution
- **Blog Publishing** — publish directly to Azure Cosmos DB with full HTML rendering and page view tracking
- **Content Scheduling** — schedule drafts for future auto-publish to blog, LinkedIn, Twitter, and Medium with a date/time picker and platform selector
- **LinkedIn Publishing** — AI-composed, insights-driven LinkedIn posts via OAuth 2.0 (feed posts or long-form articles)
- **Twitter/X Publishing** — AI-composed tweets via OAuth 2.0 with PKCE
- **Medium Publishing** — cross-post articles to Medium via integration tokens
- **Newsletter Distribution** — send blog content as formatted newsletters via Mailchimp, ConvertKit, or SMTP
- **GitHub PR Publishing** — opens a PR on your portfolio repo
- **Export** — download as Markdown, HTML, PDF, DOCX, or MDX

### Automation Pipeline
- **RSS Feed Crawler** — automated feed discovery (RSS/Atom auto-detect + HTML fallback) with configurable crawl intervals
- **Relevance Classification** — two-stage relevance scoring: keyword pre-filter + GPT-4o AI classification with topic matching
- **Technicality Ranking** — GPT-4o ranks relevant articles by technical depth; only top-N get blog generation
- **Auto-Publishing** — fully automated pipeline: scrape feeds → classify → rank → generate blog → publish → compose social posts → publish to LinkedIn/Twitter/Medium
- **Retry & Error Recovery** — failed articles retry with exponential backoff (3 attempts); partial failures tracked per-stage
- **Webhook Notifications** — pipeline events (crawl completed, blog published, LinkedIn posted, errors) sent to Microsoft Teams, Slack, or generic webhook URLs
- **Article Age Filtering** — configurable `maxArticleAgeDays` per feed to skip stale content
- **Daily Limits** — configurable daily publish limits per platform

### Analytics & SEO
- **Pipeline Analytics Dashboard** — article ratings, relevance scores, pipeline health, top topics, daily activity charts, bulk actions
- **Post Performance Tracking** — view/share/click event recording per published blog with analytics overview
- **SEO Analysis** — automated SEO scoring (title length, meta description, word count, heading structure, image alt tags, internal/external links, readability, keyword density)
- **SEO History** — track SEO score changes over time per post

### Collaboration
- **Comments System** — threaded comments panel in the editor with line-number targeting, reply chains, and resolve/unresolve
- **User Profiles** — Microsoft Entra ID (Azure AD) authentication with user-scoped data and profile management

### Developer Experience
- **Content Calendar** — visualize drafts, published posts, queued articles, and scheduled publishes in a monthly calendar view
- **Dark Mode** — full dark/light theme toggle with CSS-level overrides across all components
- **Diagnostics** — end-to-end health checks for LinkedIn, Twitter, Medium, OpenAI, image generation, Cosmos DB, and publish dry-run
- **Customizable Prompts** — edit system prompts for blog generation, AI editing, LinkedIn/Twitter/Medium composition
- **Topic Keywords** — manage keyword lists per topic to fine-tune relevance classification
- **Scheduler Settings** — configure crawl intervals, auto-publish behavior, and feed health monitoring
- **Docker Compose** — local development with hot-reload for both frontend and backend

---

## Architecture

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                       React SPA (Vite + Tailwind CSS)                        │
│                                                                              │
│  ┌──────────┐ ┌───────────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────┐│
│  │ Home      │ │ Editor        │ │ Dashboard │ │ Calendar │ │ Settings     ││
│  │ (URL gen, │ │ (Monaco,      │ │ (Pipeline │ │ (Content │ │ (Feeds,      ││
│  │  drafts,  │ │  AI edit,     │ │  health,  │ │  timeline│ │  prompts,    ││
│  │  import)  │ │  SEO, export, │ │  articles,│ │  view)   │ │  keywords,   ││
│  │           │ │  comments,    │ │  bulk ops)│ │          │ │  scheduler,  ││
│  │           │ │  schedule,    │ │           │ │          │ │  voice,      ││
│  │           │ │  distribute)  │ │           │ │          │ │  templates,  ││
│  │           │ │               │ │           │ │          │ │  diagnostics)││
│  └─────┬─────┘ └──────┬────────┘ └─────┬─────┘ └────┬─────┘ └──────┬───────┘│
└────────┼──────────────┼────────────────┼────────────┼──────────────┼─────────┘
         │              │                │            │              │
         ▼              ▼                ▼            ▼              ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         FastAPI Backend (SSE + REST)                          │
│                                                                              │
│  ┌─── Core Services ────────────────────────────────────────────────────┐    │
│  │ Blog Service (GPT-4o) │ AI Editor │ Export (MD/HTML/PDF/DOCX/MDX)   │    │
│  │ Image Generator (DALL-E) │ Relevance Classifier │ Feed Crawler      │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─── Social & Distribution ────────────────────────────────────────────┐   │
│  │ LinkedIn Service │ Twitter Service │ Medium Service │ Newsletter Svc │   │
│  │ (AI compose + multi-agent: hashtag, humanizer, validation agents)    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─── Automation ───────────────────────────────────────────────────────┐   │
│  │ APScheduler │ Auto-Publisher │ Schedule Executor │ Notification Svc  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─── Data Layer ───────────────────────────────────────────────────────┐   │
│  │ Cosmos DB Client (lazy-init containers, /id partition key)           │   │
│  │ Containers: drafts, published-blogs, feed-sources, crawled-articles, │   │
│  │   crawl-jobs, linkedin-sessions, twitter-sessions, medium-sessions,  │   │
│  │   scheduled-publishes, post-analytics, seo-tracking, comments,       │   │
│  │   voice-profiles, templates, versions, images, prompts, keywords     │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─── Auth ─────────────────────────────────────────────────────────────┐   │
│  │ Microsoft Entra ID (Azure AD) — JWT validation via python-jose       │   │
│  │ MSAL.js on frontend — AuthGuard, AuthProvider, LoginPage             │   │
│  │ Bypass mode when ENTRA_CLIENT_ID is not set (local dev)              │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│                Agent Mode (Azure AI Agent Framework)                          │
│  app/agent.py — standalone analyze → generate → publish pipeline             │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | React 19, Vite 6, TypeScript 5.8, Tailwind CSS 4 |
| **Editor** | Monaco Editor (`@monaco-editor/react`) |
| **Preview** | `react-markdown` + `remark-gfm` + `rehype-highlight` + Mermaid |
| **State** | Zustand 5 (blogStore, themeStore, toastStore) |
| **Auth (FE)** | MSAL.js (`@azure/msal-browser`, `@azure/msal-react`) |
| **Testing (FE)** | Vitest, React Testing Library, jsdom |
| **Backend** | Python 3.11, FastAPI, `sse-starlette`, APScheduler |
| **AI** | Azure OpenAI GPT-4o via `openai` SDK + `DefaultAzureCredential` |
| **Images** | Azure OpenAI DALL-E (`gpt-image-1-mini`) for hero images |
| **Auth (BE)** | Microsoft Entra ID — JWT validation via `python-jose` |
| **Database** | Azure Cosmos DB NoSQL (17+ containers, lazy-init pattern) |
| **Export** | `markdown`, `weasyprint` (PDF), `python-docx` (DOCX) |
| **Agent** | Azure AI Agent Framework (`agent_framework`) |
| **Infra** | Terraform (Azure Container Apps, ACR, Log Analytics, Entra App Registration) |
| **CI/CD** | GitHub Actions (Terraform plan/apply + Docker build/push + rolling deploy) |
| **Dev** | Docker Compose with hot-reload for both services |

---

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** (for frontend)
- Azure AI Foundry project with a **GPT-4o** deployment
- Azure Cosmos DB account (or emulator)
- GitHub PAT with `repo` scope (for blog publishing to GitHub)
- **Optional:** LinkedIn OAuth app, Twitter/X OAuth 2.0 app, Medium integration token
- **Optional:** Microsoft Entra ID app registration (for auth; leave unconfigured for local dev)
- **Optional:** Mailchimp API key, ConvertKit API secret, or SMTP credentials (for newsletters)

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### Required

| Variable | Description |
|----------|-------------|
| `PROJECT_ENDPOINT` | Azure AI Foundry / OpenAI endpoint |
| `COSMOS_ENDPOINT` | Cosmos DB account endpoint |
| `COSMOS_DATABASE` | Cosmos DB database name (default: `blog-writer`) |

### AI & Models

| Variable | Description | Default |
|----------|-------------|---------|
| `PROJECT_API_KEY` | API key (optional; falls back to `DefaultAzureCredential`) | — |
| `MODEL_DEPLOYMENT_NAME` | Deployed chat model name | `gpt-4o` |
| `IMAGE_MODEL_DEPLOYMENT_NAME` | Image generation model | `gpt-image-1-mini` |
| `API_VERSION` | Azure OpenAI API version | `2024-12-01-preview` |
| `LINKEDIN_POST_MODEL` | Model override for LinkedIn composition | `MODEL_DEPLOYMENT_NAME` |
| `TWITTER_POST_MODEL` | Model override for Twitter composition | `MODEL_DEPLOYMENT_NAME` |
| `HUMANIZER_MODEL` | Model override for humanizer agent | `MODEL_DEPLOYMENT_NAME` |
| `VALIDATION_MODEL` | Model override for validation agent | `MODEL_DEPLOYMENT_NAME` |

### Database

| Variable | Description | Default |
|----------|-------------|---------|
| `COSMOS_KEY` | Cosmos DB key (optional; falls back to `DefaultAzureCredential`) | — |
| `COSMOS_THROUGHPUT` | Container throughput (only for provisioned mode) | — |

### Social Platforms

| Variable | Description |
|----------|-------------|
| `LINKEDIN_CLIENT_ID` | LinkedIn OAuth app client ID |
| `LINKEDIN_CLIENT_SECRET` | LinkedIn OAuth app client secret |
| `LINKEDIN_REDIRECT_URI` | LinkedIn OAuth redirect URI (default: `http://localhost:8080/api/linkedin/oauth/callback`) |
| `LINKEDIN_SCOPES` | LinkedIn API scopes (default: `openid profile w_member_social`) |
| `TWITTER_CLIENT_ID` | Twitter/X OAuth 2.0 client ID |
| `TWITTER_REDIRECT_URI` | Twitter OAuth redirect URI |
| `TWITTER_SCOPES` | Twitter API scopes (default: `tweet.read tweet.write users.read offline.access`) |

### Automation

| Variable | Description |
|----------|-------------|
| `LINKEDIN_AUTO_SESSION_ID` | LinkedIn session ID for auto-publishing |
| `TWITTER_AUTO_SESSION_ID` | Twitter session ID for auto-publishing |
| `MEDIUM_AUTO_SESSION_ID` | Medium session ID for auto-publishing |
| `DEFAULT_CRAWL_INTERVAL_MINUTES` | Default RSS crawl interval (default: `60`) |
| `WEBHOOK_URL` | Webhook URL for pipeline notifications (Teams, Slack, or generic JSON) |

### Auth & Publishing

| Variable | Description |
|----------|-------------|
| `ENTRA_CLIENT_ID` | Microsoft Entra ID app client ID (leave empty to disable auth) |
| `ENTRA_TENANT_ID` | Microsoft Entra ID tenant ID |
| `GITHUB_TOKEN` | GitHub PAT with `repo` scope |
| `GITHUB_REPO` | Target GitHub repo for blog publishing (e.g., `owner/portfolio`) |
| `BLOG_BASE_URL` | Your published blog base URL (used in social posts) |

### Server

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Backend listen port | `8080` |
| `LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `DIAGNOSTICS_API_KEY` | API key for `/api/diagnostics/*` endpoints | — |

---

## Quick Start

### Option 1: Setup Script

```bash
# Linux / macOS
./setup.sh

# Windows
setup.bat
```

This installs dependencies, runs tests, and builds the frontend.

### Option 2: Docker Compose (Recommended for Development)

```bash
cp .env.example .env
# Edit .env with your values

docker compose up --build
```

- Frontend: `http://localhost:5173` (Vite dev server with hot reload)
- Backend: `http://localhost:8080` (uvicorn with hot reload)

### Option 3: Manual Setup

#### Backend

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r backend/requirements.txt
python -m backend.main
```

API starts at `http://localhost:8080`.

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

Vite dev server at `http://localhost:5173`, proxies `/api` to the backend.

---

## Running Tests

### Frontend Tests (Vitest)

```bash
cd frontend
npm test              # Run once
npm run test:watch    # Watch mode
npm run test:coverage # With coverage report
```

**21 tests** covering:
- Theme store — toggle, set, dark class management (8 tests)
- Toast store — success/error/info/warning, dismissal, auto-dismiss (7 tests)
- API client — headers, error handling, endpoints (6 tests)

### Backend Tests (pytest)

```bash
pip install pytest httpx
python -m pytest backend/tests/ -v
```

**40+ tests** covering:
- Analytics endpoints — event recording, post analytics, overview (6 tests)
- Comments — full CRUD with replies, resolve, validation (10 tests)
- SEO analysis — word count, headings, images, links, readability, keyword density (11 tests)
- Schedule — create/list/cancel with validation, status filtering (13 tests)
- Blog service — URL analysis, generation
- Export service — MD/HTML/PDF/DOCX output
- Core routers — generate, edit, CRUD

---

## Docker

### Production (Single Container)

```bash
docker build -f Dockerfile.webapp -t blog-writer .
docker run -p 8080:8080 --env-file .env blog-writer
```

Open `http://localhost:8080`.

### Development (Docker Compose)

```bash
docker compose up --build
```

Uses separate dev Dockerfiles with hot-reload:
- `Dockerfile.dev-backend` — Python 3.11 with WeasyPrint deps, `uvicorn --reload`
- `Dockerfile.dev-frontend` — Node 20 Alpine, `vite --host`

Volume mounts enable live code editing without rebuilds.

---

## Infrastructure (Terraform)

Deploy to Azure Container Apps with the Terraform module in `infra/terraform/`.

### Resources Created

- Azure Container Apps Environment + Container App
- Azure Container Registry (ACR)
- Log Analytics Workspace
- Microsoft Entra ID App Registration (optional)

### Remote State (Recommended)

The project includes a pre-configured Azure Storage remote state backend in `infra/terraform/backend.tf`. Uncomment and configure it for team/CI use:

```hcl
terraform {
  backend "azurerm" {
    resource_group_name  = "your-rg"
    storage_account_name = "yourstorageaccount"
    container_name       = "tfstate"
    key                  = "blog-writer.terraform.tfstate"
  }
}
```

### Deploy

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
terraform init
terraform apply
```

### Local Redeploy (Windows)

```powershell
.\deploy-local.ps1           # Full deploy
.\deploy-local.ps1 -WhatIf   # Dry run
```

---

## CI/CD (GitHub Actions)

The repo includes a deployment workflow at `.github/workflows/cicd-main.yml`.

**On every push to `main`:**
1. Run Terraform `fmt -check`, `validate`, `plan`, and `apply` for infrastructure
2. Build and push a new Docker image to ACR (tag = short commit SHA)
3. Run Terraform `plan` and `apply` with the new image to roll out the update

### Required GitHub Secrets

| Secret | Description |
|--------|-------------|
| `AZURE_CREDENTIALS` | Service principal JSON from `az ad sp create-for-rbac` |
| `PROJECT_API_KEY` | Azure OpenAI API key |
| `COSMOS_KEY` | Cosmos DB key |
| `GITHUB_TOKEN` | GitHub PAT |
| `LINKEDIN_CLIENT_SECRET` | LinkedIn OAuth secret |

### Required GitHub Variables

| Variable | Description |
|----------|-------------|
| `PROJECT_ENDPOINT` | Azure OpenAI endpoint |
| `COSMOS_ENDPOINT` | Cosmos DB endpoint |
| `LINKEDIN_CLIENT_ID` | LinkedIn client ID |
| `LINKEDIN_REDIRECT_URI` | LinkedIn redirect URI |

### Optional GitHub Variables

| Variable | Default |
|----------|---------|
| `MODEL_DEPLOYMENT_NAME` | `gpt-4.1-mini` |
| `API_VERSION` | `2024-05-01-preview` |
| `LINKEDIN_SCOPES` | `openid profile w_member_social` |
| `TF_RESOURCE_GROUP_NAME`, `TF_CONTAINER_APP_NAME`, `TF_CONTAINER_APP_ENV_NAME` | — |
| `TF_TAG_PURPOSE`, `TF_TAG_OWNER`, `TF_TAG_EXPIRY_DATE` | — |

---

## API Reference

### Blog Generation & Editing

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/generate` | Generate blog from URL (non-streaming) |
| `POST` | `/api/generate/stream` | Generate blog from URL (SSE streaming) |
| `POST` | `/api/edit` | AI edit content (non-streaming) |
| `POST` | `/api/edit/stream` | AI edit content (SSE streaming) |

### Drafts (CRUD)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/blogs` | List saved drafts |
| `GET` | `/api/blogs/{id}` | Get a draft by ID |
| `POST` | `/api/blogs` | Create a new draft |
| `PUT` | `/api/blogs/{id}` | Update a draft |
| `DELETE` | `/api/blogs/{id}` | Delete a draft |
| `DELETE` | `/api/blogs` | Delete all drafts |
| `GET` | `/api/blogs/{id}/versions` | List draft versions |
| `POST` | `/api/blogs/{id}/versions` | Create a version snapshot |
| `GET` | `/api/blogs/{id}/versions/{vid}` | Get a specific version |
| `POST` | `/api/blogs/{id}/versions/{vid}/restore` | Restore a version |

### Publishing

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/publish` | Publish blog (Cosmos DB + HTML) |
| `GET` | `/api/publish/{slug}` | Serve published blog HTML |
| `GET` | `/api/publish/list` | List published blogs |
| `POST` | `/api/export` | Export content (md/html/pdf/docx/mdx) |

### Social Platforms

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/linkedin/oauth/start` | Start LinkedIn OAuth flow |
| `POST` | `/api/linkedin/oauth/callback` | LinkedIn OAuth callback |
| `GET` | `/api/linkedin/status` | LinkedIn connection status |
| `DELETE` | `/api/linkedin/disconnect` | Disconnect LinkedIn session |
| `POST` | `/api/linkedin/compose` | AI-compose LinkedIn post |
| `POST` | `/api/linkedin/publish` | Publish to LinkedIn |
| `GET` | `/api/twitter/oauth/start` | Start Twitter OAuth flow |
| `GET` | `/api/twitter/status` | Twitter connection status |
| `DELETE` | `/api/twitter/disconnect` | Disconnect Twitter session |
| `POST` | `/api/twitter/compose` | AI-compose tweet |
| `POST` | `/api/twitter/publish` | Publish to Twitter |
| `POST` | `/api/medium/connect` | Connect Medium (integration token) |
| `GET` | `/api/medium/status` | Medium connection status |
| `DELETE` | `/api/medium/disconnect` | Disconnect Medium |
| `POST` | `/api/medium/publish` | Publish to Medium |

### RSS Feeds & Crawling

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/feeds` | List feed sources |
| `POST` | `/api/feeds` | Create a feed source |
| `GET` | `/api/feeds/{id}` | Get a feed source |
| `PUT` | `/api/feeds/{id}` | Update a feed source |
| `DELETE` | `/api/feeds/{id}` | Delete a feed source |
| `POST` | `/api/feeds/discover` | Auto-discover RSS feed URL |
| `POST` | `/api/feeds/{id}/crawl` | Trigger manual crawl |
| `POST` | `/api/feeds/{id}/crawl/stream` | Trigger crawl with SSE progress |
| `POST` | `/api/feeds/crawl-all/stream` | Crawl all feeds with SSE progress |
| `GET` | `/api/feeds/{id}/articles` | List feed articles |
| `GET` | `/api/feeds/articles/relevant` | List relevant articles |
| `GET` | `/api/feeds/crawl-log` | Get crawl job history |

### Content Scheduling

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/schedule` | Schedule a draft for future publish |
| `GET` | `/api/schedule` | List scheduled publishes (optional `?status=pending`) |
| `GET` | `/api/schedule/{id}` | Get a scheduled publish |
| `DELETE` | `/api/schedule/{id}` | Cancel a pending schedule |

### Analytics & SEO

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/analytics/event` | Record a post event (view, share, click) |
| `GET` | `/api/analytics/post/{slug}` | Get analytics for a post |
| `GET` | `/api/analytics/overview` | Get analytics overview for all posts |
| `POST` | `/api/seo/analyze/{slug}` | Run SEO analysis on a published blog |
| `GET` | `/api/seo/history/{slug}` | Get SEO score history |
| `GET` | `/api/seo/overview` | Get latest SEO snapshots for all posts |

### Dashboard & Pipeline

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/dashboard/stats` | Pipeline statistics (articles, relevance, health) |
| `GET` | `/api/dashboard/articles` | Dashboard articles with filters |
| `POST` | `/api/dashboard/articles/{id}/regenerate` | Regenerate blog for an article |
| `POST` | `/api/dashboard/articles/{id}/linkedin` | Promote article to LinkedIn |
| `POST` | `/api/dashboard/articles/bulk/generate` | Bulk generate blogs |
| `POST` | `/api/dashboard/articles/bulk/linkedin` | Bulk publish to LinkedIn |
| `GET` | `/api/dashboard/feed-health` | Feed health status |

### Comments (Collaboration)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/comments` | Create a comment (with optional line number and parent) |
| `GET` | `/api/comments/{draft_id}` | List comments for a draft |
| `PUT` | `/api/comments/{id}` | Update comment content or resolve status |
| `DELETE` | `/api/comments/{id}` | Delete a comment |

### Voice Profiles

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/voice-profiles` | Create a voice profile |
| `GET` | `/api/voice-profiles` | List voice profiles |
| `GET` | `/api/voice-profiles/{id}` | Get a voice profile |
| `PUT` | `/api/voice-profiles/{id}` | Update a voice profile |
| `DELETE` | `/api/voice-profiles/{id}` | Delete a voice profile |
| `POST` | `/api/voice-profiles/{id}/default` | Set as default voice |

### Content Templates

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/templates` | Create a template |
| `GET` | `/api/templates` | List templates (optional `?category=tutorial`) |
| `GET` | `/api/templates/{id}` | Get a template |
| `PUT` | `/api/templates/{id}` | Update a template |
| `DELETE` | `/api/templates/{id}` | Delete a template (built-in protected) |

### Import & Newsletter

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/import/markdown` | Import from markdown entries |
| `POST` | `/api/import/urls` | Import from URLs (auto-scrape + generate) |
| `POST` | `/api/import/wordpress` | Import from WordPress XML export |
| `POST` | `/api/newsletter/preview` | Preview newsletter HTML |
| `POST` | `/api/newsletter/send` | Send newsletter (Mailchimp/ConvertKit/SMTP) |

### Settings & Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/prompts` | List customizable prompts |
| `GET` | `/api/prompts/{name}` | Get prompt content |
| `PUT` | `/api/prompts/{name}` | Update a prompt |
| `POST` | `/api/prompts/{name}/reset` | Reset prompt to default |
| `POST` | `/api/prompts/test` | Test a prompt with sample input |
| `GET` | `/api/keywords` | List topic keyword sets |
| `GET` | `/api/keywords/{topic}` | Get keywords for a topic |
| `PUT` | `/api/keywords/{topic}` | Update keywords for a topic |
| `POST` | `/api/keywords/{topic}/reset` | Reset keywords to default |
| `POST` | `/api/keywords/{topic}/add` | Add keywords to a topic |
| `GET` | `/api/user/profile` | Get user profile |
| `GET` | `/api/user/settings` | Get user settings |
| `PUT` | `/api/user/settings` | Update user settings |
| `GET` | `/api/health` | Health check |
| `GET` | `/api/diagnostics/checks` | List available diagnostic checks |
| `POST` | `/api/diagnostics/run` | Run diagnostic checks |

---

## Project Structure

```text
blog-writer/
├── app/                                  # Agent mode (Azure AI Agent Framework)
│   ├── agent.py                          # BlogWriterAgent: analyze → generate → publish
│   ├── main.py                           # Agent entrypoint
│   ├── prompts/
│   │   └── system_prompt.md              # Agent system prompt
│   └── tools/
│       ├── github_analyzer.py            # GitHub repo analysis (agent)
│       ├── webpage_analyzer.py           # Webpage extraction (agent)
│       └── blog_publisher.py             # GitHub PR creation (agent)
│
├── backend/                              # FastAPI web backend
│   ├── main.py                           # FastAPI app entrypoint, middleware, router registration
│   ├── auth.py                           # Entra ID JWT validation
│   ├── requirements.txt                  # Python dependencies
│   ├── db/
│   │   └── cosmos_client.py              # Cosmos DB CRUD (17+ containers, lazy-init)
│   ├── models/
│   │   └── user.py                       # User model
│   ├── routers/
│   │   ├── generate.py                   # /api/generate — blog generation
│   │   ├── edit.py                       # /api/edit — AI content editing
│   │   ├── blogs.py                      # /api/blogs — draft CRUD + versions
│   │   ├── export.py                     # /api/export — multi-format export
│   │   ├── publish.py                    # /api/publish — blog publishing + serving
│   │   ├── linkedin.py                   # /api/linkedin — OAuth + compose + publish
│   │   ├── twitter.py                    # /api/twitter — OAuth + compose + publish
│   │   ├── medium.py                     # /api/medium — connect + publish
│   │   ├── feeds.py                      # /api/feeds — RSS feed management + crawling
│   │   ├── dashboard.py                  # /api/dashboard — pipeline stats + bulk actions
│   │   ├── schedule.py                   # /api/schedule — content scheduling
│   │   ├── analytics.py                  # /api/analytics — post event tracking
│   │   ├── seo.py                        # /api/seo — SEO analysis + tracking
│   │   ├── comments.py                   # /api/comments — collaborative comments
│   │   ├── voice_profiles.py             # /api/voice-profiles — voice/tone CRUD
│   │   ├── templates.py                  # /api/templates — content templates CRUD
│   │   ├── import_export.py              # /api/import — bulk import (md/url/wordpress)
│   │   ├── newsletter.py                 # /api/newsletter — email distribution
│   │   ├── prompts.py                    # /api/prompts — prompt management
│   │   ├── keywords.py                   # /api/keywords — topic keyword management
│   │   ├── user.py                       # /api/user — profile + settings
│   │   └── diagnostics.py               # /api/diagnostics — health checks
│   ├── services/
│   │   ├── blog_service.py               # URL analysis + GPT-4o blog generation
│   │   ├── ai_editor.py                  # AI-powered content editing
│   │   ├── export_service.py             # MD / HTML / PDF / DOCX / MDX export
│   │   ├── linkedin_service.py           # AI-composed LinkedIn posts (multi-agent)
│   │   ├── twitter_service.py            # AI-composed tweets
│   │   ├── medium_service.py             # Medium article preparation
│   │   ├── newsletter_service.py         # Newsletter formatting + sending
│   │   ├── feed_crawler.py               # RSS/Atom feed discovery + crawling
│   │   ├── relevance_classifier.py       # Two-stage relevance classification
│   │   ├── auto_publisher.py             # Automated pipeline: rank → generate → publish
│   │   ├── scheduler.py                  # APScheduler setup + periodic tasks
│   │   ├── schedule_executor.py          # Execute due scheduled publishes
│   │   ├── notification_service.py       # Webhook notifications (Teams/Slack/generic)
│   │   ├── image_generator.py            # DALL-E hero image generation
│   │   ├── hashtag_agent.py              # AI hashtag generation agent
│   │   ├── humanizer_agent.py            # AI post humanization agent
│   │   └── validation_agent.py           # AI post validation agent
│   ├── tools/
│   │   ├── github_analyzer.py            # GitHub repo structure + content analysis
│   │   ├── webpage_analyzer.py           # Webpage content extraction
│   │   ├── blog_publisher.py             # GitHub PR creation
│   │   ├── linkedin_publisher.py         # LinkedIn OAuth + UGC publishing
│   │   ├── twitter_publisher.py          # Twitter OAuth 2.0 + tweet publishing
│   │   └── medium_publisher.py           # Medium API publishing
│   ├── prompts/
│   │   ├── system_prompt.md              # Blog generation system prompt
│   │   ├── editor_prompt.md              # AI editor system prompt
│   │   ├── linkedin_post_prompt.md       # LinkedIn composition prompt
│   │   └── twitter_post_prompt.md        # Twitter composition prompt
│   └── tests/
│       ├── conftest.py                   # Test fixtures (FastAPI TestClient, mocks)
│       ├── test_blog_service.py          # Blog generation tests
│       ├── test_export_service.py        # Export format tests
│       ├── test_routers.py               # Core router tests
│       ├── test_analytics.py             # Analytics endpoint tests
│       ├── test_comments.py              # Comments CRUD tests
│       ├── test_seo.py                   # SEO analysis + endpoint tests
│       └── test_schedule.py              # Scheduling endpoint tests
│
├── frontend/
│   ├── package.json                      # Dependencies + scripts
│   ├── vite.config.ts                    # Vite config with API proxy
│   ├── vitest.config.ts                  # Vitest test config
│   ├── tsconfig.json                     # TypeScript config
│   └── src/
│       ├── main.tsx                      # React Router setup + routes
│       ├── index.css                     # Tailwind + dark mode overrides
│       ├── types.ts                      # Shared TypeScript interfaces
│       ├── auth/
│       │   ├── msalConfig.ts             # MSAL configuration
│       │   ├── AuthProvider.tsx           # MSAL provider wrapper
│       │   ├── AuthGuard.tsx             # Route protection component
│       │   └── LoginPage.tsx             # Login page
│       ├── pages/
│       │   ├── Home.tsx                  # Dashboard: URL gen, drafts, import, trending
│       │   ├── Editor.tsx                # Full editor: Monaco, preview, AI, SEO, comments, schedule
│       │   ├── BlogView.tsx              # Published blog viewer
│       │   ├── Dashboard.tsx             # Pipeline analytics + article management
│       │   ├── Calendar.tsx              # Content calendar (drafts, published, scheduled)
│       │   ├── Settings.tsx              # Settings hub with tab navigation
│       │   └── settings/
│       │       ├── GeneralSettings.tsx   # General app settings
│       │       ├── FeedsSettings.tsx     # RSS feed management
│       │       ├── SchedulerSettings.tsx # Crawl scheduler configuration
│       │       ├── PromptsSettings.tsx   # Prompt editor
│       │       ├── KeywordsSettings.tsx  # Topic keyword management
│       │       ├── VoiceProfilesSettings.tsx # Voice/tone profiles
│       │       ├── TemplatesSettings.tsx # Content templates
│       │       └── DiagnosticsSettings.tsx # System diagnostics
│       ├── components/
│       │   ├── Layout.tsx                # App layout: nav, theme toggle, decorative bg
│       │   ├── MonacoEditor.tsx          # Monaco editor wrapper
│       │   ├── MarkdownPreview.tsx       # Markdown/MDX preview renderer
│       │   ├── AIEditPanel.tsx           # AI editing side panel
│       │   ├── SEOPanel.tsx              # SEO analysis side panel
│       │   ├── CommentsPanel.tsx         # Threaded comments side panel
│       │   ├── VersionHistoryPanel.tsx   # Version history side panel
│       │   ├── ScheduleModal.tsx         # Schedule publish modal (date/time/platforms)
│       │   ├── ImportModal.tsx           # Bulk import modal (markdown/URL/WordPress)
│       │   ├── ExportDropdown.tsx        # Export format dropdown
│       │   ├── DistributeDropdown.tsx    # Social distribution dropdown
│       │   ├── LinkedInButton.tsx        # LinkedIn compose + publish
│       │   ├── TwitterButton.tsx         # Twitter compose + publish
│       │   ├── MediumButton.tsx          # Medium publish
│       │   ├── NewsletterButton.tsx      # Newsletter send modal
│       │   ├── ProfileDropdown.tsx       # User profile menu
│       │   └── ToastContainer.tsx        # Toast notification system
│       ├── services/
│       │   ├── api.ts                    # API client (REST + SSE) with auth headers
│       │   └── __tests__/
│       │       └── api.test.ts           # API client tests
│       ├── store/
│       │   ├── blogStore.ts              # Blog/draft state (Zustand)
│       │   ├── themeStore.ts             # Dark/light theme state
│       │   ├── toastStore.ts             # Toast notification state
│       │   └── __tests__/
│       │       ├── themeStore.test.ts    # Theme store tests
│       │       └── toastStore.test.ts    # Toast store tests
│       └── test/
│           └── setup.ts                  # Vitest test setup
│
├── infra/
│   └── terraform/                        # Azure Infrastructure as Code
│       ├── main.tf                       # Container Apps, ACR, Log Analytics
│       ├── variables.tf                  # Input variables
│       ├── outputs.tf                    # Output values
│       ├── versions.tf                   # Provider versions
│       ├── entra.tf                      # Entra ID app registration
│       ├── backend.tf                    # Remote state backend (Azure Storage)
│       └── terraform.tfvars.example      # Variable template
│
├── .github/
│   └── workflows/
│       └── cicd-main.yml                 # CI/CD: Terraform + Docker + deploy
│
├── docker-compose.yml                    # Local dev: backend + frontend with hot reload
├── Dockerfile.dev-backend                # Dev backend container (Python + WeasyPrint)
├── Dockerfile.dev-frontend               # Dev frontend container (Node + Vite)
├── Dockerfile.webapp                     # Production multi-stage build
├── Dockerfile                            # Agent container build
├── agent.yaml                            # Azure AI Agent Framework manifest
├── requirements.txt                      # Agent Python dependencies
├── deploy-local.ps1                      # Windows local deploy script
├── setup.sh                              # Dev setup script (Linux/macOS)
├── setup.bat                             # Dev setup script (Windows)
├── .env.example                          # Environment variable template
└── README.md
```

---

## Automation Pipeline Flow

```text
┌─────────────────┐
│ RSS Feed Sources │  (configured in Settings → Feeds)
│ (APScheduler)    │
└────────┬────────┘
         │ crawl on interval
         ▼
┌─────────────────┐     ┌─────────────────┐
│ Feed Crawler     │────▶│ Dedup + Age     │
│ (RSS/Atom/HTML)  │     │ Filter          │
└─────────────────┘     └────────┬────────┘
                                 │
                                 ▼
                        ┌─────────────────┐
                        │ Relevance       │
                        │ Classifier      │
                        │ (keywords +     │
                        │  GPT-4o AI)     │
                        └────────┬────────┘
                                 │ relevant articles
                                 ▼
                        ┌─────────────────┐
                        │ Technicality    │
                        │ Ranking         │
                        │ (GPT-4o)        │
                        └────────┬────────┘
                                 │ top N articles
                                 ▼
                    ┌────────────────────────┐
                    │ Blog Generator (GPT-4o)│
                    │ + Hero Image (DALL-E)  │
                    └───────────┬────────────┘
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              ┌──────────┐ ┌────────┐ ┌─────────┐
              │ Publish   │ │LinkedIn│ │ Twitter │
              │ Blog      │ │ Post   │ │ Tweet   │
              │ (Cosmos)  │ │(GPT-4o)│ │(GPT-4o) │
              └──────────┘ └────────┘ └─────────┘
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              ┌──────────┐ ┌────────┐ ┌─────────┐
              │ Webhook   │ │ Retry  │ │ Daily   │
              │ Notify    │ │ Queue  │ │ Limits  │
              └──────────┘ └────────┘ └─────────┘
```

---

## Screenshots

The application features a clean, modern UI with:

- **Home page** — URL input with gradient accents, drafts list with origin/tag filters, trending articles sidebar, published blogs, crawl activity
- **Editor** — Split-pane Monaco editor + live preview, toolbar with AI edit, SEO analysis, version history, comments, schedule, export, distribute
- **Dashboard** — Stats cards, pipeline health, top topics, daily activity chart, sortable article table with bulk actions
- **Calendar** — Monthly calendar view with color-coded items (cyan=drafts, emerald=published, amber=queued, violet=scheduled)
- **Settings** — Tabbed interface for feeds, scheduler, prompts, keywords, voice profiles, templates, diagnostics

---

## License

Private repository. All rights reserved.
