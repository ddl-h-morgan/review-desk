"""Draft rendering: GitHub-style markdown (CommonMark plus tables and strikethrough), with raw HTML off and a URL
allowlist on every href and src."""
import re
import subprocess
from html.parser import HTMLParser
from urllib.parse import unquote

import pytest

from review_desk.render import md, md_inline, safe_url


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []  # (tag, {attr: value})

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


def tags(h: str):
    p = _Tags()
    p.feed(h)
    return p.tags


def urls(h: str):
    return [a[k] for _, a in tags(h) for k in ("href", "src") if a.get(k) is not None]


# ---- GitHub-style structure ---------------------------------------------------------------------------------

def test_list_right_after_a_line_of_text_is_a_list():
    """(a) GitHub starts a list without a blank line before it; Python-Markdown made a run-on paragraph."""
    h = md("Merge commits:\n- #396: c06dfbd9\n- #591: 1a2b3c4d")
    assert "<p>Merge commits:</p>" in h
    assert re.search(r"<ul>\s*<li>#396: c06dfbd9</li>\s*<li>#591: 1a2b3c4d</li>\s*</ul>", h), h


def test_ordered_list_right_after_a_line_of_text_is_a_list():
    h = md("Steps:\n1. one\n2. two")
    assert "<p>Steps:</p>" in h and re.search(r"<ol>\s*<li>one</li>\s*<li>two</li>\s*</ol>", h), h


def test_two_space_nested_list_nests():
    """(b) Two-space indentation nests, as on GitHub; Python-Markdown needed four and flattened it."""
    h = md("- a\n  - b\n  - c\n- d")
    assert re.search(r"<li>a\s*<ul>\s*<li>b</li>\s*<li>c</li>\s*</ul>\s*</li>\s*<li>d</li>", h), h


def test_nested_ordered_list():
    h = md("1. one\n2. two\n   1. sub")
    assert re.search(r"<li>two\s*<ol>\s*<li>sub</li>\s*</ol>\s*</li>", h), h


def test_table():
    h = md("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in h and "<th>a</th>" in h and "<td>2</td>" in h


def test_table_is_wrapped_so_css_can_center_it():
    h = md("| a | b |\n|---|---|\n| 1 | 2 |")
    assert h.startswith('<div class="table-wrap"><table>') and h.rstrip().endswith("</table>\n</div>"), h


def test_table_right_after_a_line_of_text():
    h = md("Results:\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in h and "<td>1</td>" in h, h


def test_strikethrough():
    assert re.search(r"<(s|del)>old</\1> new", md("~~old~~ new"))


def test_fenced_code_keeps_text_and_language():
    h = md("```python\nif a < b:\n    print('x')\n```")
    assert '<pre><code class="language-python">' in h
    assert "if a &lt; b:\n    print(" in h


def test_fenced_code_right_after_a_line_of_text():
    h = md("Run:\n```\nmake test\n```")
    assert "<pre><code>make test\n</code></pre>" in h, h


def test_inline_code_escapes():
    assert "<code>&lt;b&gt;x&lt;/b&gt;</code>" in md("use `<b>x</b>` here")


def test_headings():
    h = md("# One\n## Two\ntext")
    assert "<h1>One</h1>" in h and "<h2>Two</h2>" in h and "<p>text</p>" in h


def test_emphasis_and_links():
    h = md("**bold** and _it_ and [a link](https://example.com/x)")
    assert "<strong>bold</strong>" in h and "<em>it</em>" in h
    assert '<a href="https://example.com/x" rel="noopener noreferrer" target="_blank">a link</a>' in h


def test_bare_url_is_linked():
    h = md("see https://example.com/x for details")
    assert '<a href="https://example.com/x" rel="noopener noreferrer" target="_blank">https://example.com/x</a>' in h


def test_relative_fragment_and_mailto_links_kept():
    h = md("[r](other.md) [f](#item-2) [m](mailto:a@example.com) [s](/p/x/)")
    assert urls(h) == ["other.md", "#item-2", "mailto:a@example.com", "/p/x/"]
    # only web links open a new tab
    assert all("target" not in a for t, a in tags(h) if t == "a")


def test_md_inline_has_no_paragraph():
    assert md_inline("**File** it") == "<strong>File</strong> it"
    assert md_inline("") == ""


# ---- security --------------------------------------------------------------------------------------------------

RAW_HTML = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "<iframe src=x></iframe>",
    "text <b onmouseover=alert(1)>bold</b> text",
    "<div>\n\n<script>alert(1)</script>\n\n</div>",
    "<!-- c --><svg onload=alert(1)>",
    "- item <img src=x onerror=alert(1)>",
    "| a |\n|---|\n| <script>x</script> |",
]


@pytest.mark.parametrize("src", RAW_HTML)
def test_raw_html_is_escaped(src):
    for h in (md(src), md_inline(src)):
        assert {t for t, _ in tags(h)} <= {"p", "ul", "li", "div", "table", "thead", "tbody", "tr", "th", "td"}, h
        assert "&lt;" in h


BAD_URLS = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    " javascript:alert(1)",
    "<java\tscript:alert(1)>",
    "<java\nscript:alert(1)>",
    "&#106;avascript:alert(1)",
    "&#x6A;avascript&#58;alert(1)",
    "javascript&colon;alert(1)",
    "java%09script:alert(1)",
    "\x01javascript:alert(1)",
    "vbscript:msgbox(1)",
    "VBScript:msgbox(1)",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "data:image/svg+xml,<svg onload=alert(1)>",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "chrome://settings",
]


def _scheme(u: str):
    cleaned = re.sub(r"[\x00-\x20\x7f]+", "", unquote(u))
    m = re.match(r"([a-zA-Z][a-zA-Z0-9+.\-]*):", cleaned)
    return m and m.group(1).lower()


@pytest.mark.parametrize("bad", BAD_URLS)
@pytest.mark.parametrize("form", ["[x]({u})", "![x]({u})", "[x][r]\n\n[r]: {u}", "<{u}>", "{u}"])
def test_unsafe_urls_never_reach_href_or_src(bad, form):
    src = form.format(u=bad)
    for h in (md(src), md_inline(src)):
        for u in urls(h):
            assert _scheme(u) in (None, "http", "https", "mailto"), (src, h)


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "JAVASCRIPT:alert(1)", "data:text/html,x", "vbscript:x"])
def test_unsafe_link_keeps_its_text_but_loses_the_url(bad):
    h = md(f"[click]({bad}) ![pic]({bad})")
    assert ">click</a>" in h and 'alt="pic"' in h
    assert urls(h) == [] and "script:" not in h.lower() and "data:" not in h


@pytest.mark.parametrize("u", ["https://example.com/a?b=c#d", "http://example.com", "mailto:x@example.com",
                               "other.md", "./a/b.md", "#frag", "/abs/path", "?q=1", "b/c.md"])
def test_safe_url_allows(u):
    assert safe_url(u)


@pytest.mark.parametrize("u", [b.strip("<>") for b in BAD_URLS if "&" not in b])
def test_safe_url_refuses(u):
    assert not safe_url(u)


def test_link_title_is_escaped():
    h = md('[t](https://example.com "a\\"><script>x</script>")')
    assert "<script" not in h and tags(h)[1][1]["title"] == 'a"><script>x</script>'


def test_txt_like_content_in_code_block_is_not_linked():
    h = md("```\nhttps://example.com/x\n```")
    assert "<a " not in h


def test_toolbar_shows_git_sha(monkeypatch):
    from review_desk import render
    monkeypatch.setattr(render, "GIT_SHA", "abc1234")
    assert '<span class="build" title="git commit this desk is running">abc1234</span>' in render._build_tag()
    monkeypatch.setattr(render, "GIT_SHA", "")
    assert render._build_tag() == ""


def _fake_checkout(monkeypatch, tmp_path):
    """Run render._git_sha from a fake review-desk checkout, so the test doesn't depend on this tree having a .git."""
    from review_desk import render
    root = tmp_path / "checkout"
    (root / "src" / "review_desk").mkdir(parents=True, exist_ok=True)
    (root / "bin").mkdir(exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    (root / "bin" / "review-desk").write_text("")
    monkeypatch.setattr(render, "__file__", str(root / "src" / "review_desk" / "render.py"))


def _stub_git(monkeypatch, result):
    from review_desk import render
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if isinstance(result, BaseException):
            raise result
        return subprocess.CompletedProcess(cmd, 0, stdout=result)
    monkeypatch.setattr(render.subprocess, "run", run)
    return render, calls


def test_git_sha_reads_short_sha_without_inherited_git_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    _fake_checkout(monkeypatch, tmp_path)
    render, calls = _stub_git(monkeypatch, "abc1234\n")
    assert render._git_sha() == "abc1234"
    cmd, kw = calls[0]
    assert cmd[:2] == ["git", "-C"] and "GIT_DIR" not in kw["env"]


def test_git_sha_empty_on_junk_or_failure(monkeypatch, tmp_path):
    _fake_checkout(monkeypatch, tmp_path)
    for result in ("not a sha\n", "", OSError("no git"), subprocess.TimeoutExpired("git", 5)):
        render, _ = _stub_git(monkeypatch, result)
        assert render._git_sha() == ""


def test_git_sha_empty_when_not_the_checkout(monkeypatch, tmp_path):
    from review_desk import render
    pkg = tmp_path / "site-packages" / "review_desk"
    pkg.mkdir(parents=True)
    (tmp_path / ".git").mkdir()  # an enclosing repository that is not review-desk
    monkeypatch.setattr(render, "__file__", str(pkg / "render.py"))
    _, calls = _stub_git(monkeypatch, "abc1234\n")
    assert render._git_sha() == "" and calls == []


# ---- links back to earlier content ---------------------------------------------------------------------------

LINKING = '''
title = "Links"

[[items]]
id = "c1-policy-routing"
title = "C-1"
kind = "x"
file = "c1.md"

[[items]]
id = "c2-follow-up"
title = "C-2"
kind = "x"
recommendation = "Depends on [C-1](#c1-policy-routing)"
file = "c2.md"
[items.facts]
Blocked-by = "[C-1](#c1-policy-routing), and [the other desk](/p/other-pkt-abc123/#c9-thing)"
'''


def _linking_page(tmp_path):
    from conftest import make_packet
    from review_desk import manifest, render
    root = make_packet(tmp_path / "pkt", LINKING, {"c1.md": "# C-1\n\nbody",
                                                  "c2.md": "See [C-1](#c1-policy-routing) first."})
    return render.packet_page("pkt-abc123", manifest.load(root))


def test_item_links_survive_in_recommendation_facts_and_drafts(tmp_path):
    page = _linking_page(tmp_path)
    links = [a for t, a in tags(page) if t == "a" and a.get("href") == "#c1-policy-routing"]
    assert len(links) == 3, page  # recommendation, fact, draft
    assert all("target" not in a for a in links)  # same page: no new tab
    assert 'id="c1-policy-routing"' in page  # the card they land on


def test_cross_packet_links_survive_and_stay_in_this_tab(tmp_path):
    page = _linking_page(tmp_path)
    a = next(a for t, a in tags(page) if t == "a" and a.get("href", "").startswith("/p/other"))
    assert a["href"] == "/p/other-pkt-abc123/#c9-thing" and "target" not in a
    h = md("[x](/p/other-pkt-abc123/#c9-thing/step-2) [y](/p/other-pkt-abc123/)")
    assert urls(h) == ["/p/other-pkt-abc123/#c9-thing/step-2", "/p/other-pkt-abc123/"]


@pytest.mark.parametrize("bad", ["javascript:/p/x/#a", "javascript://p/x/%0aalert(1)", "data:/p/x/", "vbscript:/p/x/"])
def test_packet_path_shaped_urls_with_a_bad_scheme_are_still_refused(bad):
    for h in (md(f"[x]({bad})"), md_inline(f"[x]({bad})")):
        assert urls(h) == [], h


# ---- heading ids in drafts -----------------------------------------------------------------------------------

def _heading_ids(h: str):
    return [a.get("id") for t, a in tags(h) if re.fullmatch(r"h[1-6]", t)]


@pytest.mark.parametrize("text, slug", [
    ("One", "one"),
    ("D1.4 Plan-mode tests", "d1-4-plan-mode-tests"),
    ("  Step   2: do it!  ", "step-2-do-it"),
    ("`code` **bold** [link](https://example.com/zz)", "code-bold-link"),  # the text, not the markdown or the URL
    ("Ünïcode café", "unicode-cafe"),
    ("日本語", "section"),
    ("---", "section"),
    ("x" * 100, "x" * 60),
])
def test_heading_slug_rule(text, slug):
    assert _heading_ids(md(f"## {text}", heading_prefix="plan")) == [f"plan/{slug}"]


def test_headings_get_item_prefixed_ids_only_in_drafts():
    h = md("# One\n## Two & three\ntext", heading_prefix="plan-draft")
    assert _heading_ids(h) == ["plan-draft/one", "plan-draft/two-three"]
    assert _heading_ids(md("# One\n## Two")) == [None, None]  # intro, appendix, inline fields: no ids
    assert _heading_ids(md_inline("# One")) == []


def test_repeated_headings_get_a_number():
    h = md("## A\n## A\n## A 2\n## A", heading_prefix="p")
    assert _heading_ids(h) == ["p/a", "p/a-2", "p/a-2-2", "p/a-3"]


def test_setext_headings_get_ids_too():
    assert _heading_ids(md("Title\n=====\n\nSub\n---", heading_prefix="p")) == ["p/title", "p/sub"]


@pytest.mark.parametrize("text", ['"><script>alert(1)</script>', "x\" onmouseover=\"alert(1)", "a' b=c",
                                  "&quot; onload=&quot;x", "<img src=x onerror=alert(1)>", "%22%3E", "‮\u0000x"])
def test_heading_ids_are_plain_slugs(text):
    h = md(f"## {text}", heading_prefix="p")
    ids = _heading_ids(h)
    assert len(ids) == 1 and re.fullmatch(r"p/[a-z0-9]+(-[a-z0-9]+)*", ids[0]), h
    assert "<script" not in h and "onmouseover=" not in h.split(">", 1)[0]


def test_heading_has_a_permalink_to_itself():
    h = md("## Step 2", heading_prefix="plan")
    a = [a for t, a in tags(h) if t == "a"]
    assert a == [{"class": "heading-link", "href": "#plan/step-2", "aria-label": "Link to this section"}], h


def test_heading_ids_are_unique_across_the_page_and_never_clash_with_cards(tmp_path):
    from conftest import make_packet
    from review_desk import manifest, render
    toml = LINKING.replace('id = "c2-follow-up"', 'id = "c1-policy-routing--note"')
    root = make_packet(tmp_path / "pkt", toml, {"c1.md": "# Note\n## Note\n# Bar\n# Help title",
                                               "c2.md": "# Note\n# Conn"})
    page = render.packet_page("pkt-abc123", manifest.load(root))
    ids = [a["id"] for _, a in tags(page) if "id" in a]
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)
    heads = _heading_ids(page.split('<main class="wrap">', 1)[1])
    assert "c1-policy-routing/note" in heads and "c1-policy-routing/note-2" in heads
    assert "c1-policy-routing--note/note" in heads
