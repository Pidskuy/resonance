"""Tests for app/queue.py. The key test: what the panel shows (up_next) is
exactly what Next plays, in every combination of shuffle and repeat, including
after adding songs, adding duplicates and dragging."""

import random

import pytest

from app.queue import PlayQueue

SONGS = [10, 20, 30, 40, 50]


def queue(start=30, shuffle=False, repeat="off", seed=1) -> PlayQueue:
    q = PlayQueue(random.Random(seed))
    q.shuffle, q.repeat = shuffle, repeat
    q.set(SONGS, start)
    return q


def plays(q: PlayQueue, n: int) -> list[int]:
    return [q.next() for _ in range(n)]


# ---------- the basics ----------

def test_starts_at_clicked_song_and_lists_the_rest():
    q = queue(start=30)
    assert q.current_id == 30
    assert q.up_next() == [40, 50]


@pytest.mark.parametrize("shuffle", [False, True])
@pytest.mark.parametrize("repeat", ["off", "all", "one"])
def test_up_next_is_exactly_what_next_plays(shuffle, repeat):
    q = queue(shuffle=shuffle, repeat=repeat)
    shown = q.up_next()
    assert plays(q, len(shown)) == shown


@pytest.mark.parametrize("shuffle", [False, True])
@pytest.mark.parametrize("repeat", ["off", "all", "one"])
def test_up_next_still_matches_after_adding_and_dragging(shuffle, repeat):
    q = queue(shuffle=shuffle, repeat=repeat)
    q.add(99)
    q.add(99)                                 # the same song twice
    q.add(10)
    q.move(3, 0)
    shown = q.up_next()
    assert plays(q, len(shown)) == shown


def test_end_of_queue_with_repeat_off():
    q = queue(start=50)
    assert q.up_next() == []
    assert q.next() is None
    assert q.current_id == 50                 # stays on the last song


def test_repeat_all_starts_a_new_round():
    q = queue(start=40, repeat="all")
    assert q.up_next() == [50, 10, 20, 30, 40, 50]
    assert plays(q, 2) == [50, 10]
    assert q.up_next()[:4] == [20, 30, 40, 50]


def test_repeat_one_replays_when_song_ends_but_next_still_moves_on():
    q = queue(start=30, repeat="one")
    assert q.next(auto=True) == 30            # song ended by itself → same song
    assert q.next() == 40                     # you pressed Next → next song


def test_previous_goes_back_and_puts_current_on_top():
    q = queue(start=20)
    q.next()                                  # now 30
    assert q.previous() == 20
    assert q.up_next() == [30, 40, 50]
    assert q.previous() == 20                 # nothing earlier: stays


# ---------- Add to queue ----------

def test_added_song_goes_to_the_top():
    q = queue(start=20)                       # up next: 30, 40, 50
    q.add(99)
    assert q.up_next() == [99, 30, 40, 50]


def test_several_additions_play_in_the_order_you_added_them():
    q = queue(start=20)
    q.add(97)
    q.add(98)
    q.add(99)
    assert q.up_next() == [97, 98, 99, 30, 40, 50]


def test_the_same_song_can_be_added_many_times():
    q = queue(start=20)
    q.add(30)
    q.add(30)
    assert q.up_next() == [30, 30, 30, 40, 50]
    assert plays(q, 3) == [30, 30, 30]


def test_adding_with_nothing_playing_makes_it_the_current_song():
    q = PlayQueue()
    q.add(7)
    assert q.current_id == 7 and q.up_next() == []
    q.add(8)
    assert q.up_next() == [8]


def test_your_additions_stay_on_top_when_you_start_another_list():
    q = queue(start=20)
    q.add(99)
    q.set([1, 2, 3], 1)
    assert q.up_next() == [99, 2, 3]


# ---------- dragging ----------

def test_drag_a_song_to_the_top_to_play_it_next():
    q = queue(start=10)                       # up next: 20, 30, 40, 50
    q.move(3, 0)
    assert q.up_next() == [50, 20, 30, 40]
    assert q.next() == 50


def test_drag_down():
    q = queue(start=10)
    q.move(0, 2)
    assert q.up_next() == [30, 40, 20, 50]


def test_a_dragged_song_counts_as_yours_so_new_additions_go_after_it():
    q = queue(start=10)
    q.move(3, 0)                              # 50 dragged to the top
    q.add(99)
    assert q.up_next() == [50, 99, 20, 30, 40]


def test_move_ignores_impossible_positions():
    q = queue(start=10)
    q.move(9, 0)
    q.move(-1, 0)
    assert q.up_next() == [20, 30, 40, 50]


# ---------- shuffle ----------

def test_shuffle_keeps_current_song_and_every_other_song_once():
    q = queue(start=30, shuffle=True)
    assert q.current_id == 30
    assert sorted(q.up_next()) == [10, 20, 40, 50]


def test_shuffle_leaves_your_additions_on_top():
    q = queue(start=30)
    q.add(99)
    q.set_shuffle(True)
    assert q.up_next()[0] == 99
    assert sorted(q.up_next()[1:]) == [10, 20, 40, 50]
    q.set_shuffle(False)
    assert q.up_next() == [99, 40, 50]        # back in list order after the current song


def test_turning_shuffle_off_continues_from_where_you_are_in_the_list():
    q = queue(start=10, shuffle=True)
    q.next()
    here = q.current_id
    q.set_shuffle(False)
    assert q.up_next() == [s for s in SONGS if s > here]


# ---------- jump, clear, repeat, empty ----------

def test_jump_to_a_song_in_up_next_skips_the_ones_above():
    q = queue(start=10)
    assert q.jump(2) == 40
    assert q.up_next() == [50]
    assert q.previous() == 10                 # skipped songs aren't in history


def test_jump_into_the_next_round_with_repeat_all():
    q = queue(start=40, repeat="all")         # up next: 50 | 10 20 30 40 50
    assert q.jump(2) == 20
    assert q.up_next()[:3] == [30, 40, 50]


def test_clear_keeps_only_the_current_song():
    q = queue(start=30, repeat="all")
    q.add(99)
    q.clear()
    assert q.current_id == 30
    assert q.up_next() == []
    assert q.next() is None


def test_cycle_repeat():
    q = PlayQueue()
    assert [q.cycle_repeat() for _ in range(3)] == ["all", "one", "off"]


def test_empty_queue_is_safe():
    q = PlayQueue()
    assert q.current_id is None and q.up_next() == []
    assert q.next() is None and q.previous() is None and q.jump(0) is None
    q.set_shuffle(True)
    q.move(0, 1)
    q.clear()


# ---------- sections for the Queue panel ----------

def test_queued_count_is_your_additions_at_the_top():
    q = queue(start=10)
    assert q.queued_count() == 0
    q.add(99)
    q.add(98)
    assert q.queued_count() == 2
    q.move(3, 0)                              # a list song dragged to the top joins your section
    assert q.queued_count() == 3
    q.next()
    assert q.queued_count() == 2


def test_context_name_is_remembered_and_cleared():
    q = PlayQueue()
    q.set(SONGS, 10, "All songs")
    assert q.context_name == "All songs"
    q.clear()
    assert q.context_name == ""
