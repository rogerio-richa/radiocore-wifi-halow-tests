(() => {
  "use strict";

  // Shows the video host's clock beside the player, estimated over HTTP so the
  // reading never depends on the viewing device's own clock. Compare it with the
  // time burned into each frame to read the one-way delay for this viewer.

  const display = document.getElementById("pi-clock");
  const detail = document.getElementById("pi-clock-sync");
  if (!display) return;

  const SAMPLES = 4;
  const RESYNC_MS = 30000;
  let offsetMs = null;
  let utcOffsetMs = 0;
  let uncertaintyMs = null;

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function formatTimeOfDay(ms) {
    const dayMs = 86400000;
    const inDay = ((ms % dayMs) + dayMs) % dayMs;
    const seconds = Math.floor(inDay / 1000);
    return `${pad(Math.floor(seconds / 3600))}:${pad(Math.floor((seconds % 3600) / 60))}:${pad(seconds % 60)}`;
  }

  async function sample() {
    const started = performance.now();
    const sentAt = Date.now();
    const response = await fetch("/api/time", { cache: "no-store" });
    if (!response.ok) throw new Error(`status ${response.status}`);
    const payload = await response.json();
    const roundTrip = performance.now() - started;
    return {
      roundTrip,
      offset: payload.epoch_ms - (sentAt + roundTrip / 2),
      utcOffsetMs: (payload.utc_offset_seconds || 0) * 1000,
    };
  }

  async function sync() {
    let best = null;
    for (let index = 0; index < SAMPLES; index += 1) {
      try {
        const result = await sample();
        if (!best || result.roundTrip < best.roundTrip) best = result;
      } catch (_error) {
        // A failed sample is skipped; the previous estimate stays in use.
      }
    }
    if (!best) {
      if (offsetMs === null && detail) detail.textContent = "clock sync unavailable";
      return;
    }
    offsetMs = best.offset;
    utcOffsetMs = best.utcOffsetMs;
    uncertaintyMs = Math.round(best.roundTrip / 2);
    if (detail) {
      const deviceAhead = Math.round(-offsetMs);
      const sign = deviceAhead >= 0 ? "+" : "−";
      detail.textContent = `sync ±${uncertaintyMs} ms · device clock ${sign}${Math.abs(deviceAhead)} ms`;
    }
  }

  function render() {
    if (offsetMs === null) {
      display.textContent = "--:--:--";
      return;
    }
    display.textContent = formatTimeOfDay(Date.now() + offsetMs + utcOffsetMs);
  }

  render();
  sync();
  window.setInterval(render, 200);
  window.setInterval(sync, RESYNC_MS);
})();
