"""Item dependencies: an item's `depends_on` lists the items it builds on. The manifest checks the graph (find_cycle).

A dependency is settled when it is approved and not itself waiting for a re-action. Deciding an item (approve, revise
or drop) while any of its dependencies isn't settled is an out-of-order decision: the item's record gets
"outOfOrder": [the dependencies still open]. When a dependency's decision later changes, every item decided out of order
on it gets "needsReaction": [the dependencies that changed]: its decision stays, but it is unconfirmed, and agents treat
it as pending. Deciding the item again (the same decision or another) clears the mark. Both keys are optional, so a
decisions file written before dependencies existed reads the same as before.
"""
from __future__ import annotations


def ids(value) -> list[str]:
    """A stored list of item ids; anything else (absent, or a hand edit) reads as none."""
    return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []


def settled(rec) -> bool:
    return isinstance(rec, dict) and rec.get("decision") == "approve" and not rec.get("needsReaction")


def waiting_on(item_id: str, graph: dict[str, list[str]], state: dict) -> list[str]:
    """The dependencies of item_id that aren't settled, in depends_on order."""
    return [d for d in graph.get(item_id, ()) if not settled(state.get(d))]


def needed_by(graph: dict[str, list[str]]) -> dict[str, list[str]]:
    """{item id: the items that depend on it}, in manifest order."""
    out: dict[str, list[str]] = {i: [] for i in graph}
    for i, ds in graph.items():
        for d in ds:
            out.setdefault(d, []).append(i)
    return out


def record_decision(state: dict, item_id: str, old: dict, rec: dict, graph: dict[str, list[str]]) -> list[str]:
    """Bookkeeping for a write that sets item_id's decision; rec is its new record, state the file as read (called by
    decisions.merge_item under its lock, before the write). Re-deciding re-actions the item: needsReaction goes, and
    outOfOrder is set again from the dependencies still open. If the decision changed, every decided item that was
    decided out of order and depends on item_id is flagged in state. Returns the ids newly flagged."""
    rec.pop("outOfOrder", None)
    rec.pop("needsReaction", None)
    if rec.get("decision"):
        waiting = waiting_on(item_id, graph, state)
        if waiting:
            rec["outOfOrder"] = waiting
    if (old.get("decision") or "") == (rec.get("decision") or ""):
        return []
    flagged = []
    for other in needed_by(graph).get(item_id, []):
        r = state.get(other)
        if isinstance(r, dict) and r.get("decision") and r.get("outOfOrder"):
            causes = ids(r.get("needsReaction"))
            if item_id not in causes:
                r["needsReaction"] = causes + [item_id]
                flagged.append(other)
    return flagged


def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """A dependency cycle as a path that ends where it starts (["a", "b", "a"]), or None. Iterative, so a long chain
    of items can't hit the recursion limit; ids missing from graph count as having no dependencies."""
    state: dict[str, int] = {}  # 1: on the current path, 2: finished
    for root in graph:
        if root in state:
            continue
        state[root] = 1
        path, stack = [root], [iter(graph.get(root, ()))]
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                state[path.pop()] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            elif nxt not in state:
                state[nxt] = 1
                path.append(nxt)
                stack.append(iter(graph.get(nxt, ())))
    return None
