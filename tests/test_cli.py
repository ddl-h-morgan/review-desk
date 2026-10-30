import json
import os

import pytest

from conftest import make_packet
from review_desk.cli import main
from review_desk.decisions import merge_item


def run(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_status_json(capsys, packet, state_dir):
    merge_item(packet, "first-draft", {"decision": "revise", "note": "shorter title"})
    code, out, _ = run(capsys, "status", str(packet), "--json")
    assert code == 0
    s = json.loads(out)
    assert s["title"] == "Demo packet" and s["path"] == str(packet.resolve())
    assert s["counts"] == {"approve": 0, "revise": 1, "drop": 0, "pending": 1, "total": 2}
    assert s["undecided"] == ["second-draft"]
    first = s["items"][0]
    assert first["id"] == "first-draft" and first["decision"] == "revise" and first["note"] == "shorter title"
    assert first["file"] == "first-draft.md" and first["updatedAt"]
    assert s["items"][1]["decision"] is None
    assert s["orphans"] == []
    assert s["lastTouched"] == first["updatedAt"]


def test_status_reports_orphans(capsys, packet, state_dir):
    merge_item(packet, "gone-item", {"decision": "approve"})
    s = json.loads(run(capsys, "status", str(packet), "--json")[1])
    assert s["orphans"] == ["gone-item"] and s["counts"]["pending"] == 2


def test_status_cleared_decision_counts_as_pending(capsys, packet, state_dir):
    merge_item(packet, "first-draft", {"decision": "approve"})
    merge_item(packet, "first-draft", {"decision": ""})
    s = json.loads(run(capsys, "status", str(packet), "--json")[1])
    assert s["counts"]["pending"] == 2 and s["undecided"] == ["first-draft", "second-draft"]


def test_status_text(capsys, packet, state_dir):
    merge_item(packet, "first-draft", {"decision": "approve", "note": "ship it"})
    code, out, _ = run(capsys, "status", str(packet))
    assert code == 0
    assert "1 approve" in out and "1 pending" in out
    assert "first-draft" in out and "ship it" in out
    assert "undecided: second-draft" in out


def test_status_all_registered(capsys, packet, state_dir):
    run(capsys, "add", str(packet))
    code, out, _ = run(capsys, "status", "--json")
    rows = json.loads(out)["packets"]
    assert code == 0 and rows[0]["counts"]["pending"] == 2 and rows[0]["id"]


def test_status_bad_manifest_exits_nonzero(capsys, tmp_path, state_dir):
    code, _, err = run(capsys, "status", str(tmp_path))
    assert code == 1 and "review.toml" in err


def test_add_list_remove(capsys, packet, state_dir):
    code, out, _ = run(capsys, "add", str(packet))
    assert code == 0 and "registered" in out
    code, out, _ = run(capsys, "list")
    assert "Demo packet" in out and str(packet.resolve()) in out and "2 pending" in out
    code, out, _ = run(capsys, "list", "--json")
    assert json.loads(out)["packets"][0]["title"] == "Demo packet"
    assert run(capsys, "remove", str(packet))[0] == 0
    assert run(capsys, "remove", str(packet))[0] == 1


def test_new_scaffolds_valid_manifest(capsys, tmp_path, state_dir):
    d = tmp_path / "fresh"
    code, out, _ = run(capsys, "new", str(d), "--title", "Fresh drafts")
    assert code == 0
    from review_desk.manifest import load
    p = load(d)
    assert p.title == "Fresh drafts" and p.items
    assert run(capsys, "new", str(d))[0] == 1  # refuses to overwrite


@pytest.fixture
def no_real_browser(monkeypatch):
    """Record every launch attempt; never start a desk or a browser."""
    from review_desk import cli
    monkeypatch.delenv("REVIEW_DESK_BROWSER", raising=False)
    monkeypatch.setattr(cli, "ensure_running", lambda port: None)
    calls = {"run": [], "open": [], "get": []}

    class FakeBrowser:
        def __init__(self, name):
            self.name = name

        def open(self, url, new=0, autoraise=True):
            calls["get"].append((self.name, url, new))
            return True

    def fake_get(name=None):
        if name == "no-such-browser":
            raise cli.webbrowser.Error("could not locate runnable browser")
        return FakeBrowser(name)

    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **k: calls["run"].append(cmd))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url, new=0, autoraise=True: calls["open"].append((url, new)) or True)
    monkeypatch.setattr(cli.webbrowser, "get", fake_get)
    return calls


def test_open_no_browser_prints_url(capsys, packet, state_dir, no_real_browser, monkeypatch):
    from review_desk import cli
    monkeypatch.setattr(cli, "PLATFORM", "darwin")
    monkeypatch.setenv("REVIEW_DESK_BROWSER", "Some Browser")
    code, out, _ = run(capsys, "open", str(packet), "--port", "9999", "--no-browser")
    assert code == 0 and out.strip().startswith("http://127.0.0.1:9999/p/")
    assert no_real_browser == {"run": [], "open": [], "get": []}


def test_open_macos_default_browser(capsys, state_dir, no_real_browser, monkeypatch):
    from review_desk import cli
    monkeypatch.setattr(cli, "PLATFORM", "darwin")
    code, out, _ = run(capsys, "open", "--port", "9999")
    assert code == 0 and out.strip() == "http://127.0.0.1:9999/"
    assert no_real_browser["run"] == [["open", "http://127.0.0.1:9999/"]]
    assert no_real_browser["open"] == [] and no_real_browser["get"] == []


def test_open_macos_named_browser_from_env(capsys, state_dir, no_real_browser, monkeypatch):
    from review_desk import cli
    monkeypatch.setattr(cli, "PLATFORM", "darwin")
    monkeypatch.setenv("REVIEW_DESK_BROWSER", "Some Browser")
    assert run(capsys, "open", "--port", "9999")[0] == 0
    assert no_real_browser["run"] == [["open", "-na", "Some Browser", "--args", "--new-window", "http://127.0.0.1:9999/"]]


def test_open_browser_flag_overrides_env(capsys, state_dir, no_real_browser, monkeypatch):
    from review_desk import cli
    monkeypatch.setattr(cli, "PLATFORM", "darwin")
    monkeypatch.setenv("REVIEW_DESK_BROWSER", "Some Browser")
    assert run(capsys, "open", "--port", "9999", "--browser", "Other Browser")[0] == 0
    assert no_real_browser["run"] == [["open", "-na", "Other Browser", "--args", "--new-window", "http://127.0.0.1:9999/"]]


def test_open_empty_env_means_unset(capsys, state_dir, no_real_browser, monkeypatch):
    from review_desk import cli
    monkeypatch.setattr(cli, "PLATFORM", "darwin")
    monkeypatch.setenv("REVIEW_DESK_BROWSER", "")
    assert run(capsys, "open", "--port", "9999")[0] == 0
    assert no_real_browser["run"] == [["open", "http://127.0.0.1:9999/"]]


def test_open_other_platform_uses_webbrowser(capsys, state_dir, no_real_browser, monkeypatch):
    from review_desk import cli
    monkeypatch.setattr(cli, "PLATFORM", "linux")
    assert run(capsys, "open", "--port", "9999")[0] == 0
    assert no_real_browser["open"] == [("http://127.0.0.1:9999/", 1)]
    assert no_real_browser["run"] == [] and no_real_browser["get"] == []


def test_open_other_platform_named_browser(capsys, state_dir, no_real_browser, monkeypatch):
    from review_desk import cli
    monkeypatch.setattr(cli, "PLATFORM", "linux")
    monkeypatch.setenv("REVIEW_DESK_BROWSER", "firefox")
    assert run(capsys, "open", "--port", "9999")[0] == 0
    assert no_real_browser["get"] == [("firefox", "http://127.0.0.1:9999/", 1)]
    assert no_real_browser["run"] == [] and no_real_browser["open"] == []


def test_open_other_platform_unknown_browser_fails(capsys, state_dir, no_real_browser, monkeypatch):
    from review_desk import cli
    monkeypatch.setattr(cli, "PLATFORM", "linux")
    code, _, err = run(capsys, "open", "--port", "9999", "--browser", "no-such-browser")
    assert code == 1 and "no-such-browser" in err


def test_wait_returns_when_a_decision_changes(capsys, packet, state_dir, monkeypatch):
    sleeps = []

    def sleep(n):
        sleeps.append(n)
        assert len(sleeps) < 20, "never noticed the change"
        if len(sleeps) == 3:
            merge_item(packet, "first-draft", {"decision": "approve", "note": ""})
    monkeypatch.setattr("review_desk.cli.time.sleep", sleep)
    code, out, _ = run(capsys, "wait", str(packet), "--start", "1", "--max", "2")
    assert code == 0
    assert sleeps == [1, 2, 2]  # doubles, then caps
    assert out.splitlines()[0] == "CHANGED"
    assert ["first-draft", "approve", ""] in json.loads(out.splitlines()[1])


def test_wait_notices_a_changed_note_and_a_new_item(capsys, packet, state_dir, monkeypatch):
    monkeypatch.setattr("review_desk.cli.time.sleep",
                        lambda n: merge_item(packet, "second-draft", {"decision": "", "note": "hm"}))
    assert run(capsys, "wait", str(packet), "--start", "1")[0] == 0


def test_wait_settle_fires_once_after_a_note_stops_changing(capsys, packet, state_dir, monkeypatch):
    sleeps = []
    notes = {2: "h", 3: "he", 5: "hel"}  # a short pause (one quiet poll) mid-note restarts nothing, then still

    def sleep(n):
        sleeps.append(n)
        assert len(sleeps) < 30, "never fired"
        if len(sleeps) in notes:
            merge_item(packet, "first-draft", {"decision": "", "note": notes[len(sleeps)]})
    monkeypatch.setattr("review_desk.cli.time.sleep", sleep)
    code, out, _ = run(capsys, "wait", str(packet), "--start", "1", "--settle", "3")
    assert code == 0 and out.count("CHANGED") == 1
    assert ["first-draft", None, "hel"] in json.loads(out.splitlines()[1])  # the finished note, not the first keystroke
    assert len(sleeps) == 7  # last edit seen on poll 5, then two 2 s quiet polls (4 s >= 3)


def test_wait_settle_ignores_a_change_that_is_undone(capsys, packet, state_dir, monkeypatch):
    sleeps = []

    def sleep(n):
        sleeps.append(n)
        if len(sleeps) == 1:
            merge_item(packet, "first-draft", {"decision": "", "note": "oops"})
        elif len(sleeps) == 2:
            merge_item(packet, "first-draft", {"decision": "", "note": ""})
        elif len(sleeps) == 8:
            merge_item(packet, "first-draft", {"decision": "drop", "note": ""})
        assert len(sleeps) < 30
    monkeypatch.setattr("review_desk.cli.time.sleep", sleep)
    code, out, _ = run(capsys, "wait", str(packet), "--start", "1", "--settle", "2")
    assert code == 0 and ["first-draft", "drop", ""] in json.loads(out.splitlines()[1])


def _wait_with(capsys, packet, monkeypatch, steps, *args):
    """Run `wait`; steps = {poll number: callable} done during that poll's sleep. Returns (sleeps, output lines)."""
    sleeps = []

    def sleep(n):
        sleeps.append(n)
        assert len(sleeps) < 20, "never fired"
        if len(sleeps) in steps:
            steps[len(sleeps)]()
    monkeypatch.setattr("review_desk.cli.time.sleep", sleep)
    code, out, _ = run(capsys, "wait", str(packet), "--start", "1", "--max", "1", *args)
    assert code == 0
    return sleeps, out.splitlines()


def test_wait_reports_what_changed(capsys, packet, state_dir, monkeypatch):
    merge_item(packet, "second-draft", {"decision": "revise", "note": "old words"})
    _, lines = _wait_with(capsys, packet, monkeypatch, {1: lambda: (
        merge_item(packet, "first-draft", {"decision": "approve", "note": "ship it\nsoon"}),
        merge_item(packet, "second-draft", {"decision": "", "note": ""}))})
    assert lines[0] == "CHANGED" and ["first-draft", "approve", "ship it\nsoon"] in json.loads(lines[1])
    assert "first-draft: decision pending -> approve" in lines
    assert "first-draft: note added: ship it / soon" in lines
    assert "second-draft: decision revise -> pending" in lines and "second-draft: note cleared" in lines
    assert not any("EDIT" in ln for ln in lines)


def test_wait_says_when_each_decision_was_made_and_the_draft_last_edited(capsys, packet, state_dir, monkeypatch):
    def decide():
        merge_item(packet, "first-draft", {"decision": "approve", "note": ""})
    _, lines = _wait_with(capsys, packet, monkeypatch, {1: decide})
    stamp = json.loads((packet / "review-decisions.json").read_text())["first-draft"]["updatedAt"]
    row = next(ln for ln in lines if "draft last edited" in ln)
    assert f"decided {stamp}" in row and "(decision postdates the draft)" in row
    # a draft edited after the decision is called out
    os.utime(packet / "first-draft.md", (4102444800, 4102444800))  # year 2100
    (packet / "first-draft.md").write_text("# First\n\nedited later")
    os.utime(packet / "first-draft.md", (4102444800, 4102444800))
    _, lines = _wait_with(capsys, packet, monkeypatch, {1: lambda: merge_item(packet, "first-draft", {"note": "n"})})
    assert any("DRAFT EDITED AFTER THE DECISION" in ln for ln in lines)


def test_wait_ignores_reordering_and_edits_by_default_but_reports_them(capsys, packet, state_dir, monkeypatch):
    def reorder():
        text = (packet / "review.toml").read_text()
        head, first, second = text.split("[[items]]")
        (packet / "review.toml").write_text(head + "[[items]]" + second + "[[items]]" + first)
    sleeps, lines = _wait_with(capsys, packet, monkeypatch, {
        1: reorder,
        2: lambda: (packet / "first-draft.md").write_text("# First\n\nrewritten"),
        3: lambda: (packet / "review.toml").write_text(
            (packet / "review.toml").read_text().replace('"Send it"', '"Send it, shorter"')),
        4: lambda: merge_item(packet, "second-draft", {"decision": "drop", "note": ""})})
    assert len(sleeps) == 4  # woke on the decision only
    assert "second-draft: decision pending -> drop" in lines
    assert "first-draft: EDIT draft file changed (not a decision)" in lines
    assert "second-draft: EDIT manifest entry changed (not a decision)" in lines


def test_wait_on_edits_wakes_on_a_draft_or_manifest_edit(capsys, packet, state_dir, monkeypatch):
    sleeps, lines = _wait_with(capsys, packet, monkeypatch,
                               {2: lambda: (packet / "first-draft.md").write_text("# First\n\nrewritten")}, "--on-edits")
    assert len(sleeps) == 2 and lines[0] == "CHANGED"
    assert lines[2:] == ["first-draft: EDIT draft file changed (not a decision)"]
    # a touch with the same text is not an edit
    sleeps, _ = _wait_with(capsys, packet, monkeypatch, {
        1: lambda: (packet / "first-draft.md").write_text((packet / "first-draft.md").read_text()),
        3: lambda: (packet / "review.toml").write_text(
            (packet / "review.toml").read_text().replace('"First draft"', '"First draft v2"'))}, "--on-edits")
    assert len(sleeps) == 3


def test_wait_reports_items_added_and_removed(capsys, packet, state_dir, monkeypatch):
    text = (packet / "review.toml").read_text()
    head, first, second = text.split("[[items]]")
    extra = '\n[[items]]\nid = "third"\ntitle = "Third"\nkind = "comment"\nrecommendation = "r"\n'
    _, lines = _wait_with(capsys, packet, monkeypatch,
                          {1: lambda: (packet / "review.toml").write_text(head + "[[items]]" + first + extra)})
    assert "third: item added (pending)" in lines and "second-draft: item removed" in lines


def test_wait_rejects_bad_delays_and_bad_packets(capsys, packet, tmp_path, state_dir, monkeypatch):
    monkeypatch.setattr("review_desk.cli.time.sleep", lambda n: pytest.fail("must not sleep"))
    for bad in ("0", "-1"):
        assert run(capsys, "wait", str(packet), "--start", bad)[0] == 1
    assert run(capsys, "wait", str(packet), "--max", "0")[0] == 1
    assert run(capsys, "wait", str(packet), "--settle", "-1")[0] == 1
    assert run(capsys, "wait", str(tmp_path))[0] == 1  # no review.toml


def test_wait_gives_up_after_three_failed_polls_but_survives_one(capsys, packet, state_dir, monkeypatch):
    mf = packet / "review.toml"
    good = mf.read_text()
    n = []

    def sleep(_):
        n.append(1)
        assert len(n) < 20, "never gave up"
        mf.write_text(good if len(n) == 2 else "not [valid")
    # poll 1 fails, poll 2 is fine (resets the count), polls 3-5 fail: 3 in a row
    monkeypatch.setattr("review_desk.cli.time.sleep", sleep)
    code, _, err = run(capsys, "wait", str(packet), "--start", "1")
    assert code == 1 and "gave up after 3 failed polls" in err and len(n) == 5


def test_url_prints_the_packet_url_and_registers(capsys, packet, state_dir):
    code, out, err = run(capsys, "url", str(packet), "--port", "8123")
    assert code == 0 and "registered" in err
    url = out.strip()
    assert url.startswith("http://127.0.0.1:8123/p/") and url.endswith("/") and "#" not in url


def test_url_item_adds_a_fragment_for_that_card(capsys, packet, state_dir):
    base = run(capsys, "url", str(packet))[1].strip()
    code, out, _ = run(capsys, "url", str(packet), "--item", "second-draft")
    assert code == 0 and out.strip() == base + "#second-draft"


def test_url_item_must_be_in_the_manifest_and_needs_a_packet(capsys, packet, state_dir):
    code, _, err = run(capsys, "url", str(packet), "--item", "nope")
    assert code == 1 and "no item 'nope'" in err
    code, _, err = run(capsys, "open", "--no-browser", "--item", "first-draft")
    assert code == 1 and "--item needs a packet directory" in err


def test_open_item_opens_the_item_link(capsys, packet, state_dir, monkeypatch):
    opened = []
    monkeypatch.setattr("review_desk.cli.ensure_running", lambda *a, **k: None)
    monkeypatch.setattr("review_desk.cli.open_browser", lambda url, browser=None: opened.append(url))
    code, out, _ = run(capsys, "open", str(packet), "--item", "first-draft")
    assert code == 0 and opened == [out.strip()] and out.strip().endswith("/#first-draft")
    assert run(capsys, "open", str(packet), "--no-browser", "--item", "first-draft")[1].strip() == out.strip()
    assert len(opened) == 1  # --no-browser opens nothing


def test_open_without_item_has_no_fragment(capsys, packet, state_dir):
    assert "#" not in run(capsys, "open", str(packet), "--no-browser")[1]
