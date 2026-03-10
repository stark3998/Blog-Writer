# Terraform Deployment (Azure)

This module deploys:
- Resource Group
- Log Analytics Workspace
- Container Apps Environment
- Azure Container Registry (ACR)
- Azure Container App (public ingress)

## Prerequisites

- Terraform installed (`terraform version`)
- Azure CLI installed and logged in (`az login`)
- Subscription selected (`az account set --subscription <SUBSCRIPTION_ID>`)

## 1) Build and push app image to ACR

You can first deploy infra with placeholder image, then update image after first apply.

Recommended flow after first apply output gives `acr_login_server`:

```powershell
az acr build -r <acrName> -t blog-writer-webapp:v1 -f Dockerfile.webapp .
```

Then set `container_image` in `terraform.tfvars` to:

```text
<acrLoginServer>/blog-writer-webapp:v1
```

## 2) Configure variables

```powershell
cd infra/terraform
copy terraform.tfvars.example terraform.tfvars
```

Update `terraform.tfvars` values for your environment.

## 3) Deploy

```powershell
terraform init
terraform validate
terraform plan
terraform apply -auto-approve
```

## 4) Get outputs

```powershell
terraform output
```

Notable outputs:
- `container_app_url`
- `acr_login_server`
- `portal_resource_group_url`

## Notes

- This template uses ACR admin credentials for registry pull simplicity and repeatability.
- For stricter production security, replace with managed identity + `AcrPull` role assignment.
- Keep `terraform.tfvars` out of source control when it contains secrets.
