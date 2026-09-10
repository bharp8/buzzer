(function () {
  "use strict";

  const teamsEl = document.getElementById("teams");
  const stageEl = document.getElementById("stage");
  const warningsEl = document.getElementById("warnings");
  const connEl = document.getElementById("conn-status");
  const connLabelEl = connEl.querySelector(".label");

  let socket = null;
  let reconnectDelay = 500;
  const RECONNECT_MAX_DELAY = 5000;
  let connWasDown = false;

  function wsUrl() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + window.location.host + "/ws";
  }

  function setConnStatus(state) {
    // state: "connecting" | "online" | "offline"
    connEl.classList.remove("offline", "visible");
    if (state === "online") {
      if (connWasDown) {
        // briefly show "reconnected" then fade
        connLabelEl.textContent = "reconnected";
        connEl.classList.add("visible");
        setTimeout(() => connEl.classList.remove("visible"), 1500);
      }
      connWasDown = false;
      return;
    }
    connWasDown = true;
    connEl.classList.add("visible");
    if (state === "offline") {
      connEl.classList.add("offline");
      connLabelEl.textContent = "connection lost — retrying…";
    } else {
      connLabelEl.textContent = "connecting…";
    }
  }

  function connect() {
    setConnStatus("connecting");
    socket = new WebSocket(wsUrl());

    socket.onopen = () => {
      reconnectDelay = 500;
      setConnStatus("online");
    };

    socket.onmessage = (event) => {
      try {
        const state = JSON.parse(event.data);
        render(state);
      } catch (err) {
        console.error("bad snapshot", err);
      }
    };

    socket.onclose = () => {
      setConnStatus("offline");
      scheduleReconnect();
    };

    socket.onerror = () => {
      socket.close();
    };
  }

  function scheduleReconnect() {
    setTimeout(() => {
      connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.6, RECONNECT_MAX_DELAY);
  }

  function render(state) {
    renderWarnings(state);
    renderTeams(state);
    renderStage(state);
  }

  function renderWarnings(state) {
    warningsEl.innerHTML = "";
    (state.warnings || []).forEach((w) => {
      const div = document.createElement("div");
      div.className = "warning-banner";
      div.textContent = "⚠ " + w;
      warningsEl.appendChild(div);
    });
  }

  const TEAM_CLASS = ["is-a", "is-b"];

  function renderTeams(state) {
    teamsEl.innerHTML = "";
    state.teams.forEach((team, idx) => {
      const panel = document.createElement("div");
      panel.className = "team-panel " + (TEAM_CLASS[idx] || "");

      const isWinner =
        (state.phase === "LOCKED" || state.phase === "REVEALED") && state.winner === idx;
      if (isWinner) panel.classList.add("is-winner");
      if (team.locked_out) panel.classList.add("is-locked-out");

      const name = document.createElement("div");
      name.className = "team-name";
      name.textContent = team.name;

      const right = document.createElement("div");
      right.style.display = "flex";
      right.style.alignItems = "center";

      const lockedTag = document.createElement("span");
      lockedTag.className = "team-tag locked";
      lockedTag.textContent = "locked out";
      right.appendChild(lockedTag);

      const score = document.createElement("div");
      score.className = "team-score";
      score.textContent = formatScore(team.score);
      right.appendChild(score);

      panel.appendChild(name);
      panel.appendChild(right);
      teamsEl.appendChild(panel);
    });
  }

  function formatScore(n) {
    const sign = n < 0 ? "-$" : "$";
    return sign + Math.abs(n).toLocaleString("en-US");
  }

  function renderStage(state) {
    stageEl.innerHTML = "";

    if (state.phase === "IDLE") {
      stageEl.appendChild(buildBoardGrid(state));
      return;
    }

    // READING, ARMED, LOCKED, REVEALED all show the same category + value
    // display -- no clue or answer text, the host reads those from paper.
    stageEl.appendChild(buildActiveStage(state));
  }

  function buildBoardGrid(state) {
    const grid = document.createElement("div");
    grid.className = "board-grid";

    const headerRow = document.createElement("div");
    headerRow.className = "board-row";
    headerRow.style.gridTemplateColumns = `repeat(${state.board.length}, 1fr)`;
    state.board.forEach((cat) => {
      const h = document.createElement("div");
      h.className = "category-header";
      h.textContent = cat.category;
      headerRow.appendChild(h);
    });
    grid.appendChild(headerRow);

    const rowCount = state.board.length ? state.board[0].clues.length : 0;
    for (let r = 0; r < rowCount; r++) {
      const row = document.createElement("div");
      row.className = "board-row";
      row.style.gridTemplateColumns = `repeat(${state.board.length}, 1fr)`;
      state.board.forEach((cat) => {
        const clue = cat.clues[r];
        const cell = document.createElement("div");
        cell.className = "clue-cell" + (clue.used ? " used" : "");
        cell.textContent = clue.used ? "" : "$" + clue.value;
        row.appendChild(cell);
      });
      grid.appendChild(row);
    }

    return grid;
  }

  function buildActiveStage(state) {
    const wrap = document.createElement("div");
    wrap.className = "clue-stage";
    const clue = state.active_clue;

    const tag = document.createElement("div");
    tag.className = "stage-tag";
    tag.textContent = clue && state.board[clue.category] ? state.board[clue.category].category : "";
    wrap.appendChild(tag);

    const value = document.createElement("div");
    value.className = "stage-text stage-value";
    value.textContent = clue ? "$" + clue.value : "";
    wrap.appendChild(value);

    const hasWinner = state.winner !== null && state.winner !== undefined;
    if ((state.phase === "LOCKED" || state.phase === "REVEALED") && hasWinner) {
      const banner = document.createElement("div");
      const team = state.teams[state.winner];
      banner.className = "winner-banner " + (state.winner === 0 ? "team-a" : "team-b");
      // LOCKED can be reached either by an actual buzz or by an automatic
      // hand-off after the other team answered wrong (see
      // Game.mark_incorrect), so this can't claim they buzzed in.
      banner.textContent = (team ? team.name : "Team") + (state.phase === "LOCKED" ? " — answer now!" : " got it!");
      wrap.appendChild(banner);
    }

    return wrap;
  }

  connect();
})();
