"use strict";

(function () {
  const MAX_SAMPLES = 900;
  const apSamples = [];
  const videoSamples = [];
  const elements = {
    state: document.getElementById("connection-state"),
    age: document.getElementById("sample-age"),
    total: document.getElementById("halow-total-value"),
    downlink: document.getElementById("halow-downlink-value"),
    uplink: document.getElementById("halow-uplink-value"),
    clients: document.getElementById("halow-clients-value"),
    activity: document.getElementById("radio-activity-value"),
    drops: document.getElementById("drops-value"),
    lanVideoBitrate: document.getElementById("lan-video-bitrate-value"),
    halowVideoBitrate: document.getElementById("halow-video-bitrate-value"),
    lanVideoViewers: document.getElementById("lan-video-viewers-value"),
    halowVideoViewers: document.getElementById("halow-video-viewers-value"),
    videoStatus: document.getElementById("video-status-value"),
    apCanvas: document.getElementById("ap-history-canvas"),
    videoCanvas: document.getElementById("video-history-canvas"),
    runPlay: document.getElementById("run-play"),
    runStop: document.getElementById("run-stop"),
    reviewRuns: document.getElementById("review-runs"),
    runElapsed: document.getElementById("run-elapsed"),
    runBrowser: document.getElementById("run-browser"),
    runList: document.getElementById("run-list"),
    reviewPlayer: document.getElementById("review-player"),
    reviewPlayback: document.getElementById("review-playback"),
    reviewScrubber: document.getElementById("review-scrubber"),
    exitReview: document.getElementById("exit-review"),
    clearRuns: document.getElementById("clear-runs"),
    sourceName: document.getElementById("source-name"),
    sourceResolution: document.getElementById("source-resolution"),
    outputResolution: document.getElementById("output-resolution"),
    adminVideoPoster: document.getElementById("admin-video-poster"),
  };

  let reportedAge = null;
  let ageReceivedAt = performance.now();
  let latestStatus = "waiting";
  let activeRun = null;
  let runElapsedAtUpdate = 0;
  let runUpdatedAt = performance.now();
  let reviewData = null;
  let reviewPlaying = false;
  let reviewBaseMs = 0;
  let reviewStartedAt = 0;
  let playerConfig = null;

  function formatAge(seconds) {
    if (seconds === null || !Number.isFinite(seconds)) {
      return "No AP samples";
    }
    if (seconds < 1) {
      return "AP sample now";
    }
    return `AP sample ${Math.floor(seconds)}s ago`;
  }

  function formatRate(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) {
      return "N/A";
    }
    if (number >= 1000) {
      return number.toFixed(0);
    }
    if (number >= 100) {
      return number.toFixed(1);
    }
    return number.toFixed(2);
  }

  function setStatus(status, age, serialError) {
    const labels = {
      live: "AP telemetry live",
      waiting: "Waiting for AP",
      stale: "AP telemetry stale",
      "serial-error": "AP serial error",
      reconnecting: "Dashboard reconnecting",
      offline: "Dashboard offline",
    };
    latestStatus = status;
    elements.state.dataset.status = status;
    elements.state.textContent = labels[status] || status;
    elements.state.title = serialError || "";
    reportedAge = typeof age === "number" ? age : null;
    ageReceivedAt = performance.now();
    elements.age.textContent = formatAge(reportedAge);
  }

  function dropCount(sample) {
    const fields = [
      "txq_drops",
      "rxq_drops",
      "rx_alloc_failures",
      "rx_read_failures",
      "reorder_overflow",
      "reorder_timeouts",
      "reorder_outdated",
      "reorder_retransmit",
    ];
    return fields.reduce((total, field) => total + (Number(sample[field]) || 0), 0);
  }

  function showAPSample(sample) {
    if (!sample) {
      return;
    }
    elements.total.textContent = formatRate(sample.halow_total_kbps);
    elements.downlink.textContent = formatRate(sample.halow_tx_kbps);
    elements.uplink.textContent = formatRate(sample.halow_rx_kbps);
    elements.clients.textContent = Number(sample.halow_clients) >= 0
      ? String(sample.halow_clients)
      : "N/A";
    elements.activity.textContent = sample.radio_active === 1 ? "Active" : "Quiet";
    elements.activity.dataset.active = sample.radio_active === 1 ? "true" : "false";
    elements.drops.textContent = String(dropCount(sample));
  }

  function showVideoSample(sample) {
    if (!sample) {
      return;
    }
    const available = sample.status === "live";
    elements.lanVideoBitrate.textContent = available
      ? formatRate(sample.lan_kbps)
      : "N/A";
    elements.halowVideoBitrate.textContent = available
      ? formatRate(sample.halow_kbps)
      : "N/A";
    elements.lanVideoViewers.textContent = available ? String(sample.lan_viewers) : "—";
    elements.halowVideoViewers.textContent = available ? String(sample.halow_viewers) : "—";
    elements.videoStatus.textContent = available ? "Live" : "Unavailable";
    elements.videoStatus.dataset.status = sample.status;
    elements.videoStatus.title = sample.error || "";
  }

  function appendBounded(samples, sample) {
    if (!sample) {
      return;
    }
    const last = samples[samples.length - 1];
    if (last && last.timestamp === sample.timestamp) {
      samples[samples.length - 1] = sample;
    } else {
      samples.push(sample);
      if (samples.length > MAX_SAMPLES) {
        samples.splice(0, samples.length - MAX_SAMPLES);
      }
    }
  }

  function appendAPSample(sample) {
    appendBounded(apSamples, sample);
    showAPSample(sample);
    drawAPTrace();
  }

  function appendVideoSample(sample) {
    appendBounded(videoSamples, sample);
    showVideoSample(sample);
    drawVideoTrace();
  }

  function applySnapshot(snapshot) {
    setStatus(snapshot.status, snapshot.sample_age_seconds, snapshot.serial_error);
    showAPSample(snapshot.latest);
    showVideoSample(snapshot.video);
    applyRunSnapshot(snapshot.run || { active: null });
    playerConfig = snapshot.player || { port: 8889, path: "kitties" };
    elements.sourceName.textContent = playerConfig.source_name || "unavailable";
    elements.sourceResolution.textContent = playerConfig.source_resolution || "unavailable";
    elements.outputResolution.textContent = `${playerConfig.output_profile || ""} ${playerConfig.output_resolution || ""} · ${playerConfig.bitrate_kbps || "N/A"} kbps`;
    setLivePlayerEnabled(Boolean(snapshot.run && snapshot.run.active));
  }

  function setLivePlayerEnabled(enabled) {
    const frame = document.getElementById("live-player");
    elements.adminVideoPoster.hidden = enabled;
    frame.hidden = !enabled;
    if (!enabled || !playerConfig) {
      frame.removeAttribute("src");
      return;
    }
    const player = playerConfig;
    const playerURL = new URL(location.href);
    playerURL.hostname = location.hostname;
    playerURL.port = String(player.port);
    playerURL.pathname = `/${encodeURIComponent(player.path)}/`;
    playerURL.search = "?autoplay=true&muted=true";
    playerURL.hash = "";
    if (frame.src !== playerURL.href) {
      frame.src = playerURL.href;
    }
  }

  function prepareCanvas(canvas) {
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    const bounds = canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(bounds.width * ratio));
    const height = Math.max(1, Math.round(bounds.height * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    return {
      context: canvas.getContext("2d"),
      ratio,
      width: canvas.width / ratio,
      height: canvas.height / ratio,
    };
  }

  function drawLanes(canvas, samples, lanes, emptyLabel) {
    const prepared = prepareCanvas(canvas);
    const context = prepared.context;
    if (!context) {
      return;
    }
    const { ratio, width, height } = prepared;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    const left = width < 480 ? 70 : 88;
    const right = 14;
    const top = 13;
    const bottom = 24;
    const gap = 12;
    const plotWidth = Math.max(1, width - left - right);
    const laneHeight = (height - top - bottom - gap * (lanes.length - 1)) / lanes.length;

    context.font = '10px "SFMono-Regular", Menlo, monospace';
    context.textBaseline = "middle";
    lanes.forEach((lane, laneIndex) => {
      const values = samples
        .map((sample) => Number(lane.value(sample)))
        .filter((value) => Number.isFinite(value) && value >= 0);
      const maximum = Math.max(1, ...values);
      const laneTop = top + laneIndex * (laneHeight + gap);
      const laneBottom = laneTop + laneHeight;

      context.fillStyle = laneIndex % 2 === 0 ? "rgba(17, 39, 49, 0.035)" : "rgba(255, 255, 255, 0.35)";
      context.fillRect(left, laneTop, plotWidth, laneHeight);
      context.strokeStyle = "#b7c4c5";
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(left, laneBottom + 0.5);
      context.lineTo(width - right, laneBottom + 0.5);
      context.stroke();

      context.fillStyle = "#586a72";
      context.fillText(lane.label, 5, laneTop + laneHeight / 2);
      context.textAlign = "right";
      context.fillText(`${formatRate(maximum)} kbps`, width - right, laneTop + 10);
      context.textAlign = "left";

      let drawing = false;
      context.beginPath();
      samples.forEach((sample, index) => {
        const value = Number(lane.value(sample));
        if (!Number.isFinite(value) || value < 0) {
          drawing = false;
          return;
        }
        const x = left + (samples.length <= 1 ? plotWidth : (index / (samples.length - 1)) * plotWidth);
        const y = laneBottom - 5 - Math.min(1, value / maximum) * Math.max(1, laneHeight - 15);
        if (!drawing) {
          context.moveTo(x, y);
          drawing = true;
        } else {
          context.lineTo(x, y);
        }
      });
      context.strokeStyle = lane.color;
      context.lineWidth = 2.2;
      context.lineJoin = "round";
      context.lineCap = "round";
      context.stroke();
    });

    context.fillStyle = "#586a72";
    context.textBaseline = "alphabetic";
    context.fillText(samples.length ? `${samples.length}s window` : emptyLabel, left, height - 5);
    if (samples.length) {
      context.textAlign = "right";
      context.fillText("now", width - right, height - 5);
      context.textAlign = "left";
    }
  }

  function drawAPTrace() {
    drawLanes(
      elements.apCanvas,
      apSamples,
      [
        { label: "To phone", color: "#007f73", value: (sample) => sample.halow_tx_kbps },
        { label: "To Mac", color: "#945e17", value: (sample) => sample.halow_rx_kbps },
      ],
      "Waiting for AP counters",
    );
  }

  function drawVideoTrace() {
    drawOverlay(elements.videoCanvas, videoSamples, [
      { label: "LAN", color: "#245f9b", value: (sample) => sample.lan_kbps },
      { label: "HaLow", color: "#007f73", value: (sample) => sample.halow_kbps },
    ], "Waiting for MediaMTX metrics");
  }

  function drawOverlay(canvas, samples, series, emptyLabel) {
    const prepared = prepareCanvas(canvas);
    const context = prepared.context;
    if (!context) return;
    const { ratio, width, height } = prepared;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const left = width < 480 ? 52 : 68;
    const right = 14;
    const top = 13;
    const bottom = 24;
    const plotWidth = Math.max(1, width - left - right);
    const plotHeight = Math.max(1, height - top - bottom);
    const values = [];
    series.forEach((line) => samples.forEach((sample) => {
      const value = Number(line.value(sample));
      if (Number.isFinite(value) && value >= 0) values.push(value);
    }));
    const maximum = Math.max(1, ...values);
    context.fillStyle = "rgba(17, 39, 49, 0.035)";
    context.fillRect(left, top, plotWidth, plotHeight);
    context.strokeStyle = "#b7c4c5";
    context.beginPath();
    context.moveTo(left, top + plotHeight + 0.5);
    context.lineTo(width - right, top + plotHeight + 0.5);
    context.stroke();
    context.font = '10px "SFMono-Regular", Menlo, monospace';
    context.fillStyle = "#586a72";
    context.fillText(`${formatRate(maximum)} kbps`, 4, top + 10);
    series.forEach((line) => {
      let drawing = false;
      context.beginPath();
      samples.forEach((sample, index) => {
        const value = Number(line.value(sample));
        if (!Number.isFinite(value) || value < 0) { drawing = false; return; }
        const x = left + (samples.length <= 1 ? plotWidth : index / (samples.length - 1) * plotWidth);
        const y = top + plotHeight - 5 - Math.min(1, value / maximum) * Math.max(1, plotHeight - 10);
        if (drawing) context.lineTo(x, y); else { context.moveTo(x, y); drawing = true; }
      });
      context.strokeStyle = line.color;
      context.lineWidth = 2.2;
      context.lineJoin = "round";
      context.lineCap = "round";
      context.stroke();
    });
    context.fillStyle = "#586a72";
    context.fillText(samples.length ? `${samples.length}s window` : emptyLabel, left, height - 5);
    if (samples.length) {
      context.textAlign = "right";
      context.fillText("now", width - right, height - 5);
      context.textAlign = "left";
    }
  }

  function formatElapsed(milliseconds) {
    const total = Math.max(0, Math.floor(milliseconds / 1000));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    return hours ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
      : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  function applyRunSnapshot(runSnapshot) {
    const next = runSnapshot && runSnapshot.active ? runSnapshot.active : null;
    if (next && (!activeRun || activeRun.id !== next.id)) {
      apSamples.splice(0);
      videoSamples.splice(0);
      resizeCanvases();
    }
    activeRun = next;
    runElapsedAtUpdate = next ? Number(next.elapsed_ms) || 0 : 0;
    runUpdatedAt = performance.now();
    elements.runPlay.disabled = Boolean(next);
    elements.runStop.disabled = !next;
    document.body.dataset.runState = next ? "recording" : "idle";
    setLivePlayerEnabled(Boolean(next));
    if (!next && !reviewData) elements.runElapsed.textContent = "00:00";
  }

  async function requestJSON(path, options) {
    const response = await fetch(path, Object.assign({ cache: "no-store" }, options || {}));
    if (!response.ok) throw new Error(`${path} returned ${response.status}`);
    return response.json();
  }

  async function startRun() {
    const payload = await requestJSON("/api/runs", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    applyRunSnapshot({ active: payload.run });
  }

  async function stopRun() {
    if (!activeRun) return;
    await requestJSON(`/api/runs/${encodeURIComponent(activeRun.id)}/stop`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    applyRunSnapshot({ active: null });
  }

  function runLabel(run) {
    const when = new Date(run.started_at).toLocaleString();
    const duration = run.duration_ms == null ? run.status : formatElapsed(run.duration_ms);
    return `${when} · ${duration}`;
  }

  async function openRunBrowser() {
    const payload = await requestJSON("/api/runs");
    elements.runList.replaceChildren();
    payload.runs.forEach((run) => {
      const item = document.createElement("li");
      const actions = document.createElement("div");
      actions.className = "run-choice-actions";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "run-choice";
      button.dataset.runId = run.id;
      button.setAttribute("aria-pressed", String(Boolean(reviewData && reviewData.metadata.id === run.id)));
      if (reviewData && reviewData.metadata.id === run.id) button.classList.add("run-choice--selected");
      button.textContent = runLabel(run);
      button.addEventListener("click", () => loadRun(run.id));
      actions.appendChild(button);
      if (run.status !== "recording") {
        const erase = document.createElement("button");
        erase.type = "button";
        erase.className = "control control--danger run-delete";
        erase.textContent = "Delete";
        erase.setAttribute("aria-label", `Delete run ${runLabel(run)}`);
        erase.addEventListener("click", (event) => {
          event.stopPropagation();
          deleteRun(run.id).catch((error) => { erase.title = error.message; });
        });
        actions.appendChild(erase);
      }
      item.appendChild(actions);
      elements.runList.appendChild(item);
    });
    if (!payload.runs.length) {
      const item = document.createElement("li");
      item.textContent = "No completed runs yet.";
      elements.runList.appendChild(item);
    }
    elements.runBrowser.hidden = false;
    document.body.dataset.mode = "review";
  }

  async function clearRuns() {
    if (!window.confirm("Delete all stored runs? This cannot be undone.")) return;
    await requestJSON("/api/runs", { method: "DELETE" });
    reviewData = null;
    reviewPlaying = false;
    elements.reviewPlayer.pause();
    await openRunBrowser();
  }

  async function deleteRun(runId) {
    if (!window.confirm("Delete this stored run? This cannot be undone.")) return;
    await requestJSON(`/api/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
    if (reviewData && reviewData.metadata.id === runId) {
      reviewData = null;
      reviewPlaying = false;
      elements.reviewPlayer.pause();
    }
    await openRunBrowser();
  }

  function closeRunBrowser() {
    reviewPlaying = false;
    reviewData = null;
    elements.reviewPlayer.pause();
    elements.runBrowser.hidden = true;
    document.body.dataset.mode = "live";
    elements.reviewPlayback.textContent = "Play review";
    seedHistory().catch(() => setStatus("offline", null, "Dashboard service unavailable"));
  }

  async function loadRun(runId) {
    reviewData = await requestJSON(`/api/runs/${encodeURIComponent(runId)}`);
    reviewPlaying = false;
    reviewBaseMs = 0;
    const duration = Number(reviewData.metadata.duration_ms) || 0;
    elements.reviewScrubber.max = String(duration);
    elements.reviewScrubber.value = "0";
    updateRunSelection();
    updateReviewPlaybackButton();
    setReviewTime(0);
  }

  function updateRunSelection() {
    elements.runList.querySelectorAll(".run-choice").forEach((button) => {
      const selected = Boolean(reviewData && button.dataset.runId === reviewData.metadata.id);
      button.classList.toggle("run-choice--selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
  }

  function updateReviewPlaybackButton() {
    const duration = reviewData ? Number(reviewData.metadata.duration_ms) || 0 : 0;
    elements.reviewPlayback.textContent = reviewPlaying ? "Pause review"
      : reviewData && reviewBaseMs >= duration ? "Replay review" : "Play review";
  }

  function setReviewTime(milliseconds) {
    if (!reviewData) return;
    const duration = Number(reviewData.metadata.duration_ms) || 0;
    const position = Math.max(0, Math.min(duration, milliseconds));
    elements.reviewScrubber.value = String(position);
    elements.runElapsed.textContent = formatElapsed(position);
    apSamples.splice(0);
    videoSamples.splice(0);
    reviewData.samples.forEach((event) => {
      if (event.elapsed_ms > position) return;
      if (event.kind === "ap") apSamples.push(event.sample);
      if (event.kind === "video") videoSamples.push(event.sample);
    });
    showAPSample(apSamples[apSamples.length - 1]);
    showVideoSample(videoSamples[videoSamples.length - 1]);
    resizeCanvases();
    const video = elements.reviewPlayer;
    if (Number.isFinite(video.duration) && video.duration > 0) {
      const target = (position / 1000) % video.duration;
      if (Math.abs(video.currentTime - target) > 0.35) video.currentTime = target;
    }
  }

  function toggleReviewPlayback() {
    if (!reviewData) return;
    reviewPlaying = !reviewPlaying;
    if (reviewPlaying) {
      if (reviewBaseMs >= Number(reviewData.metadata.duration_ms)) reviewBaseMs = 0;
      reviewStartedAt = performance.now();
      elements.reviewPlayer.play().catch(() => {});
    } else {
      reviewBaseMs += performance.now() - reviewStartedAt;
      elements.reviewPlayer.pause();
    }
    updateReviewPlaybackButton();
  }

  function resizeCanvases() {
    drawAPTrace();
    drawVideoTrace();
  }

  async function seedHistory() {
    const response = await fetch("/api/history", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`history request returned ${response.status}`);
    }
    const snapshot = await response.json();
    applySnapshot(snapshot);
    const activeStartedAt = snapshot.run && snapshot.run.active
      ? Date.parse(snapshot.run.active.started_at)
      : null;
    const withinActiveRun = (sample) => activeStartedAt === null
      || Date.parse(sample.timestamp) >= activeStartedAt;
    apSamples.splice(0, apSamples.length,
      ...(snapshot.samples || []).filter(withinActiveRun).slice(-MAX_SAMPLES));
    videoSamples.splice(0, videoSamples.length,
      ...(snapshot.video_samples || []).filter(withinActiveRun).slice(-MAX_SAMPLES));
    resizeCanvases();
  }

  function followEvents() {
    const events = new EventSource("/events");
    events.addEventListener("status", (event) => {
      applySnapshot(JSON.parse(event.data));
    });
    events.addEventListener("sample", (event) => {
      appendAPSample(JSON.parse(event.data));
      setStatus("live", 0, null);
    });
    events.addEventListener("video", (event) => {
      appendVideoSample(JSON.parse(event.data));
    });
    events.addEventListener("run", (event) => {
      applyRunSnapshot(JSON.parse(event.data));
    });
    events.onerror = () => {
      if (latestStatus !== "serial-error") {
        setStatus("reconnecting", reportedAge, null);
      }
    };
  }

  window.setInterval(() => {
    if (reviewPlaying && reviewData) {
      const position = reviewBaseMs + performance.now() - reviewStartedAt;
      setReviewTime(position);
      if (position >= Number(reviewData.metadata.duration_ms)) {
        reviewPlaying = false;
        reviewBaseMs = Number(reviewData.metadata.duration_ms) || 0;
        elements.reviewPlayer.pause();
        updateReviewPlaybackButton();
      }
    } else if (activeRun && !reviewData) {
      elements.runElapsed.textContent = formatElapsed(
        runElapsedAtUpdate + performance.now() - runUpdatedAt,
      );
    }
    if (reportedAge === null) {
      return;
    }
    const age = reportedAge + (performance.now() - ageReceivedAt) / 1000;
    elements.age.textContent = formatAge(age);
    if (latestStatus === "live" && age > 3) {
      elements.state.dataset.status = "stale";
      elements.state.textContent = "AP telemetry stale";
      latestStatus = "stale";
    }
  }, 500);

  elements.runPlay.addEventListener("click", () => startRun().catch((error) => {
    elements.runPlay.title = error.message;
  }));
  elements.runStop.addEventListener("click", () => stopRun().catch((error) => {
    elements.runStop.title = error.message;
  }));
  elements.reviewRuns.addEventListener("click", () => openRunBrowser().catch((error) => {
    elements.reviewRuns.title = error.message;
  }));
  elements.exitReview.addEventListener("click", closeRunBrowser);
  elements.clearRuns.addEventListener("click", () => clearRuns().catch((error) => {
    elements.clearRuns.title = error.message;
  }));
  elements.reviewPlayback.addEventListener("click", toggleReviewPlayback);
  elements.reviewScrubber.addEventListener("input", () => {
    reviewPlaying = false;
    elements.reviewPlayer.pause();
    updateReviewPlaybackButton();
    reviewBaseMs = Number(elements.reviewScrubber.value);
    setReviewTime(reviewBaseMs);
  });

  if ("ResizeObserver" in window) {
    const observer = new ResizeObserver(resizeCanvases);
    observer.observe(elements.apCanvas);
    observer.observe(elements.videoCanvas);
  } else {
    window.addEventListener("resize", resizeCanvases);
  }

  resizeCanvases();
  seedHistory().catch(() => setStatus("offline", null, "Dashboard service unavailable"));
  followEvents();
})();
