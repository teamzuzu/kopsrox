#!/usr/bin/env bash
# list available k3s versions ( stable releases only - no rc/alpha/beta )
# newest first - use one of these for k3s_version in kopsrox.ini
# usage: dev/k3s_versions.sh [count]   ( default 20, 'all' for everything fetched )
# honours $GITHUB_TOKEN to dodge the unauthenticated api rate limit
set -euo pipefail

count="${1:-20}"
auth=()
[ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer ${GITHUB_TOKEN}")

# github returns newest first - one page of 100 covers well over a year of releases
# drop pre-releases ( k3s flags every rc as prerelease ) and belt-and-braces exclude
# any tag with an -rc/-alpha/-beta/-dev/-test suffix, then keep k3s' vX.Y.Z+k3sN tags
versions=$(curl -fsSL "${auth[@]}" \
  'https://api.github.com/repos/k3s-io/k3s/releases?per_page=100' \
  | jq -r '.[] | select(.prerelease == false)
                | select(.draft == false)
                | .tag_name
                | select(test("-(rc|alpha|beta|dev|test)"; "i") | not)
                | select(test("^v[0-9]+\\.[0-9]+\\.[0-9]+\\+k3s[0-9]+$"))')

[ -z "$versions" ] && { echo "no k3s versions returned ( api rate limited? set GITHUB_TOKEN )" >&2; exit 1; }

if [ "$count" = all ]; then
  echo "$versions"
else
  echo "$versions" | head -n "$count"
fi
