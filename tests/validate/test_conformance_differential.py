"""turbohtml's conformance verdict cross-checked against the Nu Html Checker (validator.nu).

The Nu Html Checker is the reference HTML5 conformance validator and turbohtml's exemplar for this feature. Its engine
(``vnu.jar``) ships inside the ``html5validator`` distribution, a bench dependency, not a test one, and it needs a JRE
on the path; this module importorskips itself where either is absent and is omitted from the coverage gate (see
``[tool.coverage]``). It still runs and validates wherever both are present (the dev env).

turbohtml checks a deliberate subset of the hundreds of authoring rules vnu enforces, so a byte-level message diff would
be dominated by rules turbohtml does not implement. The comparison is therefore on the verdict, over curated documents
in the overlap: each case violates (or satisfies) exactly one requirement both tools check, and turbohtml's
error-severity verdict must match vnu's -- a document turbohtml calls invalid vnu must report an error for, and a
document turbohtml calls valid vnu must accept.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404  # drives the vnu.jar oracle: fixed argv, in-repo string inputs
from pathlib import Path

import pytest

from turbohtml.conformance import check_html

pytest.importorskip("vnujar", reason="html5validator (vnu.jar) is a bench dependency, absent here")

_VNU_JAR = Path(pytest.importorskip("vnujar").__file__).parent / "vnu.jar"
_JAVA = shutil.which("java")

pytestmark = pytest.mark.skipif(_JAVA is None or not _VNU_JAR.is_file(), reason="a JRE and vnu.jar are required")


def vnu_has_error(markup: str) -> bool:
    assert _JAVA is not None  # the skipif guarantees it; narrows the argv type for the checker
    completed = subprocess.run(  # noqa: S603  # fixed argv, string stdin
        [_JAVA, "-jar", str(_VNU_JAR), "--format", "json", "--stdin", "-"],
        input=markup.encode(),
        capture_output=True,
        check=False,
    )
    report = json.loads(completed.stderr)
    return any(message["type"] == "error" for message in report["messages"])


def valid_doc(inner: str) -> str:
    return f"<!DOCTYPE html><html lang=en><head><title>Doc</title></head><body>{inner}</body></html>"


_CASES = [
    pytest.param(valid_doc("<h1>Heading</h1><p>text</p>"), id="conforming"),
    pytest.param(valid_doc('<img src="x" alt="a cat">'), id="img-with-alt-conforming"),
    pytest.param(valid_doc("<img src=x>"), id="img-missing-alt"),
    pytest.param(valid_doc("<font>x</font>"), id="obsolete-element"),
    pytest.param(valid_doc('<p align="center">x</p>'), id="obsolete-attribute"),
    pytest.param(valid_doc('<span id="a"></span><span id="a"></span>'), id="duplicate-id"),
    pytest.param(valid_doc('<div role="bogus">x</div>'), id="invalid-role"),
    pytest.param("<!DOCTYPE html><html lang=en><head></head><body><p>x</p></body></html>", id="missing-title"),
]


@pytest.mark.parametrize("markup", _CASES)
def test_conformance_verdict_matches_vnu(markup: str) -> None:
    assert check_html(markup).valid is not vnu_has_error(markup)
