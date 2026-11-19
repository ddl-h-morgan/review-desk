"""Item dependencies: an item's `depends_on` lists the items it builds on. The manifest checks the graph (find_cycle).

A dependency is settled when it is approved and not itself waiting for a re-action. Deciding an item (approve, revise
or drop) while any of its dependencies isn't settled is an out-of-order decision: the item's record gets
"outOfOrder": [the dependencies still open].

When a dependency's decision changes, its decided dependents are handled by where it lands:
- approve: what an early decision assumed. The dependency is dropped from its dependents' outOfOrder (and from any
  stored needsReaction); nothing is flagged.
- anything else (revise, drop, or pending, e.g. an agent clearing a revise mark): every decided dependent gets
  "needsReaction": [the dependencies that changed], whether it was decided out of order on it or in order (an item
  decided after its dependency was approved built on that approval, which is now gone).
A flagged item's decision stays, but it is unconfirmed, and agents treat it as pending. Deciding the item again (the
same decision or another) clears the mark.

The stored needsReaction list is history; what counts is the effective flag (effective_state): only the listed
dependencies that are still not settled. A flag whose dependencies have all come back to approved (and settled) reads as
cleared without any write, so files written by earlier versions, which also flagged on a change to approve, read right.
Both keys are optional, so a decisions file written before dependencies existed reads the same as before.
"""
from __future__ import annotations


def ids(value) -> list[str]:
    """A stored list of item ids; anything else (absent, or a hand edit) reads as none."""
    return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []


def settled(rec) -> bool:
    return isinstance(rec, dict) and rec.get("decision") == "approve" and not rec.get("needsReaction")


def effective_state(state: dict) -> dict:
    """A copy of the decisions state with each needsReaction reduced to its effective flag: the listed dependencies that
    are not settled now (judged by their own effective flags, so a chain settles together). A record left with no cause
    loses the key. A non-list value (a hand edit) names no dependency that could settle, so it stays as it is. Every
    reader (status, wait, the page's state, the desk's own bookkeeping) goes through this."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in state.items()}
    lists = [k for k, v in out.items() if isinstance(v, dict) and isinstance(v.get("needsReaction"), list)]
    changed = True
    while changed:  # each pass only removes causes, so this ends; a cycle of flags (hand edits) stays flagged
        changed = False
        for k in lists:
            rec = out[k]
            causes = rec.get("needsReaction", [])
            keep = [d for d in causes if not isinstance(d, str) or not settled(out.get(d))]
            if keep != causes:
                changed = True
                if keep:
                    rec["needsReaction"] = keep
                else:
                    rec.pop("needsReaction")
    return out


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
    outOfOrder is set again from the dependencies still open. Approving settles item_id for its dependents: it leaves
    their outOfOrder and needsReaction lists. Any other change of decision flags every decided dependent in state.
    Returns the ids newly flagged."""
    view = effective_state(state)
    rec.pop("outOfOrder", None)
    rec.pop("needsReaction", None)
    if rec.get("decision"):
        waiting = waiting_on(item_id, graph, view)
        if waiting:
            rec["outOfOrder"] = waiting
    dependents = needed_by(graph).get(item_id, [])
    if rec.get("decision") == "approve":
        for other in dependents:
            r = state.get(other)
            for key in ("outOfOrder", "needsReaction") if isinstance(r, dict) else ():
                if item_id in ids(r.get(key)):
                    left = [x for x in ids(r[key]) if x != item_id]
                    if left:
                        r[key] = left
                    else:
                        del r[key]
        return []
    if (old.get("decision") or "") == (rec.get("decision") or ""):
        return []
    flagged = []
    for other in dependents:
        r = state.get(other)
        if isinstance(r, dict) and r.get("decision"):
            causes = ids(view[other].get("needsReaction"))
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
