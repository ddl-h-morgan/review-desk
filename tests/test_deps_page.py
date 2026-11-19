"""Item dependencies on the page: the card markup (render.py) and page.js's dependency block, run in node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import make_packet
from test_deps import DEPS_MANIFEST, ddesk, decide, pkt  # noqa: F401 (fixtures)
from review_desk import render
from review_desk.manifest import load

PAGE_JS = Path(__file__).resolve().parent.parent / "src" / "review_desk" / "static" / "page.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node isn't installed")


def _card(html: str, item: str) -> str:
    start = html.index(f'<article class="card" data-id="{item}"')
    return html[start:html.index("</article>", start)]


# ---- markup --------------------------------------------------------------------------------------------------------

def test_cards_link_their_dependencies_and_dependents(pkt):
    html = render.packet_page("pid", load(pkt))
    ship = _card(html, "ship")
    assert 'data-depends-on="build plan"' in ship
    assert ('<dt>Depends on</dt><dd><a href="#build">Build</a> <span class="dep-state" data-dep-state="build"></span>, '
            '<a href="#plan">Plan</a> <span class="dep-state" data-dep-state="plan"></span></dd>') in ship
    plan = _card(html, "plan")
    assert '<dt>Needed by</dt><dd><a href="#build">Build</a>' in plan and '<a href="#ship">Ship</a>' in plan
    assert "data-depends-on" not in plan and "Depends on" not in plan


def test_only_cards_with_dependencies_get_the_badge_re_action_box_and_confirm(pkt):
    html = render.packet_page("pid", load(pkt))
    for item in ("build", "ship"):
        card = _card(html, item)
        assert card.count("data-waiting hidden") == card.count("data-reaction hidden") == card.count("data-dep-confirm hidden") == 1
        assert f'<button type="button" data-dep-proceed aria-describedby="{item}-dep-confirm"></button>' in card
        assert f'<p id="{item}-dep-confirm" data-dep-confirm-text></p>' in card
        assert card.index("data-reaction") < card.index('class="decide"') < card.index("data-dep-confirm")
    for item in ("plan", "aside"):
        card = _card(html, item)
        assert "data-waiting" not in card and "data-reaction" not in card and "data-dep-confirm" not in card


def test_dependency_link_text_is_the_plain_escaped_title(tmp_path):
    p = make_packet(tmp_path, 'title="t"\n[[items]]\nid="a"\ntitle="**Bold** [x](https://example.com) <img src=x>"\n'
                              '[[items]]\nid="b"\ntitle="B"\ndepends_on=["a"]\n', files={})
    card = _card(render.packet_page("pid", load(p)), "b")
    assert '<a href="#a">Bold x &lt;img src=x&gt;</a>' in card  # no nested link, no markup, still escaped


def test_dependencies_are_part_of_the_layout_version(tmp_path):
    p = make_packet(tmp_path, 'title="t"\n[[items]]\nid="a"\ntitle="A"\n[[items]]\nid="b"\ntitle="B"\n', files={})
    before = render.layout_version("pid", load(p))
    (p / "review.toml").write_text('title="t"\n[[items]]\nid="a"\ntitle="A"\n[[items]]\nid="b"\ntitle="B"\ndepends_on=["a"]\n')
    assert render.layout_version("pid", load(p)) != before


def test_bar_counts_items_to_re_action_and_help_explains_it(pkt):
    html = render.packet_page("pid", load(pkt))
    assert '<span class="pill revise" id="n-reaction" hidden>0 to re-action</span>' in html
    assert "needs re-action" in html.split('id="help-dialog"', 1)[1]


def test_index_keeps_a_packet_with_items_to_re_action_in_view():
    row = {"id": "p", "path": "/x", "title": "T", "lastTouched": None, "needsReaction": ["build"],
           "counts": {"approve": 4, "revise": 0, "drop": 0, "pending": 0, "total": 4}}
    html = render.index_page([row])
    assert '<li class="packet">' in html and "1 to re-action" in html and "done-toggle" not in html
    assert '<li class="packet done">' in render.index_page([{**row, "needsReaction": []}])


def test_page_styles_keep_a_flagged_card_in_view_and_compare_layouts_without_states(ddesk):
    css = ddesk.get("/static/page.css")[2].decode()
    assert "body.hide-reviewed .card.needs-reaction[data-decision] { display: grid; }" in css
    js = ddesk.get("/static/page.js")[2].decode()
    assert 'for (const slot of copy?.querySelectorAll("[data-dep-state]") ?? []) slot.textContent = "";' in js
    assert 'const face = c => ["h2", ".kind", ".facts"].map(sel => bare(c.querySelector(sel))).join("\\u0000");' in js
    assert "listen(b, \"click\", () => decide(card, b.dataset.decision));" in js and "bindDeps(card);" in js
    labels = js[js.index("const CHANGE_LABEL = {"):]
    labels = labels[:labels.index("};")]
    from review_desk.changes import KINDS
    for kind in KINDS:  # every kind the desk logs has a label on the card, dependency events included
        assert f'"{kind}":' in labels or f" {kind}:" in labels, kind
    assert '"out-of-order": "Decided out of order", "needs-reaction": "Needs re-action", "re-actioned": "Re-actioned"' in labels


def test_render_marks_flagged_cards_and_counts_them(ddesk):
    """Pins how render() uses the dependency block (render() itself needs the whole page, so it isn't run here)."""
    js = ddesk.get("/static/page.js")[2].decode()
    loop = js[js.index("  function render() {"):js.index("  function setConn(")]
    for line in ('card.classList.toggle("needs-reaction", again);', "if (again) counts.reaction++;",
                 'card.querySelector("[data-status]").textContent = (label[d] ?? d) + (again ? " · needs re-action" : "");',
                 "renderDeps(card, s);", 'reaction.textContent = counts.reaction + " to re-action";',
                 "reaction.hidden = counts.reaction === 0;"):
        assert line in loop, line


def test_page_shows_the_flag_from_the_desk(ddesk, pkt):
    decide(pkt, "build", "approve")
    decide(pkt, "plan", "revise")
    state = json.loads(ddesk.get(f"/api/p/{ddesk.pid}/state")[2])
    assert state["build"]["needsReaction"] == ["plan"] and state["build"]["outOfOrder"] == ["plan"]


# ---- page.js's dependency block, against a small fake DOM ------------------------------------------------------------

HARNESS = r"""
const t = require('fs').readFileSync(%s, 'utf8');
const a = t.indexOf("  // Dependencies: a card's data-depends-on"), b = t.indexOf("  // Floating note:");
let focused = null;
const el = (extra = {}) => ({ hidden: true, textContent: "", dataset: {}, on: {}, focus() { focused = this; }, ...extra });
function mkCard(id, title, deps) {
  const p = { waiting: el({ name: "waiting" }), text: el(), again: el({ name: "reconfirm" }), askText: el(),
    go: el({ name: "proceed" }), cancel: el({ name: "cancel" }), h2: el({ textContent: title }),
    slots: deps.map(d => el({ dataset: { depState: d }, hidden: false })),
    buttons: ["approve", "revise", "drop", ""].map(d => el({ name: "button:" + d, dataset: { decision: d } })) };
  p.box = el({ querySelector: s => ({ "[data-reaction-text]": p.text, "[data-reconfirm]": p.again })[s] });
  p.ask = el({ querySelector: s => ({ "[data-dep-confirm-text]": p.askText, "[data-dep-proceed]": p.go, "[data-dep-cancel]": p.cancel })[s] });
  const has = deps.length > 0;
  return { p, dataset: has ? { id, dependsOn: deps.join(" ") } : { id },
    querySelector: s => ({ h2: p.h2, "[data-waiting]": has && p.waiting, "[data-reaction]": has && p.box,
      "[data-dep-confirm]": has && p.ask, "[data-reconfirm]": has && p.again })[s] || null,
    querySelectorAll: s => s === "[data-dep-state]" ? p.slots : s === "button[data-decision]" ? p.buttons : [] };
}
const cards = [mkCard("plan", "Plan", []), mkCard("build", "Build", ["plan"]), mkCard("ship", "Ship", ["build", "plan"])];
const card = id => cards.find(c => c.dataset.id === id);
const label = { approve: "Approved", revise: "Revise", drop: "Dropped", "": "Not reviewed" };
const puts = [];
const put = (id, patch) => puts.push([id, patch]);
const listen = (target, type, fn) => { target.on[type] = fn; };
const state = %s;
const api = new Function("cards", "state", "label", "put", "listen",
  t.slice(a, b) + "; return { renderDeps, decide, bindDeps, flagged };")(cards, state, label, put, listen);
cards.forEach(api.bindDeps);
const out = {};
%s
console.log(JSON.stringify(out));
"""


def _page(state: dict, script: str) -> dict:
    prog = HARNESS % (json.dumps(str(PAGE_JS)), json.dumps(state), script)
    r = subprocess.run([shutil.which("node"), "-e", prog], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@needs_node
@pytest.mark.parametrize("decision, verb", [("approve", "Approve"), ("revise", "Mark for revision"), ("drop", "Drop")])
def test_deciding_ahead_of_an_open_dependency_asks_first(decision, verb):
    got = _page({"plan": {"decision": "revise"}}, f"""
      api.decide(card("ship"), "{decision}");
      const p = card("ship").p;
      out.before = {{ puts: puts.length, hidden: p.ask.hidden, text: p.askText.textContent, go: p.go.textContent, focus: focused.name }};
      p.go.on.click();
      out.after = {{ puts, hidden: p.ask.hidden, focus: focused.name }};""")
    assert got["before"] == {
        "puts": 0, "hidden": False, "go": f"{verb} anyway", "focus": "proceed",
        "text": f"Not approved yet: Build (Not reviewed), Plan (Revise). {verb} this anyway? It is saved as decided out "
                "of order, and flagged for you to look at again if any of them is then marked for revision, dropped or "
                "cleared instead of approved."}
    assert got["after"] == {"puts": [["ship", {"decision": decision}]], "hidden": True, "focus": f"button:{decision}"}


@needs_node
@pytest.mark.parametrize("how", ["p.cancel.on.click()", 'p.ask.on.keydown({ key: "Escape" })'])
def test_cancelling_the_confirm_saves_nothing_and_returns_focus(how):
    got = _page({}, f"""
      const p = card("build").p;
      api.decide(card("build"), "approve");
      out.text = p.askText.textContent;
      {how};
      out.r = {{ puts, hidden: p.ask.hidden, focus: focused.name }};""")
    assert got["text"].endswith("flagged for you to look at again if that item is then marked for revision, dropped "
                                "or cleared instead of approved.")
    assert got["r"] == {"puts": [], "hidden": True, "focus": "button:approve"}


@needs_node
def test_other_keys_keep_the_confirm_open():
    got = _page({}, """
      const p = card("build").p;
      api.decide(card("build"), "approve");
      p.ask.on.keydown({ key: "Enter" });
      out.hidden = p.ask.hidden;""")
    assert got["hidden"] is False


@needs_node
@pytest.mark.parametrize("state, item, decision", [
    ({}, "build", ""),                                                        # Clear never asks
    ({}, "plan", "approve"),                                                  # no dependencies
    ({"plan": {"decision": "approve"}}, "build", "drop"),                     # all settled
    ({"plan": {"decision": "approve", "needsReaction": []}}, "build", "approve"),  # an empty flag isn't a flag
])
def test_deciding_in_order_saves_at_once(state, item, decision):
    got = _page(state, f"""
      card("{item}").p.ask.hidden = false;  // a confirm left open from an earlier click goes away
      api.decide(card("{item}"), "{decision}");
      out.puts = puts; out.hidden = card("{item}").p.ask.hidden;""")
    assert got["puts"] == [[item, {"decision": decision}]]
    assert got["hidden"] is (item != "plan")  # plan has no confirm at all (its fake one is never touched)


@needs_node
def test_a_flagged_dependency_counts_as_open():
    got = _page({"plan": {"decision": "approve", "needsReaction": ["x"]}}, """
      api.decide(card("build"), "approve");
      out.puts = puts.length; out.text = card("build").p.askText.textContent;""")
    assert got["puts"] == 0 and got["text"].startswith("Not approved yet: Plan (Approved, needs re-action).")


@needs_node
def test_cards_show_dependency_states_and_what_they_wait_on():
    got = _page({"plan": {"decision": "approve"}, "build": {"decision": "approve", "needsReaction": ["plan"]}}, """
      for (const c of cards) api.renderDeps(c, state[c.dataset.id] || {});
      const p = card("ship").p;
      out.slots = p.slots.map(s => s.textContent);
      out.waiting = [p.waiting.hidden, p.waiting.textContent];
      out.buildWaiting = card("build").p.waiting.hidden;""")
    assert got["slots"] == ["(Approved, needs re-action)", "(Approved)"]
    assert got["waiting"] == [False, "Waiting on: Build"]
    assert got["buildWaiting"] is True  # plan is approved: nothing to wait on


@needs_node
def test_a_flagged_card_explains_and_confirms_in_one_click():
    got = _page({"plan": {"decision": "revise"}, "build": {"decision": "drop", "needsReaction": ["plan"]}}, """
      const p = card("build").p;
      api.renderDeps(card("build"), state.build);
      out.box = [p.box.hidden, p.text.textContent, p.again.textContent];
      p.again.on.click();
      out.puts = puts; out.focus = focused.name;
      api.renderDeps(card("build"), { decision: "drop" });
      out.after = p.box.hidden;""")
    assert got["box"] == [False, 'Needs re-action: Plan changed after you decided this item and isn\'t approved now. '
                                 '"Dropped" is kept but unconfirmed: agents treat the item as pending until you confirm '
                                 "it or decide again.",
                          'Confirm "Dropped"']
    assert got["puts"] == [["build", {"decision": "drop"}]] and got["focus"] == "button:drop"
    assert got["after"] is True


@needs_node
def test_a_card_flagged_by_two_dependencies_names_both():
    got = _page({"plan": {"decision": "drop"}, "build": {"decision": "revise"},
                 "ship": {"decision": "approve", "needsReaction": ["build", "plan"]}}, """
      api.renderDeps(card("ship"), state.ship);
      out.text = card("ship").p.text.textContent;""")
    assert got["text"].startswith("Needs re-action: Build, Plan changed after you decided this item and aren't approved now.")


@needs_node
@pytest.mark.parametrize("rec, want", [
    ({"decision": "approve", "needsReaction": ["a"]}, True), ({"decision": "approve", "needsReaction": True}, True),
    ({"decision": "approve", "needsReaction": []}, False), ({"decision": "approve"}, False),
    ({"decision": "", "needsReaction": ["a"]}, False),
])
def test_flag_rule_matches_status(rec, want):
    assert _page({}, f"out.f = api.flagged({json.dumps(rec)});")["f"] is want


@needs_node
def test_a_cleared_card_with_a_leftover_flag_shows_no_box():
    got = _page({}, """
      api.renderDeps(card("build"), { decision: "", needsReaction: ["plan"] });
      out.hidden = card("build").p.box.hidden;""")
    assert got["hidden"] is True
