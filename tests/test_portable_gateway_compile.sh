#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="${project_dir}/.scratch/rc32-v1/build/portable-gateway-tft-test"
fqbn='heltec:esp_halow:HT-RC3268:UploadSpeed=921600,USBMode=hwcdc,CDCOnBoot=default,PSRAM=opi,EraseFlash=none'

ARDUINO_DIRECTORIES_DATA="${project_dir}/.scratch/arduino/data" \
ARDUINO_DIRECTORIES_USER="${project_dir}/.scratch/arduino/user" \
"${project_dir}/.scratch/tools/arduino-cli-1.5.1/arduino-cli" compile \
  --fqbn "${fqbn}" \
  --build-path "${build_dir}" \
  "${project_dir}/firmware/portable-gateway"

nm_tool="${project_dir}/.scratch/arduino/user/hardware/heltec/esp_halow/tools/xtensa-esp32s3-elf/bin/xtensa-esp32s3-elf-nm"
symbols="$(${nm_tool} -C "${build_dir}/portable-gateway.ino.elf")"

grep -q 'halow_mmnetif_input' <<<"${symbols}"
grep -q 'portable_tft::begin()' <<<"${symbols}"
grep -q 'portable_tft::render(portable_status::View const&)' <<<"${symbols}"

echo "PASS: portable gateway links accounting adapter and TFT renderer"
