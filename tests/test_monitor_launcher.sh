#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
launcher="${project_dir}/monitor-halow.sh"
test_dir="$(mktemp -d /tmp/rc32-monitor-launcher-test.XXXXXX)"
trap 'rm -rf "${test_dir}"' EXIT

if [[ ! -x "${launcher}" ]]; then
  echo "FAIL: monitor-halow.sh is missing or not executable" >&2
  exit 1
fi

/bin/bash -n "${launcher}"

if grep -q '/Users/' "${launcher}"; then
  echo "FAIL: launcher contains a hard-coded user home" >&2
  exit 1
fi

capture_file="${test_dir}/arguments.txt"
output_file="${test_dir}/explicit.csv"
RC32_PYTHON_BIN="${project_dir}/tests/fixtures/capture-python.sh" \
RC32_CAPTURE_FILE="${capture_file}" \
  "${launcher}" \
    --serial /tmp/fake-rc32 \
    --bind 127.0.0.1 \
    --port 19091 \
    --output "${output_file}" \
    --example-forwarded value \
    > "${test_dir}/launcher-output.txt"

expected_arguments="${test_dir}/expected.txt"
printf '%s\n' \
  "${project_dir}/halow_monitor.py" \
  "--assets" \
  "${project_dir}/monitor" \
  "--serial" \
  "/tmp/fake-rc32" \
  "--bind" \
  "127.0.0.1" \
  "--port" \
  "19091" \
  "--output" \
  "${output_file}" \
  "--example-forwarded" \
  "value" \
  > "${expected_arguments}"

cmp "${expected_arguments}" "${capture_file}"
test -d "${project_dir}/.scratch/measurements"
grep -q 'http://127.0.0.1:19091/' "${test_dir}/launcher-output.txt"
grep -q 'http://127.0.0.1:19091/measurements.csv' "${test_dir}/launcher-output.txt"

missing_output="${test_dir}/missing-python.txt"
if RC32_PYTHON_BIN="${test_dir}/does-not-exist" \
     "${launcher}" > "${missing_output}" 2>&1; then
  echo "FAIL: launcher accepted a missing Python executable" >&2
  exit 1
fi
grep -q 'Python executable not found' "${missing_output}"

echo "PASS: monitor launcher"
