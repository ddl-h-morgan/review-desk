"""Item dependencies: an item's `depends_on` lists the items it builds on. The manifest checks the graph (find_cycle).
"""
from __future__ import annotations


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
