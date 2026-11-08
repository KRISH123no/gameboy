"""The launcher: layout, labels and the play history.

None of this opens a window. The drawing functions take `pygame` as an
argument precisely so that everything deciding *what* goes on screen can be
tested without one, which is the same split the console's own menu uses.
"""

import time

import pytest

from gameboy.launcher import (
    CARD_H,
    CARD_W,
    DETAIL,
    GAP,
    SIDEBAR,
    State,
    Tile,
    ago,
    best_label,
    clean_title,
    fit,
    greeting,
    interest,
    wrap,
)
from gameboy.library import Library, Record


def tiles(*names, played=()):
    """Cards with no artwork — nothing here needs a picture."""
    history = dict(played)
    return [
        Tile(label=name, path=None, digest=f"d{i}", kind="MBC1",
             size_label="32 KB", last_played=history.get(name, 0.0))
        for i, name in enumerate(names)
    ]


# ------------------------------------------------------------------ labels


def test_a_title_keeps_only_characters_the_font_can_draw():
    """Anything else becomes a fallback box, which reads as a broken name."""
    assert clean_title("2048-GB\x00\xff\x01") == "2048-GB"
    assert clean_title("TOBU\xe9") == "TOBU"


def test_a_title_is_collapsed_rather_than_padded():
    assert clean_title("  RHYTHM   LAND  ") == "RHYTHM LAND"
    assert clean_title("-TUFF-") == "TUFF"


def test_the_longer_filename_wins_when_it_says_the_same_thing():
    """The header has sixteen bytes; `Tobu Tobu Girl` ships as `TOBU`."""
    assert best_label("TOBU", "Tobu Tobu Girl") == "Tobu Tobu Girl"


def test_the_header_wins_when_the_filename_is_unrelated():
    assert best_label("DEADEUS GB", "rom_final_v2") == "DEADEUS GB"


def test_a_missing_header_title_falls_back_to_the_filename():
    assert best_label("", "Dangan GB") == "Dangan GB"


# ------------------------------------------------------------------- text


def test_text_too_wide_is_cut_and_marked():
    cut = fit("TOBU TOBU GIRL", 60, 2)
    assert cut.endswith(".")
    assert len(cut) < len("TOBU TOBU GIRL")


def test_text_that_fits_is_left_exactly_alone():
    assert fit("TUFF", 400, 2) == "TUFF"


def test_wrapping_breaks_on_spaces_and_stops_at_the_line_limit():
    lines = wrap("TOBU TOBU GIRL DELUXE EDITION", 268, 2, lines=2)
    assert len(lines) == 2
    assert lines[0].startswith("TOBU")


def test_a_single_unbreakable_word_is_cut_rather_than_overflowing():
    lines = wrap("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", 60, 2, lines=2)
    assert all(len(line) <= 30 for line in lines)


# ------------------------------------------------------------------ layout


def test_cards_fill_the_space_left_by_the_sidebar_and_the_panel():
    state = State(tiles=tiles("a"))
    columns = state.columns(1280)
    assert columns >= 1
    used = SIDEBAR + DETAIL + 3 * GAP + columns * CARD_W + (columns - 1) * GAP
    assert used <= 1280


def test_a_narrow_window_still_shows_one_column():
    assert State(tiles=tiles("a")).columns(400) == 1


def test_a_short_window_still_shows_one_row():
    assert State(tiles=tiles("a")).rows(100) == 1


def test_the_page_scrolls_to_keep_the_cursor_on_screen():
    """The grid pages rather than sliding, so the cursor is always drawn."""
    state = State(tiles=tiles(*[f"g{i}" for i in range(20)]), view="Library")
    columns, rows = state.columns(1280), state.rows(800)
    state.index = len(state.visible) - 1
    start, end = state.page(1280, 800)
    assert start <= state.index < end
    assert end - start == columns * rows


def test_the_first_page_starts_at_the_beginning():
    state = State(tiles=tiles(*[f"g{i}" for i in range(20)]), view="Library")
    assert state.page(1280, 800)[0] == 0


def test_an_empty_library_has_no_page_and_no_selection():
    state = State(tiles=[])
    assert state.page(1280, 800) == (0, 0)
    assert state.selected is None


# ----------------------------------------------------------------- the views


def test_home_puts_the_most_recently_played_first():
    now = time.time()
    state = State(tiles=tiles("old", "new", "never",
                              played={"old": now - 9999, "new": now}))
    assert [t.label for t in state.visible][:2] == ["new", "old"]


def test_library_is_alphabetical_whatever_was_played():
    now = time.time()
    state = State(tiles=tiles("zeta", "alpha", played={"zeta": now}),
                  view="Library")
    assert [t.label for t in state.visible] == ["alpha", "zeta"]


def test_search_filters_both_views_and_resets_the_cursor():
    state = State(tiles=tiles("Tobu", "Deadeus", "Tuff"), view="Library")
    state.index = 2
    state.set_query("tu")
    assert [t.label for t in state.visible] == ["Tuff"]
    assert state.index == 0


def test_the_cursor_wraps_at_both_ends():
    state = State(tiles=tiles("a", "b", "c"), view="Library")
    state.move(-1)
    assert state.selected.label == "c"
    state.move(1)
    assert state.selected.label == "a"


def test_moving_past_the_end_of_a_filtered_list_is_safe():
    """The cursor can outlive the list it was pointing into."""
    state = State(tiles=tiles("a", "b", "c"), view="Library")
    state.index = 2
    state.query = "a"  # set directly, so the index is deliberately stale
    assert state.selected.label == "a"
    state.move(1)
    assert state.selected is not None


def test_switching_view_returns_to_the_first_card():
    state = State(tiles=tiles("a", "b"), view="Library")
    state.index = 1
    state.switch("Home")
    assert (state.view, state.index) == ("Home", 0)


# ------------------------------------------------------------ cover scoring


def test_a_blank_frame_scores_below_a_detailed_one():
    """Frame 150 of most cartridges is a logo or nothing at all."""
    blank = [0] * 100
    busy = [0, 1, 2, 3] * 25
    assert interest(busy) > interest(blank)


def test_a_two_shade_frame_scores_below_a_four_shade_one():
    assert interest([0, 1] * 50) < interest([0, 1, 2, 3] * 25)


# -------------------------------------------------------------- the history


def test_an_unplayed_cartridge_says_so_rather_than_showing_a_zero():
    assert Record("d", "t").played_label == "never"
    assert ago(0) == "never"


@pytest.mark.parametrize("seconds,expected", [
    (30, "just now"), (400, "7 min ago"), (7200, "2 h ago"),
    (200_000, "2 d ago"), (900_000, "1 w ago"),
])
def test_age_is_shown_in_the_largest_unit_that_stays_small(seconds, expected):
    assert ago(time.time() - seconds) == expected


def test_history_survives_a_rename(tmp_path):
    """A cartridge is its bytes, so moving the file keeps the hours."""
    library = Library(path=tmp_path / "library.json")
    library.played("abc", "Tobu", 3600)
    library.played("abc", "Tobu Tobu Girl", 1800)
    reloaded = Library.load(tmp_path / "library.json")
    assert reloaded.records["abc"].seconds == pytest.approx(5400)
    assert reloaded.records["abc"].launches == 2


def test_a_missing_library_file_is_an_empty_one(tmp_path):
    assert Library.load(tmp_path / "nothing.json").records == {}


def test_a_damaged_library_file_does_not_stop_the_launcher(tmp_path):
    path = tmp_path / "library.json"
    path.write_text("{not json", encoding="utf-8")
    assert Library.load(path).records == {}


def test_the_greeting_follows_the_clock():
    morning = time.mktime((2026, 11, 8, 9, 0, 0, 0, 0, -1))
    evening = time.mktime((2026, 11, 8, 21, 0, 0, 0, 0, -1))
    assert greeting(morning) == "Good morning"
    assert greeting(evening) == "Good evening"


def test_a_card_is_the_shape_of_a_game_boy_frame():
    """Squashed cover art stops being a photograph of the cartridge."""
    from gameboy.launcher import THUMB_H, THUMB_W
    from gameboy.ppu import HEIGHT, WIDTH

    assert THUMB_H == pytest.approx(THUMB_W * HEIGHT / WIDTH, abs=1)
    assert CARD_H > THUMB_H and CARD_W > THUMB_W
