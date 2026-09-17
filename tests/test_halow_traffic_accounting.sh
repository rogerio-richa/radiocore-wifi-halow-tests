#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d /tmp/rc32-halow-traffic-test.XXXXXX)"
trap 'rm -rf "${test_dir}"' EXIT

c++ \
  -std=c++11 \
  -Wall \
  -Wextra \
  -Werror \
  "${project_dir}/tests/test_halow_traffic_accounting.cpp" \
  -o "${test_dir}/test_halow_traffic_accounting"

"${test_dir}/test_halow_traffic_accounting"

echo "PASS: HaLow traffic accounting semantics"
