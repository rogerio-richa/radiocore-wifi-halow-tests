(() => {
  "use strict";

  const player = document.getElementById("video-player");
  const state = document.getElementById("video-state");
  const total = document.getElementById("total-throughput");
  const clients = document.getElementById("halow-clients");
  const activity = document.getElementById("radio-activity");
  const poster = document.getElementById("video-poster");
  let playerURL = "";

  function displayRate(value) {
    return typeof value === "number" && value >= 0 ? value.toFixed(1) : "N/A";
  }

  function applySnapshot(snapshot) {
    const latest = snapshot.latest;
    if (latest) {
      total.innerHTML = `${displayRate(latest.halow_total_kbps)} <small>kbps</small>`;
      clients.textContent = latest.halow_clients >= 0 ? String(latest.halow_clients) : "—";
      activity.textContent = latest.radio_active ? "Active" : "Idle";
    }
    const run = snapshot.run && snapshot.run.active;
    state.textContent = run ? "Capture active" : "Waiting for capture";
    const playerConfig = snapshot.player;
    if (!run || !playerConfig) {
      player.removeAttribute("src");
      playerURL = "";
      poster.hidden = false;
      return;
    }
    const url = `${location.protocol}//${location.hostname}:${playerConfig.port}/${playerConfig.path}/`;
    if (url !== playerURL) {
      playerURL = url;
      player.src = url;
    }
    poster.hidden = true;
  }

  async function refresh() {
    try {
      const response = await fetch("/api/latest", { cache: "no-store" });
      if (!response.ok) throw new Error(`status ${response.status}`);
      applySnapshot(await response.json());
    } catch (_error) {
      state.textContent = "Measurement service unavailable";
    }
  }

  refresh();
  window.setInterval(refresh, 1000);
})();
