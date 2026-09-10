(function () {
  "use strict";

  // Keyboard shortcuts need to work no matter which pane has focus. The
  // host iframe already has its own handler (host.js) that works whenever
  // focus is inside it; this one covers everything else -- the outer page
  // itself, and the board iframe, neither of which has a listener of its
  // own. Harmless if both ever fired for the same keypress (can't happen,
  // focus can only be in one place), and the server's phase checks make
  // any stray duplicate a no-op 409 rather than a corrupted state.

  async function post(path) {
    try {
      await fetch(path, { method: "POST" });
    } catch (err) {
      console.error("request failed", path, err);
    }
  }

  document.addEventListener("keydown", (e) => {
    if (e.repeat) return;
    switch (e.key) {
      case " ":
        e.preventDefault();
        post("/api/arm");
        break;
      case "1":
        post("/api/manual_buzz/0");
        break;
      case "2":
        post("/api/manual_buzz/1");
        break;
      case "y":
      case "Y":
        post("/api/mark_correct");
        break;
      case "n":
      case "N":
        post("/api/mark_incorrect");
        break;
      case "r":
      case "R":
        post("/api/reveal");
        break;
      case "Escape":
        post("/api/return_to_board");
        break;
    }
  });
})();
