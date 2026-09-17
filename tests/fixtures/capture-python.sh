#!/usr/bin/env bash

set -Eeuo pipefail
: "${RC32_CAPTURE_FILE:?RC32_CAPTURE_FILE is required}"
printf '%s\n' "$@" > "${RC32_CAPTURE_FILE}"
