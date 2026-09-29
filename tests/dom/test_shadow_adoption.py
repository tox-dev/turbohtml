from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Final

import pytest

from turbohtml import DocumentFragment, Element, ShadowRoot, Text, parse, parse_xml


@pytest.mark.parametrize("mode", ["open", "closed"])
@pytest.mark.parametrize("depth", [1, 80])
def test_shadow_adoption_preserves_aliases(mode: str, depth: int) -> None:
    host: Final = Element("div", children=[Element("span"), Text("light")])
    children: Final = host.children
    roots: Final[list[tuple[Element, ShadowRoot, Element, int]]] = []
    current = host
    for _ in range(depth):
        root = current.attach_shadow(mode=mode)
        root.set_inner_html("<section><slot></slot></section>")
        child = root.find("section")
        assert child is not None
        roots.append((current, root, child, hash(root)))
        current = child
    target: Final = Element("main")
    target.append(host)
    assert host.children == children
    for owner, root, child, saved_hash in roots:
        assert (root.host, root.children, hash(root)) == (owner, (child,), saved_hash)
        assert owner.shadow_root == (root if mode == "open" else None)


@pytest.mark.parametrize("method", ["assigned_nodes", "assigned_elements", "flattened_children"])
def test_shadow_results_during_adoption(method: str) -> None:
    host: Final = Element("div")
    for index in range(32):
        host.append(Element("span", children=[Text(str(index))]))
        host.append(Text(" "))
    expected: Final = [node for node in host.children if method != "assigned_elements" or isinstance(node, Element)]
    root: Final = host.attach_shadow()
    root.set_inner_html("<slot></slot>")
    slot: Final = root.find("slot")
    assert slot is not None
    targets: Final = (Element("main"), Element("section"))
    start: Final = Barrier(2)
    collect: Final = slot.assigned_elements if method == "assigned_elements" else slot.assigned_nodes

    def read() -> None:
        start.wait()
        for _ in range(200):
            assert (slot.flattened_children if method == "flattened_children" else collect()) == expected

    with ThreadPoolExecutor(max_workers=1) as pool:
        reader: Final = pool.submit(read)
        start.wait()
        for index in range(200):
            targets[index % 2].append(host)
        reader.result()
    assert host.parent == targets[1]


@pytest.mark.parametrize("markup", ["", "<i>one</i><b>two</b>"])
@pytest.mark.parametrize("xml", [False, True])
def test_shadow_adoption_preserves_content(markup: str, *, xml: bool) -> None:
    host: Final = Element("div")
    root: Final = host.attach_shadow()
    root.set_inner_html(markup)
    children: Final = root.children
    target: Final = parse_xml("<main/>").find("main") if xml else Element("main")
    assert target is not None
    target.append(host)
    assert (root.inner_html, root.children, root.host) == (markup, children, host)


@pytest.mark.parametrize("shadow", [False, True])
def test_fragment_adopts_shadow_hosts(*, shadow: bool) -> None:
    fragment: Final = Element("section").attach_shadow() if shadow else DocumentFragment()
    host: Final = Element("div", children=[Element("span")])
    fragment.append(host)
    root: Final = host.attach_shadow()
    root.set_inner_html("<slot></slot>")
    slot: Final = root.find("slot")
    assert slot is not None
    child: Final = host.children[0]
    target: Final = Element("main")
    target.append(fragment)
    assert (target.children, host.shadow_root, root.host, slot.assigned_nodes()) == ((host,), root, host, [child])


def test_shadow_adoption_preserves_declarative_flags() -> None:
    document: Final = parse(
        "<div><template shadowrootmode=open shadowrootdelegatesfocus shadowrootclonable>x</template></div>"
    )
    host: Final = document.find("div")
    assert host is not None
    root: Final = host.shadow_root
    assert root is not None
    Element("main").append(host)
    assert (root.mode, root.delegates_focus, root.clonable, root.text) == ("open", True, True, "x")
