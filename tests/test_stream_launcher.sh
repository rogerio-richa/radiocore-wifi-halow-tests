#!/usr/bin/env bash

set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
stream_script="${project_dir}/stream.sh"
test_dir="$(mktemp -d /tmp/rc32-unified-stream-test.XXXXXX)"
webrtc_port=18897
webrtc_udp_port=18197
rtsp_port=18557
metrics_port=19997
dashboard_port=18097
log_file="${test_dir}/stream.log"
ffmpeg_args_log="${test_dir}/ffmpeg-args.log"
ffmpeg_wrapper="${test_dir}/ffmpeg-wrapper.sh"
curl_wrapper_dir="${test_dir}/bin"
curl_wrapper="${curl_wrapper_dir}/curl"
low_resolution_video="${test_dir}/low-resolution.mp4"
persistent_log_dir="${test_dir}/persistent-logs"
stream_pid=""

cleanup() {
  if [[ -n "${stream_pid}" ]] && kill -0 "${stream_pid}" 2>/dev/null; then
    kill -TERM "${stream_pid}" 2>/dev/null || true
    wait "${stream_pid}" 2>/dev/null || true
  fi
  rm -rf "${test_dir}"
}
trap cleanup EXIT

real_ffmpeg="$(command -v ffmpeg)"
real_curl="$(command -v curl)"
"${real_ffmpeg}" \
  -v error \
  -f lavfi \
  -i 'color=c=blue:size=320x180:rate=30' \
  -t 1 \
  -c:v libx264 \
  -pix_fmt yuv420p \
  "${low_resolution_video}"

cat >"${ffmpeg_wrapper}" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" >"${RC32_TEST_FFMPEG_ARGS_LOG}"
exec "${RC32_TEST_REAL_FFMPEG}" "$@"
EOF
chmod +x "${ffmpeg_wrapper}"

mkdir "${curl_wrapper_dir}"
cat >"${curl_wrapper}" <<'EOF'
#!/usr/bin/env bash
for argument in "$@"; do
  if [[ "${argument}" == "${RC32_TEST_BLOCKED_HEALTH_URL}" ]]; then
    exit 28
  fi
done
exec "${RC32_TEST_REAL_CURL}" "$@"
EOF
chmod +x "${curl_wrapper}"

assert_adjacent_args() {
  local option="$1"
  local value="$2"
  awk -v option="${option}" -v value="${value}" '
    previous == option && $0 == value { found = 1 }
    { previous = $0 }
    END { exit !found }
  ' "${ffmpeg_args_log}"
}

for port in \
  "${webrtc_port}" \
  "${webrtc_udp_port}" \
  "${rtsp_port}" \
  "${metrics_port}" \
  "${dashboard_port}"; do
  if lsof -nP -iTCP:"${port}" -iUDP:"${port}" 2>/dev/null | grep -q .; then
    echo "FAIL: test port ${port} is already in use" >&2
    exit 1
  fi
done

RC32_BIND_IP=0.0.0.0 \
RC32_WEBRTC_PORT="${webrtc_port}" \
RC32_WEBRTC_UDP_PORT="${webrtc_udp_port}" \
RC32_RTSP_PORT="${rtsp_port}" \
RC32_METRICS_PORT="${metrics_port}" \
RC32_DASHBOARD_PORT="${dashboard_port}" \
RC32_SERIAL_DEVICE=/dev/null \
RC32_MEDIAMTX_BIN="${project_dir}/.scratch/tools/mediamtx-1.20.1/mediamtx" \
RC32_FFMPEG_BIN="${ffmpeg_wrapper}" \
RC32_TEST_FFMPEG_ARGS_LOG="${ffmpeg_args_log}" \
RC32_TEST_REAL_FFMPEG="${real_ffmpeg}" \
RC32_TEST_BLOCKED_HEALTH_URL="http://127.0.0.1:${dashboard_port}/healthz" \
RC32_TEST_REAL_CURL="${real_curl}" \
PATH="${curl_wrapper_dir}:${PATH}" \
  "${stream_script}" \
    --profile 720p \
    --bitrate-kbps 900 \
    --log-dir "${persistent_log_dir}" \
    "${low_resolution_video}" >"${log_file}" 2>&1 &
stream_pid=$!

page_url="http://127.0.0.1:${webrtc_port}/kitties"
for _ in {1..25}; do
  if curl --fail --silent --output /dev/null "${page_url}" &&
     grep -q 'RC32 real-time stream is ready:' "${log_file}"; then
    break
  fi
  if ! kill -0 "${stream_pid}" 2>/dev/null; then
    echo "FAIL: stream.sh exited before the video page was ready" >&2
    sed -n '1,180p' "${log_file}" >&2
    exit 1
  fi
  sleep 1
done

metrics_url="http://127.0.0.1:${metrics_port}/metrics"
dashboard_url="http://127.0.0.1:${dashboard_port}/"

curl --fail --silent "${metrics_url}" >"${test_dir}/metrics.txt"
grep -q 'paths' "${test_dir}/metrics.txt"

for _ in {1..10}; do
  if curl --fail --silent "${dashboard_url}healthz" >"${test_dir}/health.json"; then
    break
  fi
  sleep 1
done
grep -q '"service":"rc32-halow-monitor"' "${test_dir}/health.json"
grep -q '"api_version":4' "${test_dir}/health.json"
grep -q "${page_url}" "${log_file}"
grep -q "${dashboard_url}" "${log_file}"
grep -q 'Warning: input video is 320x180, below the selected 1280x720 output; FFmpeg will upscale it.' "${log_file}"

curl --fail --silent --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{}' \
  "${dashboard_url}api/runs" >"${test_dir}/capture.json"
grep -q '"status":"recording"' "${test_dir}/capture.json"

for _ in {1..15}; do
  if grep -q "is publishing to path 'kitties'" "${persistent_log_dir}/mediamtx.log"; then
    break
  fi
  sleep 1
done
grep -q "is publishing to path 'kitties'" "${persistent_log_dir}/mediamtx.log"

timestamp_filter="$(/usr/bin/python3 "${project_dir}/stream_timestamp.py")"
assert_adjacent_args -vf "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30,${timestamp_filter}"
/usr/bin/python3 - "${test_dir}/health.json" "$webrtc_port" <<'PYTEST'
import json
import sys
with open(sys.argv[1]) as source:
    snapshot = json.load(source)
player = snapshot['player']
assert player['port'] == int(sys.argv[2]), player
assert player['path'] == 'kitties', player
assert player['source_resolution'] == '320x180', player
assert player['output_resolution'] == '1280x720', player
assert player['bitrate_kbps'] == 900, player
PYTEST
assert_adjacent_args -level:v 3.1
assert_adjacent_args -b:v 900k
assert_adjacent_args -maxrate 900k
assert_adjacent_args -bufsize 900k
assert_adjacent_args -g 30
assert_adjacent_args -keyint_min 30

ffprobe -v error \
  -rtsp_transport tcp \
  -select_streams v:0 \
  -show_entries stream=width,height,r_frame_rate \
  -of default=noprint_wrappers=1 \
  "rtsp://127.0.0.1:${rtsp_port}/kitties" >"${test_dir}/stream-probe.txt"
grep -q '^width=1280$' "${test_dir}/stream-probe.txt"
grep -q '^height=720$' "${test_dir}/stream-probe.txt"
grep -q '^r_frame_rate=30/1$' "${test_dir}/stream-probe.txt"

for expected_log in launcher.log mediamtx.log monitor.log ffmpeg.log; do
  if [[ ! -f "${persistent_log_dir}/${expected_log}" ]]; then
    echo "FAIL: missing persistent ${expected_log} after successful startup" >&2
    exit 1
  fi
done

grep -q 'Bind address: 0.0.0.0' "${persistent_log_dir}/launcher.log"
grep -q 'WebRTC address discovery: enabled' "${persistent_log_dir}/launcher.log"
grep -q 'Starting MediaMTX' "${persistent_log_dir}/launcher.log"
grep -q 'MediaMTX is ready' "${persistent_log_dir}/launcher.log"
grep -q 'Starting HaLow monitor' "${persistent_log_dir}/launcher.log"
grep -q 'HaLow dashboard is ready' "${persistent_log_dir}/launcher.log"
grep -q 'Warning: HaLow dashboard health probe did not respond; continuing because the monitor announced readiness' "${persistent_log_dir}/launcher.log"
grep -q 'Video encoder is idle until CAPTURE' "${persistent_log_dir}/launcher.log"
grep -q 'RC32 real-time stream is ready' "${persistent_log_dir}/launcher.log"
grep -q 'MediaMTX v' "${persistent_log_dir}/mediamtx.log"
grep -q 'HaLow dashboard:' "${persistent_log_dir}/monitor.log"

kill -TERM "${stream_pid}"
set +e
wait "${stream_pid}"
shutdown_status=$?
set -e
stream_pid=""

grep -Eq 'Launcher exiting with status (0|143)' "${persistent_log_dir}/launcher.log"
grep -q "Logs saved in: ${persistent_log_dir}" "${persistent_log_dir}/launcher.log"

if [[ "${shutdown_status}" -ne 0 && "${shutdown_status}" -ne 143 ]]; then
  echo "FAIL: stream.sh returned ${shutdown_status} during shutdown" >&2
  exit 1
fi

for port in \
  "${webrtc_port}" \
  "${webrtc_udp_port}" \
  "${rtsp_port}" \
  "${metrics_port}" \
  "${dashboard_port}"; do
  if lsof -nP -iTCP:"${port}" -iUDP:"${port}" 2>/dev/null | grep -q .; then
    echo "FAIL: owned port ${port} remains in use after shutdown" >&2
    exit 1
  fi
done

echo "PASS: stream.sh applied CLI video settings and supervised all services"
