#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
stream_script="${project_dir}/stream.sh"
test_dir="$(mktemp -d /tmp/rc32-stream-cli-test.XXXXXX)"

cleanup() {
  rm -rf "${test_dir}"
}
trap cleanup EXIT

help_output="${test_dir}/help.txt"
"${stream_script}" --help >"${help_output}" 2>&1
grep -q '^Usage: stream.sh ' "${help_output}"
grep -q -- '--profile 360p|720p|1080p' "${help_output}"
grep -q -- '--bitrate-kbps N' "${help_output}"
grep -q -- '--log-dir DIRECTORY' "${help_output}"

assert_usage_error() {
  local expected_message="$1"
  shift
  local output_file="${test_dir}/error-$RANDOM.txt"

  set +e
  "${stream_script}" "$@" >"${output_file}" 2>&1
  local exit_code=$?
  set -e

  if [[ "${exit_code}" -ne 2 ]]; then
    echo "FAIL: expected exit 2 for: $*; got ${exit_code}" >&2
    sed -n '1,80p' "${output_file}" >&2
    exit 1
  fi
  grep -q -- "${expected_message}" "${output_file}"
}

assert_usage_error 'unknown option: --wat' --wat
assert_usage_error 'unsupported profile: 4k' --profile 4k
assert_usage_error '--profile requires a value' --profile
assert_usage_error '--bitrate-kbps requires a value' --bitrate-kbps
assert_usage_error '--bitrate-kbps must be a positive integer' --bitrate-kbps 0
assert_usage_error 'only one video file may be supplied' first.mp4 second.mp4

failure_log_dir="${test_dir}/preflight-failure"
failure_output="${test_dir}/preflight-failure.txt"
set +e
RC32_BIND_IP=192.0.2.1 \
  "${stream_script}" --log-dir "${failure_log_dir}" >"${failure_output}" 2>&1
failure_exit=$?
set -e

if [[ "${failure_exit}" -ne 1 ]]; then
  echo "FAIL: expected logged preflight failure to exit 1; got ${failure_exit}" >&2
  sed -n '1,120p' "${failure_output}" >&2
  exit 1
fi

for expected_log in launcher.log mediamtx.log monitor.log ffmpeg.log; do
  if [[ ! -f "${failure_log_dir}/${expected_log}" ]]; then
    echo "FAIL: missing persistent ${expected_log}" >&2
    exit 1
  fi
done

grep -Eq '^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T' "${failure_log_dir}/launcher.log"
grep -q 'Starting RC32 stream launcher' "${failure_log_dir}/launcher.log"
grep -q 'Bind address: 192.0.2.1' "${failure_log_dir}/launcher.log"
grep -q 'Serial selection: auto' "${failure_log_dir}/launcher.log"
grep -q 'Error: 192.0.2.1 is not assigned to this Mac' "${failure_log_dir}/launcher.log"
grep -q "Logs saved in: ${failure_log_dir}" "${failure_output}"

echo "PASS: stream.sh help, validation, and persistent preflight logging"
