#!/usr/bin/env bash
# Print the one image every MetaSpike job is running, or fail.
#
# For an infrastructure-only plan: the plan must not move the jobs to another image,
# so it is given the image they already run. Nobody chooses that image. It is read
# from the seven jobs, and it is accepted only if:
#
#   - the project has exactly the seven jobs, no fewer and no other;
#   - each job has one container;
#   - all seven run the same image, and each job's METASPIKE_IMAGE says the same;
#   - it is in MetaSpike's own repository, named by an immutable sha256 digest;
#   - the registry holds exactly that digest.
#
# Anything else is an error. Nothing here builds, pushes or picks a substitute.
#
#   resolve-deployed-image.sh <project> <region>
#
# The image is the only thing written to standard output.
set -euo pipefail

project="${1:?project id}"
region="${2:?region}"

repository="${region}-docker.pkg.dev/${project}/metaspike/metaspike"
expected_jobs="ms-catalogue ms-dispatch ms-features ms-parity ms-steam-players ms-warehouse ms-wikimedia"

fail() {
  echo "::error::reuse_deployed_image: $*" >&2
  exit 1
}

listed="$(gcloud run jobs list --project "${project}" --region "${region}" \
  --format='value(metadata.name)' | sort | tr '\n' ' ' | sed 's/ $//')"
[ "${listed}" = "${expected_jobs}" ] ||
  fail "expected exactly the seven jobs (${expected_jobs}) and found: ${listed:-none}"

resolved=""
for job in ${expected_jobs}; do
  description="$(gcloud run jobs describe "${job}" --project "${project}" --region "${region}" --format=json)"
  containers="$(jq '.spec.template.spec.template.spec.containers | length' <<<"${description}")"
  [ "${containers}" = "1" ] || fail "${job} has ${containers} containers, not one"
  image="$(jq -r '.spec.template.spec.template.spec.containers[0].image // ""' <<<"${description}")"
  declared="$(jq -r '[.spec.template.spec.template.spec.containers[0].env[]?
    | select(.name == "METASPIKE_IMAGE") | .value // ""] | join(" ")' <<<"${description}")"

  case "${image}" in
    "${repository}@sha256:"*) ;;
    *) fail "${job} runs an image outside ${repository} or not named by digest: ${image:-none}" ;;
  esac
  digest="${image#"${repository}@sha256:"}"
  [[ "${digest}" =~ ^[0-9a-f]{64}$ ]] || fail "${job}: the digest is not 64 lowercase hex characters"
  [ "${declared}" = "${image}" ] ||
    fail "${job}: METASPIKE_IMAGE (${declared:-unset}) is not the image it runs (${image})"
  if [ -z "${resolved}" ]; then
    resolved="${image}"
  elif [ "${image}" != "${resolved}" ]; then
    fail "the jobs do not agree on one image: ${job} runs ${image}, others run ${resolved}"
  fi
done
[ -n "${resolved}" ] || fail "no image was resolved"

held="$(gcloud artifacts docker images describe "${resolved}" --project "${project}" \
  --format='value(image_summary.fully_qualified_digest)')" ||
  fail "the registry does not hold ${resolved}"
[ "${held}" = "${resolved}" ] || fail "the registry answered with another image: ${held:-nothing}"

echo "${resolved}"
