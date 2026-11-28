# Real values live in terraform.tfvars (gitignored). Public repo: no IDs, emails or names here.

variable "subscription_id" {
  description = "Subscription of the Blog Writer resources. Null uses ARM_SUBSCRIPTION_ID or the az login default."
  type        = string
  default     = null
}

variable "resource_group_name" {
  description = "Resource group holding the container app environment and the easyauth identity."
  type        = string
  default     = "rg-blog-writer-dev"
}

variable "environment_name" {
  description = "Existing Container Apps environment (its default domain forms the app's FQDN)."
  type        = string
  default     = "cae-blog-writer-dev"
}

variable "container_app_name" {
  description = "The v2 container app (must match infra/v2 var.container_app_name)."
  type        = string
  default     = "ca-blog-writer-v2"
}

variable "easyauth_identity_name" {
  description = "User-assigned identity the Easy Auth sidecar uses as the app registration's federated credential (created by infra/v2)."
  type        = string
  default     = "id-blogwriter-v2-easyauth"
}

variable "owner_object_id" {
  description = "Entra object ID of the owner: the app registration's owner and its only assigned user."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.owner_object_id)) && var.owner_object_id != "00000000-0000-0000-0000-000000000000"
    error_message = "owner_object_id must be the owner's real Entra object ID."
  }
}

variable "entra_app_display_name" {
  description = "Display name of the Entra app registration."
  type        = string
  default     = "Blog Writer v2"
}

variable "custom_domain_hostname" {
  description = "Custom domain once v2 takes it over (cutover), e.g. writer.example.com. Adds its Easy Auth redirect URI."
  type        = string
  default     = null
}

variable "easyauth_cli_access_enabled" {
  description = "Expose api://<client id> with a user_impersonation scope pre-authorized for the Azure CLI, so the owner can call the app with a bearer token (live testing). Keep equal to infra/v2's variable of the same name."
  type        = bool
  default     = true
}

variable "federated_credential_enabled" {
  description = "Use the easyauth managed identity as the app's federated credential (no client secret). Keep true unless infra/v2 uses easyauth_credential = client_secret."
  type        = bool
  default     = true
}
