# From Two Factory-Fresh Radios to a Working HaLow Video Link

Project state documented: September 2, 2026.

This is the story of how we turned two RadioCore RC32 v1 prototypes into a self-contained video link using Wi-Fi HaLow. It is written to be read from beginning to end. The implementation details, source lineage, and reference links are collected separately in the second half.

The project began as a practical question: could we send live video through a pair of compact HaLow radios, carry one end around, and use the result as a small testbed for range, responsiveness, and eventually power consumption?

The short answer is yes. A video playing on a Mac now crosses an ordinary Wi-Fi hop, a Wi-Fi HaLow hop, and another ordinary Wi-Fi hop before appearing in Chrome on an Android phone. The portable radio also has a local screen showing whether the HaLow connection is up, its received signal strength, the number of nearby Wi-Fi clients, and the configured radio channel.

This is a successful connectivity demonstration, not yet a calibrated radio benchmark. That distinction matters throughout the story.

## Part I — The story

### 1. What we started with

We had two RadioCore RC32 v1 boards. Each combines an ESP32-S3 microcontroller with a Heltec HT-HC01 Wi-Fi HaLow module based on the Morse Micro MM6108.

Wi-Fi HaLow is the name used for IEEE 802.11ah Wi-Fi. Unlike the 2.4 GHz and 5 GHz Wi-Fi built into a phone or laptop, HaLow operates below 1 GHz. Its narrower channels and lower frequency are intended to trade peak speed for longer reach and better propagation. The Mac and Android phone used in this experiment cannot join the HaLow network directly, so each RC32 has an important second job: its ESP32-S3 provides an ordinary 2.4 GHz Wi-Fi network for a nearby phone or computer.

Our intended path was therefore:

```text
video source → base RC32 → Wi-Fi HaLow → portable RC32 → phone
```

The base would stay near the video source. The portable unit would be the end we could carry away. If the picture froze, recovered, or disappeared as the portable moved, we would have an immediate human-readable indication of link behavior. Later, an inline power meter could add trustworthy energy measurements to the same experiment.

### 2. Discovering what the boards did out of the box

Both boards powered up with firmware already installed, but plugging them into USB did not create a network connection. USB exposed power, serial diagnostics, debugging, and flashing—not Ethernet.

We preserved the complete 8 MiB flash contents of both units before changing anything. We also identified each ESP32-S3 by its hardware address before every read or write, so a temporary device name could not cause us to back up or flash the wrong board.

Inspection of the preserved firmware revealed strings such as:

```text
heltec_test
[factory test credential]
192.168.100.1
Scan start...
Halow connect timeout
Halow connected
Halow TX Test XX
Halow RX Test XX
Halow Test OK
```

Those strings strongly suggest a Heltec manufacturing or validation application: something intended to scan, connect, transmit, receive, and report whether the radio passed a test. We did not recover the original source code, so we should not claim a more precise identity than that. What we could establish was that it was not a documented IP gateway and did not provide the Mac-to-phone topology we needed.

Only after preserving both factory images did we replace it.

### 3. Finding a reproducible starting point

Rather than inventing the radio initialization from scratch, we used Heltec's published `ESP_HaLow` Arduino framework. We pinned the working tree to a specific commit and used the framework's `HT-RC3268` board definition, which matches this ESP32-S3/HaLow hardware arrangement.

Three Heltec examples shaped the result:

- `HalowAP` supplied the smallest initial proof that the toolchain, ESP32-S3, and HT-HC01 could start a HaLow access point.
- `NAPT_WiFi_to_HalowAP` became the foundation for the base gateway.
- `NAPT_HalowSTA_STATIC_to_WiFiAP` became the foundation for the portable gateway.

That first access-point proof was deliberately modest. It confirmed that we could compile, flash, power the HaLow module correctly, and make the radio start. Once that worked, we moved to the real two-ended network.

### 4. Choosing an isolated network

An early design would have placed one side of the experiment on an existing home Wi-Fi network. That was convenient, but it introduced the wrong dependencies: home credentials, the household router, existing Internet routes, and ambiguity about which path carried the video.

We chose an isolated topology instead. Both RC32 boards create ordinary 2.4 GHz access points:

```text
Mac
  │ ordinary Wi-Fi
  ▼
base RC32
  │ Wi-Fi HaLow
  ▼
portable RC32
  │ ordinary Wi-Fi
  ▼
Android phone
```

The Mac joins the base RC32. The phone joins the portable RC32. Only the two HT-HC01 modules see the HaLow backbone between them. The demonstration needs no home Wi-Fi, Internet service, cloud relay, STUN server, or TURN server.

Each RC32 routes and translates traffic using network address and port translation, or NAPT. The resulting path has two NAPT boundaries rather than behaving like one transparent Ethernet cable. The practical consequence is simple: the phone can initiate a connection to a service on the Mac, but the Mac cannot initiate an unsolicited connection directly to the phone's private address.

### 5. Replacing the factory application with two gateway roles

We gave the boards distinct jobs.

The base firmware now:

- powers and initializes the HT-HC01;
- creates the HaLow backbone as its access point;
- creates a separate ordinary Wi-Fi access point for the Mac;
- supplies addresses to nearby Wi-Fi clients;
- routes and translates traffic between ordinary Wi-Fi and HaLow; and
- makes the HaLow interface its onward route.

The portable firmware now:

- joins the base as a HaLow station;
- uses a fixed address on the HaLow link;
- creates an ordinary Wi-Fi access point for the phone;
- supplies an address to the phone;
- routes and translates the phone's traffic onto HaLow; and
- reports its real station-side received signal strength over serial.

This produced the first meaningful end-to-end network proof. A computer on the portable side could ping the Mac through both gateways and the HaLow hop. Five early replies varied from roughly 143 to 623 milliseconds, and one HTTP transfer was observed around 493 kbit/s. Those were useful signs that packets really crossed the intended path, but they were spot checks—not controlled latency or throughput measurements.

### 6. Getting real-time video across the link

With IP connectivity working, we wanted video that behaved in real time, with as little buffering as practical. A large playback buffer would hide brief drops, freezes, and recovery—the very changes a walk-around radio test is meant to reveal.

#### Our approach

Our pipeline uses FFmpeg and MediaMTX:

```text
MP4 file
  → FFmpeg, paced like a live source
  → local RTSP/TCP
  → MediaMTX
  → WebRTC over the isolated RC32 network
  → Chrome on the phone
```

FFmpeg loops the source indefinitely and converts it to a browser-friendly, low-latency stream. MediaMTX accepts that local stream and serves a WebRTC page to the phone. We use H.264 Baseline without B-frames because this is broadly compatible with browser WebRTC playback; optional audio is converted to Opus.

The chosen demonstration stream is intentionally modest: 640×360 pixels at 15 frames per second, with a 300 kbit/s video target and a 400 kbit/s maximum. That leaves room for protocol overhead and return traffic on the 1 MHz HaLow channel.

This version worked end to end. We confirmed the looping video in Android Chrome. MediaMTX recorded one active WebRTC reader, the session arrived through the expected gateway path, and the inspected session reported no discarded outbound frames.

One command now starts the media server, encoder, and monitoring service together:

```bash
./stream.sh
```

Stopping that command also stops only the processes it started and releases their ports.

At the end of the build, the two gateways remained flashed and associated. The Mac-side encoder, media server, and dashboard are intentionally on-demand tools rather than permanent background services; `stream.sh` starts them again when the demonstration is needed.

### 7. Turning a successful demo into an observable one

Seeing video was useful, but the original goal also involved understanding what the link was doing. We therefore built a live dashboard around the base RC32's serial output.

This exposed an important engineering lesson: a field with a convincing label is not automatically a real measurement.

We initially looked for modulation, coding, signal, and delivery statistics in the vendor APIs. On this build and in access-point mode:

- the rate-control table was unavailable;
- the access-point-side RSSI field remained zero;
- continuous SNR or noise was not exposed; and
- Heltec's station-list wrapper always returned zero even while the portable was connected.

We chose not to turn missing data into estimates. The dashboard marks unavailable measurements as unavailable.

For the values we could measure honestly, we repaired the observation path:

1. We fixed Heltec's station callback wrapper so the base could report the real number of associated HaLow stations.
2. We counted bytes accepted at the base's HaLow network interface in each direction.
3. We made a narrow patch to the bundled Morse receive object because it bypassed the active network-interface callback, which otherwise made received traffic invisible to our counter.
4. The Mac derives rates from consecutive cumulative counters instead of trusting a single instantaneous sample.
5. MediaMTX's own WebRTC byte counters remain a separate measurement of application video delivery.

That separation is intentional. “Traffic crossing the HaLow interface” and “video delivered by MediaMTX” are measurements at different layers. They should be similar during a one-viewer video test, but they should not be added together or presented as the radio's raw physical-layer speed.

During one representative viewing interval, MediaMTX delivered about 307 kbit/s, the base observed about 341 kbit/s traveling toward the portable side, and return traffic was about 3.5 kbit/s. The difference is expected: the HaLow interface also carries ordinary network traffic and observes a different boundary from the video server.

The approximately 400 kbit/s result was imposed by our encoder settings. It is not the maximum throughput of the radio.

### 8. Adding a local display to the portable end

The portable hardware had a small T108/RS-T108 color panel attached. It uses a 128×220 NV3001B controller, but it was not managed by our gateway firmware.

We used the `HaLowFieldTester` project only to confirm the physical display and ESP32-S3 pin mapping for this hardware family. We did not copy its application, radio roles, network configuration, regional settings, protocol, or user interface. Its application solves a different problem, and its repository did not provide a top-level license suitable for importing code.

For the actual display initialization and font data, we adapted the minimum required pieces from the BSD-licensed `Arduino_GFX` implementation and retained its complete license notice in this project.

The display driver first reads the controller identity. If it sees the expected NV3001B ID, it initializes the panel and turns on the backlight. If the panel is absent or unexpected, the firmware leaves it powered down and continues running the gateway. A display problem is therefore not allowed to take down either network.

The first working screen tried to show connection state, RSSI, traffic rates, client count, radio settings, address, and uptime all at once. It worked electronically, but most of the text was too small to read at a useful distance.

We replaced that dense page with one fixed, high-contrast screen. Every line now uses the 3× bitmap font:

```text
LINK UP
-24dBm
WIFI 1
CH 41
922.5
1 MHz
```

`LINK UP` means that the portable RC32 is associated with the base RC32 over HaLow. `WIFI 1` means one ordinary Wi-Fi client—normally the phone—is connected to the portable access point. When the HaLow link is down, the first line changes to `SEARCH` and the signal line becomes `--dBm`.

After the final flash, the portable reported the expected panel ID, rejoined the base, restored its phone-facing Wi-Fi network and NAPT, and continued reporting a real RSSI value. We also confirmed that the physical display was working.

We then hardened the portable station for the failure mode that matters most during a range walk: the HaLow association can disappear while the phone remains near the portable unit. The firmware now waits five seconds after detecting a loss, asks the HaLow station to associate again, and repeats that request every fifteen seconds until it succeeds. The phone-facing access point and NAPT are not torn down while this happens. The display changes to `SEARCH` and the serial log records each reconnect request, making a temporary fade visible without requiring a reboot.

### 9. What the prototype demonstrates

The finished prototype demonstrates that:

- two RC32/HT-HC01 devices can form an isolated routed HaLow link;
- ordinary phones and computers can use that link through the ESP32-S3 Wi-Fi interfaces;
- a browser can receive a low-latency WebRTC video stream across both NAPT boundaries;
- the system can expose real client, traffic, viewer, fault, and station-side RSSI information without inventing unavailable radio metrics; and
- the portable end can provide an immediately readable local link display.
- the portable station can recover from a temporary HaLow association loss while keeping its local phone network alive.

It does not yet establish maximum range, maximum goodput, packet-loss curves, or energy consumption. The early ping and transfer values were functional checks rather than a controlled measurement campaign. The video encoder was capped below the radio's likely capacity, and USB descriptors cannot provide reliable real-time power readings. Meaningful power data requires an inline USB power meter or dedicated current monitor sampled during defined states such as idle, association, sustained transmit, and sustained receive.

The firmware currently selects Heltec's US regulatory profile and a 1 MHz channel centered at 922.5 MHz. The work was performed in Brazil, and this project has not established that the selected profile, prototype, antenna, or power level is compliant with ANATEL rules. Successful operation is not regulatory approval.

---

## Part II — Technical reference

### Hardware and roles

| Component | Role in this project |
|---|---|
| RadioCore RC32 v1, base | HaLow access point plus ordinary 2.4 GHz Wi-Fi access point for the Mac |
| RadioCore RC32 v1, portable | HaLow station plus ordinary 2.4 GHz Wi-Fi access point for the phone |
| ESP32-S3 on each RC32 | Runs Arduino firmware, ordinary Wi-Fi, DHCP, routing, NAPT, serial diagnostics, and the portable display |
| Heltec HT-HC01 | Wi-Fi HaLow module containing a Morse Micro MM6108 transceiver |
| Mac | Video source, FFmpeg encoder, MediaMTX server, telemetry collector, and dashboard server |
| Android phone | WebRTC viewer connected to the portable unit's ordinary Wi-Fi network |

The HT-HC01 manufacturer documentation describes 1, 2, 4, and 8 MHz channel options in the 902–928 MHz version. This prototype deliberately stayed at 1 MHz.

### Final network topology

```text
Mac 10.41.0.2
  │ ordinary 2.4 GHz Wi-Fi: RC32-Base
  ▼
base RC32: Wi-Fi gateway 10.41.0.1
           HaLow AP 10.42.0.1
  │ 802.11ah: RC32-HaLow-Backbone
  │ channel 41, 922.5 MHz center, 1 MHz bandwidth
  ▼
portable RC32: HaLow station 10.42.0.2
               Wi-Fi gateway 10.43.0.1
  │ ordinary 2.4 GHz Wi-Fi: RC32-HaLow
  ▼
Android phone: DHCP address on 10.43.0.0/24
```

Passwords, hardware addresses, personal hostnames, and local filesystem locations are intentionally omitted from this publication-oriented document.

### Firmware lineage

The build uses the visible Git submodule [`firmware/vendor/ESP_HaLow`](firmware/vendor/ESP_HaLow), pinned to our `radiocore-gateway` branch of [the project fork](https://github.com/rogerio-richa/ESP_HaLow). That branch starts from Heltec commit [`f3cedf7415089d4ea80c9cbd23d672e1a88ef788`](https://github.com/HelTecAutomation/ESP_HaLow/tree/f3cedf7415089d4ea80c9cbd23d672e1a88ef788); its [upstream comparison](https://github.com/rogerio-richa/ESP_HaLow/compare/main...radiocore-gateway) shows the framework diff. The local package reports framework version 3.0.0 and bundles the Morse MM-IoT-SDK 2.10.4 family.

The exact Arduino target is:

```text
heltec:esp_halow:HT-RC3268:
  UploadSpeed=921600,
  USBMode=hwcdc,
  CDCOnBoot=default,
  PSRAM=opi,
  EraseFlash=none
```

The main upstream examples were:

- [Minimal HaLow access point](https://github.com/HelTecAutomation/ESP_HaLow/blob/f3cedf7415089d4ea80c9cbd23d672e1a88ef788/libraries/wifi-halow/examples/HalowAP/HalowAP.ino)
- [Wi-Fi-to-HaLow NAPT gateway](https://github.com/HelTecAutomation/ESP_HaLow/blob/f3cedf7415089d4ea80c9cbd23d672e1a88ef788/libraries/wifi-halow/examples/NAPT_WiFi_to_HalowAP/NAPT_WiFi_to_HalowAP.ino)
- [HaLow-station-to-Wi-Fi NAPT gateway](https://github.com/HelTecAutomation/ESP_HaLow/blob/f3cedf7415089d4ea80c9cbd23d672e1a88ef788/libraries/wifi-halow/examples/NAPT_HalowSTA_STATIC_to_WiFiAP/NAPT_HalowSTA_STATIC_to_WiFiAP.ino)

### What changed from the vendor examples

| Area | Project change | Reason |
|---|---|---|
| Base networking | Fixed isolated subnets, HaLow AP, Mac-facing Wi-Fi AP, DHCP, NAPT, and explicit default route | Keep the demonstration independent of home Wi-Fi and the Internet |
| Portable networking | Fixed HaLow station address, phone-facing Wi-Fi AP, DHCP, NAPT, and explicit default route | Give an ordinary phone access to the HaLow path |
| Base telemetry | Versioned one-second serial records with real activity, association, traffic, and driver-fault counters | Make the experiment observable and machine-readable |
| Unavailable radio data | Explicit sentinel values rather than estimated RSSI, SNR, MCS, bandwidth, guard interval, or delivery statistics | Avoid presenting guesses as measurements |
| HaLow client count | Fixed-capacity association table maintained by Heltec's station-status callback | The vendor wrapper discarded callback data and always returned zero clients |
| Traffic accounting | Accepted-byte counters around the HaLow lwIP network interface | Measure aggregate network traffic in both directions |
| Receive dispatch | Audited one-symbol patch to the bundled `mmnetif` object, with hash validation and a preserved original archive | The precompiled receive path bypassed the active interface callback and therefore bypassed RX accounting |
| Host monitor | Strict serial parser, rate derivation, bounded history, CSV output, HTTP API, server-sent events, and local dashboard | Turn firmware counters into a readable and recordable experiment |
| Media telemetry | Independent MediaMTX WebRTC session-byte and viewer counts | Keep application delivery distinct from radio-interface traffic |
| Portable display | Optional NV3001B driver, panel-ID probe, local RSSI/client status, and six-row 3× layout | Make the portable link state readable without a laptop |

Canonical source files:

- [`firmware/base-gateway/base-gateway.ino`](firmware/base-gateway/base-gateway.ino)
- [`firmware/base-gateway/halow_metrics.h`](firmware/base-gateway/halow_metrics.h)
- [`firmware/base-gateway/halow_traffic_accounting.h`](firmware/base-gateway/halow_traffic_accounting.h)
- [`firmware/portable-gateway/portable-gateway.ino`](firmware/portable-gateway/portable-gateway.ino)
- [`firmware/portable-gateway/halow_reconnect_policy.h`](firmware/portable-gateway/halow_reconnect_policy.h)
- [`firmware/portable-gateway/portable_status.cpp`](firmware/portable-gateway/portable_status.cpp)
- [`firmware/portable-gateway/portable_tft.cpp`](firmware/portable-gateway/portable_tft.cpp)
The fork carries the framework changes required by this project: AP station tracking and receive dispatch through the project's lwIP input adapter. The upstream comparison shows those changes directly, so a clean build does not need separate patch installers.

### What the dashboard measures

The base emits one schema-v3 serial record per second. The host monitor parses it and derives rates from cumulative byte and firmware-time differences.

The most useful fields are:

| Measurement | Meaning |
|---|---|
| HaLow clients | Stations currently associated or authorized according to the repaired callback table |
| Radio active | Whether the UMAC last-transmit timestamp changed during the interval |
| Toward phone | Bytes accepted by the base's HaLow interface in the base-to-portable direction, expressed as a host-derived rate |
| Toward Mac | Bytes accepted in the portable-to-base direction, expressed as a host-derived rate |
| Driver health | Queue drops, allocation/read failures, reorder events, and hardware restart count reported by the UMAC statistics API |
| Video delivery | Aggregate MediaMTX bytes sent to active WebRTC viewers, derived separately |
| Video viewers | Active MediaMTX WebRTC sessions for the demonstration path |

HaLow interface rates are not PHY bitrate, airtime, RF capacity, retry overhead, or per-station accounting. Video delivery is not added to the interface rate. A first sample, counter reset, firmware restart, or unavailable source yields “unavailable” instead of a false spike or zero.

The monitor and presentation files are:

- [`halow_monitor.py`](halow_monitor.py)
- [`monitor-halow.sh`](monitor-halow.sh)
- [`monitor/index.html`](monitor/index.html)
- [`monitor/dashboard.css`](monitor/dashboard.css)
- [`monitor/dashboard.js`](monitor/dashboard.js)

### Streaming architecture

[`stream.sh`](stream.sh) supervises three pieces:

1. MediaMTX accepts RTSP only on loopback and exposes WebRTC on the RC32-facing Mac address.
2. FFmpeg loops and transcodes the selected MP4, then publishes it to MediaMTX over local RTSP/TCP.
3. The HaLow monitor reads the base's USB serial telemetry and serves the dashboard.

The media settings shared by every profile are:

| Setting | Value |
|---|---|
| Video codec/profile | H.264 Baseline via `libx264` |
| B-frames | Disabled |
| Encoder preset/tune | `ultrafast`, `zerolatency` |
| Optional audio | Mono Opus, 32 kbit/s, 48 kHz |
| Source behavior | Native-rate pacing and indefinite looping |

The default remains the low-bandwidth profile used for the successful demonstration, while command-line arguments make controlled higher-load tests possible:

| Profile | Output | Frame rate | Video target/maximum |
|---|---:|---:|---:|
| `360p` (default) | 640×360 | 15 fps | 300/400 kbit/s |
| `720p` | 1280×720 | 30 fps | 1,500/1,800 kbit/s |
| `1080p` | 1920×1080 | 30 fps | 4,000/4,500 kbit/s |

`--profile` selects one of these presets. `--bitrate-kbps` replaces its video target and ceiling so tests can step upward without editing the launcher. The script inspects the input dimensions first and emits a non-blocking warning if the chosen output would upscale the source. The included `media/kitties.mp4` is 1920×1080 and therefore supplies native resolution for all three profiles.

Increasing the requested bitrate until playback becomes unstable measures the sustainable capacity of this particular encoded WebRTC path. It is not a direct measurement of HaLow PHY bitrate or RF efficiency.

The runtime exposes the WebRTC page and its fixed UDP media port only on the isolated Mac-facing network. RTSP and metrics remain on loopback. Unneeded MediaMTX protocols and administrative services are disabled for this demonstration.

Each invocation also creates a persistent run directory below `.scratch/logs/`, containing a timestamped launcher trace and separate MediaMTX, monitor, and FFmpeg logs. `--log-dir` can select a new explicit directory for an offline test. The dashboard server avoids reverse-DNS work during startup because the RC32 network deliberately has no Internet or DNS service. The launcher also recognizes the monitor's explicit readiness announcement, so an unsuccessful Mac-side health probe is logged as a warning rather than causing a healthy monitor to be killed. Cleanup still stops only the owned processes and removes the temporary generated MediaMTX configuration; it no longer removes the diagnostic evidence needed after a failed run.

### Display implementation

The portable screen uses the following ESP32-S3 connections:

| Display signal | GPIO |
|---|---:|
| Clock | 17 |
| Bidirectional data/MOSI | 38 |
| Chip select | 39 |
| Data/command | 16 |
| Reset | 4 |
| Panel enable, active-low | 6 |
| Backlight, active-high | 5 |

The expected controller ID is `0x300101`, with a 128×220 portrait framebuffer. The driver uses the ESP32-S3's third SPI controller at 4 MHz, separate from the MM6108's bus.

Pin mapping evidence came from [`Mesh-mazowsze/HaLowFieldTester` at commit `90ff0c7a…`](https://github.com/Mesh-mazowsze/HaLowFieldTester/tree/90ff0c7a1b83058bc6be53ca56063c7e6a8e48ea). No source from that application was imported.

The controller command table and the required subset of the classic bitmap font were adapted from [`moononournation/Arduino_GFX` at commit `b4c3cbe…`](https://github.com/moononournation/Arduino_GFX/tree/b4c3cbe2144c9c5942a073fb94a0a4ab9c23c5d6). Its BSD notice is retained in [`ARDUINO_GFX_BSD_LICENSE.txt`](firmware/portable-gateway/ARDUINO_GFX_BSD_LICENSE.txt).

### Verification and recovery

The implementation was checked at several levels:

- host-native tests for telemetry parsing, rate calculations, resets, malformed input, HTTP endpoints, history, CSV output, SSE, display status, layout size, and traffic accounting;
- tests for the pinned framework fork and its supported source/archive shapes;
- full Arduino compilation for the real HT-RC3268 target;
- launcher and lifecycle tests for the monitor, FFmpeg, MediaMTX, WebRTC, and cleanup behavior;
- identity-gated flash operations followed by esptool write-hash verification;
- serial boot checks for panel identity, HaLow association, Wi-Fi startup, NAPT, default route, and continuing RSSI; and
- live phone confirmation of WebRTC playback and the physical display.

The final verification run completed 46 Python tests plus 12 shell and firmware checks. The portable build occupied about 52% of available application flash and 16% of dynamic memory.

Complete factory flash images for both physical boards are retained privately. A later pre-display portable backup is also retained. Recovery must always begin by reading the connected ESP32-S3 identity and matching it to the correct image; transient USB device names are not sufficient identification.

### Primary references

- [Heltec HT-HC01 product page](https://heltec.org/project/ht-hc01/)
- [Heltec HT-HC01 documentation](https://docs.heltec.org/en/wifi_halow/ht-hc01/index.html)
- [Morse Micro MM6108 data sheet](https://www.morsemicro.com/resources/datasheets/chips/MM6108_Data_Sheet.pdf)
- [Heltec ESP_HaLow framework](https://github.com/HelTecAutomation/ESP_HaLow)
- [Pinned Heltec framework revision](https://github.com/HelTecAutomation/ESP_HaLow/tree/f3cedf7415089d4ea80c9cbd23d672e1a88ef788)
- [Espressif ESP-NETIF and NAPT API](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_netif_programming.html)
- [MediaMTX v1.20.1 release](https://github.com/bluenviron/mediamtx/releases/tag/v1.20.1)
- [MediaMTX WebRTC browser playback](https://mediamtx.org/docs/read/web-browsers)
- [MediaMTX WebRTC codec and connectivity guidance](https://mediamtx.org/docs/features/webrtc-specific-features)
- [FFmpeg command-line documentation](https://ffmpeg.org/ffmpeg.html)
- [Arduino CLI v1.5.1 release](https://github.com/arduino/arduino-cli/releases/tag/v1.5.1)
- [HaLowFieldTester pin-mapping reference](https://github.com/Mesh-mazowsze/HaLowFieldTester/tree/90ff0c7a1b83058bc6be53ca56063c7e6a8e48ea)
- [Arduino_GFX display-driver reference](https://github.com/moononournation/Arduino_GFX/tree/b4c3cbe2144c9c5942a073fb94a0a4ab9c23c5d6)

## Closing perspective

The useful result is not merely that a video played. We began with two opaque factory-programmed devices and ended with a reproducible, recoverable, observable system whose limits are stated as carefully as its successes.

The radio path is isolated. The firmware lineage is pinned. Both original flashes are preserved. The screen and dashboard report measurements we can defend, while unavailable measurements remain visibly unavailable. Video works from one ordinary consumer device to another across the HaLow hop, and the portable end can be moved without carrying a laptop simply to know whether it is still connected.

That makes this prototype a sound starting instrument for range and power experiments—even though it is not, by itself, the result of those experiments.
