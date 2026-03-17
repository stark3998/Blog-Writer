variable "project_name" {
  description = "Project/application name used in resource naming."
  type        = string
  default     = "blog-writer"
}

variable "environment" {
  description = "Environment name (e.g. dev, test, prod)."
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "eastus"
}

variable "location_short" {
  description = "Short location code used for name generation."
  type        = string
  default     = "eus"
}

variable "resource_group_name" {
  description = "Resource group name for all deployed resources."
  type        = string
  default     = "rg-blog-writer-dev"
}

variable "container_app_name" {
  description = "Container App name."
  type        = string
  default     = "ca-blog-writer-dev"
}

variable "container_app_environment_name" {
  description = "Container App Environment name."
  type        = string
  default     = "cae-blog-writer-dev"
}

variable "log_analytics_workspace_name" {
  description = "Log Analytics workspace name."
  type        = string
  default     = "law-blog-writer-dev"
}

variable "acr_sku" {
  description = "Azure Container Registry SKU."
  type        = string
  default     = "Basic"
}

variable "container_image" {
  description = "Container image to run in Container Apps."
  type        = string
  default     = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
}

variable "target_port" {
  description = "Container application port."
  type        = number
  default     = 8080
}

variable "min_replicas" {
  description = "Minimum replicas for Container App."
  type        = number
  default     = 1
}

variable "max_replicas" {
  description = "Maximum replicas for Container App."
  type        = number
  default     = 3
}

variable "container_cpu" {
  description = "CPU cores allocated to the container."
  type        = number
  default     = 1
}

variable "container_memory" {
  description = "Memory allocated to the container."
  type        = string
  default     = "2Gi"
}

variable "container_app_env_vars" {
  description = "Plaintext environment variables for the Container App."
  type        = map(string)
  default = {
    PORT = "8080"
  }
}

variable "container_app_secrets" {
  description = "Sensitive environment variables to inject as Container App secrets."
  type        = map(string)
  default     = {}
}

variable "container_app_secret_env_vars" {
  description = "Map of env var name => secret name from container_app_secrets."
  type        = map(string)
  default     = {}
}

## ── Portfolio Container App ──────────────────────────────────────────

variable "portfolio_container_app_name" {
  description = "Container App name for the portfolio site."
  type        = string
  default     = "ca-portfolio-dev"
}

variable "portfolio_container_image" {
  description = "Container image for the portfolio app."
  type        = string
  default     = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
}

variable "portfolio_target_port" {
  description = "Portfolio container application port."
  type        = number
  default     = 3000
}

variable "portfolio_min_replicas" {
  description = "Minimum replicas for the portfolio Container App."
  type        = number
  default     = 1
}

variable "portfolio_max_replicas" {
  description = "Maximum replicas for the portfolio Container App."
  type        = number
  default     = 3
}

variable "portfolio_container_cpu" {
  description = "CPU cores allocated to the portfolio container."
  type        = number
  default     = 0.5
}

variable "portfolio_container_memory" {
  description = "Memory allocated to the portfolio container."
  type        = string
  default     = "1Gi"
}

variable "portfolio_env_vars" {
  description = "Plaintext environment variables for the portfolio Container App."
  type        = map(string)
  default = {
    NODE_ENV       = "production"
    COSMOS_DATABASE = "blog-writer"
  }
}

variable "portfolio_secrets" {
  description = "Sensitive values to inject as portfolio Container App secrets."
  type        = map(string)
  default     = {}
}

variable "portfolio_secret_env_vars" {
  description = "Map of env var name => secret name for the portfolio Container App."
  type        = map(string)
  default     = {}
}

## ── Entra ID ───────────────────────────────────────────────────────

variable "entra_client_id" {
  description = "Client (application) ID of the existing Entra ID app registration."
  type        = string
}

## ── Common ──────────────────────────────────────────────────────────

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default = {
    app = "blog-writer"
  }
}
