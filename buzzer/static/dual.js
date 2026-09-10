(function () {
  "use strict";

  // Self-contained: connects its own /ws, renders both panes directly.
  // No iframes -- lets keyboard shortcuts work everywhere on the page with
  // a single top-level listener, and lets the board pane's IDLE grid be
  // genuinely clickable (a plain read-only /board page can't do that).

  const phaseBadgeEl = document.getElementById("phase-badge");
  const connDotEl = document.getElementById("conn-dot");
  const warningsEl = document.getElementById("warnings");
  const teamsEl = document.getElementById("teams");
  const activeClueEl = document.getElementById("active-clue");
  const roundLabelEl = document.getElementById("round-label");
  const boardTeamsEl = document.getElementById("board-teams");
  const boardStageEl = document.getElementById("board-stage");

  const btnArm = document.getElementById("btn-arm");
  const btnReveal = document.getElementById("btn-reveal");
  const btnCorrect = document.getElementById("btn-correct");
  const btnIncorrect = document.getElementById("btn-incorrect");
  const btnBack = document.getElementById("btn-back");
  const btnNextRound = document.getElementById("btn-next-round");
  const btnStartFj = document.getElementById("btn-start-fj");

  const TEAM_CLASS = ["is-a", "is-b"];

  let reconnectDelay = 500;
  const RECONNECT_MAX_DELAY = 5000;

  function wsUrl() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + window.location.host + "/ws";
  }

  function connect() {
    setConn("connecting");
    const socket = new WebSocket(wsUrl());

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
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.6, RECONNECT_MAX_DELAY);
    };
    socket.onerror = () => socket.close();
  }

  function setConn(state) {
    connDotEl.classList.toggle("offline", state !== "online");
    connDotEl.querySelector(".label").textContent =
      state === "online" ? "connected" : state === "offline" ? "reconnecting…" : "connecting…";
  }

  async function post(path, body) {
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok && res.status !== 409) {
        console.error("request failed", path, res.status);
      }
      return res;
    } catch (err) {
      console.error("request error", path, err);
    }
  }

  // Clicking a tile auto-arms it -- no separate step. A Daily Double skips
  // READING entirely (see Game.select_clue), so only arm() when the clue
  // actually landed in READING.
  async function selectAndArm(category, row) {
    const res = await post("/api/select_clue", { category, row });
    if (!res || !res.ok) return;
    try {
      const snap = await res.json();
      if (snap.phase === "READING") {
        await post("/api/arm");
      }
    } catch (err) {
      console.error("bad select_clue response", err);
    }
  }

  function formatScore(n) {
    const sign = n < 0 ? "-$" : "$";
    return sign + Math.abs(n).toLocaleString("en-US");
  }

  function render(state) {
    renderHostPane(state);
    renderBoardPane(state);
  }

  // ---------------- host pane ----------------

  function renderHostPane(state) {
    phaseBadgeEl.textContent = state.phase;
    phaseBadgeEl.className = "phase-" + state.phase;

    warningsEl.innerHTML = "";
    (state.warnings || []).forEach((w) => {
      const div = document.createElement("div");
      div.className = "warning-banner";
      div.textContent = "⚠ " + w;
      warningsEl.appendChild(div);
    });

    renderTeamCards(state);
    renderActiveClueSummary(state);
    renderRoundBar(state);
    renderActions(state);
  }

  function renderTeamCards(state) {
    teamsEl.innerHTML = "";
    state.teams.forEach((team, idx) => {
      const card = document.createElement("div");
      card.className = "team-card " + (TEAM_CLASS[idx] || "");

      const isWinner =
        (state.phase === "LOCKED" || state.phase === "REVEALED") && state.winner === idx;
      if (isWinner) card.classList.add("is-winner");
      if (team.locked_out) card.classList.add("is-locked-out");

      const nameRow = document.createElement("div");
      nameRow.className = "team-name-row";
      const nameSpan = document.createElement("span");
      nameSpan.textContent = team.name;
      nameRow.appendChild(nameSpan);
      if (team.locked_out) {
        const tag = document.createElement("span");
        tag.className = "team-tag";
        tag.textContent = "locked out";
        nameRow.appendChild(tag);
      }
      card.appendChild(nameRow);

      const score = document.createElement("div");
      score.className = "team-score";
      score.textContent = formatScore(team.score);
      card.appendChild(score);

      // The only way to resolve a Daily Double or Final Jeopardy wager --
      // neither of those touches score automatically, by design (see
      // README "Daily Double & Final Jeopardy"), so this needs to be
      // reachable from here since /dual has no board-select grid host
      // pane to fall back to.
      const adjustBtn = document.createElement("button");
      adjustBtn.className = "pill-btn";
      adjustBtn.textContent = "Adjust";
      adjustBtn.addEventListener("click", () => {
        const raw = window.prompt(`Adjust ${team.name} score by (e.g. -100 or 200):`);
        if (raw === null || raw.trim() === "") return;
        const delta = parseInt(raw, 10);
        if (Number.isNaN(delta)) return;
        post("/api/adjust_score", { team: idx, delta });
      });
      card.appendChild(adjustBtn);

      teamsEl.appendChild(card);
    });
  }

  function renderActiveClueSummary(state) {
    if (state.phase === "FINAL_JEOPARDY") {
      activeClueEl.classList.remove("hidden");
      activeClueEl.innerHTML = "";
      const meta = document.createElement("div");
      meta.className = "clue-meta";
      meta.textContent = "Final Jeopardy — " + (state.final_jeopardy_category || "");
      activeClueEl.appendChild(meta);
      return;
    }

    const clue = state.active_clue;
    if (!clue || state.phase === "IDLE") {
      activeClueEl.classList.add("hidden");
      activeClueEl.innerHTML = "";
      return;
    }
    activeClueEl.classList.remove("hidden");
    const catName = state.board[clue.category] ? state.board[clue.category].category : "";
    activeClueEl.innerHTML = "";
    const meta = document.createElement("div");
    meta.className = "clue-meta";
    meta.textContent = clue.daily_double ? catName + " — Daily Double" : catName + " — $" + clue.value;
    activeClueEl.appendChild(meta);
  }

  function renderRoundBar(state) {
    const roundNum = (state.round_index ?? 0) + 1;
    roundLabelEl.textContent = `Round ${roundNum} of ${state.total_rounds}: ${state.round_name}`;
    const idle = state.phase === "IDLE";
    btnNextRound.disabled = !idle || state.round_index >= state.total_rounds - 1;
    btnStartFj.disabled = !idle;
  }

  function renderActions(state) {
    const phase = state.phase;
    btnArm.disabled = phase !== "READING";
    btnReveal.disabled = !(phase === "READING" || phase === "ARMED" || phase === "LOCKED");
    btnCorrect.disabled = phase !== "LOCKED";
    btnIncorrect.disabled = phase !== "LOCKED";
    btnBack.disabled = !(phase === "REVEALED" || phase === "FINAL_JEOPARDY");
  }

  // ---------------- board pane ----------------

  function renderBoardPane(state) {
    renderBoardTeams(state);
    renderBoardStage(state);
  }

  function renderBoardTeams(state) {
    boardTeamsEl.innerHTML = "";
    state.teams.forEach((team, idx) => {
      const panel = document.createElement("div");
      panel.className = "board-team-panel " + (TEAM_CLASS[idx] || "");

      const isWinner =
        (state.phase === "LOCKED" || state.phase === "REVEALED") && state.winner === idx;
      if (isWinner) panel.classList.add("is-winner");
      if (team.locked_out) panel.classList.add("is-locked-out");

      const name = document.createElement("div");
      name.className = "board-team-name";
      name.textContent = team.name;
      panel.appendChild(name);

      const score = document.createElement("div");
      score.className = "board-team-score";
      score.textContent = formatScore(team.score);
      panel.appendChild(score);

      boardTeamsEl.appendChild(panel);
    });
  }

  function renderBoardStage(state) {
    boardStageEl.innerHTML = "";

    if (state.phase === "IDLE") {
      const label = document.createElement("div");
      label.className = "round-label";
      label.textContent = state.round_name || "";
      boardStageEl.appendChild(label);
      boardStageEl.appendChild(buildClickableGrid(state));
      return;
    }

    if (state.phase === "FINAL_JEOPARDY") {
      boardStageEl.appendChild(buildFinalJeopardyStage(state));
      return;
    }

    boardStageEl.appendChild(buildActiveStage(state));
  }

  function buildClickableGrid(state) {
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
      state.board.forEach((cat, catIdx) => {
        const clue = cat.clues[r];
        const cell = document.createElement("button");
        cell.className = "clue-cell" + (clue.used ? " used" : "");
        cell.textContent = clue.used ? "" : "$" + clue.value;
        cell.disabled = clue.used;
        cell.addEventListener("click", () => selectAndArm(catIdx, r));
        row.appendChild(cell);
      });
      grid.appendChild(row);
    }

    return grid;
  }

  function buildFinalJeopardyStage(state) {
    const wrap = document.createElement("div");
    wrap.className = "clue-stage";
    const tag = document.createElement("div");
    tag.className = "stage-tag";
    tag.textContent = "Final Jeopardy";
    wrap.appendChild(tag);
    const value = document.createElement("div");
    value.className = "stage-value";
    value.textContent = state.final_jeopardy_category || "";
    wrap.appendChild(value);
    return wrap;
  }

  function buildActiveStage(state) {
    const wrap = document.createElement("div");
    wrap.className = "clue-stage";
    const clue = state.active_clue;
    const catName = clue && state.board[clue.category] ? state.board[clue.category].category : "";

    const tag = document.createElement("div");
    tag.className = "stage-tag";
    tag.textContent = catName;
    wrap.appendChild(tag);

    const value = document.createElement("div");
    if (clue && clue.daily_double) {
      value.className = "stage-value daily-double";
      value.textContent = "Daily Double";
    } else {
      value.className = "stage-value";
      value.textContent = clue ? "$" + clue.value : "";
    }
    wrap.appendChild(value);

    const hasWinner = state.winner !== null && state.winner !== undefined;
    if ((state.phase === "LOCKED" || state.phase === "REVEALED") && hasWinner) {
      const banner = document.createElement("div");
      const team = state.teams[state.winner];
      banner.className = "winner-banner " + (state.winner === 0 ? "team-a" : "team-b");
      banner.textContent = (team ? team.name : "Team") + (state.phase === "LOCKED" ? " — answer now!" : " got it!");
      wrap.appendChild(banner);
    }

    return wrap;
  }

  // ---------------- actions ----------------

  btnArm.addEventListener("click", () => post("/api/arm"));
  btnReveal.addEventListener("click", () => post("/api/reveal"));
  btnCorrect.addEventListener("click", () => post("/api/mark_correct"));
  btnIncorrect.addEventListener("click", () => post("/api/mark_incorrect"));
  btnBack.addEventListener("click", () => post("/api/return_to_board"));
  btnNextRound.addEventListener("click", () => post("/api/next_round"));
  btnStartFj.addEventListener("click", () => post("/api/start_final_jeopardy"));

  document.addEventListener("keydown", (e) => {
    if (e.repeat) return;
    switch (e.key) {
      case " ":
        e.preventDefault();
        if (!btnArm.disabled) post("/api/arm");
        break;
      case "1":
        post("/api/manual_buzz/0");
        break;
      case "2":
        post("/api/manual_buzz/1");
        break;
      case "y":
      case "Y":
        if (!btnCorrect.disabled) post("/api/mark_correct");
        break;
      case "n":
      case "N":
        if (!btnIncorrect.disabled) post("/api/mark_incorrect");
        break;
      case "r":
      case "R":
        if (!btnReveal.disabled) post("/api/reveal");
        break;
      case "Escape":
        if (!btnBack.disabled) post("/api/return_to_board");
        break;
    }
  });

  connect();
})();
