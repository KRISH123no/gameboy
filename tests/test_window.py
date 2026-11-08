"""The two window loops, driven headlessly with a scripted keyboard.

Everything else in this suite tests what goes on screen without opening one.
These tests open one — against SDL's dummy driver, which renders to memory —
because the loops themselves have been where the worst bugs lived, and none
of them were reachable from a test of the layout.

Two shipped bugs this file exists to prevent:

* The window never opened at all for two months, because the key map was
  built from `f"K_{name}"` and pygame spells its letter constants in lower
  case. Every headless test passed the whole time.
* Pressing PLAY and coming back crashed with "display Surface quit": the
  launcher asked the old surface for its size after `play` had already torn
  the display down.
"""


import pytest

pygame = pytest.importorskip("pygame")


@pytest.fixture
def headless(monkeypatch):
    """A display that renders into memory, and a clock that never waits."""
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    pygame.display.quit()
    pygame.init()

    class NoClock:
        def tick(self, fps=0):
            return 0

        def get_fps(self):
            return 60.0

    monkeypatch.setattr(pygame.time, "Clock", NoClock)
    yield
    pygame.display.quit()


def press(key, unicode=""):
    return pygame.event.Event(pygame.KEYDOWN, key=key, unicode=unicode)


def scripted(monkeypatch, frames: list[list]):
    """Feed the loop one list of events per poll, then quit it.

    Returns a counter the test can read: how many frames were drawn and how
    far through the script the loop got.
    """
    state = {"poll": 0, "frames": 0}

    def event_get():
        index = state["poll"]
        state["poll"] += 1
        if index < len(frames):
            return frames[index]
        return [pygame.event.Event(pygame.QUIT)]

    monkeypatch.setattr(pygame.event, "get", event_get)
    monkeypatch.setattr(pygame.display, "flip",
                        lambda: state.__setitem__("frames", state["frames"] + 1))
    return state


def test_the_launcher_opens_draws_and_closes(headless, monkeypatch, tmp_path):
    """The whole loop, with nothing but the built-in cartridge to show."""
    from gameboy import display

    state = scripted(monkeypatch, [
        [], [],
        [press(pygame.K_RIGHT)],
        [press(pygame.K_DOWN)],
        [press(pygame.K_TAB)],
        [press(pygame.K_LEFT)],
        [press(pygame.K_ESCAPE)],
    ])
    assert display.launch(roms=str(tmp_path)) == 0
    assert state["frames"] > 3, "the loop exited without drawing anything"


def test_typing_in_the_search_box_filters_and_clears(headless, monkeypatch, tmp_path):
    from gameboy import display

    scripted(monkeypatch, [
        [],
        [press(pygame.K_SLASH, "/")],
        [press(pygame.K_z, "z")],          # matches nothing
        [press(pygame.K_BACKSPACE)],
        [press(pygame.K_RETURN, "\r")],    # leave the box
        [press(pygame.K_ESCAPE)],
    ])
    assert display.launch(roms=str(tmp_path)) == 0


def test_resizing_the_window_does_not_end_the_session(headless, monkeypatch, tmp_path):
    from gameboy import display

    scripted(monkeypatch, [
        [],
        [pygame.event.Event(pygame.VIDEORESIZE, w=1500, h=900)],
        [pygame.event.Event(pygame.VIDEORESIZE, w=400, h=300)],  # below the floor
        [press(pygame.K_ESCAPE)],
    ])
    assert display.launch(roms=str(tmp_path)) == 0


def test_playing_a_cartridge_and_coming_back(headless, monkeypatch, tmp_path):
    """The round trip that crashed: launcher, game, escape, launcher, quit.

    `play` tears the display down and builds its own, so the launcher has to
    survive its surface being replaced underneath it — and has to write down
    how long the cartridge was played for on the way back.
    """
    from gameboy import display, library

    # Point the history at a temporary home rather than stubbing the loader,
    # so the file really is written and really is read back.
    monkeypatch.setattr(library, "home", lambda: tmp_path / "home")

    scripted(monkeypatch, [
        [],
        [press(pygame.K_RETURN, "\r")],   # PLAY
        [], [],
        [press(pygame.K_ESCAPE)],         # consumed by play(): back to the launcher
        [],
        [press(pygame.K_ESCAPE)],         # quit the launcher
    ])
    assert display.launch(roms=str(tmp_path)) == 0

    reloaded = library.Library.load()
    assert len(reloaded.records) == 1, "the session was not written down"
    record = next(iter(reloaded.records.values()))
    assert record.launches == 1
    assert record.seconds >= 0


def test_closing_the_window_leaves_rather_than_returning_to_the_menu(
    headless, monkeypatch, tmp_path
):
    from gameboy import display

    scripted(monkeypatch, [[], [pygame.event.Event(pygame.QUIT)]])
    assert display.launch(roms=str(tmp_path)) == 0


def test_the_consoles_own_menu_still_opens(headless, monkeypatch, tmp_path):
    """The small 160x144 menu is a separate loop and a separate risk."""
    from gameboy import display

    scripted(monkeypatch, [
        [],
        [press(pygame.K_DOWN)],
        [press(pygame.K_UP)],
        [press(pygame.K_p)],              # switch palette
        [press(pygame.K_ESCAPE)],
    ])
    assert display.home(roms=str(tmp_path)) == 0


def test_every_key_in_the_map_is_a_real_pygame_constant():
    """The two-month bug, as one assertion.

    `f"K_{name}"` produced `K_up`, which does not exist, and the window died
    on the line that built the map.
    """
    from gameboy.display import KEY_MAP

    for name in KEY_MAP:
        assert hasattr(pygame, name), name
    assert len(set(KEY_MAP.values())) == len(KEY_MAP), "a button is bound twice"
