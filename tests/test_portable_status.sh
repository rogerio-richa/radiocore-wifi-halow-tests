#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d /tmp/rc32-portable-status-test.XXXXXX)"
trap 'rm -rf "${test_dir}"' EXIT

c++ \
  -std=c++11 \
  -Wall \
  -Wextra \
  -Werror \
  "${project_dir}/tests/test_portable_status.cpp" \
  "${project_dir}/firmware/portable-gateway/portable_status.cpp" \
  -o "${test_dir}/test_portable_status"

"${test_dir}/test_portable_status"

echo "PASS: portable status model"
