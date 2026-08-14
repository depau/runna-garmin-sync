def test_load_missing_returns_default(state):
    assert state.load("nope.json") is None
    assert state.load("nope.json", {}) == {}


def test_save_and_load_roundtrip(state):
    state.save("x.json", {"a": [1, 2], "s": "è"})
    assert state.load("x.json") == {"a": [1, 2], "s": "è"}
    state.save("x.json", {"b": 1})
    assert state.load("x.json") == {"b": 1}
    assert not list(state.dir.glob("*.tmp"))  # no leftover temp files
