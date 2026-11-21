from llama_launcher.ui.marquee import Marquee

TEXT = "".join(chr(ord("a") + i % 26) for i in range(70))  # 70 chars


def test_short_text_never_moves():
    m = Marquee(64)
    m.set_text("short")
    for _ in range(50):
        m.tick()
    assert m.view() == "short" and not m.scrolls


def test_holds_start_1s_scrolls_one_char_per_step_holds_end_1s_then_restarts():
    m = Marquee(64, step_ms=250, start_hold_ms=1000, hold_ms=1000)  # measure=len: the limit is 64 characters
    m.set_text(TEXT)
    views = [m.view()]  # one entry per 0.25 s
    for _ in range(4 + 5 + 4 + 4):
        m.tick()
        views.append(m.view())
    starts = [TEXT.index(v) for v in views]
    assert all(len(v) == 64 for v in views)
    assert starts[:4] == [0] * 4                 # chars 1-64 held 4 x 0.25 s = 1 s
    assert starts[4:10] == [1, 2, 3, 4, 5, 6]    # then one character per 0.25 s, up to chars 7-70
    assert starts[10:13] == [6] * 3              # chars 7-70 shown 1 s in total (arrival + 3 more steps)
    assert starts[13:17] == [0] * 4              # back to the start, held 1 s again
    assert starts[17] == 1                       # and scrolling again


def test_width_limit_with_uneven_characters():
    def wide(s):  # 'W' is three units wide
        return sum(3 if c == "W" else 1 for c in s)

    m = Marquee(10, measure=wide, start_hold_ms=0)
    m.set_text("WWWiiiiiiiiii")  # 9 + 10 = 19 units
    assert m.view() == "WWWi"       # 9 + 1 = 10 units: as much as fits
    m.tick()
    assert m.view() == "WWiiii"     # one character dropped, more narrow ones fit
    m.tick()
    assert m.view() == "Wiiiiiii"   # 3 + 7 = 10 units
    m.tick()
    assert m.offset == 3 and m.view() == "iiiiiiiiii"  # the end fits: stop here
    m.tick()
    assert m.offset == 3  # holding


def test_new_text_restarts_but_same_text_does_not():
    m = Marquee(64, start_hold_ms=0)
    m.set_text(TEXT)
    m.tick()
    m.tick()
    m.set_text(TEXT)
    assert m.offset == 2
    m.set_text(TEXT + "x")
    assert m.offset == 0
