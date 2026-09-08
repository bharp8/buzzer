"""State machine tests. No hardware, no FastAPI -- game.py only."""
import pytest

from buzzer.game import Category, Clue, Game, IllegalTransitionError, Phase, Team, build_categories

MS = 1_000_000  # nanoseconds per millisecond


def make_game(**kwargs):
    teams = [Team("Team A"), Team("Team B")]
    categories = [
        Category(
            name=f"Cat {i}",
            clues=[
                Clue(value=(r + 1) * 200, text=f"clue {i}-{r}", answer=f"answer {i}-{r}")
                for r in range(5)
            ],
        )
        for i in range(5)
    ]
    return Game(teams, categories, **kwargs)


def armed_game(**kwargs):
    g = make_game(**kwargs)
    g.select_clue(0, 0, tick_ns=0)
    g.arm(tick_ns=1 * MS)
    return g


# Test 5 first: this is the one that actually proves the fairness property.
def test_earlier_tick_wins_regardless_of_callback_order():
    g = armed_game()

    # Team B's callback is dispatched first but carries the LATER tick.
    g.buzz(1, tick_ns=101 * MS)
    assert g.phase is Phase.LOCKED
    assert g.winner == 1

    # Team A's callback is dispatched second but carries the EARLIER tick.
    g.buzz(0, tick_ns=100 * MS)
    assert g.winner == 0
    assert g.winner_tick == 100 * MS


def test_first_buzz_in_armed_latches_second_ignored():
    g = armed_game()
    g.buzz(0, tick_ns=10 * MS)
    assert g.phase is Phase.LOCKED
    assert g.winner == 0

    # A second, later-tick buzz from the other team must not change anything.
    g.buzz(1, tick_ns=11 * MS)
    assert g.winner == 0
    assert g.phase is Phase.LOCKED


def test_buzz_does_nothing_in_idle():
    g = make_game()
    g.buzz(0, tick_ns=1 * MS)
    assert g.phase is Phase.IDLE
    assert g.winner is None


def test_buzz_does_nothing_in_locked_without_earlier_tick():
    g = armed_game()
    g.buzz(0, tick_ns=10 * MS)
    assert g.phase is Phase.LOCKED

    # Later tick from the other team in LOCKED -- no effect.
    g.buzz(1, tick_ns=50 * MS)
    assert g.winner == 0
    assert g.phase is Phase.LOCKED


def test_buzz_does_nothing_in_revealed():
    g = armed_game()
    g.buzz(0, tick_ns=10 * MS)
    g.mark_correct(tick_ns=20 * MS)
    assert g.phase is Phase.REVEALED

    g.buzz(1, tick_ns=30 * MS)
    assert g.phase is Phase.REVEALED
    assert g.winner == 0


def test_false_start_locks_team_out_for_250ms_from_arm():
    g = make_game()
    g.select_clue(0, 0, tick_ns=0)
    g.buzz(0, tick_ns=5 * MS)  # early buzz during READING
    assert g.phase is Phase.READING  # does not latch
    assert g.winner is None

    g.arm(tick_ns=10 * MS)
    assert g.phase is Phase.ARMED

    # Still within the 250ms penalty window measured from arm.
    g.buzz(0, tick_ns=10 * MS + 100 * MS)
    assert g.phase is Phase.ARMED
    assert g.winner is None


def test_false_start_team_can_latch_after_window_expires():
    g = make_game()
    g.select_clue(0, 0, tick_ns=0)
    g.buzz(0, tick_ns=5 * MS)
    g.arm(tick_ns=10 * MS)

    still_locked = 10 * MS + 100 * MS
    g.buzz(0, tick_ns=still_locked)
    assert g.phase is Phase.ARMED
    assert g.winner is None

    # Past both the false-start window and the unrelated debounce window.
    after_window = 10 * MS + 250 * MS + 100 * MS
    g.buzz(0, tick_ns=after_window)
    assert g.phase is Phase.LOCKED
    assert g.winner == 0


def test_debounce_two_edges_on_one_pin_count_once():
    g = make_game()
    g.select_clue(0, 0, tick_ns=0)
    g.arm(tick_ns=1 * MS)

    g.buzz(0, tick_ns=10 * MS)
    assert g.phase is Phase.LOCKED
    assert len(g.buzz_log) == 1

    # Contact bounce 5ms later on the same pin -- default debounce is 20ms,
    # so this must be fully suppressed, not just ignored-by-phase.
    g.buzz(0, tick_ns=15 * MS)
    assert len(g.buzz_log) == 1


def test_mark_incorrect_deducts_rearms_and_excludes_team():
    g = armed_game()
    g.buzz(0, tick_ns=10 * MS)
    g.mark_incorrect(tick_ns=20 * MS)

    assert g.phase is Phase.ARMED
    assert g.snapshot(now_tick=21 * MS)["teams"][0]["score"] == -200

    # The excluded team cannot buzz again on this clue.
    g.buzz(0, tick_ns=30 * MS)
    assert g.phase is Phase.ARMED
    assert g.winner is None

    # The other team still can.
    g.buzz(1, tick_ns=40 * MS)
    assert g.phase is Phase.LOCKED
    assert g.winner == 1


def test_all_teams_wrong_reveals():
    g = armed_game()
    g.buzz(0, tick_ns=10 * MS)
    g.mark_incorrect(tick_ns=20 * MS)
    assert g.phase is Phase.ARMED

    g.buzz(1, tick_ns=30 * MS)
    g.mark_incorrect(tick_ns=40 * MS)
    assert g.phase is Phase.REVEALED
    assert g.reveal_text is not None


def test_illegal_transitions_rejected_without_mutating_state():
    g = make_game()
    with pytest.raises(IllegalTransitionError):
        g.arm(tick_ns=1 * MS)  # can't arm from IDLE
    assert g.phase is Phase.IDLE

    with pytest.raises(IllegalTransitionError):
        g.mark_correct(tick_ns=1 * MS)
    assert g.phase is Phase.IDLE

    g2 = armed_game()
    with pytest.raises(IllegalTransitionError):
        g2.select_clue(0, 0, tick_ns=1 * MS)  # wrong phase, and already used
    assert g2.phase is Phase.ARMED
    assert g2._categories[0].clues[0].used is True

    with pytest.raises(IllegalTransitionError):
        g2.select_clue(99, 0, tick_ns=1 * MS)  # out of range
    assert g2.phase is Phase.ARMED


def test_snapshot_never_leaks_answer_outside_revealed():
    g = armed_game()
    snap = g.snapshot(now_tick=5 * MS)
    assert snap["reveal"] is None
    assert "answer" not in snap["active_clue"]

    g.buzz(0, tick_ns=10 * MS)
    snap_locked = g.snapshot(now_tick=15 * MS)
    assert snap_locked["reveal"] is None

    g.mark_correct(tick_ns=20 * MS)
    snap_revealed = g.snapshot(now_tick=25 * MS)
    assert snap_revealed["phase"] == "REVEALED"
    assert snap_revealed["reveal"] is not None


# --- a bit of extra coverage for the actions not in the ten required cases ---

def test_mark_correct_awards_points_and_reveals():
    g = armed_game()
    g.buzz(1, tick_ns=10 * MS)
    g.mark_correct(tick_ns=20 * MS)
    snap = g.snapshot(now_tick=21 * MS)
    assert snap["teams"][1]["score"] == 200
    assert snap["phase"] == "REVEALED"


def test_return_to_board_resets_to_idle():
    g = armed_game()
    g.buzz(0, tick_ns=10 * MS)
    g.mark_correct(tick_ns=20 * MS)
    g.return_to_board(tick_ns=30 * MS)
    assert g.phase is Phase.IDLE
    assert g.active_clue is None


def test_reveal_action_skips_adjudication():
    g = armed_game()
    g.reveal(tick_ns=10 * MS)
    assert g.phase is Phase.REVEALED
    assert g.reveal_text is not None


def test_adjust_score_any_phase():
    g = make_game()
    g.adjust_score(0, -100)
    assert g.snapshot(now_tick=0)["teams"][0]["score"] == -100


def test_build_categories_rejects_malformed_content():
    with pytest.raises(ValueError):
        build_categories({"teams": ["A"], "categories": []})
    with pytest.raises(ValueError):
        build_categories({"teams": ["A", "B"], "categories": []})
