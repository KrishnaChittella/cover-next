#!/usr/bin/env bash
# Builds the application image from a base fetched by its pinned digest, from the
# first registry that really serves that digest.
#
#   build-image.sh <tag> [context]
#
# The Dockerfile pins the official Python base by digest. That digest is the trust
# anchor: a registry is only somewhere to fetch those exact bytes from. Google's public
# mirror is tried first and Docker Hub, the canonical source, second, so an anonymous
# Docker Hub rate limit no longer stops a build that the mirror can serve.
#
# For each registry in turn the script
#   1. fetches the manifest by digest and checks that the bytes hash to that digest,
#   2. pulls the image by digest, which Docker verifies layer by layer,
# and builds from the first registry that passes both. If none does, it fails: there
# is no tag to fall back to and no unpinned build. No credential is used; a registry
# that asks for a token is given the anonymous one it offers to everybody.
set -euo pipefail

tag="${1:?usage: build-image.sh <tag> [context]}"
context="${2:-.}"
dockerfile="${context}/Dockerfile"
registries="${BASE_REGISTRIES:-mirror.gcr.io/library docker.io/library}"
image="python"

fail() { echo "::error::build-image: $*" >&2; exit 1; }

# Every stage must take the same base, by digest, through BASE_REGISTRY.
pattern='^FROM \$\{BASE_REGISTRY\}/python:[A-Za-z0-9._-]+@(sha256:[0-9a-f]{64})( AS [a-z]+)?$'
digest=""
stages=0
while IFS= read -r line; do
  [[ "${line}" =~ ${pattern} ]] \
    || fail "a stage does not take the pinned base through BASE_REGISTRY: ${line}"
  [ -z "${digest}" ] || [ "${digest}" = "${BASH_REMATCH[1]}" ] || fail "the stages pin different base digests"
  digest="${BASH_REMATCH[1]}"
  stages=$((stages + 1))
done < <(grep -E '^FROM ' "${dockerfile}")
[ "${stages}" -ge 1 ] || fail "no FROM line in ${dockerfile}"

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# The manifest by digest, into ${work}/manifest. Anonymous; follows the registry's own
# token handshake if it asks for one. The token is never printed.
fetch_manifest() {
  local host="$1" path="$2" url code challenge realm service scope token
  url="https://${host}/v2/${path}/manifests/${digest}"
  local accept="Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"
  code="$(curl -sS -L --max-time 30 -H "${accept}" -D "${work}/headers" -o "${work}/manifest" -w '%{http_code}' "${url}")" || return 1
  if [ "${code}" = "401" ]; then
    challenge="$(grep -i '^www-authenticate:' "${work}/headers" | tr -d '\r' | head -1)"
    realm="$(sed -n 's/.*realm="\([^"]*\)".*/\1/p' <<<"${challenge}")"
    service="$(sed -n 's/.*service="\([^"]*\)".*/\1/p' <<<"${challenge}")"
    scope="$(sed -n 's/.*scope="\([^"]*\)".*/\1/p' <<<"${challenge}")"
    [ -n "${realm}" ] || return 1
    [ -n "${scope}" ] || scope="repository:${path}:pull"
    token="$(curl -sS --max-time 30 -G --data-urlencode "service=${service}" --data-urlencode "scope=${scope}" "${realm}" | jq -r '.token // .access_token // empty')" || return 1
    [ -n "${token}" ] || return 1
    code="$(curl -sS -L --max-time 30 -H "${accept}" -H "Authorization: Bearer ${token}" -o "${work}/manifest" -w '%{http_code}' "${url}")" || return 1
  fi
  [ "${code}" = "200" ]
}

chosen=""
for registry in ${registries}; do
  host="${registry%%/*}"
  path="${registry#*/}/${image}"
  [ "${host}" != "docker.io" ] || host="registry-1.docker.io"
  reference="${registry}/${image}@${digest}"
  if ! fetch_manifest "${host}" "${path}"; then
    echo "::notice::build-image: ${registry} did not serve the pinned base digest" >&2
    continue
  fi
  served="sha256:$(sha256sum "${work}/manifest" | cut -d' ' -f1)"
  if [ "${served}" != "${digest}" ]; then
    echo "::warning::build-image: ${registry} served bytes that are not the pinned digest" >&2
    continue
  fi
  if ! docker pull --quiet "${reference}" >/dev/null; then
    echo "::notice::build-image: ${registry} has the manifest but the pull failed" >&2
    continue
  fi
  chosen="${registry}"
  break
done

[ -n "${chosen}" ] || fail "no registry served the pinned base ${digest}; nothing was built"

echo "build-image: base ${image}@${digest} verified and pulled from ${chosen}" >&2
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### Base image"
    echo "- pinned digest: \`${digest}\`"
    echo "- fetched from: \`${chosen}\` (manifest bytes hash to the pinned digest)"
  } >> "${GITHUB_STEP_SUMMARY}"
fi

# --pull and --no-cache: nothing is taken from a stale local cache, and the
# security-update layer really runs.
docker build --pull --no-cache --build-arg "BASE_REGISTRY=${chosen}" --tag "${tag}" "${context}"
