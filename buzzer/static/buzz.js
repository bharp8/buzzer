(function () {
  "use strict";

  // Per-team fallback buzzer. Only use when the real hardware buttons
  // aren't working: this button's timestamp is captured on server receipt
  // over Wi-Fi, not a kernel edge, so it can't offer the same fairness
  // guarantee as the real buzzers (see PI_TODO.md).

  const parts = window.location.pathname.split("/").filter(Boolean);
  const team = parseInt(parts[parts.length - 1], 10);

  const connEl = document.getElementById("conn-dot");
  const connLabelEl = connEl.querySelector(".label");
  const teamNameEl = document.getElementById("team-name");
  const buzzBtn = document.getElementById("buzz-btn");
  const statusEl = document.getElementById("status-text");

  if (!Number.isInteger(team)) {
    statusEl.textContent = "Invalid buzzer link.";
    return;
  }

  document.body.classList.add(team === 0 ? "team-a" : "team-b");

  let socket = null;
  let reconnectDelay = 500;
  const RECONNECT_MAX_DELAY = 5000;
  let cooldownUntil = 0;
  let online = false;

  function wsUrl() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + window.location.host + "/ws";
  }

  function connect() {
    setConn("connecting");
    socket = new WebSocket(wsUrl());

    socket.onopen = () => {
      reconnectDelay = 500;
      setConn("online");
    };
    socket.onmessage = (event) => {
      try {
        render(JSON.parse(event.data));
      } catch (err) {
        console.error("bad snapshot", err);
      }
    };
    socket.onclose = () => {
      setConn("offline");
      scheduleReconnect();
    };
    socket.onerror = () => socket.close();
  }

  function scheduleReconnect() {
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.6, RECONNECT_MAX_DELAY);
  }

  function setConn(state) {
    online = state === "online";
    connEl.classList.toggle("offline", !online);
    connLabelEl.textContent =
      state === "online" ? "connected" : state === "offline" ? "reconnecting…" : "connecting…";
    updateButtonEnabled();
  }

  function updateButtonEnabled() {
    buzzBtn.disabled = !online || Date.now() < cooldownUntil;
  }

  function render(state) {
    const myTeam = state.teams[team];
    if (!myTeam) return;

    teamNameEl.textContent = myTeam.name;

    const isWinner =
      (state.phase === "LOCKED" || state.phase === "REVEALED") && state.winner === team;
    const otherWon =
      (state.phase === "LOCKED" || state.phase === "REVEALED") &&
      state.winner !== null &&
      state.winner !== undefined &&
      state.winner !== team;

    buzzBtn.classList.toggle("is-winner", isWinner);
    buzzBtn.classList.toggle("is-locked-out", !!myTeam.locked_out);

    if (myTeam.locked_out) {
      statusEl.textContent = "Locked out — you buzzed too early. Wait for it to clear.";
    } else if (isWinner) {
      statusEl.textContent = "You buzzed in! Waiting for the host.";
    } else if (otherWon) {
      const other = state.teams[state.winner];
      statusEl.textContent = (other ? other.name : "The other team") + " buzzed in first.";
    } else if (state.phase === "READING") {
      statusEl.textContent = "Wait for it — buzzing now counts as a false start.";
    } else if (state.phase === "ARMED") {
      statusEl.textContent = "Buzz now!";
    } else if (state.phase === "IDLE") {
      statusEl.textContent = "Waiting for the host to pick a clue.";
    } else {
      statusEl.textContent = "";
    }

    updateButtonEnabled();
  }

  async function sendBuzz() {
    if (Date.now() < cooldownUntil) return;
    cooldownUntil = Date.now() + 400;
    updateButtonEnabled();
    buzzBtn.classList.add("tapped");
    setTimeout(() => buzzBtn.classList.remove("tapped"), 150);
    setTimeout(updateButtonEnabled, 400);
    try {
      await fetch(`/api/manual_buzz/${team}?source=phone`, { method: "POST" });
    } catch (err) {
      console.error("buzz failed", err);
    }
  }

  buzzBtn.addEventListener("click", sendBuzz);
  buzzBtn.addEventListener(
    "touchstart",
    (e) => {
      e.preventDefault();
      sendBuzz();
    },
    { passive: false }
  );

  connect();
})();
