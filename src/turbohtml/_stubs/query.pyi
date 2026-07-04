# Subsystem: query (_c/query) — XPath compilation hooks, smart-string registration, and the compiled expression object.
from collections.abc import Callable
from typing import final

from .dom import Element, Node

def _xpath_parse(expression: str, /) -> str: ...
def _css_to_xpath(selector: str, prefix: str, /) -> str: ...
def _register_xpath_string(xpath_string_type: type, /) -> None: ...
def _register_selector_error(selector_error_type: type[ValueError], /) -> None: ...

@final
class XPath:
    def __init__(
        self,
        expression: str,
        /,
        *,
        smart_strings: bool = ...,
        extensions: dict[tuple[str | None, str], Callable[..., str | float | bool]] | None = ...,
    ) -> None: ...
    def __call__(self, node: Node, /, **variables: str | float | bool) -> list[Element | str]: ...
    @property
    def path(self) -> str: ...
