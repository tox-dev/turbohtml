"""html5lib as a fuzz reference, with the tree-construction rules it departs from the WHATWG standard corrected."""

from __future__ import annotations

import copy
import inspect
import types
from collections.abc import Callable
from typing import Any, Final, TypeAlias

import html5lib
from html5lib.constants import specialElements
from html5lib.html5parser import impliedTagToken

_Untyped: TypeAlias = Any  # simplint: ignore[imprecise-annotation]  # html5lib ships no types for its phases
_Token: TypeAlias = dict[str, Any]  # simplint: ignore[imprecise-annotation]  # html5lib token dictionaries
_Kwargs: TypeAlias = Any  # simplint: ignore[imprecise-annotation]  # html5lib keyword arguments pass through unchanged
_Process: TypeAlias = Callable[[_Untyped, _Token], _Token | None]


def parse_reference(markup: str) -> _Untyped:
    return html5lib_parser(namespaceHTMLElements=False).parse(markup)


def html5lib_parser(**kwargs: _Kwargs) -> html5lib.HTMLParser:
    parser: Final = html5lib.HTMLParser(**kwargs)
    in_body: Final = type(parser.phases["inBody"])
    end_handlers: Final = _with_method(in_body, "endTagHandler", in_body.endTagFormatting, _adoption_agency)
    end_handlers["br"] = _end_tag_br
    end_handlers["select"] = _end_tag_select
    start_handlers: Final = _with_method(in_body, "startTagHandler", in_body.startTagListItem, _start_tag_list_item)
    start_handlers["select"] = _start_tag_select
    parser.phases["inBody"] = type(
        "CorrectedInBodyPhase",
        (in_body,),
        {
            "endTagFormatting": _adoption_agency,
            "endTagHandler": end_handlers,
            "startTagHandler": start_handlers,
            "processStartTag": _any_token_ends_newline_drop(in_body.processStartTag),
            "processEndTag": _any_token_ends_newline_drop(in_body.processEndTag),
            "processCharacters": _any_token_ends_newline_drop(in_body.processCharacters),
            "processComment": _any_token_ends_newline_drop(in_body.processComment),
            "processDoctype": _any_token_ends_newline_drop(in_body.processDoctype),
        },
    )(parser, parser.tree)
    for name, phase_name in (("CorrectedInCellPhase", "inCell"), ("CorrectedInCaptionPhase", "inCaption")):
        phase_class: Final = type(parser.phases[phase_name])
        parser.phases[phase_name] = type(name, (phase_class,), {"processSpaceCharacters": _space_to_in_body})(
            parser, parser.tree
        )
    in_table_text: Final = type(parser.phases["inTableText"])
    parser.phases["inTableText"] = type(
        "CorrectedInTableTextPhase", (in_table_text,), {"processDoctype": _flush_table_text_on_doctype}
    )(parser, parser.tree)
    in_table: Final = type(parser.phases["inTable"])
    in_table_space: Final = in_table.processSpaceCharacters

    def _in_table_space(self: _Untyped, token: _Token) -> _Untyped:
        # WHATWG "in table" hands whitespace to the in-body rules when the current node is not a table element, so the
        # newline rule after pre, listing and textarea applies to it; html5lib keeps the in-table text rule.
        if self.tree.openElements[-1].name in {"table", "tbody", "tfoot", "thead", "tr"}:
            return in_table_space(self, token)
        return self.parser.phases["inBody"].processSpaceCharacters(token)

    table_handlers: Final = copy.copy(inspect.getattr_static(in_table, "startTagHandler"))
    table_handlers.default = _in_table_start_tag_other
    parser.phases["inTable"] = type(
        "CorrectedInTablePhase",
        (in_table,),
        {
            "startTagHandler": table_handlers,
            "processSpaceCharacters": _in_table_space,
        },
    )(parser, parser.tree)
    for name, phase_name in (
        ("CorrectedInFramesetPhase", "inFrameset"),
        ("CorrectedAfterFramesetPhase", "afterFrameset"),
    ):
        phase_class: Final = type(parser.phases[phase_name])
        parser.phases[phase_name] = type(name, (phase_class,), {"processCharacters": _insert_frameset_whitespace})(
            parser, parser.tree
        )
    original_reset: Final = parser.resetInsertionMode

    def reset_insertion_mode(self: _Untyped) -> None:
        # Fragment parsing keeps html5lib's own reset, which knows the fragment context element.
        if self.innerHTML:
            original_reset()
            return
        _walk_reset(self)

    parser.resetInsertionMode = types.MethodType(reset_insertion_mode, parser)
    return parser


def _with_method(phase: _Untyped, dispatcher_name: str, original: _Untyped, replacement: _Untyped) -> _Untyped:
    dispatcher: Final = inspect.getattr_static(phase, dispatcher_name)
    handlers: Final = copy.copy(dispatcher)
    handlers.update({name: replacement for name, method in dispatcher.items() if method is original})
    return handlers


def _adoption_agency(self: _Untyped, token: _Token) -> None:
    # Copied from html5lib's InBodyPhase.endTagFormatting, with the inner loop running until the formatting element
    # is reached: html5lib stops after three iterations (html5parser.py, `while innerLoopCounter < 3`).
    for _ in range(8):
        formatting = self.tree.elementInActiveFormattingElements(token["name"])
        if not formatting or (formatting in self.tree.openElements and not self.tree.elementInScope(formatting.name)):
            self.endTagOther(token)
            return
        if formatting not in self.tree.openElements:
            self.parser.parseError("adoption-agency-1.2", {"name": token["name"]})
            self.tree.activeFormattingElements.remove(formatting)
            return
        if formatting != self.tree.openElements[-1]:
            self.parser.parseError("adoption-agency-1.3", {"name": token["name"]})

        formatting_index = self.tree.openElements.index(formatting)
        furthest_block = next(
            (element for element in self.tree.openElements[formatting_index:] if element.nameTuple in specialElements),
            None,
        )
        if furthest_block is None:
            element = self.tree.openElements.pop()
            while element != formatting:
                element = self.tree.openElements.pop()
            self.tree.activeFormattingElements.remove(element)
            return

        bookmark, last_node = _clone_path(self, formatting, furthest_block)
        _reinsert(self, formatting, furthest_block, last_node, bookmark)


def _clone_path(self: _Untyped, formatting: _Untyped, furthest_block: _Untyped) -> tuple[int, _Untyped]:
    bookmark: int = self.tree.activeFormattingElements.index(formatting)
    last_node = node = furthest_block
    inner_loop_counter = 0
    index = self.tree.openElements.index(node)
    while True:
        inner_loop_counter += 1
        index -= 1
        node = self.tree.openElements[index]
        if node == formatting:
            return bookmark, last_node
        if inner_loop_counter > 3 and node in self.tree.activeFormattingElements:
            self.tree.activeFormattingElements.remove(node)
        if node not in self.tree.activeFormattingElements:
            self.tree.openElements.remove(node)
            continue
        if last_node == furthest_block:
            bookmark = self.tree.activeFormattingElements.index(node) + 1
        clone = node.cloneNode()
        self.tree.activeFormattingElements[self.tree.activeFormattingElements.index(node)] = clone
        self.tree.openElements[self.tree.openElements.index(node)] = clone
        if last_node.parent:
            last_node.parent.removeChild(last_node)
        clone.appendChild(last_node)
        last_node = clone


def _reinsert(
    self: _Untyped, formatting: _Untyped, furthest_block: _Untyped, last_node: _Untyped, bookmark: int
) -> None:
    common_ancestor = self.tree.openElements[self.tree.openElements.index(formatting) - 1]
    if last_node.parent:
        last_node.parent.removeChild(last_node)
    if common_ancestor.name in {"table", "tbody", "tfoot", "thead", "tr"}:
        parent, insert_before = self.tree.getTableMisnestedNodePosition()
        parent.insertBefore(last_node, insert_before)
    else:
        common_ancestor.appendChild(last_node)
    clone = formatting.cloneNode()
    furthest_block.reparentChildren(clone)
    furthest_block.appendChild(clone)
    self.tree.activeFormattingElements.remove(formatting)
    self.tree.activeFormattingElements.insert(bookmark, clone)
    self.tree.openElements.remove(formatting)
    self.tree.openElements.insert(self.tree.openElements.index(furthest_block) + 1, clone)


def _end_tag_br(self: _Untyped, token: _Token) -> None:
    # WHATWG treats </br> as a <br> start tag, which sets the frameset-ok flag to not ok; html5lib's endTagBr leaves
    # the flag alone, so a frameset start tag after </br> is accepted instead of ignored.
    self.parser.parseError("unexpected-end-tag-treated-as", {"originalName": token["name"], "newName": "br element"})
    self.tree.reconstructActiveFormattingElements()
    self.tree.insertElement(impliedTagToken("br", "StartTag"))
    self.tree.openElements.pop()
    self.parser.framesetOK = False


def _end_tag_select(self: _Untyped, token: _Token) -> None:
    if self.tree.elementInScope("select"):
        _pop_through(self, "select")
        self.parser.resetInsertionMode()
    else:
        self.parser.parseError("unexpected-end-tag", {"name": token["name"]})


def _pop_through(self: _Untyped, name: str) -> None:
    element = self.tree.openElements.pop()
    while element.name != name:
        element = self.tree.openElements.pop()


def _start_tag_list_item(self: _Untyped, token: _Token) -> None:
    # html5lib ends the open list item or p through the current phase, which inside a table is the in-table phase;
    # its voodoo end-tag path clears insertFromTable, so the new item lands in the table instead of before it.
    self.parser.framesetOK = False
    stop_names: Final = {"li": ("li",), "dt": ("dt", "dd"), "dd": ("dt", "dd")}[token["name"]]
    stop: Final = next(
        node
        for node in reversed(self.tree.openElements)
        if node.name in stop_names or (node.nameTuple in specialElements and node.name not in {"address", "div", "p"})
    )
    if stop.name in stop_names:
        self.parser.phases["inBody"].processEndTag(impliedTagToken(stop.name, "EndTag"))
    if self.tree.elementInScope("p", variant="button"):
        self.parser.phases["inBody"].processEndTag(impliedTagToken("p", "EndTag"))
    self.tree.insertElement(token)


def _start_tag_select(self: _Untyped, token: _Token) -> None:
    # The current WHATWG spec has no "in select" insertion mode: a select start tag inside an open
    # select closes it, and otherwise the element is inserted while the in-body rules keep handling its content.
    if self.tree.elementInScope("select"):
        self.parser.parseError("unexpected-select-in-select")
        _pop_through(self, "select")
        self.parser.resetInsertionMode()
        return
    self.tree.reconstructActiveFormattingElements()
    self.tree.insertElement(token)
    self.parser.framesetOK = False


def _any_token_ends_newline_drop(process: _Process) -> _Process:
    # The newline after <pre>, <listing> or <textarea> is dropped only when it is the next token; html5lib keeps the
    # rule until a whitespace token arrives, so a <pre> followed by an end tag drops a later newline.
    def process_token(self: _Untyped, token: _Token) -> _Token | None:
        self.processSpaceCharacters = self.processSpaceCharactersNonPre
        return process(self, token)

    return process_token


def _space_to_in_body(self: _Untyped, token: _Token) -> _Untyped:
    # WHATWG "in cell" and "in caption" process whitespace with the in-body rules, which own the newline rule.
    return self.parser.phases["inBody"].processSpaceCharacters(token)


def _flush_table_text_on_doctype(self: _Untyped, token: _Token) -> _Token:
    # WHATWG "in table text" treats every non-character token as "anything else", which flushes the pending text
    # before the token is reprocessed; html5lib's InTableTextPhase (html5parser.py processComment) skips the flush.
    self.flushCharacters()
    self.parser.phase = self.originalPhase
    return token


def _in_table_start_tag_other(self: _Untyped, token: _Token) -> _Token | None:
    # The in-body rule for a start tag can ask for the token to be reprocessed, as a nested button does; html5lib's
    # in-table fallback drops that request, so the second button never exists.
    self.parser.parseError("unexpected-start-tag-implies-table-voodoo", {"name": token["name"]})
    self.tree.insertFromTable = True
    reprocess = self.parser.phases["inBody"].processStartTag(token)
    self.tree.insertFromTable = False
    return reprocess


def _insert_frameset_whitespace(self: _Untyped, token: _Token) -> None:
    # WHATWG "in frameset" inserts each whitespace character and ignores the rest, but html5lib drops a character token
    # that mixes letters with whitespace, so the whitespace after the first letter is lost.
    whitespace: Final = "".join(character for character in token["data"] if character in "\t\n\f\r ")
    if whitespace:
        self.tree.insertText(whitespace)


def _walk_reset(self: _Untyped) -> None:
    # The current WHATWG reset rule has no case for select, so the walk passes over an open select to the element
    # below it; html5lib's version maps select to its removed in-select mode.
    new_modes: Final = {
        "td": "inCell",
        "th": "inCell",
        "tr": "inRow",
        "tbody": "inTableBody",
        "thead": "inTableBody",
        "tfoot": "inTableBody",
        "caption": "inCaption",
        "colgroup": "inColumnGroup",
        "table": "inTable",
        "head": "inBody",
        "body": "inBody",
        "frameset": "inFrameset",
        "html": "beforeHead",
    }
    node = next(
        node
        for node in self.tree.openElements[::-1]
        if node.namespace == self.tree.defaultNamespace and node.name in new_modes
    )
    self.phase = self.phases[new_modes[node.name]]


__all__ = [
    "html5lib_parser",
    "parse_reference",
]
