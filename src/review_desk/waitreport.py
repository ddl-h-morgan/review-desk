"""What `review-desk wait` compares and reports: a packet's state at one moment, and the difference between two moments.

The state is read straight from the packet's files (not from review-changes.json, which only a running desk fills in):
per item, its decision, note and updatedAt, a fingerprint of its manifest entry, and its draft file's content hash and
last-edit time. Items are keyed by id, so reordering the manifest is no change. Read-only.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import decisions as dec
from .changes import MAX_HASHED, _sha, item_fingerprint, iso_from_epoch
from .fsutil import read_regular
from .manifest import load


def _draft(path: Path | None):
    """(content fingerprint, last-edit time) of a draft file; (None, None) when there is none."""
    if path is None:
        return None, None
    try:
        st = os.stat(path)
        fp = f"stat:{st.st_size}:{st.st_mtime_ns}" if st.st_size > MAX_HASHED else _sha(read_regular(path))
        return fp, iso_from_epoch(st.st_mtime)
    except OSError:
        return None, None


def state(directory) -> dict:
    """{"order": [item ids], "items": {id: {decision, note, updatedAt, manifest, draft, draftEdited}}}.
    Raises ManifestError or DecisionsError."""
    p = load(directory)
    recs = dec.read(p.dir)
    items = {}
    for it in p.items:
        rec = recs.get(it.id) or {}
        draft, edited = _draft(p.file_path(it))
        items[it.id] = {"decision": rec.get("decision") or None, "note": rec.get("note") or "",
                        "updatedAt": rec.get("updatedAt"), "manifest": item_fingerprint(it), "draft": draft,
                        "draftEdited": edited}
    return {"order": [it.id for it in p.items], "items": items}


def key(s: dict, edits: bool):
    """What counts as a change: decisions and notes (and items added or removed); with edits, manifest and draft edits too."""
    fields = ("decision", "note", "manifest", "draft") if edits else ("decision", "note")
    return {i: tuple(v[f] for f in fields) for i, v in s["items"].items()}


def snapshot(s: dict) -> list:
    """The compact per-item list `wait` has always printed: [id, decision, note], in manifest order."""
    return [[i, s["items"][i]["decision"], s["items"][i]["note"]] for i in s["order"]]


def _label(decision) -> str:
    return decision or "pending"


def _quote(note: str) -> str:
    return " / ".join(ln for ln in note.splitlines() if ln.strip()) or note


def report(old: dict, new: dict) -> list[str]:
    """Human-readable lines for what differs between two states. Decision and note changes, items added or removed,
    and (labelled as edits, never as decisions) manifest-entry and draft-file edits. Each decided item that changed
    also says when it was decided and when its draft was last edited, so 'the decision postdates the draft' is visible."""
    lines = []
    for i in new["order"]:
        n, o = new["items"][i], old["items"].get(i)
        if o is None:
            lines.append(f"{i}: item added ({_label(n['decision'])})" + (f", note: {_quote(n['note'])}" if n["note"] else ""))
            continue
        mine = []
        if n["decision"] != o["decision"]:
            mine.append(f"decision {_label(o['decision'])} -> {_label(n['decision'])}")
        if n["note"] != o["note"]:
            if not n["note"].strip():
                mine.append("note cleared")
            else:
                mine.append(f"note {'changed' if o['note'].strip() else 'added'}: {_quote(n['note'])}")
        if n["manifest"] != o["manifest"]:
            mine.append("EDIT manifest entry changed (not a decision)")
        if n["draft"] != o["draft"]:
            mine.append("EDIT draft file changed (not a decision)")
        if not mine:
            continue
        lines += [f"{i}: {m}" for m in mine]
        if n["decision"]:
            when = f"{i}:   decided {n['updatedAt'] or 'unknown'}; draft last edited {n['draftEdited'] or 'n/a'}"
            if n["updatedAt"] and n["draftEdited"]:
                when += " (decision postdates the draft)" if n["updatedAt"] >= n["draftEdited"] \
                    else " (DRAFT EDITED AFTER THE DECISION)"
            lines.append(when)
    lines += [f"{i}: item removed" for i in old["order"] if i not in new["items"]]
    return lines
