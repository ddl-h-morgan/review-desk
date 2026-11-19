import pytest

from conftest import MANIFEST, make_packet
from review_desk.manifest import ManifestError, load


def test_parses_packet_and_items(packet):
    p = load(packet)
    assert p.title == "Demo packet"
    assert p.intro.startswith("Two drafts")
    assert [(l.label, l.url) for l in p.links] == [("Ticket", "https://example.com/T-1")]
    assert [i.id for i in p.items] == ["first-draft", "second-draft"]
    first, second = p.items
    assert first.kind == "new Task" and first.recommendation == "**File** it"
    assert [(l.label, l.url) for l in first.links] == [("https://example.com/a", "https://example.com/a")]
    assert second.facts == {"Overlaps": "[issue 1](https://example.com/issues/1)"}
    assert p.file_path(second) == (packet / "sub/second.md").resolve()
    assert p.item("second-draft") is second and p.item("nope") is None


def test_missing_manifest(tmp_path):
    with pytest.raises(ManifestError, match="review.toml"):
        load(tmp_path)


def test_bad_toml(tmp_path):
    make_packet(tmp_path, "title = ")
    with pytest.raises(ManifestError, match="TOML"):
        load(tmp_path)


@pytest.mark.parametrize("bad, msg", [
    ('[[items]]\nid="a"\ntitle="x"\n', "title"),                                      # packet title missing
    ('title="t"\n[[items]]\nid="Bad Id"\ntitle="x"\n', "id"),                           # id not a slug
    ('title="t"\n[[items]]\nid="a"\ntitle="x"\n[[items]]\nid="a"\ntitle="y"\n', "duplicate"),
    ('title="t"\n[[items]]\nid="a"\n', "title"),                                        # item title missing
    ('title="t"\n[[items]]\nid="a"\ntitle="x"\nfile="../escape.md"\n', "outside"),      # traversal
    ('title="t"\n[[items]]\nid="a"\ntitle="x"\nfile="/etc/passwd"\n', "relative"),     # absolute
    ('title="t"\n[[items]]\nid="a"\ntitle="x"\nfile="x.html"\n', ".md"),               # wrong type
    ('title="t"\n[[items]]\nid="a"\ntitle="x"\nlinks=[{url="javascript:alert(1)"}]\n', "http"),
    ('title="t"\nitems="nope"\n', "items"),
])
def test_rejects_invalid(tmp_path, bad, msg):
    make_packet(tmp_path, bad, files={})
    with pytest.raises(ManifestError, match=msg):
        load(tmp_path)


def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("secret")
    pkt = make_packet(tmp_path / "p", 'title="t"\n[[items]]\nid="a"\ntitle="x"\nfile="link.md"\n', files={})
    (pkt / "link.md").symlink_to(outside)
    with pytest.raises(ManifestError, match="outside"):
        load(pkt)


def test_missing_file_is_allowed_but_reported(tmp_path):
    p = load(make_packet(tmp_path, MANIFEST, files={}))
    assert p.file_path(p.items[0]) is not None
    assert not p.file_path(p.items[0]).exists()


# ---- depends_on ----------------------------------------------------------------------------------------------------

def _items(*entries: str) -> str:
    return 'title="t"\n' + "".join(f'[[items]]\n{e}\n' for e in entries)


def test_depends_on_is_read_in_order_and_may_point_forward(tmp_path):
    p = load(make_packet(tmp_path, _items('id="a"\ntitle="A"\ndepends_on=["c", "b"]', 'id="b"\ntitle="B"',
                                          'id="c"\ntitle="C"\ndepends_on=["b"]'), files={}))
    assert [i.depends_on for i in p.items] == [["c", "b"], [], ["b"]]
    assert p.dependencies() == {"a": ["c", "b"], "b": [], "c": ["b"]}


@pytest.mark.parametrize("entries, msg", [
    (('id="a"\ntitle="A"\ndepends_on=["nope", "b"]', 'id="b"\ntitle="B"'), r"item 'a': 'depends_on' names no item in this packet: nope$"),
    (('id="a"\ntitle="A"\ndepends_on=["a"]',), r"item 'a': an item can't depend on itself"),
    (('id="a"\ntitle="A"\ndepends_on=["Not A Slug"]',), r"item 'a': 'depends_on' entries are item ids matching"),
    (('id="a"\ntitle="A"\ndepends_on="b"', 'id="b"\ntitle="B"'), r"'depends_on' must be a list of item ids"),
    (('id="a"\ntitle="A"\ndepends_on=[1]',), r"'depends_on' must be a list of item ids"),
    (('id="a"\ntitle="A"\ndepends_on=["b", "b"]', 'id="b"\ntitle="B"'), r"lists an id twice"),
    (('id="a"\ntitle="A"\ndepends_on=["b"]', 'id="b"\ntitle="B"\ndepends_on=["a"]'), r"dependency cycle: a -> b -> a$"),
    (('id="x"\ntitle="X"', 'id="a"\ntitle="A"\ndepends_on=["x", "b"]', 'id="b"\ntitle="B"\ndepends_on=["c"]',
      'id="c"\ntitle="C"\ndepends_on=["a"]'), r"dependency cycle: a -> b -> c -> a$"),
])
def test_depends_on_rejects(tmp_path, entries, msg):
    make_packet(tmp_path, _items(*entries), files={})
    with pytest.raises(ManifestError, match=msg):
        load(tmp_path)


def test_find_cycle_reports_only_the_loop_and_handles_long_chains():
    from review_desk.deps import find_cycle
    assert find_cycle({"a": ["b"], "b": ["c"], "c": ["b"]}) == ["b", "c", "b"]  # "a" leads in but isn't part of it
    assert find_cycle({"a": ["b", "c"], "b": ["c"], "c": []}) is None  # a diamond is not a cycle
    chain = {f"i{n}": [f"i{n + 1}"] for n in range(5000)}
    assert find_cycle(chain) is None
    chain["i5000"] = ["i0"]
    assert len(find_cycle(chain)) == 5002


def test_item_fingerprint_unchanged_without_dependencies_and_follows_them(tmp_path):
    import hashlib
    import json
    from review_desk.changes import item_fingerprint
    a, b = load(make_packet(tmp_path, _items('id="a"\ntitle="A"', 'id="b"\ntitle="B"\ndepends_on=["a"]'), files={})).items
    old_rule = hashlib.sha1(json.dumps({"title": "A", "kind": "", "recommendation": "", "file": None, "links": [],
                                        "facts": {}}, sort_keys=True).encode()).hexdigest()
    assert item_fingerprint(a) == old_rule  # existing packets don't see every item as "edited" after an upgrade
    b2 = load(make_packet(tmp_path, _items('id="a"\ntitle="A"', 'id="b"\ntitle="B"'), files={})).items[1]
    assert item_fingerprint(b) != item_fingerprint(b2)
