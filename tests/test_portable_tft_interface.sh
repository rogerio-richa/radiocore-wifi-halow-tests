#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d /tmp/rc32-portable-tft-interface-test.XXXXXX)"
trap 'rm -rf "${test_dir}"' EXIT

c++ \
  -std=c++11 \
  -Wall \
  -Wextra \
  -Werror \
  -DPORTABLE_TFT_HOST_TEST \
  "${project_dir}/tests/test_portable_tft_interface.cpp" \
  "${project_dir}/firmware/portable-gateway/portable_tft.cpp" \
  "${project_dir}/firmware/portable-gateway/portable_status.cpp" \
  -o "${test_dir}/test_portable_tft_interface"

"${test_dir}/test_portable_tft_interface"

echo "PASS: portable TFT interface"
