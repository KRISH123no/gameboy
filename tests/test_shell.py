"""The home screen: the font, the cartridge list, and the cursor."""

import pytest

from gameboy import font
from gameboy.shell import SUFFIXES, Entry, Menu, find_roms

# -------------------------------------------------------------- the font


@pytest.mark.parametrize("character", list("ABCXYZ0129 .-:>"))
def test_every_glyph_is_the_size_it_claims(character):
    rows = font.glyph(character)
    assert len(rows) == font.HEIGHT
    assert all(len(row) == font.WIDTH for row in rows)


def test_every_glyph_is_only_ones_and_zeroes():
    for character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,-_:/><()+!?'":
        for row in font.glyph(character):
            assert set(row) <= {"0", "1"}, character


def test_lowercase_reads_as_uppercase():
    assert font.glyph("a") == font.glyph("A")


def test_an_undrawn_character_is_visible_rather_than_absent():
    """A silent gap looks like a bug in the layout, not a missing glyph."""
    box = font.glyph("é")
    assert box != font.glyph(" ")


def test_width_accounts_for_the_gap_between_letters():
    assert font.measure("A") == font.WIDTH
    assert font.measure("AB") == font.WIDTH * 2 + font.TRACKING
    assert font.measure("") == 0


def test_drawing_stays_inside_the_surface():
    """Text near the edge must clip, not raise."""
    pygame = pytest.importorskip("pygame")

    surface = pygame.Surface((20, 10), depth=8)
    surface.fill(0)
    font.draw(surface, "LONGER THAN THE SURFACE", 14, 6, 3)
    assert surface.get_at_mapped((19, 9)) in (0, 3)


def test_drawing_puts_pixels_where_the_glyph_says():
    pygame = pytest.importorskip("pygame")

    surface = pygame.Surface((16, 16), depth=8)
    surface.fill(0)
    font.draw(surface, "I", 2, 2, 3)
    rows = font.glyph("I")
    for row, bits in enumerate(rows):
        for column, bit in enumerate(bits):
            # get_at_mapped, not get_at: on a palettised surface get_at
            # returns the colour the index points at, not the index.
            expected = 3 if bit == "1" else 0
            assert surface.get_at_mapped((2 + column, 2 + row)) == expected


def test_draw_reports_where_it_finished():
    pygame = pytest.importorskip("pygame")

    surface = pygame.Surface((80, 16), depth=8)
    assert font.draw(surface, "ABC", 0, 0, 3) == 3 * (font.WIDTH + font.TRACKING)


# ------------------------------------------------------------- the list


def test_the_demo_is_always_offered(tmp_path):
    entries = find_roms(str(tmp_path))
    assert entries[0].path is None and "Demo" in entries[0].label


def test_cartridges_in_the_folder_are_listed(tmp_path):
    (tmp_path / "tetris.gb").write_bytes(b"x" * 2048)
    (tmp_path / "notes.txt").write_text("not a rom")
    labels = [e.label for e in find_roms(str(tmp_path))]
    assert "tetris" in labels and "notes" not in labels


@pytest.mark.parametrize("suffix", SUFFIXES)
def test_both_cartridge_extensions_count(tmp_path, suffix):
    (tmp_path / f"game{suffix}").write_bytes(b"x" * 2048)
    assert any(e.label == "game" for e in find_roms(str(tmp_path)))


def test_the_same_file_is_not_listed_twice(tmp_path):
    """`.` and `~/Desktop` are the same folder often enough to matter."""
    (tmp_path / "a.gb").write_bytes(b"x" * 1024)
    entries = find_roms(str(tmp_path))
    paths = [e.path for e in entries if e.path]
    assert len(paths) == len(set(paths))


def test_a_missing_folder_is_not_an_error(tmp_path):
    assert find_roms(str(tmp_path / "nope"))[0].path is None


# ------------------------------------------------------------ the cursor


def rows(n):
    return Menu([Entry(f"g{i}", None) for i in range(n)], rows=5)


def test_it_starts_on_the_first_entry():
    assert rows(4).selected.label == "g0"


def test_down_moves_down():
    menu = rows(4)
    menu.move(1)
    assert menu.selected.label == "g1"


def test_it_wraps_at_the_bottom():
    menu = rows(3)
    menu.move(3)
    assert menu.selected.label == "g0"


def test_it_wraps_at_the_top():
    menu = rows(3)
    menu.move(-1)
    assert menu.selected.label == "g2"


def test_an_empty_list_has_nothing_selected_and_does_not_crash():
    menu = Menu([])
    menu.move(1)
    assert menu.selected is None


def test_a_short_list_is_shown_whole():
    assert rows(3).window == (0, 3)


def test_a_long_list_scrolls_to_keep_the_cursor_visible():
    menu = rows(20)
    menu.index = 12
    start, end = menu.window
    assert start <= 12 < end and end - start == 5


def test_the_window_never_runs_past_the_end():
    menu = rows(20)
    menu.index = 19
    start, end = menu.window
    assert end == 20 and end - start == 5
