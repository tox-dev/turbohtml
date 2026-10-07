from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from subprocess import run  # ruff: ignore[suspicious-subprocess-import] - libFuzzer owns a separate interpreter.
from typing import TYPE_CHECKING, Final, cast

import pytest
from fuzz.atheris_runtime import build_runtime

if TYPE_CHECKING:
    from typing import Protocol

    class _Runtime(Protocol):
        def path(self) -> str: ...


_CONSUME: Final = """
import sys
from pathlib import Path
from typing import Final
from fuzz.atheris_targets import public_targets
_TARGET: Final = next(target for target in public_targets() if target.name == sys.argv[1])
_TARGET.callback(Path(sys.argv[2]).read_bytes())
"""


_LIST: Final = """
import json
from fuzz.atheris_targets import owner_inventory, public_targets
print(json.dumps({"owners": len(owner_inventory()), "targets": [target.name for target in public_targets()]}))
"""


@pytest.mark.oracle
def test_atheris_public_consumers_run_with_native_coverage(tmp_path: Path) -> None:
    if sys.platform != "linux" or importlib.util.find_spec("atheris") is None:
        pytest.skip("Atheris's native runtime requires its optional Linux wheel")
    runtime: Final = cast("_Runtime", importlib.import_module("atheris"))
    library: Final = build_runtime(Path(runtime.path()) / "libclang_rt.fuzzer_no_main.a", tmp_path / "runtime")
    environment: Final = {
        **os.environ,
        "LD_PRELOAD": str(library),
        "PYTHONPATH": str(Path(__file__).parents[2] / "tools"),
        "GCOV_PREFIX": str(tmp_path / "inventory-gcda"),
    }
    inventory: Final = run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed interpreter and owned modules.
        [sys.executable, "-c", _LIST],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    targets: Final = json.loads(inventory.stdout)
    assert (targets["owners"], len(targets["targets"])) == (209, 30)
    outcomes: Final[dict[str, dict[str, int | str]]] = {}
    for target in targets["targets"]:
        corpus: Final = tmp_path / target
        corpus.mkdir()
        seed: Final = (
            b"<root>one</root>"
            if target == "xml-schema"
            else b"p.x"
            if target == "css-translate"
            else b"p { color: red; }"
            if target == "css-stylesheet"
            else b'consume("kept",external);'
            if target == "javascript"
            else "水😀".encode()
        )
        (corpus / "utf8").write_bytes(seed)
        counters: Final = tmp_path / f"{target}-gcda"
        run(  # ruff: ignore[subprocess-without-shell-equals-true] - public callback and private native counters.
            [sys.executable, "-c", _CONSUME, target, str(corpus / "utf8")],
            env={**environment, "GCOV_PREFIX": str(counters)},
            capture_output=True,
            check=True,
        )
        result = run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed interpreter and finite public corpus.
            [
                sys.executable,
                "-m",
                "fuzz.atheris_targets",
                "--target",
                target,
                "--corpus",
                str(corpus),
                "-atheris_runs=8",
                "-seed=1",
                "-max_len=64",
                "-detect_leaks=0",
            ],
            env={**environment, "GCOV_PREFIX": str(tmp_path / f"{target}-gcda")},
            capture_output=True,
            check=False,
        )
        (tmp_path / f"{target}.log").write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, (target, result.stdout + result.stderr)
        assert b"ATHERIS_REJECTION_BRIDGE=1" in result.stderr
        assert b"Done 8" in result.stderr
        assert b"inline 8-bit counters" in result.stderr
        assert b"PC tables" in result.stderr
        assert b"cov:" in result.stderr
        assert b"ft:" in result.stderr
        assert b"Coverage symbols are being provided by a library other than libFuzzer" not in result.stderr
        manifest: Final = json.loads(corpus.with_suffix(".json").read_text())
        assert (manifest["target"], bool(manifest["exports"])) == (target, True)
        native_counters: Final = tuple(counters.rglob("*.gcda"))
        assert native_counters, target
        outcomes[target] = {
            "exit": result.returncode,
            "corpus": str(corpus),
            "native_counter_files": len(native_counters),
        }
    (tmp_path / "public-targets.json").write_text(json.dumps(outcomes, indent=2) + "\n", encoding="utf-8")
