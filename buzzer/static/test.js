(function () {
  "use strict";

  // Hardware bring-up aid: prompts for each team's button in turn and
  // confirms the right pin fired, then loops. Independent of game phase --
  // /ws/test relays every raw edge regardless of what the main game state
  // machine does with it, so this works even while IDLE.

  const connEl = document.getElementById("conn-dot");
  const connLabelEl = connEl.querySelector(".label");
  const promptEl = document.getElementById("prompt");
  const promptTeamEl = document.getElementById("prompt-team");
  const promptDetailEl = document.getElementById("prompt-detail");
  const restartBtn = document.getElementById("restart-btn");
  const checklistEl = document.getElementById("checklist");
  const logEl = document.getElementById("log");

  const TEAM_CLASS = ["team-a", "team-b"];

  let teams = [];
  let current = 0;
  let lastTick = null;
  let advanceTimer = null;

  let socket = null;
  let reconnectDelay = 500;

  // See board.js for why: /ws/test gets a lightweight {"type":"ping"}
  // heartbeat every ~15s (server config HEARTBEAT_INTERVAL_S) so a
  // silently-dead connection (Wi-Fi power-save, an idle NAT/AP timeout)
  // can be detected and force-reconnected here, instead of just sitting
  // stale until a manual refresh.
  let lastMessageAt = Date.now();
  const STALE_MS = 40000;
  setInterval(() => {
    if (socket && socket.readyState === WebSocket.OPEN && Date.now() - lastMessageAt > STALE_MS) {
      console.warn("no message in " + STALE_MS + "ms, forcing reconnect");
      socket.close();
    }
  }, 5000);

  function wsUrl() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + window.location.host + "/ws/test";
  }

  function connect() {
    setConn("connecting");
    lastMessageAt = Date.now();
    socket = new WebSocket(wsUrl());

    socket.onopen = () => {
      reconnectDelay = 500;
      setConn("online");
    };
    socket.onmessage = (event) => {
      lastMessageAt = Date.now();
      try {
        handleMessage(JSON.parse(event.data));
      } catch (err) {
        console.error("bad message", err);
      }
    };
    socket.onclose = () => {
      setConn("offline");
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.6, 5000);
    };
    socket.onerror = () => socket.close();
  }

  function setConn(state) {
    connEl.classList.toggle("offline", state !== "online");
    connLabelEl.textContent =
      state === "online" ? "connected" : state === "offline" ? "reconnecting…" : "connecting…";
  }

  function handleMessage(msg) {
    if (msg.type === "hello") {
      teams = msg.teams;
      current = 0;
      buildChecklist();
      showWaiting();
      return;
    }
    if (msg.type === "buzz") {
      handleBuzz(msg);
    }
  }

  function buildChecklist() {
    checklistEl.innerHTML = "";
    teams.forEach((name, idx) => {
      const row = document.createElement("div");
      row.className = "check-row";
      row.id = "check-" + idx;
      const mark = document.createElement("div");
      mark.className = "mark";
      const label = document.createElement("div");
      label.textContent = name;
      row.appendChild(mark);
      row.appendChild(label);
      checklistEl.appendChild(row);
    });
    updateChecklistHighlight();
  }

  function updateChecklistHighlight() {
    teams.forEach((_, idx) => {
      const row = document.getElementById("check-" + idx);
      if (!row) return;
      row.classList.toggle("current", idx === current);
    });
  }

  function showWaiting() {
    if (!teams.length) return;
    clearTimeout(advanceTimer);
    promptEl.className = current === 0 ? "waiting-a" : "waiting-b";
    promptTeamEl.textContent = teams[current];
    promptDetailEl.textContent = "Waiting for a press…";
    updateChecklistHighlight();
  }

  function showAllDone() {
    promptEl.className = "all-done";
    promptTeamEl.textContent = "All buttons tested!";
    promptDetailEl.textContent = "Starting over…";
    teams.forEach((_, idx) => document.getElementById("check-" + idx)?.classList.remove("current"));
    advanceTimer = setTimeout(() => {
      current = 0;
      teams.forEach((_, idx) => document.getElementById("check-" + idx)?.classList.remove("done"));
      showWaiting();
    }, 1800);
  }

  function handleBuzz(msg) {
    logEvent(msg);

    if (msg.team !== current) {
      return; // wrong button pressed -- logged above, don't advance
    }

    const deltaMs =
      lastTick !== null ? ((msg.tick - lastTick) / 1e6).toFixed(1) + "ms since last edge" : "";
    lastTick = msg.tick;

    document.getElementById("check-" + current)?.classList.add("done");
    promptEl.className = "got-it";
    promptTeamEl.textContent = teams[current] + " ✓";
    promptDetailEl.textContent = "Registered" + (deltaMs ? " — " + deltaMs : "");

    clearTimeout(advanceTimer);
    advanceTimer = setTimeout(() => {
      current += 1;
      if (current >= teams.length) {
        showAllDone();
      } else {
        showWaiting();
      }
    }, 900);
  }

  function logEvent(msg) {
    const row = document.createElement("div");
    row.className = "log-row " + (TEAM_CLASS[msg.team] || "");
    const teamName = teams[msg.team] || "team " + msg.team;
    row.innerHTML =
      teamName +
      " — <span class='result'>" +
      msg.result +
      "</span> (phase " +
      msg.phase +
      ", source " +
      msg.source +
      ")";
    logEl.appendChild(row);
    while (logEl.children.length > 100) {
      logEl.removeChild(logEl.firstChild);
    }
  }

  restartBtn.addEventListener("click", () => {
    clearTimeout(advanceTimer);
    current = 0;
    lastTick = null;
    teams.forEach((_, idx) => document.getElementById("check-" + idx)?.classList.remove("done"));
    showWaiting();
  });
  restartBtn.hidden = false;

  connect();
})();
