# RC32 Wi-Fi HaLow point-to-point demo

This project runs an isolated video and link-monitoring demo across two RadioCore RC32 v1 prototypes built around the Heltec HT-HC01 Wi-Fi HaLow radio. The Mac serves the content; the Android phone reaches it through the portable RC32 and the HaLow link. Home Wi-Fi and Internet access are not part of the data path.

For a plain-language account of how the project evolved from the factory firmware to the working video link, read [`docs/walkthrough.md`](docs/walkthrough.md). This README is the compact operating and technical reference.

## Network layout

```text
Mac 10.41.0.2
  │  ordinary 2.4 GHz Wi-Fi: RC32-Base
  │  10.41.0.0/24
  ▼
base RC32: 10.41.0.1 + NAPT + HaLow AP 10.42.0.1
  │  802.11ah: RC32-HaLow-Backbone
  │  10.42.0.0/24
  ▼
portable RC32: HaLow station 10.42.0.2 + NAPT + 2.4 GHz AP 10.43.0.1
  │  ordinary 2.4 GHz Wi-Fi: RC32-HaLow
  │  10.43.0.0/24
  ▼
Android phone (DHCP)
```

This is a routed, double-NAPT topology rather than a transparent bridge. The phone initiates connections toward the Mac. The Mac cannot initiate a connection directly to the phone's `10.43.0.x` address.

| Setting | Base side | Portable side |
|---|---|---|
| HaLow role/address | AP, `10.42.0.1/24` | station, `10.42.0.2/24` |
| HaLow SSID | `RC32-HaLow-Backbone` | connects to the same SSID |
| Ordinary Wi-Fi SSID | `RC32-Base` | `RC32-HaLow` |
| Ordinary Wi-Fi gateway | `10.41.0.1/24` | `10.43.0.1/24` |
| Ordinary Wi-Fi channel | 1 | 6 |
| Passphrases | set in `firmware/rc32_secrets.h` | set in `firmware/rc32_secrets.h` |

The passphrases are compiled in from `firmware/rc32_secrets.h`, which is ignored by Git. Copy [`firmware/rc32_secrets.example.h`](firmware/rc32_secrets.example.h) to that name and set your own values before flashing; without it the sketches fall back to a placeholder passphrase.

The base stays connected to the Mac over USB for power, serial telemetry, and flashing. USB is not a network interface in this firmware. The Mac must also join `RC32-Base`; the phone must join `RC32-HaLow`. Android may warn that `RC32-HaLow` has no Internet access—choose to remain connected.

## Run the demonstration

Connect the base over USB, join `RC32-Base` on the Mac, and confirm that the Mac owns `10.41.0.2`.

Start the indefinitely looping, low-latency WebRTC video and telemetry dashboard in one terminal:

```bash
./stream.sh
```

The default input is `media/kitties.mp4`. Pass another MP4 as the positional argument when needed:

```bash
./stream.sh /path/to/another-video.mp4
```

The known-good default remains 360p. Select a higher-resolution profile with command-line arguments:

```bash
./stream.sh --profile 720p
./stream.sh --profile 1080p
```

The profiles are:

| Profile | Output | Frame rate | Video target/maximum |
|---|---:|---:|---:|
| `360p` (default) | 640×360 | 15 fps | 300/400 kbit/s |
| `720p` | 1280×720 | 30 fps | 1,500/1,800 kbit/s |
| `1080p` | 1920×1080 | 30 fps | 4,000/4,500 kbit/s |

For a controlled bandwidth test, override the selected profile's video target and ceiling with `--bitrate-kbps`. Keep the profile and source fixed, increase the value between runs, and watch both playback and the dashboard:

```bash
./stream.sh --profile 720p --bitrate-kbps 750
./stream.sh --profile 720p --bitrate-kbps 1000
./stream.sh --profile 720p --bitrate-kbps 1500
./stream.sh --profile 720p --bitrate-kbps 2000
```

The source can follow the options, for example `./stream.sh --profile 1080p --bitrate-kbps 2500 /path/to/video.mp4`. Run `./stream.sh --help` for the command summary. At startup, the launcher inspects the source dimensions. If they are smaller than the selected output, it warns that FFmpeg will upscale the video but continues running. Source videos are not part of this repository; see [`media/README.md`](media/README.md) for what to place there. The reference `media/kitties.mp4` used in these tests was a native 1920×1080 source.

This procedure measures sustainable delivery through this H.264/WebRTC application path. It does not directly measure raw PHY bitrate, airtime capacity, or RF efficiency. A practical limit is where delivered video no longer follows the requested rate, playback becomes unstable, or the HaLow interface rate stops increasing consistently.

`stream.sh` starts MediaMTX, FFmpeg, and the HaLow monitor. It reuses a compatible monitor that is already running and stops only the processes it started. Monitor startup does not perform a reverse-DNS lookup, so it remains prompt on the intentionally isolated network. The launcher accepts either a compatible `/healthz` response or the monitor's own readiness announcement; if only the local health probe fails, it records a warning and continues instead of terminating a running monitor. The standalone monitor remains useful when video is not needed:

```bash
./monitor-halow.sh
```

Every `stream.sh` run keeps an offline diagnostic record below `.scratch/logs/stream-<timestamp>-<pid>/`. The launcher prints that directory before it begins checking the network, so the evidence survives even when startup fails. Each run contains:

- `launcher.log`: timestamped configuration, preflight, component-start, readiness, failure, and shutdown events;
- `mediamtx.log`: MediaMTX startup and WebRTC/RTSP events;
- `monitor.log`: serial selection and dashboard-service output; and
- `ffmpeg.log`: encoder and publisher errors.

Use `--log-dir` when a predictable directory will be easier to retrieve after reconnecting to the Internet. The destination must not already exist:

```bash
./stream.sh --log-dir .scratch/logs/offline-test --profile 720p
```

After reconnecting to the Internet, inspect the launcher record with:

```bash
sed -n '1,240p' .scratch/logs/offline-test/launcher.log
```

The component logs in the same directory provide the detail behind a failed stage. The launcher deliberately records only its selected settings and discovered device paths; it does not dump the shell environment or credentials.

If more than one USB modem is connected, select the base explicitly:

```bash
./monitor-halow.sh --serial /dev/cu.usbmodem101
```

On the phone, connected to `RC32-HaLow`, open:

- Video-only client page: `http://10.41.0.2:8091/`
- Measurement dashboard: `http://10.41.0.2:8091/admin/`
- Network path explainer: `http://10.41.0.2:8091/admin/path/`, also reached by clicking the Mac → Base → Portable → Phone strip in the dashboard header; it draws the Raspberry Pi, both RC32s, and the phone and lists what runs on each for walkthroughs and demonstrations

The dashboard embeds the live video above the telemetry. Each video frame carries an
elapsed `HH:MM:SS.mmm` timestamp burned in by FFmpeg. It starts with the encoder,
continues across source-video loops, and resets when the stream restarts. Open the
same dashboard on the Mac and phone to compare playback. Align screen recordings
using a shared real-world event before comparing the visible timestamps; aligning
by the video itself removes the delay difference. This measures relative playback
delay, not total capture-to-display latency. Both players count as video viewers.
The player starts muted; use its controls to enable sound. The standalone monitor's
`--webrtc-port` option defaults to `8889`; `stream.sh` passes its selected port.
The video-only root page embeds that internal MediaMTX path and displays the
base-firmware interface throughput, client count, and radio activity. RSSI and
SNR are explicitly shown as unavailable because the base AP firmware has no
reliable values for them.

Use **CAPTURE** to start one global experiment run from either dashboard. Before
capture, the live-video frame remains detached; CAPTURE starts a fresh encoder,
connects every open dashboard to the same WebRTC feed, resets its charts, and
begins the shared run clock. The burned frame timestamp therefore begins at
`00:00` for each capture. **STOP** closes the run and detaches the live viewers. **Review runs**
replays the stored readings and
overlaid LAN/HaLow delivery traces; the original MP4 appears as a **Source
reference** and follows the review clock. It is context rather than a recording of
either endpoint, so it cannot reproduce browser buffering, freezes, or displayed
delay. The frame clock visible in external screen recordings remains the evidence
for comparing endpoint delay. Review provides a **Delete** control for each
completed or interrupted run and **Delete stored runs** for a full reset; both
leave an active capture intact.

Press `Ctrl+C` in the stream terminal to stop the owned video and monitoring processes. Set `RC32_MONITOR_ENABLED=0` for a stream-only diagnostic run. Other useful stream overrides are `RC32_METRICS_PORT`, `RC32_DASHBOARD_PORT`, `RC32_DASHBOARD_BIND`, and `RC32_SERIAL_DEVICE`.

The monitor uses only the Python standard library. It binds to `10.41.0.2:8091`, reads 115200-baud USB serial telemetry, retains the latest 900 samples in memory, and writes every valid sample to a new CSV below `.scratch/measurements/`. The page offers the current run at `/measurements.csv`. Existing output files are never overwritten. Experiment runs contain only metadata and append-only JSON Lines measurements below `.scratch/runs/`; no video is copied or transcoded. The default retention is 100 completed runs.

### Raspberry Pi dual-interface host

A Raspberry Pi can replace the Mac as the video and telemetry host. Connect
`eth0` to the ordinary LAN for administration, join `wlan0` to `RC32-Base` as
`10.41.0.2/24`, and connect the base RC32 over USB for serial telemetry. Start
the server on every Pi interface so the LAN and HaLow paths reach the same
encoder and elapsed-time source:

```bash
RC32_BIND_IP=0.0.0.0 \
RC32_DASHBOARD_BIND=0.0.0.0 \
RC32_VIDEO_ENCODER=h264_v4l2m2m \
./stream.sh --profile 360p media/kitties-360p.mp4
```

`RC32_VIDEO_ENCODER` accepts `libx264` (the portable default) or
`h264_v4l2m2m` (the Raspberry Pi hardware encoder). On systems with FFmpeg's
`drawtext` filter, the launcher uses it for the elapsed clock; otherwise it
uses its font-independent seven-segment renderer. A source already scaled to
the selected profile avoids repeatedly decoding and scaling 1080p on a Pi 3.
The current Pi 3 test used a 640x360, 15 fps H.264/AAC source and reduced
FFmpeg CPU use from about two cores to about one third of one core.

While Ethernet is connected, open `http://<Pi-Ethernet-IP>:8091/` from the
LAN. Across HaLow, open `http://10.41.0.2:8091/`. Both pages play the same
stream. Ethernet can be disconnected after startup; the HaLow address and
local services remain active. Linux USB serial devices (`/dev/ttyACM*` and
`/dev/ttyUSB*`) and macOS USB modem devices are auto-detected.

To start the same command at boot, [`deploy/rc32-stream.service`](deploy/rc32-stream.service) is a systemd unit for a Pi whose checkout lives in `/home/pi/radiocore`; adjust the user and paths to match yours.

Useful monitor overrides are:

```text
--serial DEVICE
--bind ADDRESS
--port PORT
--output PATH
--media-metrics-url URL
--media-path PATH
--runs-dir DIRECTORY
--run-retention COUNT
--halow-viewer-network CIDR
--source-video PATH
```

The HTTP service exposes only the dashboard assets, measurement endpoints, run lifecycle endpoints below `/api/runs`, `/source-video`, `/events`, `/healthz`, and `/measurements.csv`. It has no upload, directory-listing, command, or firmware endpoint.

## What the monitor measures

The dashboard contains two deliberately separate instruments. **HaLow AP traffic** is aggregate network-interface throughput measured by the base firmware for all traffic crossing HaLow. In the AP panel, **Toward phone** is HaLow downlink (base to portable) and **Toward Mac** is HaLow uplink (portable to base). **WebRTC video delivery** is application traffic sent by MediaMTX to active viewers. MediaMTX sessions whose remote address belongs to `10.41.0.0/24` form the HaLow trace; other valid remote addresses form the LAN trace. The two rates share one overlaid chart and are never summed into a viewer bitrate. They describe server output rather than bytes received or frames rendered by a browser.

The base emits one schema-v3 `HALOW_METRIC` record per second. Station count comes from HaLow AP association callbacks. Cumulative byte counters come from accepted packets at the HaLow lwIP interface, and the host derives rates from counter and firmware-uptime deltas. Radio activity and UMAC fault counters remain independent of the optional rate-control table.

| Field | Meaning |
|---|---|
| `v` | Telemetry schema version; currently `3`. The service also accepts and normalizes schemas v1 and v2. |
| `uptime_ms` | Base firmware uptime at sampling, in milliseconds. |
| `sample_ms` | Time covered by this sample, normally about 1,000 ms. |
| `rate_available` | `1` when the AP rate-control table is available; `0` on the current firmware build. |
| `radio_active` | `1` when the AP's last-transmit timestamp changed during the interval. |
| `halow_clients` | Number of associated or authorized HaLow stations reported through AP callbacks. |
| `traffic_counters_available` | `1` when HaLow network-interface accounting is installed; otherwise `0`. |
| `halow_tx_bytes_total` | Cumulative bytes accepted toward the portable side at the HaLow interface. |
| `halow_rx_bytes_total` | Cumulative bytes accepted from the portable side at the HaLow interface. |
| `halow_tx_kbps` | Host-derived base-to-portable throughput in decimal kilobits per second. |
| `halow_rx_kbps` | Host-derived portable-to-base throughput in decimal kilobits per second. |
| `halow_total_kbps` | Sum of the independently derived HaLow TX and RX interface rates. |
| `traffic` | `1` when the interval contains HaLow transmit attempts; otherwise `0`. |
| `mcs` | Dominant interval MCS when rate data is available. Unavailable or idle sentinel: `-1`. |
| `bw_mhz` | Dominant interval channel width when available: 1, 2, or 4 MHz. Sentinel: `0`. |
| `sgi` | Dominant interval guard bit when available: `0` for long GI, `1` for short GI. Sentinel: `-1`. |
| `tx_attempts` | Sum of HaLow transmit attempts when rate data is available. |
| `tx_success` | Sum of successful HaLow transmissions when rate data is available. |
| `delivery_pct` | `tx_success / tx_attempts × 100` when available. Sentinel: `-1`. |
| `txq_drops` | TX queue-full drops during the interval. |
| `rxq_drops` | RX queue-full drops during the interval. |
| `rx_alloc_failures` | Driver RX allocation failures during the interval. |
| `rx_read_failures` | Driver RX read or malformed-page failures during the interval. |
| `reorder_overflow` | RX A-MPDU reorder-buffer overflow flushes during the interval. |
| `reorder_timeouts` | RX reorder-list timeout flushes during the interval. |
| `reorder_outdated` | Outdated sequence-number drops during the interval. |
| `reorder_retransmit` | Repeated sequence-number drops during the interval. |
| `hw_restarts` | Cumulative HaLow driver hardware-restart notifications since boot. |

The HaLow rates are direct **AP network-interface observations**, not negotiated PHY rate, raw RF bitrate, channel occupancy, or signal strength. They include all stations and ordinary link traffic visible to lwIP, but exclude Wi-Fi PHY/MAC overhead, radio retries, and airtime. In this Heltec/Morse build, `mmwlan_get_rc_stats()` is unavailable in AP mode and the UMAC RSSI field reads zero; the dashboard does not invent either value. Per-station byte accounting is also unavailable.

MediaMTX exposes cumulative WebRTC session bytes on `127.0.0.1:9998` by default. The monitor derives aggregate video-delivery kbps once per second. A healthy service with no viewer reports `0 kbps`; an unreachable metrics service reports `N/A`. The loopback metrics listener and RTSP publisher are not exposed to either RC32 network.

## Firmware

Canonical sources are:

- Base HaLow AP and telemetry gateway: `firmware/base-gateway/base-gateway.ino`
- Pure rate decoder/summarizer: `firmware/base-gateway/halow_metrics.h`
- Pure accepted-frame traffic counter: `firmware/base-gateway/halow_traffic_accounting.h`
- Portable HaLow station and phone gateway: `firmware/portable-gateway/portable-gateway.ino`
- Portable HaLow reconnect policy: `firmware/portable-gateway/halow_reconnect_policy.h`
- Portable display status/rate model: `firmware/portable-gateway/portable_status.h`
- Optional T108/NV3001B panel driver: `firmware/portable-gateway/portable_tft.h`
- Display-driver BSD notice: `firmware/portable-gateway/ARDUINO_GFX_BSD_LICENSE.txt`

Both target this exact Arduino FQBN:

```text
heltec:esp_halow:HT-RC3268:UploadSpeed=921600,USBMode=hwcdc,CDCOnBoot=default,PSRAM=opi,EraseFlash=none
```

The vendor framework is kept as a visible Git submodule at [`firmware/vendor/ESP_HaLow`](firmware/vendor/ESP_HaLow), pointing to the `radiocore-gateway` branch of [our fork](https://github.com/rogerio-richa/ESP_HaLow). The fork is based on Heltec's [ESP_HaLow repository](https://github.com/HelTecAutomation/ESP_HaLow.git); compare the branch directly with [upstream main](https://github.com/rogerio-richa/ESP_HaLow/compare/main...radiocore-gateway). Build tooling may copy this pinned checkout into `.scratch/arduino/user/hardware/heltec/esp_halow`; `.scratch/` is disposable and ignored. Arduino CLI v1.5.1 is below `.scratch/tools/arduino-cli-1.5.1/`.

The fork carries the two small framework changes required by this project: AP station tracking in `HalowAP.cpp` and receive dispatch through the project's lwIP input adapter. They are visible in the fork's upstream comparison; no separate patch-install step is required.

Compile the base with:

```bash
ARDUINO_DIRECTORIES_DATA="$PWD/.scratch/arduino/data" \
ARDUINO_DIRECTORIES_USER="$PWD/.scratch/arduino/user" \
.scratch/tools/arduino-cli-1.5.1/arduino-cli compile \
  --fqbn 'heltec:esp_halow:HT-RC3268:UploadSpeed=921600,USBMode=hwcdc,CDCOnBoot=default,PSRAM=opi,EraseFlash=none' \
  --build-path .scratch/rc32-v1/build/base-gateway-monitor \
  firmware/base-gateway
```

Before uploading, identify the USB target with `esptool read_mac` and confirm it is the base unit. Do not flash the battery-powered portable unit when updating base telemetry.

Compile the portable gateway with the same FQBN and isolated toolchain:

```bash
ARDUINO_DIRECTORIES_DATA="$PWD/.scratch/arduino/data" \
ARDUINO_DIRECTORIES_USER="$PWD/.scratch/arduino/user" \
.scratch/tools/arduino-cli-1.5.1/arduino-cli compile \
  --fqbn 'heltec:esp_halow:HT-RC3268:UploadSpeed=921600,USBMode=hwcdc,CDCOnBoot=default,PSRAM=opi,EraseFlash=none' \
  --build-path .scratch/rc32-v1/build/portable-gateway-tft \
  firmware/portable-gateway
```

Before uploading the portable image, confirm with `esptool read_mac` that the USB target is the portable unit, and preserve its current flash. The patched Morse receive archive requires both gateway sketches to export the `halow_mmnetif_input` adapter; this is intentional and allows accepted receive traffic to pass through each active lwIP input callback.

The portable station also has an application-level reconnect guard. If the HaLow association remains down for five seconds, the firmware requests a fresh station association and repeats that request every 15 seconds until the link returns. When the association returns, it reinstalls the HaLow interface as the default route and reattaches traffic accounting before forwarding phone traffic again. The phone-facing access point and NAPT are left running during this process.

**Known limitation.** Link losses caused by either radio restarting, including outages of about a minute, recover on their own and the stream resumes. A genuine RF fade with both radios still powered (carrying the portable out of range and back) can leave the portable reporting `LINK UP` while no IP traffic crosses the HaLow link; the base still lists the station and transmits to it, but receives nothing. Power-cycling the portable restores the link. The cause has not been identified.

### Portable T108 display

The portable firmware supports the attached Heltec T108/RS-T108 display as an optional local instrument. It probes the 128x220 NV3001B controller for ID `0x300101` before enabling the backlight. A missing or unexpected panel is powered down and logged once; HaLow, the phone-facing Wi-Fi AP, and NAPT continue headlessly.

The portrait page uses a dark background with bright green, cyan, and white status text. It is a static six-row, 3x-font instrument showing `LINK UP` (the portable RC32 is connected to the base RC32 over HaLow), station-side `HaLow.RSSI()`, the phone-facing Wi-Fi client count, channel 41, 922.5 MHz center frequency, and 1 MHz bandwidth. While disconnected, `LINK UP` changes to `SEARCH` and the RSSI reads `--dBm`. Traffic rates, IP address, and uptime are intentionally omitted so every displayed value remains legible at the larger size. The screen does not show SNR because no continuous connected-state SNR API is available.

The RadioCore ESP32-S3 pin map is clock 17, bidirectional SDA/MOSI 38, CS 39, DC 16, reset 4, active-low panel enable 6, and active-high backlight 5. The minimal command/font implementation is adapted from BSD-licensed `moononournation/Arduino_GFX` commit `b4c3cbe2144c9c5942a073fb94a0a4ab9c23c5d6`; its license is retained beside the driver. No HaLowFieldTester application, networking, protocol, UI, role, or regional configuration is included.

The video service uses FFmpeg with H.264 Baseline/Opus and the official [MediaMTX v1.20.1 release](https://github.com/bluenviron/mediamtx/releases/tag/v1.20.1). Its MediaMTX binary is below `.scratch/tools/mediamtx-1.20.1/`; the Mac's FFmpeg installation must provide `libx264` and `libopus`. RTSP remains loopback-only, and the WebRTC service uses no STUN, TURN, cloud service, or Internet access.

## Tests

Run the automated checks from the project root:

```bash
/usr/bin/python3 -m unittest -v \
  tests.test_halow_monitor \
  tests.test_halow_monitor_integration \
  tests.test_dashboard_assets \
  tests.test_stream_timestamp
tests/test_halow_metrics.sh
tests/test_halow_traffic_accounting.sh
tests/test_portable_status.sh
tests/test_portable_tft_interface.sh
tests/test_halow_reconnect.sh
tests/test_portable_gateway_compile.sh
tests/test_monitor_launcher.sh
tests/test_stream_cli.sh
tests/test_stream_launcher.sh
.scratch/tests/test_stream_webrtc.sh
.scratch/tests/test_stream_logging.sh
```

The Python and stream integration tests bind temporary loopback ports and create pseudo-terminals. The firmware tests compile the pure rate summarizer and traffic-accounting semantics on the Mac; the Arduino CLI commands above verify the complete board targets.

## Recovery

Before flashing a board for the first time, read out its full 8 MiB flash with `esptool read_flash 0 0x800000 <file>` and keep the image with its SHA-256, so the factory firmware can be restored. Always re-read the physical device MAC immediately before any flash write or recovery operation.

## Radio profile caveat

The current controlled-prototype firmware explicitly selects Heltec's `US` profile and HaLow channel 41 (1 MHz, 922–923 MHz occupied). Testing has taken place physically in Brazil. This project makes no claim that this profile, channel, antenna, output power, or prototype is compliant with Brazilian requirements. Confirm the supported Heltec/Morse Micro country profile and the applicable ANATEL authorization before radiating outside controlled prototype testing.
