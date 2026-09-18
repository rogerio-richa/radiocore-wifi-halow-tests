#!/usr/bin/env bash

set -Eeuo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

stream_profile="360p"
bitrate_override_kbps=""
video_argument=""
log_dir_argument=""
bind_ip="${RC32_BIND_IP:-10.41.0.2}"
webrtc_port="${RC32_WEBRTC_PORT:-8889}"
webrtc_udp_port="${RC32_WEBRTC_UDP_PORT:-8189}"
rtsp_port="${RC32_RTSP_PORT:-8554}"
metrics_port="${RC32_METRICS_PORT:-9998}"
dashboard_bind="${RC32_DASHBOARD_BIND:-$bind_ip}"
dashboard_port="${RC32_DASHBOARD_PORT:-8091}"
probe_ip="$bind_ip"
webrtc_interfaces=false
webrtc_hosts="[\"$bind_ip\"]"
if [[ "$bind_ip" == "0.0.0.0" || "$bind_ip" == "::" ]]; then
  probe_ip="127.0.0.1"
  webrtc_interfaces=true
  webrtc_hosts="[]"
fi
serial_device="${RC32_SERIAL_DEVICE:-auto}"
monitor_enabled="${RC32_MONITOR_ENABLED:-1}"
video_encoder="${RC32_VIDEO_ENCODER:-libx264}"
ffmpeg_bin="${RC32_FFMPEG_BIN:-$(command -v ffmpeg || true)}"
ffprobe_bin="${RC32_FFPROBE_BIN:-$(command -v ffprobe || true)}"
mediamtx_bin="${RC32_MEDIAMTX_BIN:-$script_dir/.scratch/tools/mediamtx-1.20.1/mediamtx}"
monitor_launcher="${RC32_MONITOR_LAUNCHER:-$script_dir/monitor-halow.sh}"
runtime_dir=""
log_dir=""
launcher_log=""
mediamtx_log=""
ffmpeg_log=""
monitor_log=""
logging_ready=0
mediamtx_pid=""
ffmpeg_pid=""
monitor_pid=""
monitor_reused=0

fail() {
  if [[ "$logging_ready" -eq 1 ]]; then
    log_line "Error: $*" >&2
  else
    echo "Error: $*" >&2
  fi
  exit 1
}

log_line() {
  formatted_log_line="[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*"
  printf '%s\n' "$formatted_log_line"
  if [[ "$logging_ready" -eq 1 && -n "$launcher_log" ]]; then
    printf '%s\n' "$formatted_log_line" >>"$launcher_log"
  fi
}

print_usage() {
  echo "Usage: $(basename -- "$0") [--profile 360p|720p|1080p] [--bitrate-kbps N] [--log-dir DIRECTORY] [video.mp4]"
  echo
  echo "Video options:"
  echo "  --profile NAME       360p (default), 720p, or 1080p"
  echo "  --bitrate-kbps N     Override the profile's video target and ceiling"
  echo "  --log-dir DIRECTORY  Save this run's persistent logs in a new directory"
  echo "  -h, --help           Show this help"
}

usage_error() {
  echo "Error: $*" >&2
  print_usage >&2
  exit 2
}

cleanup() {
  exit_code=$?
  trap - EXIT INT TERM

  if [[ -n "$ffmpeg_pid" ]] && kill -0 "$ffmpeg_pid" 2>/dev/null; then
    kill -TERM "$ffmpeg_pid" 2>/dev/null || true
  fi
  [[ -z "$ffmpeg_pid" ]] || wait "$ffmpeg_pid" 2>/dev/null || true

  if [[ -n "$monitor_pid" ]] && kill -0 "$monitor_pid" 2>/dev/null; then
    kill -TERM "$monitor_pid" 2>/dev/null || true
  fi
  [[ -z "$monitor_pid" ]] || wait "$monitor_pid" 2>/dev/null || true

  if [[ -n "$mediamtx_pid" ]] && kill -0 "$mediamtx_pid" 2>/dev/null; then
    kill -TERM "$mediamtx_pid" 2>/dev/null || true
  fi
  [[ -z "$mediamtx_pid" ]] || wait "$mediamtx_pid" 2>/dev/null || true

  if [[ -n "$runtime_dir" && "$runtime_dir" == /tmp/rc32-webrtc.* ]]; then
    rm -rf -- "$runtime_dir"
  fi

  if [[ "$logging_ready" -eq 1 ]]; then
    log_line "Launcher exiting with status $exit_code"
    log_line "Logs saved in: $log_dir"
  fi

  exit "$exit_code"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -h|--help)
      print_usage
      exit 0
      ;;
    --profile)
      [[ "$#" -ge 2 ]] || usage_error "--profile requires a value"
      stream_profile="$2"
      shift 2
      ;;
    --bitrate-kbps)
      [[ "$#" -ge 2 ]] || usage_error "--bitrate-kbps requires a value"
      bitrate_override_kbps="$2"
      shift 2
      ;;
    --log-dir)
      [[ "$#" -ge 2 ]] || usage_error "--log-dir requires a value"
      log_dir_argument="$2"
      shift 2
      ;;
    --)
      shift
      while [[ "$#" -gt 0 ]]; do
        [[ -z "$video_argument" ]] || usage_error "only one video file may be supplied"
        video_argument="$1"
        shift
      done
      ;;
    -*)
      usage_error "unknown option: $1"
      ;;
    *)
      [[ -z "$video_argument" ]] || usage_error "only one video file may be supplied"
      video_argument="$1"
      shift
      ;;
  esac
done

case "$stream_profile" in
  360p)
    video_width=640
    video_height=360
    video_fps=15
    video_bitrate_kbps=300
    video_maxrate_kbps=400
    video_bufsize_kbps=400
    h264_level=3.0
    ;;
  720p)
    video_width=1280
    video_height=720
    video_fps=30
    video_bitrate_kbps=1500
    video_maxrate_kbps=1800
    video_bufsize_kbps=1800
    h264_level=3.1
    ;;
  1080p)
    video_width=1920
    video_height=1080
    video_fps=30
    video_bitrate_kbps=4000
    video_maxrate_kbps=4500
    video_bufsize_kbps=4500
    h264_level=4.0
    ;;
  *)
    usage_error "unsupported profile: $stream_profile"
    ;;
esac

if [[ -n "$bitrate_override_kbps" ]]; then
  [[ "$bitrate_override_kbps" =~ ^[1-9][0-9]*$ ]] || usage_error "--bitrate-kbps must be a positive integer"
  video_bitrate_kbps="$bitrate_override_kbps"
  video_maxrate_kbps="$bitrate_override_kbps"
  video_bufsize_kbps="$bitrate_override_kbps"
fi

video_path="${video_argument:-${RC32_VIDEO_PATH:-$script_dir/media/kitties.mp4}}"
# Every frame carries the host's wall clock, HH:MM:SS, independent of when each viewer connects.
timestamp_args=(--fps "$video_fps")
if "$ffmpeg_bin" -hide_banner -filters 2>/dev/null | grep -E ' drawtext[[:space:]]' >/dev/null; then
  timestamp_args+=(--drawtext)
fi
timestamp_filter="$("${RC32_PYTHON_BIN:-/usr/bin/python3}" "$script_dir/stream_timestamp.py" "${timestamp_args[@]}")"
video_filter="scale=${video_width}:${video_height}:force_original_aspect_ratio=decrease,pad=${video_width}:${video_height}:(ow-iw)/2:(oh-ih)/2,fps=${video_fps},${timestamp_filter}"

if [[ -n "$log_dir_argument" ]]; then
  log_dir="$log_dir_argument"
else
  log_timestamp="$(date '+%Y%m%d-%H%M%S')"
  log_dir="$script_dir/.scratch/logs/stream-${log_timestamp}-$$"
fi

if [[ -e "$log_dir" ]]; then
  fail "log directory already exists: $log_dir"
fi
mkdir -p "$(dirname -- "$log_dir")" || fail "cannot create log parent directory: $log_dir"
mkdir "$log_dir" || fail "cannot create log directory: $log_dir"
launcher_log="$log_dir/launcher.log"
mediamtx_log="$log_dir/mediamtx.log"
ffmpeg_log="$log_dir/ffmpeg.log"
monitor_log="$log_dir/monitor.log"
touch "$launcher_log" "$mediamtx_log" "$ffmpeg_log" "$monitor_log" || fail "cannot create log files in: $log_dir"

logging_ready=1

log_line "Starting RC32 stream launcher"
log_line "Logs saved in: $log_dir"
log_line "Bind address: $bind_ip"
log_line "Serial selection: $serial_device"
log_line "WebRTC address discovery: $([[ "$webrtc_interfaces" == true ]] && echo enabled || echo disabled)"
log_line "Video profile: $stream_profile (${video_width}x${video_height}, ${video_fps} fps, ${video_bitrate_kbps} kbit/s)"
log_line "Video encoder: $video_encoder"
if [[ "$serial_device" == "auto" ]]; then
  serial_candidates="$(find /dev -maxdepth 1 \( -name 'cu.usbmodem*' -o -name 'ttyACM*' -o -name 'ttyUSB*' \) -print 2>/dev/null | sort)"
  if [[ -n "$serial_candidates" ]]; then
    serial_candidates_inline="$(printf '%s\n' "$serial_candidates" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
    log_line "Auto-detected serial candidates: $serial_candidates_inline"
  else
    log_line "Auto-detected serial candidates: none"
  fi
fi
log_line "Checking launcher prerequisites"

validate_port() {
  port_name="$1"
  port_value="$2"
  [[ "$port_value" =~ ^[0-9]+$ ]] || fail "$port_name must be numeric"
  [[ "$port_value" -ge 1 && "$port_value" -le 65535 ]] || fail "$port_name must be between 1 and 65535"
}

port_is_busy() {
  lsof -nP -iTCP:"$1" -iUDP:"$1" 2>/dev/null | grep -q .
}

[[ -n "$ffmpeg_bin" && -x "$ffmpeg_bin" ]] || fail "ffmpeg is not installed or executable"
[[ -x "$mediamtx_bin" ]] || fail "MediaMTX is not executable at $mediamtx_bin"
[[ -r "$video_path" && -s "$video_path" ]] || fail "video not found or empty: $video_path"

if [[ -n "$ffprobe_bin" && -x "$ffprobe_bin" ]]; then
  source_dimensions="$(
    "$ffprobe_bin" \
      -v error \
      -select_streams v:0 \
      -show_entries stream=width,height \
      -of csv=s=x:p=0 \
      "$video_path" 2>/dev/null || true
  )"
  if [[ "$source_dimensions" =~ ^([0-9]+)x([0-9]+)$ ]]; then
    source_width="${BASH_REMATCH[1]}"
    source_height="${BASH_REMATCH[2]}"
    if [[ "$source_width" -lt "$video_width" || "$source_height" -lt "$video_height" ]]; then
      log_line "Warning: input video is ${source_width}x${source_height}, below the selected ${video_width}x${video_height} output; FFmpeg will upscale it." >&2
    fi
  else
    log_line "Warning: input video resolution could not be inspected; continuing without the resolution check." >&2
  fi
else
  log_line "Warning: ffprobe is unavailable; continuing without the input-resolution check." >&2
fi

source_dimensions="${source_dimensions:-unavailable}"
poster_path="$log_dir/video-poster.jpg"
if ! "$ffmpeg_bin" -hide_banner -loglevel error -y -i "$video_path" -frames:v 1 -q:v 3 "$poster_path"; then
  log_line "Warning: could not create video first-frame preview." >&2
  rm -f -- "$poster_path"
fi

validate_port RC32_WEBRTC_PORT "$webrtc_port"
validate_port RC32_WEBRTC_UDP_PORT "$webrtc_udp_port"
validate_port RC32_RTSP_PORT "$rtsp_port"
validate_port RC32_METRICS_PORT "$metrics_port"
validate_port RC32_DASHBOARD_PORT "$dashboard_port"

if [[ "$monitor_enabled" != "0" && "$monitor_enabled" != "1" ]]; then
  fail "RC32_MONITOR_ENABLED must be 0 or 1"
fi

case "$video_encoder" in
  libx264)
    video_encoder_args=(-c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline -bf 0)
    rate_control_args=(-maxrate "${video_maxrate_kbps}k" -bufsize "${video_bufsize_kbps}k")
    ;;
  h264_v4l2m2m)
    video_encoder_args=(-c:v h264_v4l2m2m -profile:v 66)
    rate_control_args=()
    ;;
  *)
    fail "RC32_VIDEO_ENCODER must be libx264 or h264_v4l2m2m"
    ;;
esac

encoder_command_parts=(
  "$ffmpeg_bin" -hide_banner -loglevel error -nostdin -re -stream_loop -1
  -i "$video_path" -map 0:v:0 -map '0:a:0?' -vf "$video_filter"
  "${video_encoder_args[@]}" -level:v "$h264_level" -pix_fmt yuv420p
  -b:v "${video_bitrate_kbps}k" "${rate_control_args[@]}"
  -g "$video_fps" -keyint_min "$video_fps" -sc_threshold 0
  -c:a libopus -b:a 32k -ac 1 -ar 48000 -rtsp_transport tcp -f rtsp
  "rtsp://127.0.0.1:$rtsp_port/kitties"
)
printf -v encoder_command '%q ' "${encoder_command_parts[@]}"

service_ports=("$webrtc_port" "$webrtc_udp_port" "$rtsp_port" "$metrics_port")
if [[ "$monitor_enabled" == "1" ]]; then
  service_ports+=("$dashboard_port")
fi
for ((left = 0; left < ${#service_ports[@]}; left++)); do
  for ((right = left + 1; right < ${#service_ports[@]}; right++)); do
    if [[ "${service_ports[left]}" == "${service_ports[right]}" ]]; then
      fail "WebRTC, RTSP, metrics, and dashboard ports must be different"
    fi
  done
done

if [[ "$bind_ip" != "0.0.0.0" && "$bind_ip" != "::" ]] && ! ifconfig | awk -v ip="$bind_ip" '$1 == "inet" && $2 == ip { found = 1 } END { exit !found }'; then
  fail "$bind_ip is not assigned to this Mac; connect it to RC32-Base first"
fi

for port in "$webrtc_port" "$webrtc_udp_port" "$rtsp_port" "$metrics_port"; do
  port_is_busy "$port" && fail "port $port is already in use"
done
log_line "Launcher prerequisites passed"

dashboard_probe_ip="$dashboard_bind"
if [[ "$dashboard_bind" == "0.0.0.0" || "$dashboard_bind" == "::" ]]; then
  dashboard_probe_ip="127.0.0.1"
fi
dashboard_url="http://$dashboard_probe_ip:$dashboard_port/"
dashboard_announcement_url="http://$dashboard_bind:$dashboard_port/"
if [[ "$monitor_enabled" == "1" ]]; then
  dashboard_health="$(curl --fail --silent --max-time 1 "${dashboard_url}healthz" 2>/dev/null || true)"
  if [[ "$dashboard_health" == *'"service":"rc32-halow-monitor"'* &&
        "$dashboard_health" == *'"api_version":4'* ]]; then
    monitor_reused=1
    log_line "Reusing compatible HaLow dashboard at $dashboard_url"
  elif port_is_busy "$dashboard_port"; then
    fail "dashboard port $dashboard_port is occupied by an incompatible service"
  else
    [[ -x "$monitor_launcher" ]] || fail "monitor launcher is not executable at $monitor_launcher"
  fi
fi

runtime_dir="$(mktemp -d /tmp/rc32-webrtc.XXXXXX)"
config_path="$runtime_dir/mediamtx.yml"

cat >"$config_path" <<EOF
logLevel: info
logDestinations: [stdout]
api: false
metrics: true
metricsAddress: 127.0.0.1:$metrics_port
pprof: false
playback: false
rtsp: true
rtspTransports: [tcp]
rtspAddress: 127.0.0.1:$rtsp_port
rtmp: false
hls: false
webrtc: true
webrtcAddress: $bind_ip:$webrtc_port
webrtcEncryption: false
webrtcAllowOrigins: ["*"]
webrtcLocalUDPAddress: $bind_ip:$webrtc_udp_port
webrtcLocalTCPAddress: ""
webrtcIPsFromInterfaces: $webrtc_interfaces
webrtcAdditionalHosts: $webrtc_hosts
webrtcICEServers2: []
srt: false
moq: false
paths:
  kitties:
    source: publisher
EOF

page_url="http://$probe_ip:$webrtc_port/kitties"
log_line "Starting MediaMTX (component log: $mediamtx_log)"
log_line "Waiting for MediaMTX at $page_url"
"$mediamtx_bin" "$config_path" >"$mediamtx_log" 2>&1 &
mediamtx_pid=$!

page_ready=0
for _ in {1..15}; do
  if curl --fail --silent --show-error --output /dev/null "$page_url" 2>/dev/null; then
    page_ready=1
    break
  fi
  if ! kill -0 "$mediamtx_pid" 2>/dev/null; then
    wait "$mediamtx_pid" || true
    sed -n '1,160p' "$mediamtx_log" >&2
    fail "MediaMTX exited before its WebRTC page became ready"
  fi
  sleep 1
done

if [[ "$page_ready" -ne 1 ]]; then
  sed -n '1,160p' "$mediamtx_log" >&2
  fail "timed out waiting for MediaMTX at $page_url"
fi
log_line "MediaMTX is ready at $page_url"

if [[ "$monitor_enabled" == "1" && "$monitor_reused" -eq 0 ]]; then
  log_line "Starting HaLow monitor (component log: $monitor_log)"
  log_line "Waiting for HaLow dashboard at $dashboard_url"
  "$monitor_launcher" \
    --serial "$serial_device" \
    --bind "$dashboard_bind" \
    --port "$dashboard_port" \
    --media-metrics-url "http://127.0.0.1:$metrics_port/metrics" \
    --media-path kitties \
    --webrtc-port "$webrtc_port" \
    --source-video "$video_path" \
    --video-source-resolution "$source_dimensions" \
    --video-output-profile "$stream_profile" \
    --video-output-resolution "${video_width}x${video_height}" \
    --video-bitrate-kbps "$video_bitrate_kbps" \
    --video-poster "$poster_path" \
    --runs-dir "$script_dir/.scratch/runs" \
    --halow-viewer-network "${RC32_HALOW_VIEWER_NETWORK:-10.41.0.0/24}" \
    --encoder-command "$encoder_command" \
    --encoder-log "$ffmpeg_log" \
    >"$monitor_log" 2>&1 &
  monitor_pid=$!

  dashboard_ready=0
  dashboard_announced=0
  for _ in {1..15}; do
    if grep -Fqx -- "HaLow dashboard: $dashboard_announcement_url" "$monitor_log"; then
      dashboard_health="$(curl --fail --silent --max-time 1 "${dashboard_url}healthz" 2>/dev/null || true)"
      if [[ "$dashboard_health" != *'"service":"rc32-halow-monitor"'* ||
            "$dashboard_health" != *'"api_version":4'* ]]; then
        dashboard_announced=1
      fi
      dashboard_ready=1
      break
    fi
    dashboard_health="$(curl --fail --silent --max-time 1 "${dashboard_url}healthz" 2>/dev/null || true)"
    if [[ "$dashboard_health" == *'"service":"rc32-halow-monitor"'* &&
        "$dashboard_health" == *'"api_version":4'* ]]; then
      dashboard_ready=1
      break
    fi
    if ! kill -0 "$monitor_pid" 2>/dev/null; then
      wait "$monitor_pid" || true
      sed -n '1,160p' "$monitor_log" >&2
      fail "HaLow monitor exited before its dashboard became ready"
    fi
    sleep 1
  done
  if [[ "$dashboard_ready" -ne 1 ]]; then
    sed -n '1,160p' "$monitor_log" >&2
    fail "timed out waiting for HaLow dashboard at $dashboard_url"
  fi
  if [[ "$dashboard_announced" -eq 1 ]]; then
    log_line "Warning: HaLow dashboard health probe did not respond; continuing because the monitor announced readiness" >&2
  fi
  log_line "HaLow dashboard is ready at $dashboard_url"
elif [[ "$monitor_enabled" == "0" ]]; then
  log_line "HaLow monitor is disabled"
fi

if [[ "$monitor_enabled" == "1" ]]; then
  log_line "Video encoder is idle until CAPTURE"
else
  log_line "Encoding and looping: $video_path"
  log_line "Starting FFmpeg (component log: $ffmpeg_log)"
  log_line "Waiting for FFmpeg to publish path kitties"

"$ffmpeg_bin" \
  -hide_banner \
  -loglevel error \
  -nostdin \
  -re \
  -stream_loop -1 \
  -i "$video_path" \
  -map 0:v:0 \
  -map '0:a:0?' \
  -vf "$video_filter" \
  "${video_encoder_args[@]}" \
  -level:v "$h264_level" \
  -pix_fmt yuv420p \
  -b:v "${video_bitrate_kbps}k" \
  "${rate_control_args[@]}" \
  -g "$video_fps" \
  -keyint_min "$video_fps" \
  -sc_threshold 0 \
  -c:a libopus \
  -b:a 32k \
  -ac 1 \
  -ar 48000 \
  -rtsp_transport tcp \
  -f rtsp \
  "rtsp://127.0.0.1:$rtsp_port/kitties" 2>"$ffmpeg_log" &
ffmpeg_pid=$!

publisher_ready=0
for _ in {1..15}; do
  if grep -q "is publishing to path 'kitties'" "$mediamtx_log"; then
    publisher_ready=1
    break
  fi
  if ! kill -0 "$ffmpeg_pid" 2>/dev/null; then
    wait "$ffmpeg_pid" || true
    sed -n '1,160p' "$ffmpeg_log" >&2
    sed -n '1,160p' "$mediamtx_log" >&2
    fail "ffmpeg exited before publishing the stream"
  fi
  if ! kill -0 "$mediamtx_pid" 2>/dev/null; then
    wait "$mediamtx_pid" || true
    sed -n '1,160p' "$mediamtx_log" >&2
    fail "MediaMTX stopped while ffmpeg was starting"
  fi
  sleep 1
done

if [[ "$publisher_ready" -ne 1 ]]; then
  sed -n '1,160p' "$mediamtx_log" >&2
  fail "timed out waiting for ffmpeg to publish the stream"
fi
  log_line "FFmpeg is publishing path kitties"
fi

echo
log_line "RC32 real-time stream is ready: $page_url"
if [[ "$monitor_enabled" == "1" ]]; then
  log_line "HaLow and video telemetry: $dashboard_url"
fi
echo
log_line "Connect the phone to RC32-HaLow and press Ctrl+C here to stop."

if [[ "$monitor_enabled" == "1" ]]; then
  while kill -0 "$mediamtx_pid" 2>/dev/null &&
        kill -0 "$monitor_pid" 2>/dev/null; do
    sleep 1
  done
else
  while kill -0 "$mediamtx_pid" 2>/dev/null &&
        kill -0 "$ffmpeg_pid" 2>/dev/null; do
    sleep 1
  done
fi

if [[ "$monitor_enabled" == "0" ]] && ! kill -0 "$ffmpeg_pid" 2>/dev/null; then
  wait "$ffmpeg_pid" || true
  sed -n '1,160p' "$ffmpeg_log" >&2
  fail "ffmpeg stopped unexpectedly"
fi

if [[ -n "$monitor_pid" ]] && ! kill -0 "$monitor_pid" 2>/dev/null; then
  wait "$monitor_pid" || true
  sed -n '1,160p' "$monitor_log" >&2
  fail "HaLow monitor stopped unexpectedly"
fi

wait "$mediamtx_pid" || true
sed -n '1,160p' "$mediamtx_log" >&2
fail "MediaMTX stopped unexpectedly"
