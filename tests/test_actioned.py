"""`actioned`: an item that was dealt with outside the packet (e.g. a review submitted on GitHub). It stays in the
manifest (nothing is removed), shows the reason, and hides with "Hide reviewed" like approved and dropped cards."""
from review_desk import manifest, render, summary
from conftest import make_packet

M = '''
title = "P"
[[items]]
id = "done-pr"
title = "Done PR"
actioned = "review submitted on GitHub (APPROVED) 2026-11-19"
[[items]]
id = "open-pr"
title = "Open PR"
'''


def test_manifest_parses_actioned(tmp_path):
    p = manifest.load(make_packet(tmp_path, M, files={}))
    assert [i.actioned for i in p.items] == ["review submitted on GitHub (APPROVED) 2026-11-19", ""]


def test_card_marks_and_shows_actioned(tmp_path):
    p = manifest.load(make_packet(tmp_path, M, files={}))
    page = render.packet_page("pid", p)
    done = page[page.index('id="done-pr"') - 200: page.index('id="open-pr"')]
    assert 'data-actioned="1"' in done and "review submitted on GitHub" in done
    assert 'data-actioned' not in page[page.index('id="open-pr"') - 200:]


def test_hide_reviewed_hides_actioned_cards():
    css = (render.STATIC / "page.css").read_text() if hasattr(render, "STATIC") else open(render.__file__.replace("render.py", "static/page.css")).read()
    assert 'body.hide-reviewed .card:not(.just-decided)[data-actioned="1"]' in css


def test_summary_counts_actioned_and_keeps_it_out_of_undecided(tmp_path):
    p = manifest.load(make_packet(tmp_path, M, files={}))
    s = summary.summarize(p, {})
    assert s["counts"]["actioned"] == 1 and s["counts"]["pending"] == 1 and s["undecided"] == ["open-pr"]
    assert [i["actioned"] for i in s["items"]] == ["review submitted on GitHub (APPROVED) 2026-11-19", ""]


def test_flagged_card_stays_visible_even_if_actioned():
    """needs-reaction must win over actioned under "Hide reviewed": equal specificity, so its show rule must come later."""
    css = open(render.__file__.replace("render.py", "static/page.css")).read()
    assert css.index('[data-actioned="1"] { display: none; }') < css.index("body.hide-reviewed .card.needs-reaction[data-decision] { display: grid; }")


def test_flagged_actioned_item_still_needs_reaction(tmp_path):
    p = manifest.load(make_packet(tmp_path, M, files={}))
    s = summary.summarize(p, {"done-pr": {"decision": "approve", "needsReaction": ["open-pr"], "updatedAt": "2026-11-19T00:00:00Z"}})
    assert s["needsReaction"] == ["done-pr"] and s["counts"]["approve"] == 1 and s["counts"]["actioned"] == 0
