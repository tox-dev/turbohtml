"""html5lib as a fuzz reference, with the tree-construction rules it departs from the WHATWG standard corrected."""

from __future__ import annotations

import copy
import inspect
import types
from collections.abc import Callable
from typing import Any, Final, TypeAlias

import html5lib
from html5lib import html5parser
from html5lib.constants import namespaces, scopingElements, specialElements
from html5lib.html5parser import impliedTagToken
from html5lib.treebuilders import base as treebuilder_base
from html5lib.treebuilders.base import Marker

_Untyped: TypeAlias = Any  # simplint: ignore[imprecise-annotation]  # html5lib ships no types for its phases
_Token: TypeAlias = dict[str, Any]  # simplint: ignore[imprecise-annotation]  # html5lib token dictionaries
_Kwargs: TypeAlias = Any  # simplint: ignore[imprecise-annotation]  # html5lib keyword arguments pass through unchanged
_Process: TypeAlias = Callable[[_Untyped, _Token], _Token | None]


_TEMPLATE: Final[tuple[str, str]] = (namespaces["html"], "template")
# html5lib's special category and scope lists omit template, which the standard includes in both; the tree builder
# reads these sets at parse time, so they are replaced once for every parser.
_SPECIAL: Final[frozenset[tuple[str, str]]] = specialElements | {_TEMPLATE}
html5parser.specialElements = _SPECIAL  # ty: ignore[missing-attribute]  # html5lib imports this name without re-exporting it
treebuilder_base.listElementsMap = {
    None: (frozenset(scopingElements | {_TEMPLATE}), False),
    "button": (frozenset(scopingElements | {_TEMPLATE, (namespaces["html"], "button")}), False),
    "list": (
        frozenset(scopingElements | {_TEMPLATE, (namespaces["html"], "ol"), (namespaces["html"], "ul")}),
        False,
    ),
    "table": (frozenset([(namespaces["html"], "html"), (namespaces["html"], "table"), _TEMPLATE]), False),
    "select": (frozenset([(namespaces["html"], "optgroup"), (namespaces["html"], "option")]), True),
}


def parse_reference(markup: str) -> _Untyped:
    return html5lib_parser(namespaceHTMLElements=False).parse(markup)


def html5lib_parser(**kwargs: _Kwargs) -> html5lib.HTMLParser:
    parser: Final[_Untyped] = html5lib.HTMLParser(**kwargs)
    parser.templateModes = []
    _correct_in_body_and_template(parser)
    _correct_head_and_column_group(parser)
    _correct_text_and_foreign(parser)
    _correct_tables(parser)
    original_reset: Final = parser.resetInsertionMode

    def reset_insertion_mode(self: _Untyped) -> None:
        # Fragment parsing keeps html5lib's own reset, which knows the fragment context element.
        if self.innerHTML:
            original_reset()
            return
        _walk_reset(self)

    parser.resetInsertionMode = types.MethodType(reset_insertion_mode, parser)
    foster_position: Final = parser.tree.getTableMisnestedNodePosition

    def position(self: _Untyped) -> _Untyped:
        # WHATWG "appropriate place for inserting a node": a template opened after the last table takes the misplaced
        # node.
        templates: Final = [
            index
            for index, node in enumerate(self.openElements)
            if node.name == "template" and node.namespace == self.defaultNamespace
        ]
        tables: Final = [
            index
            for index, node in enumerate(self.openElements)
            if node.name == "table" and node.namespace == self.defaultNamespace
        ]
        if templates and (not tables or templates[-1] > tables[-1]):
            return self.openElements[templates[-1]], None
        return foster_position()

    parser.tree.getTableMisnestedNodePosition = types.MethodType(position, parser.tree)
    return parser


def _correct_in_body_and_template(parser: _Untyped) -> None:
    head_start_tags: Final[tuple[str, ...]] = (
        "base",
        "basefont",
        "bgsound",
        "link",
        "meta",
        "noframes",
        "script",
        "style",
        "template",
        "title",
    )
    in_body: Final = type(parser.phases["inBody"])
    end_handlers: Final = _with_method(in_body, "endTagHandler", in_body.endTagFormatting, _adoption_agency)
    end_handlers["br"] = _end_tag_br
    end_handlers["select"] = _end_tag_select
    end_handlers["template"] = _end_to_in_head
    start_handlers: Final = _with_method(in_body, "startTagHandler", in_body.startTagListItem, _start_tag_list_item)
    start_handlers["select"] = _start_tag_select
    start_handlers["frameset"] = _frameset_outside_template(in_body.startTagFrameset)
    start_handlers["body"] = _body_outside_template(in_body.startTagBody)
    for name in head_start_tags:
        start_handlers[name] = _start_to_in_head
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
            "processEOF": _eof_in_body(in_body.processEOF),
        },
    )(parser, parser.tree)
    in_body_corrected: Final = type(parser.phases["inBody"])
    template_start: Final = copy.copy(start_handlers)
    template_start.clear()
    template_start.default = _template_other_start
    template_start.update(dict.fromkeys(head_start_tags, _start_to_in_head))
    template_start.update(dict.fromkeys(("caption", "colgroup", "tbody", "tfoot", "thead"), _template_mode("inTable")))
    template_start["col"] = _template_mode("inColumnGroup")
    template_start["tr"] = _template_mode("inTableBody")
    template_start.update(dict.fromkeys(("td", "th"), _template_mode("inRow")))
    template_end: Final = copy.copy(end_handlers)
    template_end.clear()
    template_end.default = _template_ignore_end
    template_end["template"] = _end_to_in_head
    parser.phases["inTemplate"] = type(
        "CorrectedInTemplatePhase",
        (in_body_corrected,),
        {"startTagHandler": template_start, "endTagHandler": template_end, "processEOF": _template_eof},
    )(parser, parser.tree)


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
            (element for element in self.tree.openElements[formatting_index:] if element.nameTuple in _SPECIAL),
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
    while element.name != name or element.namespace != self.tree.defaultNamespace:
        element = self.tree.openElements.pop()


def _end_to_in_head(self: _Untyped, token: _Token) -> _Untyped:
    return self.parser.phases["inHead"].processEndTag(token)


def _start_tag_list_item(self: _Untyped, token: _Token) -> None:
    # html5lib ends the open list item or p through the current phase, which inside a table is the in-table phase;
    # its voodoo end-tag path clears insertFromTable, so the new item lands in the table instead of before it.
    self.parser.framesetOK = False
    stop_names: Final = {"li": ("li",), "dt": ("dt", "dd"), "dd": ("dt", "dd")}[token["name"]]
    stop: Final = next(
        node
        for node in reversed(self.tree.openElements)
        if node.name in stop_names or (node.nameTuple in _SPECIAL and node.name not in {"address", "div", "p"})
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


def _frameset_outside_template(original: _Process) -> _Process:
    # WHATWG "in body": a frameset start tag is ignored while a template element is open.
    def start_frameset(self: _Untyped, token: _Token) -> None:
        if (
            _has_html_template(self)
            or len(self.tree.openElements) < 2
            or self.tree.openElements[1].name != "body"
            or not self.parser.framesetOK
        ):
            self.parser.parseError("unexpected-start-tag", {"name": "frameset"})
            return
        original(self, token)

    return start_frameset


def _has_html_template(self: _Untyped) -> bool:
    return any(
        node.name == "template" and node.namespace == self.tree.defaultNamespace for node in self.tree.openElements
    )


def _body_outside_template(original: _Process) -> _Process:
    # WHATWG "in body": a body start tag is ignored while a template element is open.
    def start_body(self: _Untyped, token: _Token) -> None:
        if _has_html_template(self):
            self.parser.parseError("unexpected-start-tag", {"name": "body"})
            return
        original(self, token)

    return start_body


def _start_to_in_head(self: _Untyped, token: _Token) -> _Untyped:
    return self.parser.phases["inHead"].processStartTag(token)


def _any_token_ends_newline_drop(process: _Process) -> _Process:
    # The newline after <pre>, <listing> or <textarea> is dropped only when it is the next token; html5lib keeps the
    # rule until a whitespace token arrives, so a <pre> followed by an end tag drops a later newline.
    def process_token(self: _Untyped, token: _Token) -> _Token | None:
        self.processSpaceCharacters = self.processSpaceCharactersNonPre
        return process(self, token)

    return process_token


def _eof_in_body(original: Callable[[_Untyped], _Untyped]) -> Callable[[_Untyped], _Untyped]:
    # WHATWG "in body": an end of file with open templates is processed by the "in template" rules.
    def process_eof(self: _Untyped) -> _Untyped:
        if not self.parser.templateModes:
            return original(self)
        self.parser.phase = self.parser.phases["inTemplate"]
        self.parser.phase.processEOF()
        return None

    return process_eof


def _template_other_start(self: _Untyped, token: _Token) -> _Token:
    self.parser.templateModes[-1] = "inBody"
    self.parser.phase = self.parser.phases["inBody"]
    return token


def _template_mode(mode: str) -> _Process:
    # WHATWG "in template": the current template insertion mode is replaced and the token is reprocessed in that mode.
    def switch(self: _Untyped, token: _Token) -> _Token:
        self.parser.templateModes[-1] = mode
        self.parser.phase = self.parser.phases[mode]
        return token

    return switch


def _template_ignore_end(self: _Untyped, token: _Token) -> None:
    self.parser.parseError("unexpected-end-tag", {"name": token["name"]})


def _template_eof(self: _Untyped) -> None:
    self.parser.parseError("expected-closing-tag-but-got-eof")
    _pop_through(self, "template")
    self.tree.clearActiveFormattingElements()
    self.parser.templateModes.pop()
    self.parser.resetInsertionMode()
    while self.parser.phase.processEOF():
        pass


def _correct_head_and_column_group(parser: _Untyped) -> None:
    in_head: Final = type(parser.phases["inHead"])
    head_start: Final = copy.copy(inspect.getattr_static(in_head, "startTagHandler"))
    head_start["template"] = _head_template_start
    head_end: Final = copy.copy(inspect.getattr_static(in_head, "endTagHandler"))
    head_end["template"] = _head_template_end
    parser.phases["inHead"] = type(
        "CorrectedInHeadPhase", (in_head,), {"startTagHandler": head_start, "endTagHandler": head_end}
    )(parser, parser.tree)
    after_head: Final = type(parser.phases["afterHead"])
    after_start: Final = copy.copy(inspect.getattr_static(after_head, "startTagHandler"))
    after_start["template"] = _start_to_in_head
    after_end: Final = copy.copy(inspect.getattr_static(after_head, "endTagHandler"))
    after_end["template"] = _end_to_in_head
    parser.phases["afterHead"] = type(
        "CorrectedAfterHeadPhase", (after_head,), {"startTagHandler": after_start, "endTagHandler": after_end}
    )(parser, parser.tree)
    in_column_group: Final = type(parser.phases["inColumnGroup"])
    column_start: Final = copy.copy(inspect.getattr_static(in_column_group, "startTagHandler"))
    column_start["template"] = _start_to_in_head
    column_end: Final = copy.copy(inspect.getattr_static(in_column_group, "endTagHandler"))
    column_end["template"] = _end_to_in_head
    column_end["colgroup"] = _end_tag_colgroup
    parser.phases["inColumnGroup"] = type(
        "CorrectedInColumnGroupPhase",
        (in_column_group,),
        {
            **_newline_drop_cleared(in_column_group),
            "startTagHandler": column_start,
            "endTagHandler": column_end,
            "ignoreEndTagColgroup": _ignore_end_tag_colgroup,
            "endTagColgroup": _end_tag_colgroup,
            "processCharacters": _with_column_whitespace(in_column_group.processCharacters),
        },
    )(parser, parser.tree)
    for name, phase_name in (("CorrectedInCellPhase", "inCell"), ("CorrectedInCaptionPhase", "inCaption")):
        phase_class: Final = type(parser.phases[phase_name])
        parser.phases[phase_name] = type(
            name,
            (phase_class,),
            {"processSpaceCharacters": _space_to_in_body, **_newline_drop_cleared(phase_class)},
        )(parser, parser.tree)


def _head_template_start(self: _Untyped, token: _Token) -> None:
    # WHATWG "in head": a template start tag marks the active formatting elements and enters "in template".
    self.tree.insertElement(token)
    self.tree.activeFormattingElements.append(Marker)
    self.parser.framesetOK = False
    self.parser.phase = self.parser.phases["inTemplate"]
    self.parser.templateModes.append("inTemplate")


def _head_template_end(self: _Untyped, _token: _Token) -> None:
    if not _has_html_template(self):
        self.parser.parseError("unexpected-end-tag", {"name": "template"})
        return
    while self.tree.openElements[-1].name in {
        "caption",
        "colgroup",
        "dd",
        "dt",
        "li",
        "optgroup",
        "option",
        "p",
        "rb",
        "rp",
        "rt",
        "rtc",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
    }:
        self.tree.openElements.pop()
    if self.tree.openElements[-1].name != "template":
        self.parser.parseError("unexpected-end-tag", {"name": "template"})
    _pop_through(self, "template")
    self.tree.clearActiveFormattingElements()
    self.parser.templateModes.pop()
    self.parser.resetInsertionMode()


def _end_tag_colgroup(self: _Untyped, _token: _Token) -> None:
    # WHATWG "in column group": an end tag, or any token other than whitespace, a col or a template tag, closes
    # the group only when the group is the current node; otherwise the token is ignored, even inside a template.
    if _ignore_end_tag_colgroup(self):
        self.parser.parseError("unexpected-end-tag", {"name": "colgroup"})
    else:
        self.tree.openElements.pop()
        self.parser.phase = self.parser.phases["inTable"]


def _newline_drop_cleared(phase_class: _Untyped) -> dict[str, _Untyped]:
    # WHATWG: the newline after pre, listing and textarea is the next token only, whichever insertion mode handles it.
    return {
        name: _clears_newline_drop(getattr(phase_class, name))
        for name in ("processStartTag", "processEndTag", "processCharacters", "processComment", "processDoctype")
    }


def _clears_newline_drop(original: _Process) -> _Process:
    def process(self: _Untyped, token: _Token) -> _Untyped:
        self.parser.phases["inBody"].processSpaceCharacters = self.parser.phases["inBody"].processSpaceCharactersNonPre
        return original(self, token)

    return process


def _ignore_end_tag_colgroup(self: _Untyped) -> bool:
    return self.tree.openElements[-1].name != "colgroup"


def _with_column_whitespace(original: _Process) -> _Process:
    # WHATWG "in column group": each whitespace character is inserted and the other characters are ignored when the
    # current node is not a colgroup; html5lib drops a token that mixes both.
    def characters(self: _Untyped, token: _Token) -> _Token | None:
        if self.tree.openElements[-1].name == "colgroup":
            return original(self, token)
        _insert_whitespace(self, token)
        return None

    return characters


def _insert_whitespace(self: _Untyped, token: _Token) -> None:
    whitespace: Final = "".join(character for character in token["data"] if character in "\t\n\f\r ")
    if whitespace:
        self.tree.insertText(whitespace)


def _space_to_in_body(self: _Untyped, token: _Token) -> _Untyped:
    # WHATWG "in cell" and "in caption" process whitespace with the in-body rules, which own the newline rule.
    return self.parser.phases["inBody"].processSpaceCharacters(token)


def _correct_text_and_foreign(parser: _Untyped) -> None:
    in_foreign: Final = type(parser.phases["inForeignContent"])
    foreign_end_tag: Final = in_foreign.processEndTag

    def end_tag(self: _Untyped, token: _Token) -> _Token | None:
        # WHATWG "in foreign content": an end tag for br or p pops the foreign elements up to an HTML element or
        # integration point, and the token is reprocessed there; html5lib keeps the foreign element open.
        if token["name"] not in {"br", "p"}:
            return foreign_end_tag(self, token)
        self.parser.parseError("unexpected-end-tag", {"name": token["name"]})
        while (
            self.tree.openElements[-1].namespace != self.tree.defaultNamespace
            and not self.parser.isHTMLIntegrationPoint(self.tree.openElements[-1])
            and not self.parser.isMathMLTextIntegrationPoint(self.tree.openElements[-1])
        ):
            self.tree.openElements.pop()
        return self.parser.phase.processEndTag(token)

    parser.phases["inForeignContent"] = type(
        "CorrectedInForeignContentPhase", (in_foreign,), {"processEndTag": end_tag}
    )(parser, parser.tree)
    in_table_text: Final = type(parser.phases["inTableText"])
    parser.phases["inTableText"] = type(
        "CorrectedInTableTextPhase", (in_table_text,), {"processDoctype": _flush_table_text_on_doctype}
    )(parser, parser.tree)
    for name, phase_name in (
        ("CorrectedInFramesetPhase", "inFrameset"),
        ("CorrectedAfterFramesetPhase", "afterFrameset"),
    ):
        phase_class: Final = type(parser.phases[phase_name])
        parser.phases[phase_name] = type(name, (phase_class,), {"processCharacters": _insert_frameset_whitespace})(
            parser, parser.tree
        )


def _flush_table_text_on_doctype(self: _Untyped, token: _Token) -> _Token:
    # WHATWG "in table text" treats every non-character token as "anything else", which flushes the pending text
    # before the token is reprocessed; html5lib's InTableTextPhase (html5parser.py processComment) skips the flush.
    self.flushCharacters()
    self.parser.phase = self.originalPhase
    return token


def _insert_frameset_whitespace(self: _Untyped, token: _Token) -> None:
    # WHATWG "in frameset" inserts each whitespace character and ignores the rest, but html5lib drops a character token
    # that mixes letters with whitespace, so the whitespace after the first letter is lost.
    _insert_whitespace(self, token)


def _correct_tables(parser: _Untyped) -> None:
    in_table: Final = type(parser.phases["inTable"])
    in_table_space: Final = in_table.processSpaceCharacters

    def _in_table_space(self: _Untyped, token: _Token) -> _Untyped:
        # WHATWG "in table" hands whitespace to the in-body rules when the current node is not a table element, so the
        # newline rule after pre, listing and textarea applies to it; html5lib keeps the in-table text rule.
        if self.tree.openElements[-1].name in {"table", "tbody", "tfoot", "thead", "tr"}:
            return in_table_space(self, token)
        return self.parser.phases["inBody"].processSpaceCharacters(token)

    in_table_body: Final = type(parser.phases["inTableBody"])
    table_body_start: Final = _with_method(
        in_table_body, "startTagHandler", in_table_body.startTagTableOther, _table_body_start_other
    )
    table_body_end: Final = _with_method(
        in_table_body, "endTagHandler", in_table_body.endTagTable, _table_body_end_table
    )
    parser.phases["inTableBody"] = type(
        "CorrectedInTableBodyPhase",
        (in_table_body,),
        {
            **_newline_drop_cleared(in_table_body),
            "clearStackToTableBodyContext": _clear_stack_to(("tbody", "tfoot", "thead", "template", "html")),
            "startTagHandler": table_body_start,
            "endTagHandler": table_body_end,
        },
    )(parser, parser.tree)
    in_row: Final = type(parser.phases["inRow"])
    row_end: Final = _with_method(in_row, "endTagHandler", in_row.endTagTr, _end_tag_tr)
    parser.phases["inRow"] = type(
        "CorrectedInRowPhase",
        (in_row,),
        {
            **_newline_drop_cleared(in_row),
            "clearStackToTableRowContext": _clear_stack_to(("tr", "template", "html")),
            "endTagTr": _end_tag_tr,
            "endTagHandler": row_end,
        },
    )(parser, parser.tree)
    table_handlers: Final = copy.copy(inspect.getattr_static(in_table, "startTagHandler"))
    table_handlers.default = _in_table_start_tag_other
    table_handlers["template"] = _start_to_in_head
    table_start: Final = in_table.startTagTable

    def start_table(self: _Untyped, token: _Token) -> _Token | None:
        # WHATWG "in table": a table start tag without a table in table scope is ignored; html5lib always reprocesses
        # it, which loops forever when the current mode is a row or cell inside a template.
        if not self.tree.elementInScope("table", variant="table"):
            self.parser.parseError("unexpected-start-tag-ignored", {"name": "table"})
            return None
        return table_start(self, token)

    table_handlers["table"] = start_table
    table_end: Final = _with_method(in_table, "endTagHandler", in_table.endTagTable, _table_end_table)
    table_end["template"] = _end_to_in_head
    parser.phases["inTable"] = type(
        "CorrectedInTablePhase",
        (in_table,),
        {
            "processEOF": _table_eof,
            "startTagHandler": table_handlers,
            "endTagHandler": table_end,
            "processSpaceCharacters": _in_table_space,
            **_newline_drop_cleared(in_table),
            "clearStackToTableContext": _clear_stack_to(("table", "template", "html")),
        },
    )(parser, parser.tree)


def _table_body_start_other(self: _Untyped, token: _Token) -> _Token | None:
    # WHATWG "in table body": a cell, column or caption start tag without a table body in table scope is ignored.
    if any(self.tree.elementInScope(name, variant="table") for name in ("tbody", "thead", "tfoot")):
        self.clearStackToTableBodyContext()
        self.endTagTableRowGroup(impliedTagToken(self.tree.openElements[-1].name))
        return token
    self.parser.parseError("unexpected-start-tag-in-table-body", {"name": token["name"]})
    return None


def _table_body_end_table(self: _Untyped, token: _Token) -> _Token | None:
    if any(self.tree.elementInScope(name, variant="table") for name in ("tbody", "thead", "tfoot")):
        self.clearStackToTableBodyContext()
        self.endTagTableRowGroup(impliedTagToken(self.tree.openElements[-1].name))
        return token
    self.parser.parseError("unexpected-end-tag", {"name": "table"})
    return None


def _clear_stack_to(names: tuple[str, ...]) -> Callable[[_Untyped], None]:
    # WHATWG "clear the stack back to a table ... context": pops until one of the names, which include template.
    def clear(self: _Untyped) -> None:
        while self.tree.openElements[-1].name not in names:
            self.tree.openElements.pop()

    return clear


def _end_tag_tr(self: _Untyped, _token: _Token) -> None:
    # WHATWG "in row": a tr end tag without a tr in table scope is ignored; html5lib asserts here outside fragments.
    if self.tree.elementInScope("tr", variant="table"):
        self.clearStackToTableRowContext()
        self.tree.openElements.pop()
        self.parser.phase = self.parser.phases["inTableBody"]
    else:
        self.parser.parseError("unexpected-end-tag", {"name": "tr"})


def _in_table_start_tag_other(self: _Untyped, token: _Token) -> _Token | None:
    # The in-body rule for a start tag can ask for the token to be reprocessed, as a nested button does; html5lib's
    # in-table fallback drops that request, so the second button never exists.
    self.parser.parseError("unexpected-start-tag-implies-table-voodoo", {"name": token["name"]})
    self.tree.insertFromTable = True
    reprocess = self.parser.phases["inBody"].processStartTag(token)
    self.tree.insertFromTable = False
    return reprocess


def _table_end_table(self: _Untyped, _token: _Token) -> None:
    # WHATWG "in table": an end tag for table closes the table when one is in table scope and is ignored otherwise.
    if self.tree.elementInScope("table", variant="table"):
        self.tree.generateImpliedEndTags()
        if self.tree.openElements[-1].name != "table":
            self.parser.parseError(
                "end-tag-too-early-named", {"gotName": "table", "expectedName": self.tree.openElements[-1].name}
            )
        while self.tree.openElements[-1].name != "table":
            self.tree.openElements.pop()
        self.tree.openElements.pop()
        self.parser.resetInsertionMode()
    else:
        self.parser.parseError("unexpected-end-tag", {"name": "table"})


def _table_eof(self: _Untyped) -> None:
    # WHATWG "in table": an end of file is handled by the "in body" rules, which stop parsing; html5lib asserts that
    # the current node is the root, which a foreign html element also satisfies by name.
    self.parser.parseError("eof-in-table")


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
        "head": "inHead",
        "body": "inBody",
        "frameset": "inFrameset",
        "html": "afterHead" if self.tree.headPointer is not None else "beforeHead",
    }
    node = next(
        node
        for node in self.tree.openElements[::-1]
        if node.namespace == self.tree.defaultNamespace and (node.name in new_modes or node.name == "template")
    )
    if node.name == "template":
        self.phase = self.phases[self.templateModes[-1]]
    else:
        self.phase = self.phases[new_modes[node.name]]


__all__ = [
    "html5lib_parser",
    "parse_reference",
]
