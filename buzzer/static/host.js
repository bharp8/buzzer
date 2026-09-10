(function () {
  "use strict";

  const teamsEl = document.getElementById("teams");
  const warningsEl = document.getElementById("warnings");
  const activeClueEl = document.getElementById("active-clue");
  const phaseBadgeEl = document.getElementById("phase-badge");
  const connDotEl = document.getElementById("conn-dot");
  const boardGridEl = document.getElementById("board-grid");
  const phoneBuzzerLinksEl = document.getElementById("phone-buzzer-links");
  const roundLabelEl = document.getElementById("round-label");

  const btnArm = document.getElementById("btn-arm");
  const btnReveal = document.getElementById("btn-reveal");
  const btnCorrect = document.getElementById("btn-correct");
  const btnIncorrect = document.getElementById("btn-incorrect");
  const btnBack = document.getElementById("btn-back");
  const btnNextRound = document.getElementById("btn-next-round");
  const btnStartFj = document.getElementById("btn-start-fj");
  const resetBtn = document.getElementById("reset-btn");

  let socket = null;
  let reconnectDelay = 500;
  const RECONNECT_MAX_DELAY = 5000;
  let latest = null;
  let phoneLinksBuilt = false;

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
        latest = JSON.parse(event.data);
        render(latest);
      } catch (err) {
        console.error("bad snapshot", err);
      }
    };
    socket.onclose = () => {
      setConn("offline");
      scheduleReconnect();
    };
    socket.onerror = () => {
      socket.close();
    };
  }

  function scheduleReconnect() {
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.6, RECONNECT_MAX_DELAY);
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

  // Selecting a tile auto-arms it immediately -- no separate host step.
  // A Daily Double skips READING entirely (see Game.select_clue), so only
  // follow up with arm() when the clue actually landed in READING.
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

  const TEAM_CLASS = ["is-a", "is-b"];

  function render(state) {
    phaseBadgeEl.textContent = state.phase;
    phaseBadgeEl.className = "phase-" + state.phase;

    warningsEl.innerHTML = "";
    (state.warnings || []).forEach((w) => {
      const div = document.createElement("div");
      div.className = "warning-banner";
      div.textContent = "⚠ " + w;
      warningsEl.appendChild(div);
    });

    renderTeams(state);
    renderActiveClue(state);
    renderActions(state);
    renderRoundBar(state);
    renderBoardGrid(state);
    renderPhoneBuzzerLinks(state);
  }

  function renderTeams(state) {
    teamsEl.innerHTML = "";
    const canBuzz = state.phase === "READING" || state.phase === "ARMED";

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

      const buttons = document.createElement("div");
      buttons.className = "team-buttons";

      const buzzBtn = document.createElement("button");
      buzzBtn.className = "pill-btn buzz";
      buzzBtn.textContent = "Buzz " + (idx + 1);
      buzzBtn.disabled = !canBuzz;
      buzzBtn.addEventListener("click", () => post(`/api/manual_buzz/${idx}`));
      buttons.appendChild(buzzBtn);

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
      buttons.appendChild(adjustBtn);

      card.appendChild(buttons);
      teamsEl.appendChild(card);
    });
  }

  function renderActiveClue(state) {
    if (state.phase === "FINAL_JEOPARDY") {
      activeClueEl.classList.remove("hidden");
      activeClueEl.innerHTML = "";
      const meta = document.createElement("div");
      meta.className = "clue-meta";
      meta.textContent = "Final Jeopardy";
      activeClueEl.appendChild(meta);
      const text = document.createElement("div");
      text.textContent = state.final_jeopardy_category || "";
      activeClueEl.appendChild(text);
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

  function renderActions(state) {
    const phase = state.phase;
    btnArm.disabled = phase !== "READING";
    btnReveal.disabled = !(phase === "READING" || phase === "ARMED" || phase === "LOCKED");
    btnCorrect.disabled = phase !== "LOCKED";
    btnIncorrect.disabled = phase !== "LOCKED";
    btnBack.disabled = !(phase === "REVEALED" || phase === "FINAL_JEOPARDY");
  }

  function renderRoundBar(state) {
    const roundNum = (state.round_index ?? 0) + 1;
    roundLabelEl.textContent = `Round ${roundNum} of ${state.total_rounds}: ${state.round_name}`;
    const idle = state.phase === "IDLE";
    btnNextRound.disabled = !idle || state.round_index >= state.total_rounds - 1;
    btnStartFj.disabled = !idle;
  }

  function renderBoardGrid(state) {
    boardGridEl.innerHTML = "";
    if (state.phase === "FINAL_JEOPARDY") return;
    const selectable = state.phase === "IDLE" || state.phase === "REVEALED";

    state.board.forEach((cat, catIdx) => {
      const row = document.createElement("div");
      row.className = "board-cat-row";

      const name = document.createElement("div");
      name.className = "board-cat-name";
      name.textContent = cat.category;
      row.appendChild(name);

      cat.clues.forEach((clue, rowIdx) => {
        const btn = document.createElement("button");
        btn.className = "board-clue-btn" + (clue.daily_double ? " is-daily-double" : "");
        btn.textContent = clue.used ? "" : "$" + clue.value;
        btn.disabled = !selectable || clue.used;
        btn.addEventListener("click", () => selectAndArm(catIdx, rowIdx));
        row.appendChild(btn);
      });

      boardGridEl.appendChild(row);
    });
  }

  function renderPhoneBuzzerLinks(state) {
    if (phoneLinksBuilt) return;
    phoneLinksBuilt = true;
    phoneBuzzerLinksEl.innerHTML = "";
    state.teams.forEach((team, idx) => {
      const url = `${window.location.origin}/buzz/${idx}`;
      const row = document.createElement("a");
      row.className = "phone-buzzer-link " + (TEAM_CLASS[idx] || "");
      row.href = `/buzz/${idx}`;
      row.target = "_blank";
      row.rel = "noopener";
      row.textContent = `${team.name}: ${url}`;
      phoneBuzzerLinksEl.appendChild(row);
    });
  }

  btnArm.addEventListener("click", () => post("/api/arm"));
  btnReveal.addEventListener("click", () => post("/api/reveal"));
  btnCorrect.addEventListener("click", () => post("/api/mark_correct"));
  btnIncorrect.addEventListener("click", () => post("/api/mark_incorrect"));
  btnBack.addEventListener("click", () => post("/api/return_to_board"));
  btnNextRound.addEventListener("click", () => post("/api/next_round"));
  btnStartFj.addEventListener("click", () => post("/api/start_final_jeopardy"));
  resetBtn.addEventListener("click", () => {
    if (window.confirm("Reset the entire game? Scores and board progress will be lost.")) {
      post("/api/reset_game");
    }
  });

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
