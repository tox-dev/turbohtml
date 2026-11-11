from __future__ import annotations

import threading
from typing import Final

import turbohtml
from turbohtml._html import _tree_verify


def test_concurrent_extends_with_one_moving_foreign_node_keep_both_trees_intact() -> None:
    child: Final = turbohtml.Element("i")
    home: Final = turbohtml.Element("aside", children=[child])
    parents: Final = [turbohtml.Element("p") for _ in range(4)]
    root: Final = turbohtml.Element("main", children=parents)
    start: Final = threading.Barrier(len(parents) + 1)

    def extender(parent: turbohtml.Element) -> None:
        start.wait()
        for _ in range(2_000):
            # importing child resumes root's lock afterwards, and another extender holding it can link the copy first
            parent.extend([child])

    def returner() -> None:
        start.wait()
        for _ in range(2_000):
            home.append(child)

    threads = [threading.Thread(target=extender, args=(parent,)) for parent in parents]
    threads.append(threading.Thread(target=returner))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert (_tree_verify(root), _tree_verify(home)) == ((0, 0, 0, 0), (0, 0, 0, 0))
