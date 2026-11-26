# GitHub Actions OIDC: federated identity credentials on the user-assigned
# identities. No AZURE_CREDENTIALS or ARM_CLIENT_SECRET anywhere. Subjects are
# case-sensitive and must match the 'sub' claim GitHub issues exactly.

locals {
  github_federations = {
    ci_deploy = {
      identity = "ci_deploy"
      subject  = "repo:${var.github_owner}/${var.github_repo}:environment:${var.github_deploy_environment}"
    }
    ci_infra = {
      identity = "ci_infra"
      subject  = "repo:${var.github_owner}/${var.github_repo}:environment:${var.github_infra_environment}"
    }
    portfolio_build = {
      identity = "portfolio_build"
      subject  = "repo:${local.portfolio_owner}/${var.portfolio_repo}:ref:refs/heads/${var.portfolio_branch}"
    }
  }
}

# One credential per identity, so there are no concurrent federated-credential
# writes on the same identity (Azure rejects those).
resource "azurerm_federated_identity_credential" "github" {
  for_each = local.github_federations

  name                      = "github-${replace(each.key, "_", "-")}"
  user_assigned_identity_id = azurerm_user_assigned_identity.this[each.value.identity].id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = local.github_issuer
  subject                   = each.value.subject
}
