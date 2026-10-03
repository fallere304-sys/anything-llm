from buddy.chat.context import build_context


def hist(*pairs):
    return [{"role": r, "content": c} for r, c in pairs]


def test_all_fit():
    msgs, dropped = build_context("SYS", hist(("user", "a"), ("assistant", "b"), ("user", "c")), 1000)
    assert [m.role for m in msgs] == ["system", "user", "assistant", "user"] and dropped == 0


def test_drops_oldest_first():
    h = hist(("user", "x" * 100), ("assistant", "y" * 100), ("user", "z" * 100))
    msgs, dropped = build_context("S", h, 250)
    assert dropped == 1 and msgs[1].content.startswith("y") and msgs[-1].content.startswith("z")


def test_latest_always_kept_even_over_budget():
    msgs, dropped = build_context("S", hist(("user", "a"), ("user", "q" * 5000)), 100)
    assert dropped == 1 and msgs[-1].content == "q" * 5000
