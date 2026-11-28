#!/usr/bin/env bash
# Build the Blog Writer v2 image in ACR, roll the container app to it and smoke-test it.
#
# The owner's local helper. .github/workflows/deploy.yml runs the same commands one step at a
# time, so the freeze windows, the rollout wait and the smoke checks exist only here.
# Needs az (signed in: `az login` locally, GitHub OIDC in CI), git, curl and jq. No secrets.
# Runs on macOS's bash 3.2 as well as bash 5.
#
# Usage: scripts/deploy-v2.sh [options] [command ...]
#
# Commands (default: freeze build update wait smoke; with --skip-build:
# freeze image-exists update wait smoke):
#   freeze        refuse to deploy inside a publish window (America/New_York): Monday 08:30-09:30
#                 and Tuesday-Thursday 09:30-12:30, unless --force
#   build         az acr build of the repository root: blog-writer-v2:<tag>, BUILD_SHA=<tag>
#   image-exists  check that blog-writer-v2:<tag> is in the registry (before a rollback)
#   update        az containerapp update --image only (the image bakes BW_BUILD_SHA=<tag>;
#                 Terraform owns every environment variable, so none are set here)
#   wait          poll until the app's latest revision runs the image and is ready (bounded)
#   smoke         GET /api/health returns 200 and <tag>; GET / and GET /api/me without a
#                 session are refused (302 to the Easy Auth login, or 401)
#
# Options (each falls back to the environment variable in brackets):
#   -r, --registry NAME         [ACR_NAME]            container registry name
#   -g, --resource-group NAME   [RESOURCE_GROUP]      resource group of the container app
#       --registry-group NAME   [ACR_RESOURCE_GROUP]  resource group of the registry (default: -g)
#   -a, --app NAME              [CONTAINER_APP]       container app name
#       --container NAME        [CONTAINER_NAME]      container to update, if the app has several
#   -t, --tag TAG               [IMAGE_TAG]           image tag and build SHA. Default: the short
#                               HEAD SHA, plus -dirty-<UTC time> when server/, web/, contracts/,
#                               Dockerfile or .dockerignore have uncommitted changes (a unique tag,
#                               so a second dirty deploy still rolls out)
#       --skip-build                                  deploy an existing tag (rollback)
#   -f, --force                 [DEPLOY_FORCE=true]   deploy inside a publish window
#       --url ORIGIN            [SMOKE_BASE_URL]      smoke-test this origin, not the app's FQDN
#   -h, --help
# Also: ACR_LOGIN_SERVER (default: read from the registry), DEPLOY_WAIT_TIMEOUT_S (900),
# DEPLOY_SMOKE_TIMEOUT_S (300).
#
# Examples:
#   ACR_NAME=myregistry RESOURCE_GROUP=my-rg CONTAINER_APP=my-app scripts/deploy-v2.sh
#   scripts/deploy-v2.sh -r myregistry -g my-rg -a my-app --skip-build -t 3f2c1ab   # roll back
#
# Output is safe for public CI logs: Azure CLI messages pass through a filter that hides GUIDs
# (subscription, tenant and object IDs) and email addresses.

set -euo pipefail

readonly IMAGE_REPO="blog-writer-v2"
readonly TIMEZONE="America/New_York"
# BW_BUILD_SHA's rule in blogwriter.core.settings; also a valid image tag.
readonly TAG_PATTERN='^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$'
# Azure resource names as this script accepts them (never starting with "-", no spaces).
readonly NAME_PATTERN='^[A-Za-z0-9][A-Za-z0-9._()-]{0,89}$'

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

ACR_NAME=${ACR_NAME:-}
ACR_RESOURCE_GROUP=${ACR_RESOURCE_GROUP:-}
ACR_LOGIN_SERVER=${ACR_LOGIN_SERVER:-}
RESOURCE_GROUP=${RESOURCE_GROUP:-}
CONTAINER_APP=${CONTAINER_APP:-}
CONTAINER_NAME=${CONTAINER_NAME:-}
TAG=${IMAGE_TAG:-}
SMOKE_BASE_URL=${SMOKE_BASE_URL:-}
DEPLOY_FORCE=${DEPLOY_FORCE:-false}
WAIT_TIMEOUT_S=${DEPLOY_WAIT_TIMEOUT_S:-900}
SMOKE_TIMEOUT_S=${DEPLOY_SMOKE_TIMEOUT_S:-300}
SKIP_BUILD=false
COMMANDS=""
WORK_DIR=""

# --- output -------------------------------------------------------------------------------------

log() { printf '%s\n' "$*" >&2; }

# Ends the script; in GitHub Actions the message also becomes an error annotation.
die() {
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    printf '::error::%s\n' "$*" >&2
  else
    printf 'deploy-v2: error: %s\n' "$*" >&2
  fi
  exit 1
}

usage() { sed -n '2,/^$/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }

# Appends a line to the GitHub job summary (no-op outside Actions).
summary() {
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then printf '%s\n' "$*" >>"$GITHUB_STEP_SUMMARY"; fi
}

# Line-buffered sed where the flag exists (GNU: -u, BSD: -l), so build logs stream live.
if sed --version >/dev/null 2>&1; then SED_STREAM="-u"; else SED_STREAM="-l"; fi
readonly SED_STREAM

# Hides GUIDs (subscription, tenant, object and client IDs) and email addresses. Azure error
# messages quote resource IDs and principals, and public repository logs are readable by anyone.
redact() {
  sed -E "$SED_STREAM" \
    -e 's/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/<guid>/g' \
    -e 's/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/<email>/g'
}

# Runs az with its stderr (warnings and errors) redacted. Stdout stays the caller's: every query
# here selects plain fields (names, states, images), never whole resources.
az_() {
  local err status=0
  err="$WORK_DIR/az.stderr"
  az "$@" --only-show-errors 2>"$err" || status=$?
  redact <"$err" >&2
  return "$status"
}

# --- configuration ------------------------------------------------------------------------------

need_value() { [[ "$2" -ge 2 ]] || die "$1 needs a value"; }

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -r | --registry) need_value "$1" $#; ACR_NAME=$2; shift 2 ;;
      -g | --resource-group) need_value "$1" $#; RESOURCE_GROUP=$2; shift 2 ;;
      --registry-group) need_value "$1" $#; ACR_RESOURCE_GROUP=$2; shift 2 ;;
      -a | --app) need_value "$1" $#; CONTAINER_APP=$2; shift 2 ;;
      --container) need_value "$1" $#; CONTAINER_NAME=$2; shift 2 ;;
      -t | --tag) need_value "$1" $#; TAG=$2; shift 2 ;;
      --url) need_value "$1" $#; SMOKE_BASE_URL=$2; shift 2 ;;
      --skip-build) SKIP_BUILD=true; shift ;;
      -f | --force) DEPLOY_FORCE=true; shift ;;
      -h | --help) usage; exit 0 ;;
      -*) die "unknown option: $1 (see --help)" ;;
      freeze | build | image-exists | update | wait | smoke) COMMANDS="$COMMANDS $1"; shift ;;
      *) die "unknown command: $1 (see --help)" ;;
    esac
  done
  if [[ -z "$COMMANDS" ]]; then
    if [[ "$SKIP_BUILD" == true ]]; then
      COMMANDS="freeze image-exists update wait smoke"
    else
      COMMANDS="freeze build update wait smoke"
    fi
  fi
  ACR_RESOURCE_GROUP=${ACR_RESOURCE_GROUP:-$RESOURCE_GROUP}
  case "$DEPLOY_FORCE" in
    true | 1 | yes) DEPLOY_FORCE=true ;;
    *) DEPLOY_FORCE=false ;;
  esac
}

# Fails unless every named variable is set and looks like an Azure resource name.
require() {
  local name value
  for name in "$@"; do
    value=${!name:-}
    [[ -n "$value" ]] || die "missing $name (an option or environment variable; see --help)"
    [[ "$value" =~ $NAME_PATTERN ]] || die "$name is not a valid resource name: $value"
  done
}

for_commands() { # true when any of the given commands will run
  local command
  for command in "$@"; do
    case " $COMMANDS " in *" $command "*) return 0 ;; esac
  done
  return 1
}

need_tools() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is required but not installed"
  done
}

default_tag() {
  local sha
  sha=$(git -C "$REPO_ROOT" rev-parse --short HEAD) || die "not a git checkout: pass --tag"
  if [[ -n "$(git -C "$REPO_ROOT" status --porcelain -- server web contracts Dockerfile .dockerignore)" ]]; then
    printf '%s-dirty-%s\n' "$sha" "$(date -u +%Y%m%d%H%M%S)"
  else
    printf '%s\n' "$sha"
  fi
}

resolve_config() {
  if for_commands build image-exists update wait smoke; then
    if [[ -z "$TAG" ]]; then
      need_tools git
      TAG=$(default_tag)
    fi
    [[ "$TAG" =~ $TAG_PATTERN ]] || die "invalid tag '$TAG': 1-64 characters of [0-9A-Za-z._-], starting with a letter or digit"
    need_tools jq curl
  fi
  if for_commands build image-exists update wait || { for_commands smoke && [[ -z "$SMOKE_BASE_URL" ]]; }; then
    need_tools az
  fi
  if for_commands build image-exists update wait; then require ACR_NAME; fi
  if for_commands build; then require ACR_RESOURCE_GROUP; fi
  if for_commands update wait; then require RESOURCE_GROUP CONTAINER_APP; fi
  if for_commands smoke && [[ -z "$SMOKE_BASE_URL" ]]; then require RESOURCE_GROUP CONTAINER_APP; fi
  if [[ -n "$CONTAINER_NAME" ]]; then require CONTAINER_NAME; fi
  # Plain integers only: bash arithmetic would evaluate anything else as an expression.
  [[ "$WAIT_TIMEOUT_S" =~ ^[0-9]{1,5}$ ]] || die "DEPLOY_WAIT_TIMEOUT_S must be a number of seconds"
  [[ "$SMOKE_TIMEOUT_S" =~ ^[0-9]{1,5}$ ]] || die "DEPLOY_SMOKE_TIMEOUT_S must be a number of seconds"
}

login_server() {
  local server=""
  if [[ -n "$ACR_LOGIN_SERVER" ]]; then
    printf '%s\n' "$ACR_LOGIN_SERVER"
    return
  fi
  if server=$(az_ acr show --name "$ACR_NAME" --resource-group "$ACR_RESOURCE_GROUP" \
    --query loginServer --output tsv) && [[ -n "$server" ]]; then
    printf '%s\n' "$server"
  else
    log "Could not read the registry's login server; assuming <name>.azurecr.io."
    printf '%s.azurecr.io\n' "$(printf '%s' "$ACR_NAME" | tr '[:upper:]' '[:lower:]')"
  fi
}

image_ref() { printf '%s/%s:%s\n' "$(login_server)" "$IMAGE_REPO" "$TAG"; }

# --- freeze -------------------------------------------------------------------------------------

# Prints the publish window a moment falls in, or nothing. $1: ISO weekday (1 = Monday), $2: HHMM,
# both in America/New_York. Windows are half-open: 09:30 on a Monday is already outside.
freeze_window() {
  local day=$1 hhmm=$((10#$2))
  if [[ "$day" == 1 ]] && ((hhmm >= 830 && hhmm < 930)); then
    printf 'Monday 08:30-09:30\n'
  elif [[ "$day" == [234] ]] && ((hhmm >= 930 && hhmm < 1230)); then
    printf 'Tuesday-Thursday 09:30-12:30\n'
  fi
}

cmd_freeze() {
  local window
  window=$(freeze_window "$(TZ="$TIMEZONE" date +%u)" "$(TZ="$TIMEZONE" date +%H%M)")
  if [[ -z "$window" ]]; then
    log "Deploy freeze: outside the publish windows."
  elif [[ "$DEPLOY_FORCE" == true ]]; then
    log "Deploy freeze: inside the $window publish window ($TIMEZONE); continuing (--force)."
  else
    die "Deploy freeze: inside the $window publish window ($TIMEZONE). Deploy after it ends, or re-run with --force (workflow input: force)."
  fi
}

# --- build and rollout --------------------------------------------------------------------------

cmd_build() {
  local path
  # Everything the Dockerfile copies: a missing file fails here, not 10 minutes into a build.
  for path in Dockerfile .dockerignore server/pyproject.toml server/uv.lock server/README.md \
    server/src server/vendor/package.json server/vendor/package-lock.json web/package.json \
    web/package-lock.json contracts/published-blogs.v2.schema.json contracts/sanitizer-profile.json; do
    [[ -e "$REPO_ROOT/$path" ]] || die "$path is missing; the image cannot be built"
  done
  log "Building ${IMAGE_REPO}:${TAG} in registry ${ACR_NAME} (az acr build) ..."
  # pipefail: the pipeline fails when az does.
  az acr build --registry "$ACR_NAME" --resource-group "$ACR_RESOURCE_GROUP" \
    --image "${IMAGE_REPO}:${TAG}" --build-arg "BUILD_SHA=${TAG}" \
    --file "$REPO_ROOT/Dockerfile" --platform linux/amd64 --timeout 3600 --output none \
    "$REPO_ROOT" 2>&1 | redact
  summary "Built \`${IMAGE_REPO}:${TAG}\`."
}

cmd_image_exists() {
  az_ acr repository show --name "$ACR_NAME" --image "${IMAGE_REPO}:${TAG}" --output none \
    || die "${IMAGE_REPO}:${TAG} was not found in registry ${ACR_NAME} (or cannot be read)"
  log "Found ${IMAGE_REPO}:${TAG} in registry ${ACR_NAME}."
}

# The image the target container runs now (empty if it cannot be read).
current_image() {
  local containers
  containers=$(az_ containerapp show --name "$CONTAINER_APP" --resource-group "$RESOURCE_GROUP" \
    --query "properties.template.containers[].{name: name, image: image}" --output json) || return 0
  jq -r --arg name "$CONTAINER_NAME" \
    'if $name == "" then .[0].image else (map(select(.name == $name)) | .[0].image) end // empty' \
    <<<"$containers"
}

cmd_update() {
  local image previous
  image=$(image_ref)
  previous=$(current_image)
  log "Updating ${CONTAINER_APP}: ${previous:-unknown image} -> ${image}"
  # ${CONTAINER_NAME:+...} stays unquoted: it expands to nothing or to two words.
  az_ containerapp update --name "$CONTAINER_APP" --resource-group "$RESOURCE_GROUP" \
    ${CONTAINER_NAME:+--container-name "$CONTAINER_NAME"} \
    --image "$image" --output none
  if [[ "$previous" == */"${IMAGE_REPO}:"* && "$previous" != "$image" ]]; then
    log "Previous tag: ${previous##*:}. To roll back: --skip-build --tag ${previous##*:} (workflow input rollback_to)."
    summary "Previous image: \`${previous##*/}\`; roll back with \`rollback_to=${previous##*:}\`."
  fi
}

cmd_wait() {
  local image deadline app latest ready revision provisioning running health state=""
  image=$(image_ref)
  deadline=$((SECONDS + WAIT_TIMEOUT_S))
  log "Waiting up to ${WAIT_TIMEOUT_S}s for a ready revision running ${image} ..."
  while :; do
    if app=$(az_ containerapp show --name "$CONTAINER_APP" --resource-group "$RESOURCE_GROUP" \
      --query "{latest: properties.latestRevisionName, ready: properties.latestReadyRevisionName}" \
      --output json); then
      latest=$(jq -r '.latest // ""' <<<"$app")
      ready=$(jq -r '.ready // ""' <<<"$app")
      if [[ -n "$latest" ]] && revision=$(az_ containerapp revision show --name "$CONTAINER_APP" \
        --resource-group "$RESOURCE_GROUP" --revision "$latest" \
        --query "{images: properties.template.containers[].image, provisioning: properties.provisioningState, running: properties.runningState, health: properties.healthState}" \
        --output json); then
        if jq -e --arg image "$image" 'any(.images[]?; . == $image)' <<<"$revision" >/dev/null; then
          provisioning=$(jq -r '.provisioning // ""' <<<"$revision")
          running=$(jq -r '.running // ""' <<<"$revision")
          health=$(jq -r '.health // ""' <<<"$revision")
          state="revision ${latest}: provisioning ${provisioning:-?}, running ${running:-?}, health ${health:-?}; latest ready: ${ready:-none}"
          if [[ "$provisioning" == Failed || "$running" == Failed ]]; then
            die "Rollout failed: $state"
          fi
          if [[ "$latest" == "$ready" && "$provisioning" == Provisioned && "$running" == Running* && "$health" != Unhealthy ]]; then
            log "Ready: $state"
            return 0
          fi
        else
          state="latest revision ${latest} does not run ${image}"
        fi
      fi
    fi
    ((SECONDS < deadline)) || die "Timed out after ${WAIT_TIMEOUT_S}s waiting for the rollout. Last state: ${state:-unknown}"
    log "  ${state:-waiting for the app}"
    sleep 15
  done
}

# --- smoke --------------------------------------------------------------------------------------

smoke_base_url() {
  local fqdn
  if [[ -n "$SMOKE_BASE_URL" ]]; then
    printf '%s\n' "${SMOKE_BASE_URL%/}"
    return
  fi
  fqdn=$(az_ containerapp show --name "$CONTAINER_APP" --resource-group "$RESOURCE_GROUP" \
    --query properties.configuration.ingress.fqdn --output tsv) || die "could not read the app's ingress FQDN"
  [[ -n "$fqdn" ]] || die "the app has no ingress FQDN (is external ingress on?)"
  printf 'https://%s\n' "$fqdn"
}

# HTTP status of a GET without cookies or redirects ("000" when no response); headers go to
# $WORK_DIR/headers.
http_get() { # url body-file [curl options...]
  local url=$1 body=$2 code=""
  shift 2
  code=$(curl --silent --show-error --proto '=http,https' --max-time 20 "$@" \
    --dump-header "$WORK_DIR/headers" --output "$body" --write-out '%{http_code}' "$url") || true
  printf '%s\n' "${code:-000}"
}

expect_refused() { # base path allowed-statuses
  local base=$1 path=$2 allowed=$3 code location=""
  code=$(http_get "$base$path" /dev/null --retry 3 --retry-delay 3 --retry-connrefused)
  case " $allowed " in
    *" $code "*) ;;
    *) die "GET $path without a session returned HTTP $code; expected one of: $allowed" ;;
  esac
  if [[ "$code" == 302 ]]; then
    location=$(tr -d '\r' <"$WORK_DIR/headers" | awk 'tolower($1) == "location:" { print $2; exit }')
    case "$location" in
      */.auth/login/* | https://login.microsoftonline.com/*) ;;
      *) die "GET $path redirected to '${location%%\?*}', not to the Easy Auth login" ;;
    esac
  fi
  log "  GET $path without a session: $code${location:+ -> ${location%%\?*}}"
}

cmd_smoke() {
  local base deadline code="" body="$WORK_DIR/health.json"
  base=$(smoke_base_url)
  log "Smoke-testing ${base} for ${TAG} ..."
  deadline=$((SECONDS + SMOKE_TIMEOUT_S))
  # Traffic may still reach the old revision for a moment, so wait for the new SHA.
  while :; do
    : >"$body"
    code=$(http_get "$base/api/health" "$body")
    if [[ "$code" == 200 ]] && jq -e --arg sha "$TAG" '.status == "ok" and .sha == $sha' "$body" >/dev/null 2>&1; then
      break
    fi
    ((SECONDS < deadline)) || die "GET /api/health did not return 200 with sha ${TAG} within ${SMOKE_TIMEOUT_S}s (last: HTTP ${code}, body: $(head -c 200 "$body" | redact))"
    sleep 10
  done
  log "  GET /api/health: 200, sha ${TAG}"
  expect_refused "$base" / "302 401"
  expect_refused "$base" /api/me "302 401 403"
  summary "Smoke test passed for \`${TAG}\`: health reports the SHA; / and /api/me refuse requests without a session."
}

# --- main ---------------------------------------------------------------------------------------

main() {
  local command
  parse_args "$@"
  resolve_config
  WORK_DIR=$(mktemp -d)
  trap 'rm -rf "$WORK_DIR"' EXIT
  for command in $COMMANDS; do
    case "$command" in
      freeze) cmd_freeze ;;
      build) cmd_build ;;
      image-exists) cmd_image_exists ;;
      update) cmd_update ;;
      wait) cmd_wait ;;
      smoke) cmd_smoke ;;
    esac
  done
}

# Sourcing the file (tests) defines the functions without running anything.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
