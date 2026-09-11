"""State machine tests. No hardware, no FastAPI -- game.py only."""
import pytest

from buzzer.game import Category, Clue, Game, IllegalTransitionError, Phase, Round, Team, build_content

MS = 1_000_000  # nanoseconds per millisecond


def make_round(prefix="Cat", n=5, daily_doubles=()):
    return Round(
        name="Jeopardy",
        categories=[
            Category(
                name=f"{prefix} {i}",
                clues=[
                    Clue(value=(r + 1) * 200, daily_double=(i, r) in daily_doubles) for r in range(5)
                ],
            )
            for i in range(n)
        ],
    )


def make_game(teams=None, rounds=None, final_jeopardy_category="Everything", **kwargs):
    teams = teams or [Team("Red"), Team("Blue")]
    rounds = rounds or [make_round()]
    return Game(teams, rounds, final_jeopardy_category, **kwargs)


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


def test_mark_incorrect_no_penalty_auto_locks_other_team():
    g = armed_game()
    g.buzz(0, tick_ns=10 * MS)
    g.mark_incorrect(tick_ns=20 * MS)

    # No score penalty for a wrong answer.
    assert g.snapshot(now_tick=21 * MS)["teams"][0]["score"] == 0

    # With only one team left (the default two-team game), they're locked
    # in directly -- no re-buzz needed, there's no one to race against.
    assert g.phase is Phase.LOCKED
    assert g.winner == 1

    # The excluded team can't retake control even if they buzz again.
    g.buzz(0, tick_ns=30 * MS)
    assert g.phase is Phase.LOCKED
    assert g.winner == 1


def test_all_teams_wrong_reveals():
    g = armed_game()
    g.buzz(0, tick_ns=10 * MS)
    g.mark_incorrect(tick_ns=20 * MS)
    assert g.phase is Phase.LOCKED
    assert g.winner == 1  # auto-locked, no re-buzz needed

    g.mark_incorrect(tick_ns=30 * MS)
    assert g.phase is Phase.REVEALED


def test_mark_incorrect_rearms_when_multiple_teams_still_eligible():
    # With more than two teams, auto-locking only kicks in once exactly one
    # is left -- with two or more still eligible there's no single team to
    # pick, so they have to buzz for it.
    g = make_game(teams=[Team("Red"), Team("Blue"), Team("Green")])
    g.select_clue(0, 0, tick_ns=0)
    g.arm(tick_ns=1 * MS)

    g.buzz(0, tick_ns=10 * MS)
    g.mark_incorrect(tick_ns=20 * MS)
    assert g.phase is Phase.ARMED  # two teams (1, 2) still eligible
    assert g.winner is None

    g.buzz(1, tick_ns=30 * MS)
    g.mark_incorrect(tick_ns=40 * MS)
    assert g.phase is Phase.LOCKED  # only team 2 left -- auto-locked
    assert g.winner == 2


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


def test_snapshot_never_contains_clue_or_answer_text():
    # There is no clue/answer text anywhere in this system -- the host reads
    # both from paper. Confirm the snapshot genuinely can't leak any.
    g = armed_game()
    snap = g.snapshot(now_tick=5 * MS)
    assert "reveal" not in snap
    assert "text" not in snap["active_clue"]
    assert "answer" not in snap["active_clue"]


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


def test_return_to_board_abandons_a_clue_with_no_scoring():
    # The universal "done with this clue" action: works directly from
    # READING/ARMED/LOCKED, not just after adjudicating, with no score
    # change either way -- nobody buzzed (or the host is bailing for any
    # other reason), so nothing happened.
    for setup in (
        lambda g: None,  # READING: right after select_clue, nothing else
        lambda g: g.arm(tick_ns=1 * MS),  # ARMED
        lambda g: (g.arm(tick_ns=1 * MS), g.buzz(0, tick_ns=2 * MS)),  # LOCKED
    ):
        g = make_game()
        g.select_clue(0, 0, tick_ns=0)
        setup(g)
        g.return_to_board(tick_ns=100 * MS)
        assert g.phase is Phase.IDLE
        assert g.active_clue is None
        assert g.snapshot(now_tick=100 * MS)["teams"][0]["score"] == 0
        assert g.snapshot(now_tick=100 * MS)["teams"][1]["score"] == 0


def test_return_to_board_illegal_from_idle():
    g = make_game()
    with pytest.raises(IllegalTransitionError):
        g.return_to_board(tick_ns=0)


def test_adjust_score_any_phase():
    g = make_game()
    g.adjust_score(0, -100)
    assert g.snapshot(now_tick=0)["teams"][0]["score"] == -100


def test_build_content_rejects_malformed_content():
    with pytest.raises(ValueError):
        build_content({"teams": ["A"], "rounds": []})  # only 1 team
    with pytest.raises(ValueError):
        build_content({"teams": ["A", "B"], "rounds": []})  # no rounds
    with pytest.raises(ValueError):
        # rounds present but no final_jeopardy
        build_content(
            {
                "teams": ["A", "B"],
                "rounds": [
                    {
                        "name": "Jeopardy",
                        "categories": [
                            {"name": f"Cat {i}", "clues": [{"value": (r + 1) * 200} for r in range(5)]}
                            for i in range(5)
                        ],
                    }
                ],
            }
        )


# ---- Daily Double ----


def test_daily_double_skips_buzzer_race_straight_to_revealed():
    g = make_game(rounds=[make_round(daily_doubles={(0, 0)})])
    g.select_clue(0, 0, tick_ns=0)

    # No READING/ARMED window at all -- straight to REVEALED.
    assert g.phase is Phase.REVEALED
    assert g.active_clue.daily_double is True

    # A real buzz during this window must have no effect whatsoever.
    g.buzz(0, tick_ns=5 * MS)
    assert g.phase is Phase.REVEALED
    assert g.winner is None

    snap = g.snapshot(now_tick=10 * MS)
    assert snap["active_clue"]["daily_double"] is True


def test_daily_double_flag_false_for_normal_clue():
    g = armed_game()
    snap = g.snapshot(now_tick=5 * MS)
    assert snap["active_clue"]["daily_double"] is False


# ---- Rounds ----


def test_next_round_advances_and_rejects_past_the_last_round():
    g = make_game(rounds=[make_round(prefix="R1"), make_round(prefix="R2")])
    assert g.snapshot(now_tick=0)["round_index"] == 0

    g.next_round(tick_ns=0)
    assert g.snapshot(now_tick=0)["round_index"] == 1
    assert g._categories[0].name == "R2 0"

    with pytest.raises(IllegalTransitionError):
        g.next_round(tick_ns=0)  # no further rounds


def test_next_round_only_from_idle():
    g = make_game(rounds=[make_round(prefix="R1"), make_round(prefix="R2")])
    g.select_clue(0, 0, tick_ns=0)
    with pytest.raises(IllegalTransitionError):
        g.next_round(tick_ns=0)
    assert g.snapshot(now_tick=0)["round_index"] == 0


def test_each_round_has_independent_used_state():
    g = make_game(rounds=[make_round(prefix="R1"), make_round(prefix="R2")])
    g.select_clue(0, 0, tick_ns=0)
    g.arm(tick_ns=1 * MS)
    g.buzz(0, tick_ns=2 * MS)
    g.mark_correct(tick_ns=3 * MS)
    g.return_to_board(tick_ns=4 * MS)

    g.next_round(tick_ns=5 * MS)
    # Round 2's clue at the same coordinates is untouched by round 1 play.
    assert g._categories[0].clues[0].used is False
    g.select_clue(0, 0, tick_ns=6 * MS)  # must not raise "already used"
    assert g.phase is Phase.READING


# ---- Final Jeopardy ----


def test_final_jeopardy_starts_from_idle_and_blocks_buzzes():
    g = make_game(final_jeopardy_category="Movies")
    g.start_final_jeopardy(tick_ns=0)
    assert g.phase is Phase.FINAL_JEOPARDY
    assert g.snapshot(now_tick=0)["final_jeopardy_category"] == "Movies"

    g.buzz(0, tick_ns=1 * MS)
    assert g.phase is Phase.FINAL_JEOPARDY
    assert g.winner is None


def test_final_jeopardy_not_startable_mid_clue():
    g = armed_game()
    with pytest.raises(IllegalTransitionError):
        g.start_final_jeopardy(tick_ns=0)


def test_return_to_board_from_final_jeopardy():
    g = make_game()
    g.start_final_jeopardy(tick_ns=0)
    g.return_to_board(tick_ns=1 * MS)
    assert g.phase is Phase.IDLE
