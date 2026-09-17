#!/usr/bin/env bash

set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${RC32_PYTHON_BIN:-/usr/bin/python3}"

if [[ ! -x "${python_bin}" ]]; then
  echo "Error: Python executable not found or not executable: ${python_bin}" >&2
  exit 1
fi

measurements_dir="${script_dir}/.scratch/measurements"
mkdir -p "${measurements_dir}"

dashboard_bind="10.41.0.2"
dashboard_port="8091"
output_path=""
arguments=("$@")
argument_count="${#arguments[@]}"
index=0
while (( index < argument_count )); do
  argument="${arguments[index]}"
  case "${argument}" in
    --bind)
      if (( index + 1 < argument_count )); then
        dashboard_bind="${arguments[index + 1]}"
      fi
      ((index += 1))
      ;;
    --bind=*)
      dashboard_bind="${argument#*=}"
      ;;
    --port)
      if (( index + 1 < argument_count )); then
        dashboard_port="${arguments[index + 1]}"
      fi
      ((index += 1))
      ;;
    --port=*)
      dashboard_port="${argument#*=}"
      ;;
    --output)
      if (( index + 1 < argument_count )); then
        output_path="${arguments[index + 1]}"
      fi
      ((index += 1))
      ;;
    --output=*)
      output_path="${argument#*=}"
      ;;
  esac
  ((index += 1))
done

command_arguments=(
  "${script_dir}/halow_monitor.py"
  "--assets"
  "${script_dir}/monitor"
)

if [[ -z "${output_path}" ]]; then
  timestamp="$(date '+%Y%m%d-%H%M%S')"
  output_path="${measurements_dir}/halow-${timestamp}-$$.csv"
  command_arguments+=("--output" "${output_path}")
fi
command_arguments+=("$@")

echo "HaLow quality dashboard: http://${dashboard_bind}:${dashboard_port}/"
echo "CSV download: http://${dashboard_bind}:${dashboard_port}/measurements.csv"
echo "Saving measurements: ${output_path}"

exec "${python_bin}" "${command_arguments[@]}"
