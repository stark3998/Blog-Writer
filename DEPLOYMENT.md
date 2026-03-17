# Blog Writer — Azure Deployment Plan

## Architecture Overview

```
┌──────────────────────────────────────────────────┐
│               Azure Container Apps               │
│  ┌────────────────────────────────────────────┐  │
│  │  Dockerfile.webapp (multi-stage)           │  │
│  │  ┌─────────────┐  ┌────────────────────┐  │  │
│  │  │ React SPA   │  │ FastAPI Backend     │  │  │
│  │  │ (Vite)      │  │ (uvicorn :8080)    │  │  │
│  │  │ /dist       │  │ /api/*             │  │  │
│  │  └─────────────┘  └────────────────────┘  │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
        │                │               │
        ▼                ▼               ▼
  Azure OpenAI     Azure Cosmos DB   GitHub API
  (gpt-4.1-mini)  (blog-writer DB)  (repo analysis
                                     + publishing)
```

**Single container** serves both the React frontend (static files) and FastAPI backend on port 8080.

---

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) installed
- [Terraform >= 1.5](https://developer.hashicorp.com/terraform/downloads) installed
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- An Azure subscription with permissions to create resources
- Existing Azure services already provisioned:
  - **Azure OpenAI** (or AI Foundry) with a `gpt-4.1-mini` deployment
  - **Azure Cosmos DB** account with a `blog-writer` database

---

## Step 1: Authenticate with Azure

```bash
# Login to Azure
az login

# Set your subscription (if you have multiple)
az account set --subscription "<your-subscription-id>"
```

## Fast Path: Local Deploy Script

For repeat deployments from your local machine, run:

```powershell
.\deploy-local.ps1
```

Optional flags:

```powershell
.\deploy-local.ps1 -Tag v2
.\deploy-local.ps1 -SkipInfraApply
.\deploy-local.ps1 -SkipHealthCheck
```

The script will:

- Run `terraform init` and `terraform validate`
- Optionally run infra `plan` and `apply`
- Build and push the app image with `az acr build`
- Update `container_image` in `infra/terraform/terraform.tfvars`
- Run rollout `plan` and `apply`
- Call `/api/health` and print the app and Azure Portal URLs

---

## Step 2: Provision Infrastructure with Terraform

The Terraform config in `infra/terraform/` creates:
- Resource Group
- Log Analytics Workspace
- Container App Environment
- Azure Container Registry (ACR)
- Container App

```bash
cd infra/terraform

# Initialize Terraform
terraform init

# Review the plan
terraform plan

# Apply (creates all resources)
terraform apply
```

After apply, note the outputs:
- `acr_login_server` — your ACR URL (e.g., `blogwriterdeveus******.azurecr.io`)
- `acr_name` — your ACR name
- `container_app_url` — your app's public URL

---

## Step 3: Build and Push the Docker Image

```bash
# Go back to repo root
cd ../..

# Get ACR credentials from Terraform output
ACR_NAME=$(cd infra/terraform && terraform output -raw acr_name)
ACR_LOGIN_SERVER=$(cd infra/terraform && terraform output -raw acr_login_server)

# Login to ACR
az acr login --name $ACR_NAME

# Build the multi-stage image (frontend + backend)
docker build -f Dockerfile.webapp -t $ACR_LOGIN_SERVER/blog-writer-webapp:v1 .
docker build -f Dockerfile.webapp -t blogwriterdeveus6c2uw5.azurecr.io/blog-writer-webapp:v1 .

# Push to ACR
docker push $ACR_LOGIN_SERVER/blog-writer-webapp:v1
```

---

## Step 4: Update Terraform with the Real Image

Edit `infra/terraform/terraform.tfvars` and replace the `container_image` value:

```hcl
container_image = "<your-acr>.azurecr.io/blog-writer-webapp:v1"
```

Then re-apply:

```bash
cd infra/terraform
terraform apply
```

---

## Step 5: Update the LinkedIn Redirect URI

Once your Container App is deployed, get the FQDN:

```bash
terraform output container_app_fqdn
```

Then update these values in `terraform.tfvars`:

```hcl
LINKEDIN_REDIRECT_URI = "https://<your-container-app-fqdn>/api/linkedin/oauth/callback"
```

Also update this redirect URI in your **LinkedIn Developer App** settings at https://www.linkedin.com/developers/.

Run `terraform apply` again to propagate the change.

---

## Step 6: Verify the Deployment

```bash
# Get the app URL
APP_URL=$(cd infra/terraform && terraform output -raw container_app_url)

# Health check
curl $APP_URL/api/health
# Expected: {"status":"healthy","version":"2.0.0"}

# Open in browser
echo $APP_URL
```

---

## Environment Variables Reference

### Plaintext (set in `container_app_env_vars`)

| Variable | Description |
|---|---|
| `PORT` | Server port (8080) |
| `PROJECT_ENDPOINT` | Azure OpenAI / AI Foundry endpoint URL |
| `MODEL_DEPLOYMENT_NAME` | Model deployment name (e.g., `gpt-4.1-mini`) |
| `API_VERSION` | Azure OpenAI API version |
| `COSMOS_ENDPOINT` | Cosmos DB endpoint URL |
| `COSMOS_DATABASE` | Cosmos DB database name |
| `GITHUB_REPO` | Target repo for blog publishing (e.g., `owner/portfolio`) |
| `LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LINKEDIN_CLIENT_ID` | LinkedIn OAuth app client ID |
| `LINKEDIN_REDIRECT_URI` | LinkedIn OAuth callback URL |
| `LINKEDIN_SCOPES` | LinkedIn OAuth scopes |
| `LINKEDIN_COSMOS_SESSION_CONTAINER` | Cosmos container for LinkedIn sessions |
| `LINKEDIN_COSMOS_STATE_CONTAINER` | Cosmos container for OAuth state |
| `LINKEDIN_OAUTH_STATE_TTL_SECONDS` | OAuth state expiry (900s) |

### Secrets (set in `container_app_secrets` + `container_app_secret_env_vars`)

| Variable | Description |
|---|---|
| `PROJECT_API_KEY` | Azure OpenAI API key |
| `COSMOS_KEY` | Cosmos DB primary key |
| `GITHUB_TOKEN` | GitHub PAT (needs `repo` scope for content read/write + PR creation) |
| `LINKEDIN_CLIENT_SECRET` | LinkedIn OAuth client secret |

---

## Redeploying Updates

When you make code changes:

```bash
# Bump the tag (v2, v3, etc.)
TAG=v2

# Build and push
ACR_LOGIN_SERVER=$(cd infra/terraform && terraform output -raw acr_login_server)
docker build -f Dockerfile.webapp -t $ACR_LOGIN_SERVER/blog-writer-webapp:$TAG .
docker push $ACR_LOGIN_SERVER/blog-writer-webapp:$TAG

# Update terraform.tfvars with the new tag
# container_image = "<acr>.azurecr.io/blog-writer-webapp:v2"

# Apply
cd infra/terraform
terraform apply
```

---

## Estimated Azure Costs (Dev/Single Instance)

| Resource | SKU | ~Monthly Cost |
|---|---|---|
| Container Apps | 1 vCPU, 2 GiB | ~$35-50 |
| Container Registry | Basic | ~$5 |
| Log Analytics | 30-day retention | ~$2-5 |
| **Total (infra only)** | | **~$42-60** |

> Cosmos DB and Azure OpenAI costs are separate and depend on usage.

---

## Troubleshooting

```bash
# View Container App logs
az containerapp logs show \
  --name ca-blog-writer-dev \
  --resource-group rg-blog-writer-dev \
  --type console \
  --follow

# Check Container App status
az containerapp show \
  --name ca-blog-writer-dev \
  --resource-group rg-blog-writer-dev \
  --query "properties.runningStatus"

# Restart the Container App
az containerapp revision restart \
  --name ca-blog-writer-dev \
  --resource-group rg-blog-writer-dev \
  --revision <revision-name>
```
