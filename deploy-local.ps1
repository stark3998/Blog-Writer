[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Tag = (Get-Date -Format "yyyyMMddHHmmss"),
    [switch]$DryRun,
    [switch]$SkipHealthCheck,
    [switch]$SkipInfraApply
)

$ErrorActionPreference = "Stop"

if ($DryRun) {
    $WhatIfPreference = $true
}

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required but was not found in PATH."
    }
}

function Invoke-Terraform {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & terraform @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Terraform command failed: terraform $($Arguments -join ' ')"
    }
}

function Invoke-AcrBuild {
    param(
        [Parameter(Mandatory = $true)][string]$Registry,
        [Parameter(Mandatory = $true)][string]$Tag
    )

    & az acr build --registry $Registry --image "blog-writer-webapp:$Tag" --file Dockerfile.webapp .
    if ($LASTEXITCODE -ne 0) {
        throw "ACR build failed."
    }
}

function Get-TerraformOutput {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [switch]$AllowMissing
    )

    $value = & terraform -chdir=infra/terraform output -raw $Name
    if ($LASTEXITCODE -ne 0) {
        if ($AllowMissing) {
            return ""
        }
        throw "Failed to read Terraform output '$Name'."
    }
    return $value.Trim()
}

function Update-ContainerImageInTfvars {
    param(
        [Parameter(Mandatory = $true)][string]$TfvarsPath,
        [Parameter(Mandatory = $true)][string]$Image
    )

    $content = Get-Content -Path $TfvarsPath -Raw
    $updated = [regex]::Replace(
        $content,
        'container_image\s*=\s*"[^"]*"',
        "container_image = `"$Image`"",
        1
    )

    if ($updated -eq $content) {
        throw "Could not locate container_image in $TfvarsPath"
    }

    Set-Content -Path $TfvarsPath -Value $updated -NoNewline
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$tfvarsPath = Join-Path $repoRoot "infra/terraform/terraform.tfvars"
if (-not (Test-Path $tfvarsPath)) {
    throw "Missing infra/terraform/terraform.tfvars. Copy infra/terraform/terraform.tfvars.example first."
}

Write-Step "Checking prerequisites"
Assert-Command -Name "az"
Assert-Command -Name "terraform"

$accountJson = az account show --output json 2>$null
if (-not $accountJson) {
    throw "Azure CLI is not logged in. Run 'az login' first."
}

$account = $accountJson | ConvertFrom-Json
Write-Host "Azure subscription: $($account.name) [$($account.id)]"

Write-Step "Initializing and validating Terraform"
Invoke-Terraform -Arguments @('-chdir=infra/terraform', 'init', '-input=false')
Invoke-Terraform -Arguments @('-chdir=infra/terraform', 'validate')

if ($SkipInfraApply) {
    Write-Step "Planning infrastructure changes (infra apply skipped)"
    Invoke-Terraform -Arguments @('-chdir=infra/terraform', 'plan', '-out=tfplan-infra-local', '-input=false')
} else {
    Write-Step "Planning infrastructure changes"
    Invoke-Terraform -Arguments @('-chdir=infra/terraform', 'plan', '-out=tfplan-infra-local', '-input=false')
    if ($PSCmdlet.ShouldProcess('Azure infrastructure', 'terraform apply -auto-approve tfplan-infra-local')) {
        Invoke-Terraform -Arguments @('-chdir=infra/terraform', 'apply', '-auto-approve', 'tfplan-infra-local')
    }
}

$acrName = Get-TerraformOutput -Name 'acr_name' -AllowMissing
$acrLoginServer = Get-TerraformOutput -Name 'acr_login_server' -AllowMissing

if (-not $acrName -or -not $acrLoginServer) {
    if ($WhatIfPreference) {
        Write-Warning 'Terraform outputs for ACR are not available yet. Skipping image build and app rollout preview.'
        return
    }

    throw 'Terraform outputs for ACR are not available. Run the infrastructure deployment first.'
}

$fullImage = "$acrLoginServer/blog-writer-webapp:$Tag"

Write-Step "Building and pushing image with ACR Tasks"
Write-Host "Image: $fullImage"
if ($PSCmdlet.ShouldProcess($fullImage, 'az acr build')) {
    Invoke-AcrBuild -Registry $acrName -Tag $Tag
}

Write-Step "Persisting latest image tag to terraform.tfvars"
if ($PSCmdlet.ShouldProcess($tfvarsPath, "Update container_image to $fullImage")) {
    Update-ContainerImageInTfvars -TfvarsPath $tfvarsPath -Image $fullImage
}

Write-Step "Planning and applying app rollout"
Invoke-Terraform -Arguments @('-chdir=infra/terraform', 'validate')
if ($WhatIfPreference) {
    Invoke-Terraform -Arguments @('-chdir=infra/terraform', 'plan', '-out=tfplan-app-local', '-input=false', '-var', "container_image=$fullImage")
} else {
    Invoke-Terraform -Arguments @('-chdir=infra/terraform', 'plan', '-out=tfplan-app-local', '-input=false')
    if ($PSCmdlet.ShouldProcess('Azure Container App rollout', 'terraform apply -auto-approve tfplan-app-local')) {
        Invoke-Terraform -Arguments @('-chdir=infra/terraform', 'apply', '-auto-approve', 'tfplan-app-local')
    }
}

$appUrl = Get-TerraformOutput -Name 'container_app_url'
$portalUrl = Get-TerraformOutput -Name 'portal_resource_group_url'

if ($WhatIfPreference) {
    Write-Step 'Preview complete'
    Write-Host 'No Azure resources or local files were changed.'
} elseif (-not $SkipHealthCheck) {
    Write-Step "Running health check"
    $health = Invoke-RestMethod -Uri "$appUrl/api/health" -Method Get -TimeoutSec 60
    Write-Host ("Health: " + ($health | ConvertTo-Json -Compress))
}

Write-Step "Deployment complete"
Write-Host "App URL: $appUrl"
Write-Host "Portal URL: $portalUrl"