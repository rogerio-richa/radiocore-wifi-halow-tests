#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d /tmp/rc32-halow-reconnect-test.XXXXXX)"
trap 'rm -rf "${test_dir}"' EXIT

c++ \
  -std=c++11 \
  -Wall \
  -Wextra \
  -Werror \
  "${project_dir}/tests/test_halow_reconnect.cpp" \
  -o "${test_dir}/test_halow_reconnect"

"${test_dir}/test_halow_reconnect"

echo "PASS: HaLow reconnect policy"
