"""Item dependencies: out-of-order decisions, re-action flags, and how status, wait and the change log report them."""
import json

import pytest

from conftest import make_packet
from review_desk import changes, waitreport
from review_desk.cli import main
from review_desk.decisions import merge_item, read
from review_desk.manifest import load

DEPS_MANIFEST = '''
title = "Plan, build, ship"

[[items]]
id = "plan"
title = "Plan"

[[items]]
id = "build"
title = "Build"
depends_on = ["plan"]

[[items]]
id = "ship"
title = "Ship"
depends_on = ["build", "plan"]

[[items]]
id = "aside"
title = "Unrelated"
'''


@pytest.fixture
def pkt(tmp_path):
    return make_packet(tmp_path / "deps", DEPS_MANIFEST, files={})


def decide(pkt, item, decision, **extra):
    """A write as the desk makes it: with the packet's dependency graph. Returns (record, newly flagged ids)."""
    flagged = []
    rec = merge_item(pkt, item, {"decision": decision, **extra}, load(pkt).dependencies(), flagged)
    return rec, flagged


def status(capsys, pkt):
    assert main(["status", str(pkt), "--json"]) == 0
    return json.loads(capsys.readouterr().out)


# ---- out of order --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("decision", ["approve", "revise", "drop"])
def test_deciding_ahead_of_an_open_dependency_is_marked_out_of_order(pkt, decision):
    rec, _ = decide(pkt, "build", decision)
    assert rec["decision"] == decision and rec["outOfOrder"] == ["plan"]


@pytest.mark.parametrize("dep_decision", [None, "", "revise", "drop"])
def test_a_dependency_counts_as_open_unless_approved(pkt, dep_decision):
    if dep_decision is not None:
        decide(pkt, "plan", dep_decision)
    assert decide(pkt, "build", "approve")[0]["outOfOrder"] == ["plan"]


def test_deciding_in_order_leaves_no_mark(pkt):
    decide(pkt, "plan", "approve")
    decide(pkt, "build", "approve")
    rec, _ = decide(pkt, "ship", "approve")
    assert "outOfOrder" not in rec and "needsReaction" not in rec
    assert set(read(pkt)["ship"]) == {"decision", "note", "updatedAt"}  # the file keeps its old shape


def test_out_of_order_lists_only_the_open_dependencies_in_manifest_order(pkt):
    decide(pkt, "plan", "approve")
    assert decide(pkt, "ship", "drop")[0]["outOfOrder"] == ["build"]


def test_clearing_is_never_out_of_order(pkt):
    decide(pkt, "build", "approve")
    rec, _ = decide(pkt, "build", "")
    assert "outOfOrder" not in rec


def test_a_dependency_awaiting_a_re_action_counts_as_open(pkt):
    decide(pkt, "build", "approve")           # out of order: plan is pending
    decide(pkt, "plan", "revise")             # plan changed: build needs a re-action
    assert read(pkt)["build"]["needsReaction"] == ["plan"]
    assert decide(pkt, "ship", "approve")[0]["outOfOrder"] == ["build", "plan"]
    # an approved dependency whose flag still applies (its cause isn't an approved item) is open too
    _write(pkt, {"plan": {"decision": "approve"}, "build": {"decision": "approve", "needsReaction": ["no-such-item"]}})
    assert decide(pkt, "ship", "approve")[0]["outOfOrder"] == ["build"]


# ---- needs re-action -----------------------------------------------------------------------------------------------

def test_a_dependency_change_flags_only_items_decided_out_of_order_on_it(pkt):
    decide(pkt, "build", "approve")           # out of order
    decide(pkt, "aside", "approve")           # no dependencies
    rec, flagged = decide(pkt, "plan", "revise")
    assert flagged == ["build"]
    state = read(pkt)
    assert state["build"]["needsReaction"] == ["plan"] and state["build"]["decision"] == "approve"
    assert "needsReaction" not in state["aside"] and "needsReaction" not in rec
    assert "ship" not in state  # pending items aren't flagged: they still need a decision anyway


@pytest.mark.parametrize("to", ["drop", "revise", ""])
def test_items_decided_in_order_are_flagged_when_the_approval_they_built_on_goes(pkt, to):
    decide(pkt, "plan", "approve")
    decide(pkt, "build", "drop")
    decide(pkt, "ship", "approve")            # in order on plan, out of order on build (dropped)
    assert decide(pkt, "plan", to)[1] == ["build", "ship"]
    state = read(pkt)
    assert state["build"]["needsReaction"] == ["plan"] and state["ship"]["needsReaction"] == ["plan"]
    assert "outOfOrder" not in state["build"] and state["build"]["decision"] == "drop"


def test_rewriting_the_same_decision_or_only_the_note_flags_nothing(pkt):
    decide(pkt, "plan", "revise")
    decide(pkt, "build", "approve")
    assert decide(pkt, "plan", "revise")[1] == []
    merge_item(pkt, "plan", {"note": "more detail"}, load(pkt).dependencies())
    assert "needsReaction" not in read(pkt)["build"]


def test_a_flag_names_each_changed_dependency_once_and_survives_note_edits(pkt):
    decide(pkt, "ship", "approve")            # out of order on build and plan
    assert decide(pkt, "build", "drop")[1] == ["ship"]
    assert decide(pkt, "build", "revise")[1] == []  # already flagged for build
    assert decide(pkt, "plan", "drop")[1] == ["build", "ship"]  # build too: it was decided before plan
    merge_item(pkt, "ship", {"note": "hm"}, load(pkt).dependencies())
    assert read(pkt)["ship"]["needsReaction"] == ["build", "plan"]


def test_re_deciding_clears_the_flag_and_re_checks_the_order(pkt):
    decide(pkt, "build", "approve")
    decide(pkt, "plan", "revise")
    rec, _ = decide(pkt, "build", "approve")  # re-confirmed while plan is still open
    assert "needsReaction" not in rec and rec["outOfOrder"] == ["plan"]
    decide(pkt, "plan", "approve")
    rec, _ = decide(pkt, "build", "drop")     # changed, now in order
    assert "needsReaction" not in rec and "outOfOrder" not in rec


def test_re_confirming_does_not_flag_the_items_that_depend_on_it(pkt):
    decide(pkt, "build", "approve")
    decide(pkt, "ship", "approve")
    decide(pkt, "plan", "revise")              # flags build and ship
    assert decide(pkt, "build", "approve")[1] == []  # same decision: nothing changed for ship
    assert read(pkt)["ship"]["needsReaction"] == ["plan"]


def test_a_decision_written_without_the_graph_still_clears_the_marks(pkt):
    decide(pkt, "build", "approve")
    decide(pkt, "plan", "revise")
    rec = merge_item(pkt, "build", {"decision": "approve"})
    assert "needsReaction" not in rec and "outOfOrder" not in rec


def test_older_decisions_files_load_and_report_no_marks(pkt, capsys):
    (pkt / "review-decisions.json").write_text(json.dumps(
        {"build": {"decision": "approve", "note": "", "updatedAt": "2026-01-01T00:00:00.000Z"}}))
    s = status(capsys, pkt)
    build = s["items"][1]
    assert build["outOfOrder"] is False and build["needsReaction"] is False and s["needsReaction"] == []
    assert decide(pkt, "plan", "drop")[1] == ["build"]  # unmarked counts as decided in order: its basis is gone


def test_hand_edited_marks_of_the_wrong_type_are_tolerated(pkt, capsys):
    (pkt / "review-decisions.json").write_text(json.dumps(
        {"build": {"decision": "approve", "note": "", "outOfOrder": True, "needsReaction": "yes"}}))
    s = status(capsys, pkt)
    assert s["needsReaction"] == ["build"] and s["items"][1]["changedDependencies"] == []
    assert decide(pkt, "plan", "drop")[1] == ["build"]
    assert read(pkt)["build"]["needsReaction"] == ["plan"]


@pytest.mark.parametrize("to", ["approve", "drop"])
def test_a_cleared_item_with_a_leftover_mark_is_not_flagged(pkt, to):
    (pkt / "review-decisions.json").write_text(json.dumps({"build": {"decision": "", "note": "", "outOfOrder": ["plan"]}}))
    assert decide(pkt, "plan", to)[1] == []


# ---- status --------------------------------------------------------------------------------------------------------

def test_status_json_reports_dependencies_and_re_action(pkt, capsys):
    decide(pkt, "build", "approve")
    decide(pkt, "plan", "revise")
    s = status(capsys, pkt)
    assert s["needsReaction"] == ["build"] and s["undecided"] == ["ship", "aside"]
    items = {i["id"]: i for i in s["items"]}
    assert items["build"] | {"updatedAt": None} == items["build"] | {
        "dependsOn": ["plan"], "waitingOn": ["plan"], "outOfOrder": True, "needsReaction": True,
        "changedDependencies": ["plan"], "updatedAt": None}
    assert items["ship"]["dependsOn"] == ["build", "plan"] and items["ship"]["waitingOn"] == ["build", "plan"]
    assert items["plan"]["outOfOrder"] is False and items["plan"]["needsReaction"] is False
    assert items["aside"]["dependsOn"] == [] and items["aside"]["waitingOn"] == []
    decide(pkt, "build", "drop")              # build re-decided while plan is open: out of order again
    decide(pkt, "ship", "approve")
    decide(pkt, "build", "revise")            # flags ship
    decide(pkt, "plan", "approve")
    assert status(capsys, pkt)["items"][2]["waitingOn"] == ["build"]  # plan is settled; build is revise


def test_a_cleared_item_is_not_reported_as_needing_a_re_action(pkt, capsys):
    (pkt / "review-decisions.json").write_text(json.dumps(
        {"build": {"decision": "", "note": "", "outOfOrder": ["plan"], "needsReaction": ["plan"]}}))
    s = status(capsys, pkt)
    assert s["needsReaction"] == [] and s["items"][1]["outOfOrder"] is False


def test_status_text_says_what_needs_a_re_action(pkt, capsys):
    decide(pkt, "build", "approve")
    decide(pkt, "plan", "revise")
    assert main(["status", str(pkt)]) == 0
    out = capsys.readouterr().out
    assert "NEEDS RE-ACTION (treat as pending): plan changed after it was decided and is not approved now" in out
    assert "waiting on: build, plan" in out
    assert "needs re-action (treat as pending): build" in out


def test_status_of_all_packets_counts_a_re_action_as_waiting(pkt, capsys, state_dir):
    main(["add", str(pkt)])
    for item in ("plan", "build", "ship", "aside"):
        decide(pkt, item, "approve")
    capsys.readouterr()
    assert main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["packetsWithPending"] == 0
    decide(pkt, "plan", "revise")  # plan reopens; build and ship were decided in order, so nothing is flagged
    decide(pkt, "build", "approve")  # re-decided while plan is open: out of order
    decide(pkt, "plan", "drop")      # flags build and ship
    decide(pkt, "ship", "approve")   # ship re-confirmed
    main(["status", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert rows["packets"][0]["needsReaction"] == ["build"] and rows["packets"][0]["counts"]["pending"] == 0
    assert rows["packetsWithPending"] == 1
    main(["status"])
    assert "0 pending" in (out := capsys.readouterr().out) and "(+1 to re-action)" in out


# ---- wait ----------------------------------------------------------------------------------------------------------

def _flagged_and_not(pkt):
    """Two decisions files that differ only in build's re-action flag (no decision or note differs)."""
    decide(pkt, "build", "approve")
    decide(pkt, "plan", "revise")
    flagged = read(pkt)
    unflagged = json.loads(json.dumps(flagged))
    del unflagged["build"]["needsReaction"]
    return flagged, unflagged


def _write(pkt, data):
    (pkt / "review-decisions.json").write_text(json.dumps(data))


@pytest.mark.parametrize("edits", [False, True])
def test_a_re_action_flag_alone_is_a_change_for_wait(pkt, edits):
    flagged, unflagged = _flagged_and_not(pkt)
    on = waitreport.state(pkt)
    _write(pkt, unflagged)
    off = waitreport.state(pkt)
    assert waitreport.key(on, edits) != waitreport.key(off, edits)
    assert waitreport.report(off, on) == [
        "build: NEEDS RE-ACTION, treat as pending: plan changed after it was decided and is not approved now",
        f"build:   decided {flagged['build']['updatedAt']}; draft last edited n/a"]
    assert waitreport.report(on, off)[0] == "build: re-action flag cleared"


def test_wait_wakes_when_an_item_is_flagged(pkt, capsys, monkeypatch, state_dir):
    flagged, unflagged = _flagged_and_not(pkt)
    _write(pkt, unflagged)
    sleeps = []

    def sleep(n):
        sleeps.append(n)
        assert len(sleeps) < 10, "never noticed the flag"
        if len(sleeps) == 2:
            _write(pkt, flagged)
    monkeypatch.setattr("review_desk.cli.time.sleep", sleep)
    assert main(["wait", str(pkt), "--start", "1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "CHANGED" and ["build", "approve", ""] in json.loads(out[1])  # the snapshot keeps its shape
    assert "build: NEEDS RE-ACTION, treat as pending: plan changed after it was decided and is not approved now" in out


def test_a_pending_item_has_no_flag_for_wait(pkt):
    (pkt / "review-decisions.json").write_text(json.dumps({"build": {"decision": "", "needsReaction": ["plan"]}}))
    assert waitreport.state(pkt)["items"]["build"]["needsReaction"] == []


# ---- the desk: PUT and the change log -------------------------------------------------------------------------------

@pytest.fixture
def ddesk(registry, pkt):
    import threading
    from conftest import Client
    from review_desk.server import make_server
    entry = registry.add(pkt)
    srv = make_server(0, registry)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    client = Client(srv.server_address[1])
    client.pid = entry.id
    yield client
    srv.shutdown()
    srv.server_close()


PAGE = {"X-Review-Page": "1"}


def test_put_marks_flags_and_logs_dependency_events(ddesk, pkt):
    code, _, body = ddesk.put_item(ddesk.pid, "build", {"decision": "approve"}, headers=PAGE)
    assert code == 200 and json.loads(body)["outOfOrder"] == ["plan"]
    assert ddesk.put_item(ddesk.pid, "build", {"note": "later"}, headers=PAGE)[0] == 200  # not a new decision: not logged
    assert ddesk.put_item(ddesk.pid, "build", {"decision": "approve"}, headers=PAGE)[0] == 200  # nor is the same one again
    assert ddesk.put_item(ddesk.pid, "plan", {"decision": "revise"}, headers=PAGE)[0] == 200
    assert read(pkt)["build"]["needsReaction"] == ["plan"]
    code, _, body = ddesk.put_item(ddesk.pid, "build", {"decision": "approve"}, headers=PAGE)
    assert code == 200 and "needsReaction" not in json.loads(body)
    log = json.loads(ddesk.get(f"/api/p/{ddesk.pid}/changes")[2])
    assert [(e["kind"], e.get("detail")) for e in log["build"]] == [
        ("out-of-order", "approve while waiting on plan"),
        ("needs-reaction", "plan changed: not reviewed → revise"),
        ("re-actioned", "approve confirmed"),
        ("out-of-order", "approve while waiting on plan")]
    assert "plan" not in log  # the page's own writes to plan stay unlisted


def test_change_log_says_how_a_re_action_changed_the_decision(pkt):
    before = {"decision": "approve", "needsReaction": ["plan"], "outOfOrder": ["plan"]}
    changes.record_write(pkt, "build", before, {"decision": "drop"}, external=False, at="t1")
    changes.record_write(pkt, "build", {"decision": "drop", "needsReaction": ["plan"]}, {"decision": ""}, external=False, at="t2")
    assert [(e["kind"], e["detail"]) for e in changes.read(pkt)["build"]] == [
        ("re-actioned", "approve → drop"), ("re-actioned", "drop → not reviewed")]


def test_an_agents_revise_clear_flags_items_decided_ahead_of_it(ddesk, pkt):
    ddesk.put_item(ddesk.pid, "plan", {"decision": "revise", "note": "split it"}, headers=PAGE)
    ddesk.put_item(ddesk.pid, "build", {"decision": "approve"}, headers=PAGE)
    code = ddesk.put_item(ddesk.pid, "plan", {"decision": "", "note": "", "ifDecision": "revise", "ifNote": "split it"})[0]
    assert code == 200 and read(pkt)["build"]["needsReaction"] == ["plan"]


@pytest.mark.parametrize("field", ["outOfOrder", "needsReaction"])
def test_clients_cannot_write_the_marks(ddesk, pkt, field):
    ddesk.put_item(ddesk.pid, "build", {"decision": "approve"}, headers=PAGE)
    code, _, body = ddesk.put_item(ddesk.pid, "build", {"note": "x", field: []})
    assert code == 400 and field in json.loads(body)["error"]
    assert read(pkt)["build"]["outOfOrder"] == ["plan"]


# ---- settling: a dependency approved after the items that rely on it ------------------------------------------------

def test_approving_a_pending_dependency_settles_items_decided_ahead_of_it_without_flagging(pkt, capsys):
    decide(pkt, "build", "revise")            # out of order on plan
    decide(pkt, "ship", "drop")               # out of order on build and plan
    rec, flagged = decide(pkt, "plan", "approve")
    assert flagged == [] and "outOfOrder" not in rec
    state = read(pkt)
    assert "needsReaction" not in state["build"] and "needsReaction" not in state["ship"]
    assert "outOfOrder" not in state["build"]  # plan is approved: nothing left open
    assert state["ship"]["outOfOrder"] == ["build"]  # build is still listed, plan is gone
    s = status(capsys, pkt)
    assert s["needsReaction"] == [] and s["items"][1]["outOfOrder"] is False and s["items"][2]["outOfOrder"] is True


@pytest.mark.parametrize("dep_from", ["revise", "drop"])
def test_approving_a_revised_or_dropped_dependency_settles_too(pkt, dep_from):
    decide(pkt, "plan", dep_from)
    decide(pkt, "build", "approve")
    assert decide(pkt, "plan", "approve")[1] == []
    assert set(read(pkt)["build"]) == {"decision", "note", "updatedAt"}


@pytest.mark.parametrize("to", ["drop", "revise", ""])
def test_a_dependency_leaving_approve_flags_items_settled_by_it(pkt, to):
    decide(pkt, "build", "approve")           # out of order
    decide(pkt, "plan", "approve")            # settles build
    rec, flagged = decide(pkt, "plan", to)
    assert flagged == ["build"] and read(pkt)["build"]["needsReaction"] == ["plan"]


def test_re_confirming_a_flagged_approval_settles_its_dependents(pkt):
    decide(pkt, "build", "approve")           # out of order on plan
    decide(pkt, "plan", "revise")             # flags build
    decide(pkt, "ship", "approve")            # out of order on build (flagged) and plan
    assert read(pkt)["ship"]["outOfOrder"] == ["build", "plan"]
    assert decide(pkt, "build", "approve")[1] == []  # build confirmed (same decision): settled again
    ship = read(pkt)["ship"]
    assert ship["outOfOrder"] == ["plan"] and "needsReaction" not in ship


def test_the_agents_revise_clear_then_approval_round_trip(ddesk, pkt):
    ddesk.put_item(ddesk.pid, "plan", {"decision": "approve"}, headers=PAGE)
    ddesk.put_item(ddesk.pid, "build", {"decision": "approve"}, headers=PAGE)  # in order
    ddesk.put_item(ddesk.pid, "plan", {"decision": "revise", "note": "split it"}, headers=PAGE)
    ddesk.put_item(ddesk.pid, "plan", {"decision": "", "note": "", "ifDecision": "revise", "ifNote": "split it"})
    assert json.loads(ddesk.get(f"/api/p/{ddesk.pid}/state")[2])["build"]["needsReaction"] == ["plan"]
    ddesk.put_item(ddesk.pid, "plan", {"decision": "approve"}, headers=PAGE)  # the revised plan approved
    state = json.loads(ddesk.get(f"/api/p/{ddesk.pid}/state")[2])
    assert "needsReaction" not in state["build"] and "needsReaction" not in read(pkt)["build"]
    log = json.loads(ddesk.get(f"/api/p/{ddesk.pid}/changes")[2])
    assert [(e["kind"], e.get("detail")) for e in log["build"]] == [("needs-reaction", "plan changed: approve → revise")]


# ---- the effective flag: stored flags whose dependencies are approved again read as cleared --------------------------

STALE = {  # what earlier versions left behind: plan approved after build and ship, and both flagged for it
    "plan": {"decision": "approve", "note": "", "updatedAt": "2026-01-01T00:00:03.000Z"},
    "build": {"decision": "approve", "note": "", "outOfOrder": ["plan"], "needsReaction": ["plan"],
              "updatedAt": "2026-01-01T00:00:01.000Z"},
    "ship": {"decision": "approve", "note": "", "outOfOrder": ["build", "plan"], "needsReaction": ["plan"],
             "updatedAt": "2026-01-01T00:00:02.000Z"},
}


def test_a_stored_flag_on_an_approved_dependency_reads_as_cleared(pkt, capsys, state_dir):
    _write(pkt, STALE)
    s = status(capsys, pkt)
    assert s["needsReaction"] == []
    assert [(i["needsReaction"], i["changedDependencies"]) for i in s["items"][1:3]] == [(False, []), (False, [])]
    assert waitreport.state(pkt)["items"]["build"]["needsReaction"] == []
    main(["add", str(pkt)])
    capsys.readouterr()
    main(["status", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert rows["packets"][0]["needsReaction"] == [] and rows["packetsWithPending"] == 1  # aside is still pending
    assert read(pkt) == STALE  # derived on read: nothing is written


def test_the_desk_serves_the_effective_flag(ddesk, pkt):
    _write(pkt, STALE)
    state = json.loads(ddesk.get(f"/api/p/{ddesk.pid}/state")[2])
    assert "needsReaction" not in state["build"] and state["build"]["outOfOrder"] == ["plan"]
    # re-deciding an item whose stored flag no longer applies isn't logged as a re-action
    ddesk.put_item(ddesk.pid, "build", {"decision": "approve"}, headers=PAGE)
    assert "build" not in json.loads(ddesk.get(f"/api/p/{ddesk.pid}/changes")[2])


def test_a_dependency_with_a_stale_stored_flag_counts_as_settled(pkt):
    _write(pkt, STALE)
    assert "outOfOrder" not in decide(pkt, "ship", "drop")[0]


def test_a_stale_stored_flag_does_not_hide_the_next_change(pkt):
    _write(pkt, STALE)
    assert decide(pkt, "plan", "drop")[1] == ["build", "ship"]
    assert read(pkt)["ship"]["needsReaction"] == ["plan"]


def test_a_flag_keeps_only_the_dependencies_still_open(pkt, capsys):
    _write(pkt, {"plan": {"decision": "revise"}, "build": {"decision": "approve"},
                 "ship": {"decision": "drop", "needsReaction": ["build", "plan"]}})
    ship = status(capsys, pkt)["items"][2]
    assert ship["needsReaction"] is True and ship["changedDependencies"] == ["plan"]


def test_a_chain_of_flags_settles_together_and_holds_together():
    from review_desk.deps import effective_state
    chain = {"ship": {"decision": "drop", "needsReaction": ["build"]},  # dependents first: one pass isn't enough
             "build": {"decision": "approve", "needsReaction": ["plan"]}, "plan": {"decision": "approve"}}
    eff = effective_state(chain)
    assert "needsReaction" not in eff["build"] and "needsReaction" not in eff["ship"]
    assert chain["build"]["needsReaction"] == ["plan"]  # the input is untouched
    chain["plan"]["decision"] = "revise"
    eff = effective_state(chain)
    assert eff["build"]["needsReaction"] == ["plan"] and eff["ship"]["needsReaction"] == ["build"]
    loop = {"a": {"decision": "approve", "needsReaction": ["b"]}, "b": {"decision": "approve", "needsReaction": ["a"]}}
    assert effective_state(loop) == loop  # a hand-made cycle of flags stays flagged
    odd = {"a": {"decision": "approve", "needsReaction": [1, "plan"]}, "plan": {"decision": "approve"}, "x": 3}
    assert effective_state(odd) == {"a": {"decision": "approve", "needsReaction": [1]}, "plan": {"decision": "approve"}, "x": 3}


def test_wait_wakes_when_a_flag_settles(pkt, capsys, monkeypatch, state_dir):
    decide(pkt, "build", "approve")
    decide(pkt, "plan", "revise")             # build flagged
    before = waitreport.state(pkt)
    sleeps = []

    def sleep(n):
        sleeps.append(n)
        assert len(sleeps) < 10, "never noticed the flag settle"
        if len(sleeps) == 2:
            data = read(pkt)
            data["plan"]["decision"] = "approve"  # as an older desk wrote it: build's stored flag stays
            _write(pkt, data)
    monkeypatch.setattr("review_desk.cli.time.sleep", sleep)
    assert main(["wait", str(pkt), "--start", "1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "CHANGED" and "build: re-action flag cleared" in out
    after = waitreport.state(pkt)
    assert after["items"]["build"]["needsReaction"] == [] and before["items"]["build"]["needsReaction"] == ["plan"]
