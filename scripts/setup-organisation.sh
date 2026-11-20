#!/usr/bin/env bash
# Sets up a GitHub organisation for the Claude review workflow (.github/workflows/claude-review.yml): the membership
# GitHub App, and the four organisation settings that callers pass to the workflow. See "Setting up an organisation"
# in README.md.
#
#   CLAUDE_REVIEW_APP_CLIENT_ID    variable  The membership app's client id.
#   CLAUDE_REVIEW_APP_PRIVATE_KEY  secret    The membership app's private key.
#   CLAUDE_ROUTINE_TOKEN           secret    The code-review routine's bearer token.
#   CLAUDE_ROUTINE_TRIGGER_ID      variable  The routine's trigger id (trig_...).
#
# Every setting is visible to all of the organisation's repositories. A setting that is already there is kept unless
# you say to replace it. Private keys and tokens only ever travel on stdin to `gh secret set`: they are never shown,
# never put on a command line, and never written to disk by this script.
#
# Needs gh, signed in as an owner of the organisation with the admin:org scope, and jq. Uses python3, when it is
# there, to catch GitHub's answer after it creates the app; without it, you paste an address instead. Runs with the
# bash that comes with macOS (3.2). The environment variable SETUP_ORGANISATION_PYTHON names another python3.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/setup-organisation.sh [--org <org>] [--check | --rotate-key]

Sets up a GitHub organisation for the Claude review workflow: the membership GitHub App, and the organisation
settings CLAUDE_REVIEW_APP_CLIENT_ID, CLAUDE_REVIEW_APP_PRIVATE_KEY, CLAUDE_ROUTINE_TOKEN and
CLAUDE_ROUTINE_TRIGGER_ID.

  --org <org>     The organisation (default: cipherstash).
  --check         Only report what is set up, and whether the app is installed. Changes nothing. Exits 1 if
                  anything needs fixing.
  --rotate-key    Only replace the app's private key with a new one you generate on GitHub.
  -h, --help      Show this help.
EOF
}

CLIENT_ID_VARIABLE=CLAUDE_REVIEW_APP_CLIENT_ID
KEY_SECRET=CLAUDE_REVIEW_APP_PRIVATE_KEY
TOKEN_SECRET=CLAUDE_ROUTINE_TOKEN
TRIGGER_VARIABLE=CLAUDE_ROUTINE_TRIGGER_ID
# The secret the workflow used before it took the client id as a variable. Only reported, never deleted.
OLD_ID_SECRET=CLAUDE_REVIEW_APP_ID

org=cipherstash
mode=setup
while [ $# -gt 0 ]; do
  case "$1" in
    --org)
      [ $# -ge 2 ] || { usage >&2; exit 2; }
      org=$2
      shift 2
      ;;
    --check | --rotate-key)
      [ "$mode" = setup ] || { usage >&2; exit 2; }
      mode=${1#--}
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done
if ! [[ $org =~ ^[A-Za-z0-9][A-Za-z0-9-]*$ ]]; then
  echo "Error: \"$org\" is not an organisation name." >&2
  exit 2
fi

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

script_dir=$(cd "$(dirname "$0")" && pwd)
python=${SETUP_ORGANISATION_PYTHON:-python3}

# Files the script makes (the listener's output, or the page that posts the manifest) go in a private directory that is
# removed on exit, with the listener, if it is still running.
work_dir=$(mktemp -d)
listener_pid=
cleanup() {
  if [ -n "$listener_pid" ]; then
    kill "$listener_pid" 2>/dev/null || true
  fi
  rm -rf "$work_dir"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# Asks $1, with the default $2 shown in brackets, and puts the answer (or the default) in ANSWER.
ask() {
  if [ -n "${2-}" ]; then
    printf '%s [%s]: ' "$1" "$2"
  else
    printf '%s: ' "$1"
  fi
  IFS= read -r ANSWER || { echo; die "No answer, so stopping here."; }
  [ -n "$ANSWER" ] || ANSWER=${2-}
}

# Asks the yes-or-no question $1, with the default $2 (y or n). Returns 0 for yes.
yes_no() {
  local hint='[y/N]'
  [ "$2" = y ] && hint='[Y/n]'
  while :; do
    printf '%s %s ' "$1" "$hint"
    IFS= read -r ANSWER || { echo; die "No answer, so stopping here."; }
    case "${ANSWER:-$2}" in
      y | Y | yes) return 0 ;;
      n | N | no) return 1 ;;
    esac
    echo "Type y or n."
  done
}

# Opens $1 in the browser, and prints it in case that does not work.
open_url() {
  echo "Opening $1 (if no browser opens, copy that address into one)"
  if [ "$(uname -s)" = Darwin ]; then
    open "$1" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$1" >/dev/null 2>&1 || true
  fi
}

# Reads the names of the organisation's secrets into SECRET_NAMES and its variables, as JSON, into VARIABLES.
load_settings() {
  SECRET_NAMES=$(gh api --paginate "orgs/$org/actions/secrets" | jq -r '.secrets[].name') ||
    die "Could not list $org's Actions secrets."
  VARIABLES=$(gh api --paginate "orgs/$org/actions/variables" | jq -s '[.[].variables[]]') ||
    die "Could not list $org's Actions variables."
}

has_secret() {
  printf '%s\n' "$SECRET_NAMES" | grep -qx -- "$1"
}

variable_value() {
  printf '%s' "$VARIABLES" | jq -r --arg name "$1" '.[] | select(.name == $name) | .value'
}

set_variable() {
  gh variable set "$1" --org "$org" --visibility all --body "$2" >/dev/null
  echo "Stored $1 ($2)."
}

# Stores stdin as the secret $1.
set_secret() {
  gh secret set "$1" --org "$org" --visibility all >/dev/null
  echo "Stored $1."
}

# Finds the installation on the organisation of the app with client id $1. Returns 0 and sets INSTALL_SLUG,
# INSTALL_MEMBERS (its Members permission) and INSTALL_SUSPENDED when it is installed, 1 when it is not, and 2 when the
# installations cannot be listed. An installation that does not carry its app's client id is matched through its app.
find_installation() {
  local list slug id members suspended
  INSTALL_SLUG= INSTALL_MEMBERS= INSTALL_SUSPENDED=
  list=$(gh api --paginate "orgs/$org/installations" 2>/dev/null |
    jq -r '.installations[] | [.app_slug, .client_id // "", .permissions.members // "", .suspended_at // ""] | join("|")') ||
    return 2
  while IFS='|' read -r slug id members suspended; do
    [ -n "$slug" ] || continue
    if [ -z "$id" ]; then
      id=$(gh api "apps/$slug" 2>/dev/null | jq -r '.client_id // ""') || id=
    fi
    if [ "$id" = "$1" ]; then
      INSTALL_SLUG=$slug INSTALL_MEMBERS=$members INSTALL_SUSPENDED=$suspended
      return 0
    fi
  done <<EOF
$list
EOF
  return 1
}

# Prints one line of the check: a status (ok, missing, problem or note) and what it is about.
report() {
  printf '  %-8s %s\n' "$1" "$2"
  case "$1" in
    missing | problem) problems=$((problems + 1)) ;;
  esac
}

run_check() {
  local client_id trigger_id rc
  problems=0
  load_settings
  echo "Checking $org for the Claude review workflow:"
  client_id=$(variable_value "$CLIENT_ID_VARIABLE")
  if [ -z "$client_id" ]; then
    report missing "$CLIENT_ID_VARIABLE"
  else
    report ok "$CLIENT_ID_VARIABLE is $client_id"
    find_installation "$client_id" && rc=0 || rc=$?
    case $rc in
      0)
        if [ -n "$INSTALL_SUSPENDED" ]; then
          report problem "The app $INSTALL_SLUG is suspended on $org. Unsuspend it at https://github.com/organizations/$org/settings/installations"
        elif [ "$INSTALL_MEMBERS" != read ]; then
          report problem "The app $INSTALL_SLUG is installed on $org but does not have Members: read. Add it at https://github.com/organizations/$org/settings/apps/$INSTALL_SLUG/permissions and accept it on the installation."
        else
          report ok "The app $INSTALL_SLUG is installed on $org with Members: read"
        fi
        ;;
      1) report problem "No app with client id $client_id is installed on $org. Run this script without --check to install it." ;;
      *) report problem "Could not list the apps installed on $org (that needs an organisation owner, and gh's admin:org scope). Look at https://github.com/organizations/$org/settings/installations" ;;
    esac
  fi
  if has_secret "$KEY_SECRET"; then
    report ok "$KEY_SECRET is set"
  else
    report missing "$KEY_SECRET"
  fi
  if has_secret "$TOKEN_SECRET"; then
    report ok "$TOKEN_SECRET is set"
  else
    report missing "$TOKEN_SECRET"
  fi
  trigger_id=$(variable_value "$TRIGGER_VARIABLE")
  if [ -z "$trigger_id" ]; then
    report missing "$TRIGGER_VARIABLE"
  elif [[ $trigger_id == trig_* ]]; then
    report ok "$TRIGGER_VARIABLE is $trigger_id"
  else
    report problem "$TRIGGER_VARIABLE is $trigger_id, which does not start with trig_"
  fi
  if has_secret "$OLD_ID_SECRET"; then
    report note "$OLD_ID_SECRET is no longer used by the workflow. Delete it with: gh secret delete $OLD_ID_SECRET --org $org"
  fi
  if [ "$problems" -eq 0 ]; then
    echo "Everything is set up."
    return 0
  fi
  echo "$problems thing(s) to fix. Run scripts/setup-organisation.sh to fix them."
  return 1
}

# The newest .pem file in ~/Downloads, preferring one named after the app $1, as GitHub names them.
newest_pem() {
  local pattern file best=
  for pattern in "$1."'*.pem' '*.pem'; do
    for file in "$HOME"/Downloads/$pattern; do
      [ -f "$file" ] || continue
      if [ -z "$best" ] || [ "$file" -nt "$best" ]; then
        best=$file
      fi
    done
    [ -z "$best" ] || break
  done
  printf '%s' "$best"
}

# Has you generate a new private key for the app $1 on GitHub, and stores the downloaded file as the key secret.
rotate_key() {
  local settings="https://github.com/organizations/$org/settings/apps/$1" path
  open_url "$settings"
  echo "On that page, under \"Private keys\", click \"Generate a private key\". Your browser downloads a .pem file."
  while :; do
    ask "Path to the downloaded .pem file (press Enter for the newest .pem in ~/Downloads)"
    path=$ANSWER
    [ -n "$path" ] || path=$(newest_pem "$1")
    case "$path" in
      "~/"*) path="$HOME/${path#\~/}" ;;
    esac
    if [ -z "$path" ]; then
      echo "There is no .pem file in ~/Downloads. Type the file's path."
    elif [ -f "$path" ] && head -n 1 "$path" | grep -Eq '^-----BEGIN (RSA )?PRIVATE KEY-----'; then
      break
    else
      echo "$path is not a private key file. Type the path of the .pem file GitHub downloaded."
    fi
  done
  set_secret "$KEY_SECRET" <"$path"
  echo "Now delete the old key on the same page ($settings): under \"Private keys\", delete every key but the newest. An old key keeps working until it is deleted."
  if yes_no "Delete $path from this computer now?" y; then
    rm -f -- "$path"
    echo "Deleted $path."
  fi
}

# Has you install the app $1, with client id $2, on the organisation. With $3 set to new, the app was just created, so
# the script does not look for an installation before opening the install page. Returns once it is installed, or you
# type skip.
ensure_installed() {
  local rc
  if [ "${3-}" != new ]; then
    find_installation "$2" && rc=0 || rc=$?
    if [ "$rc" -eq 0 ]; then
      echo "$1 is installed on $org."
      return 0
    fi
  fi
  open_url "https://github.com/apps/$1/installations/new"
  echo "Install the app on $org. The app only reads team membership, so it needs no repositories: choose \"Only select repositories\" and pick none if GitHub lets you, or just $org/workflows if it does not. Then click Install."
  while :; do
    ask "Press Enter once it is installed, or type skip to go on without it"
    if [ "$ANSWER" = skip ]; then
      echo "Going on without it. The check at the end will say the app is not installed."
      return 0
    fi
    find_installation "$2" && rc=0 || rc=$?
    case $rc in
      0)
        echo "$1 is installed on $org."
        return 0
        ;;
      2)
        echo "Could not list the apps installed on $org, so the script cannot confirm the install. Going on."
        return 0
        ;;
    esac
    echo "$1 is not installed on $org yet."
  done
}

# Gets the manifest flow's code with the listener: the browser posts the manifest to GitHub, and GitHub sends it back
# to the listener with the code.
code_from_listener() {
  local out="$work_dir/listener.out" port= _
  printf '%s' "$manifest" | "$python" -I "$script_dir/manifest_listener.py" "$org" >"$out" &
  listener_pid=$!
  for _ in $(seq 1 100); do
    port=$(head -n 1 "$out")
    [ -z "$port" ] || break
    kill -0 "$listener_pid" 2>/dev/null || break
    sleep 0.1
  done
  [ -n "$port" ] || die "The listener that catches GitHub's answer did not start."
  open_url "http://127.0.0.1:$port/"
  echo "In the browser, check the app's name, then click \"Create GitHub App for $org\". Waiting up to 10 minutes for GitHub..."
  if ! wait "$listener_pid"; then
    listener_pid=
    die "GitHub did not answer within 10 minutes. If the app was created, run this script again and type its name to use it."
  fi
  listener_pid=
  CODE=$(sed -n 2p "$out")
}

# Gets the manifest flow's code without python3: a page on disk posts the manifest, and you paste the address GitHub
# sends the browser to, which does not load.
code_from_paste() {
  local page="$work_dir/create-app.html" state
  state=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
  printf '%s' "$manifest" | jq -r --arg org "$org" --arg state "$state" '.redirect_url = "http://127.0.0.1:1/callback" |
    @html "<!doctype html><meta charset=utf-8><title>Create the app on GitHub</title><form id=\"f\" method=\"post\" action=\"https://github.com/organizations/\($org)/settings/apps/new?state=\($state)\"><input type=\"hidden\" name=\"manifest\" value=\"\(tojson)\"><p>Sending you to GitHub. If nothing happens, press the button.</p><button type=\"submit\">Create the app on GitHub</button></form><script>document.getElementById(\"f\").submit()</script>"' >"$page"
  open_url "file://$page"
  echo "In the browser, check the app's name, then click \"Create GitHub App for $org\". GitHub then sends the browser to an address starting http://127.0.0.1:1/callback, which does not load."
  while :; do
    ask "Paste that whole address from the browser's address bar"
    CODE=$(printf '%s' "$ANSWER" | sed -n 's/.*[?&]code=\([A-Za-z0-9_-]*\).*/\1/p')
    [ -z "$CODE" ] || return 0
    echo "No code in that address. Paste the whole address, which has ?code= in it."
  done
}

# Creates the membership app with GitHub's manifest flow, and stores its client id and private key.
create_app() {
  local default="$org-review-membership" name conversion slug client_id
  while :; do
    ask "Name for the new app" "$default"
    name=$ANSWER
    [ "${#name}" -le 34 ] && break
    echo "GitHub allows at most 34 characters in an app's name."
  done
  manifest=$(jq -n --arg name "$name" --arg org "$org" '{
    name: $name,
    url: "https://github.com/cipherstash/workflows",
    description: "Looks up team membership for the Claude review workflow. Needs only Members: read.",
    public: false,
    hook_attributes: {url: ("https://github.com/" + $org), active: false},
    default_permissions: {members: "read"},
    default_events: []
  }')
  if command -v "$python" >/dev/null 2>&1; then
    code_from_listener
  else
    code_from_paste
  fi
  [[ $CODE =~ ^[A-Za-z0-9_-]+$ ]] || die "GitHub's answer had no usable code."
  # The answer holds the private key: it stays in this variable, and goes to gh on stdin.
  conversion=$(gh api -X POST "app-manifests/$CODE/conversions") ||
    die "GitHub did not turn the code into an app. A code works once, within an hour: run this script again."
  slug=$(printf '%s' "$conversion" | jq -r .slug)
  client_id=$(printf '%s' "$conversion" | jq -r .client_id)
  echo "Created the app $slug: https://github.com/organizations/$org/settings/apps/$slug"
  set_variable "$CLIENT_ID_VARIABLE" "$client_id"
  printf '%s' "$conversion" | jq -j .pem | set_secret "$KEY_SECRET"
  conversion=
  ensure_installed "$slug" "$client_id" new
}

# Uses the existing app $1: stores its client id, has you install it, and has you make it a new private key, because
# the key an existing app had cannot be read back.
use_existing_app() {
  local app client_id= members
  if app=$(gh api "apps/$1" 2>/dev/null); then
    client_id=$(printf '%s' "$app" | jq -r '.client_id // ""')
    members=$(printf '%s' "$app" | jq -r '.permissions.members // ""')
    if [ "$members" != read ]; then
      echo "Warning: $1 does not have Members: read. Add it at https://github.com/organizations/$org/settings/apps/$1/permissions"
    fi
  fi
  if [ -z "$client_id" ]; then
    open_url "https://github.com/organizations/$org/settings/apps/$1"
    echo "GitHub did not give this app's client id. It is on that page, under \"About\", as \"Client ID\"."
    while :; do
      ask "Client ID"
      client_id=$ANSWER
      [[ $client_id =~ ^Iv[A-Za-z0-9._-]+$ ]] && break
      echo "A client id starts with Iv, like Iv23li..."
    done
  fi
  set_variable "$CLIENT_ID_VARIABLE" "$client_id"
  ensure_installed "$1" "$client_id"
  echo "The secret $KEY_SECRET cannot be read back, so make a new private key for $1."
  rotate_key "$1"
}

setup_app() {
  local client_id rc
  echo "== The membership app"
  client_id=$(variable_value "$CLIENT_ID_VARIABLE")
  if [ -n "$client_id" ] && has_secret "$KEY_SECRET"; then
    find_installation "$client_id" && rc=0 || rc=$?
    if [ "$rc" -eq 0 ]; then
      echo "The membership app $INSTALL_SLUG is set up and installed on $org."
      if yes_no "Make a new private key for it?" n; then
        rotate_key "$INSTALL_SLUG"
      fi
      return 0
    fi
    echo "$CLIENT_ID_VARIABLE is $client_id, but no app with that client id is installed on $org (or the list of installed apps could not be read)."
  else
    echo "The membership app is not set up: $CLIENT_ID_VARIABLE and $KEY_SECRET are not both set."
  fi
  echo "To use an app that already exists, type its name as in its address (github.com/apps/<name>). To create a new app, press Enter."
  while :; do
    ask "Existing app"
    if [ -z "$ANSWER" ]; then
      create_app
      return 0
    fi
    if [[ $ANSWER =~ ^[A-Za-z0-9-]+$ ]]; then
      use_existing_app "$ANSWER"
      return 0
    fi
    echo "An app's name in its address has only letters, digits and hyphens."
  done
}

setup_routine_token() {
  echo "== The code-review routine's token"
  if has_secret "$TOKEN_SECRET" && ! yes_no "$TOKEN_SECRET is already set. Replace it?" n; then
    return 0
  fi
  echo "Paste the routine's token, from its API trigger on claude.ai. It does not show as you type."
  while :; do
    printf 'Token: '
    IFS= read -rs ANSWER || { echo; die "No answer, so stopping here."; }
    echo
    [ -z "$ANSWER" ] || break
    echo "The token is empty. Paste it again."
  done
  printf '%s' "$ANSWER" | set_secret "$TOKEN_SECRET"
  ANSWER=
}

setup_trigger_id() {
  local current
  echo "== The code-review routine's trigger id"
  current=$(variable_value "$TRIGGER_VARIABLE")
  if [ -n "$current" ] && ! yes_no "$TRIGGER_VARIABLE is already $current. Replace it?" n; then
    return 0
  fi
  while :; do
    ask "Trigger id, from the routine's API trigger on claude.ai"
    [[ $ANSWER =~ ^trig_[A-Za-z0-9]+$ ]] && break
    echo "A trigger id starts with trig_, like trig_01ABC. Try again."
  done
  set_variable "$TRIGGER_VARIABLE" "$ANSWER"
}

command -v gh >/dev/null 2>&1 || die "This script needs gh, the GitHub CLI: https://cli.github.com"
command -v jq >/dev/null 2>&1 || die "This script needs jq: https://jqlang.org"
gh api "orgs/$org/actions/secrets/public-key" >/dev/null 2>&1 ||
  die "gh cannot manage $org's Actions secrets. Sign in as an owner of $org, and give gh the admin:org scope: gh auth refresh -h github.com -s admin:org"

case $mode in
  check)
    run_check
    ;;
  rotate-key)
    load_settings
    client_id=$(variable_value "$CLIENT_ID_VARIABLE")
    slug=
    if [ -n "$client_id" ] && find_installation "$client_id"; then
      slug=$INSTALL_SLUG
    fi
    while [ -z "$slug" ]; do
      ask "Which app's key? Its name as in its address (github.com/apps/<name>)"
      if [[ $ANSWER =~ ^[A-Za-z0-9-]+$ ]]; then
        slug=$ANSWER
      fi
    done
    rotate_key "$slug"
    echo "Done. Run with --check to see the whole setup."
    ;;
  setup)
    load_settings
    setup_app
    setup_routine_token
    setup_trigger_id
    echo
    run_check
    ;;
esac
