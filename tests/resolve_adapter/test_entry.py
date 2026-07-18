from resolve_adapter.minoru_studio_resolve import entry


def test_entry_requires_resolve_global(monkeypatch):
    messages = []
    monkeypatch.setattr(
        "resolve_adapter.minoru_studio_resolve.entry.show_error",
        lambda title, text: messages.append((title, text)),
    )
    assert entry.run({}) == 2
    assert "Resolve" in messages[0][1]


def test_entry_passes_resolve_to_ui(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "resolve_adapter.minoru_studio_resolve.entry.launch",
        lambda resolve: calls.append(resolve),
    )
    marker = object()
    assert entry.run({"resolve": marker}) == 0
    assert calls == [marker]
