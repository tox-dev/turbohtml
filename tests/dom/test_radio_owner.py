from __future__ import annotations

from typing import Final

import pytest

from turbohtml import Element, parse


@pytest.mark.parametrize(
    ("markup", "same_owner"),
    [
        pytest.param("<form><input></form><input>", False, id="outside-form"),
        pytest.param(
            "<form><svg><form><foreignObject><input></foreignObject></form></svg><input></form>",
            True,
            id="foreign-form-descendant",
        ),
        pytest.param("<form><input></form><form><input></form>", False, id="separate-forms"),
        pytest.param("<form id=f><input></form><input form=f>", True, id="external-owner"),
        pytest.param("<form id=f><input></form><input form=F>", False, id="case-sensitive-owner"),
        pytest.param("<form id=é水😀><input></form><input form=é水😀>", True, id="unicode-owner"),
        pytest.param("<form id=f></form><input form=f><input form=f>", True, id="external-pair"),
        pytest.param("<form id=f></form><form id=g></form><input form=f><input form=g>", False, id="external-forms"),
        pytest.param("<form id=f></form><input form=f><input form=missing>", False, id="external-missing"),
        pytest.param('<form id=f></form><input form=f><input form="">', False, id="external-empty"),
        pytest.param("<form id=f><input><input form=missing></form>", False, id="missing-owner"),
        pytest.param('<form id=f><input><input form=""></form>', False, id="empty-owner"),
        pytest.param("<form id=f><input><input form></form>", False, id="boolean-owner"),
        pytest.param("<input><form><input form=missing></form>", True, id="both-ownerless"),
        pytest.param("<form id=f><input></form><form><input form=f></form>", True, id="owner-overrides-ancestor"),
        pytest.param("<div id=f></div><form id=f><input></form><input form=f>", False, id="nonform-id-first"),
        pytest.param("<form id=f><input></form><div id=f></div><input form=f>", True, id="form-id-first"),
        pytest.param("<svg><g id=f></g></svg><form id=f><input></form><input form=f>", False, id="foreign-id-first"),
        pytest.param(
            "<div id=a></div><div id=i></div><form id=q><input></form><input form=q>", True, id="id-collision"
        ),
        pytest.param(
            '<b id=""></b><div id=a></div><div id=i></div><form id=q><input form=y></form><input>',
            True,
            id="missing-collision",
        ),
        pytest.param("text<!--c--><i id></i><form id=f><input></form><input form=f>", True, id="id-walk"),
    ],
)
@pytest.mark.parametrize("selected", [pytest.param(0, id="first"), pytest.param(1, id="second")])
@pytest.mark.parametrize("indexed", [pytest.param(False, id="walk"), pytest.param(True, id="index")])
def test_radio_group_form_owner(markup: str, selected: int, *, same_owner: bool, indexed: bool) -> None:
    document: Final = parse(markup)
    radios: Final = [node for node in document.descendants if isinstance(node, Element) and node.tag == "input"]
    for radio in radios:
        radio.attrs.update(type="radio", name="group", checked="")
    if indexed:
        document.select("input")
    radios[selected].checked = True
    assert [radio.checked for radio in radios] == [index == selected or not same_owner for index in range(2)]


@pytest.mark.parametrize("selected", [pytest.param(0, id="outer"), pytest.param(1, id="inner")])
@pytest.mark.parametrize("trailing", [pytest.param(False, id="last"), pytest.param(True, id="followed")])
def test_radio_group_nested_forms(selected: int, *, trailing: bool) -> None:
    outer: Final = Element("form")
    inner: Final = Element("form")
    outer.append(Element("input", {"type": "radio", "name": "x", "checked": ""}))
    inner.append(Element("input", {"type": "radio", "name": "x", "checked": ""}))
    outer.append(Element("div", children=[inner]))
    if trailing:
        outer.append(Element("input", {"type": "radio", "name": "x", "checked": ""}))
    radios: Final = list(outer.find_all("input"))
    radios[selected].checked = True
    assert [radio.checked for radio in radios] == [True, True, *([selected == 1] if trailing else [])]


@pytest.mark.parametrize("connected", [pytest.param(False, id="detached"), pytest.param(True, id="connected")])
@pytest.mark.parametrize("selected", [pytest.param(0, id="first"), pytest.param(1, id="second")])
def test_radio_group_explicit_owner_requires_connection(selected: int, *, connected: bool) -> None:
    document: Final = parse(
        "<form><input type=radio name=x checked><input type=radio name=x form=missing checked></form>"
    )
    form: Final = document.select("form")[0]
    radios: Final = list(form.find_all("input"))
    if not connected:
        form.extract()
    radios[selected].checked = True
    assert [radio.checked for radio in radios] == [index == selected or connected for index in range(2)]


@pytest.mark.parametrize("connected", [pytest.param(False, id="detached"), pytest.param(True, id="connected")])
def test_radio_group_shadow_form_owner(*, connected: bool) -> None:
    document: Final = parse("<form id=f><input type=radio name=x checked></form><div></div>")
    host: Final = document.select("div")[0]
    if not connected:
        host.extract()
    shadow: Final = host.attach_shadow()
    form: Final = Element("form", {"id": "f"})
    form.append(Element("input", {"type": "radio", "name": "x", "checked": ""}))
    shadow.append(form)
    shadow.append(Element("input", {"type": "radio", "name": "x", "form": "f"}))
    first, selected = shadow.find_all("input")
    selected.checked = True
    assert (document.select("input")[0].checked, first.checked, selected.checked) == (True, not connected, True)


@pytest.mark.parametrize("selected", [pytest.param(0, id="inside"), pytest.param(1, id="outside")])
def test_radio_group_ignores_foreign_form_ancestor(selected: int) -> None:
    document: Final = parse(
        "<svg><form><foreignObject><input type=radio name=x checked></foreignObject></form></svg>"
        "<input type=radio name=x checked>"
    )
    radios: Final = list(document.find_all("input"))
    radios[selected].checked = True
    assert [radio.checked for radio in radios] == [index == selected for index in range(2)]


def test_radio_group_includes_radio_root() -> None:
    root: Final = Element("input", {"type": "radio", "name": "x", "checked": ""})
    root.append(Element("input", {"type": "radio", "name": "x"}))
    child: Final = root.children[0]
    assert isinstance(child, Element)
    child.checked = True
    assert (root.checked, child.checked) == (False, True)


@pytest.mark.parametrize("count", [pytest.param(1, id="small"), pytest.param(64, id="wide")])
@pytest.mark.parametrize("external", [pytest.param(False, id="inside"), pytest.param(True, id="outside")])
def test_radio_group_many_explicit_owners(count: int, *, external: bool) -> None:
    document: Final = parse(
        "<div></div>" * count
        + '<form id="target"><input type=radio name=x checked></form>'
        + "".join(f'<input type=radio name=x form=target checked id="field{index}">' for index in range(count))
        + "<input type=radio name=x form=target>"
    )
    radios: Final = list(document.find_all("input"))
    selected: Final = len(radios) - 1 if external else 0
    radios[selected].checked = True
    assert [radio.checked for radio in radios] == [index == selected for index in range(len(radios))]


@pytest.mark.parametrize("owned", [pytest.param(False, id="ownerless"), pytest.param(True, id="form")])
@pytest.mark.parametrize("inside", [pytest.param(False, id="outside"), pytest.param(True, id="inside")])
def test_radio_group_deep_ancestor_ownership(*, owned: bool, inside: bool) -> None:
    container: Final = "form" if owned else "section"
    selected_html: Final = "<input type=radio name=x>"
    document: Final = parse(
        f"<{container}>"
        + "<div>" * 32
        + "<input type=radio name=x checked><span><input type=radio name=x checked></span>"
        + "</div>" * 32
        + (selected_html if inside else "")
        + f"</{container}>"
        + ("" if inside else selected_html)
    )
    first, second, selected = document.find_all("input")
    selected.checked = True
    assert (first.checked, second.checked, selected.checked) == (owned and not inside, owned and not inside, True)
