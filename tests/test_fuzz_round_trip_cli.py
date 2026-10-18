"""The round-trip oracle driver end to end: findings on disk, the log it keeps clean, and the runs it refuses."""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final
from zipfile import ZipFile

import pytest
from coverage import CoverageData
from fuzz.atheris_header import SEED_HEADER
from fuzz.atheris_targets import public_targets
from fuzz.cflite import BUILD_IMAGE, RUN_IMAGE
from fuzz.cflite import main as fuzz_cflite_main
from fuzz.fuzz import main as fuzz_alloc_cli_main
from fuzz.fuzz import main as fuzz_sanitizer_cli_main
from fuzz.fuzz import main as fuzz_smoke_main
from fuzz.fuzz import main as fuzz_url_cli_main
from fuzz.oss_fuzz import atheris_target_names, fuzzer_name, write_harnesses
from fuzz.oss_fuzz import main as fuzz_oss_fuzz_main
from fuzz.reachability import main as fuzz_reachability_main
from fuzz.reachability import target_functions, unreached
from fuzz.round_trip_oracles import ORACLES, Floor, Oracle, OutOfScopeError, main

if TYPE_CHECKING:
    import random
    from collections.abc import Callable
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture


def _flag_x(text: str) -> str | None:
    return "broken" if "x" in text else None


def _out_of_scope(text: str) -> str | None:
    raise OutOfScopeError(text)


def _documented_depth(text: str) -> str | None:
    msg = f"{text} does not support trees nested 1024 levels or deeper"
    raise RecursionError(msg)


def _other_recursion(text: str) -> str | None:
    raise RecursionError(text)


def _key_error(text: str) -> str | None:
    raise KeyError(text)


def _seeds() -> list[str]:
    return ["axb", "a"]


def _generate(_rng: random.Random) -> str:
    return "zxz"


def _fired() -> dict[str, bool]:
    return {"control": True}


def _silent() -> dict[str, bool]:
    return {"control": False}


_ONE_CASE: Final = Floor(1, 0.5)


def _probe(
    check: Callable[[str], str | None],
    controls: Callable[[], dict[str, bool]] = _fired,
    floor: Floor = _ONE_CASE,
) -> dict[str, Oracle]:
    return {"probe": Oracle(check, _generate, _seeds, controls, floor)}


@pytest.fixture
def crash_dir(tmp_path: Path) -> Path:
    return tmp_path / "crashes"


def test_round_trip_cli_writes_a_minimized_finding(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(ORACLES, _probe(_flag_x), clear=True)
    code = main(["--minutes", "0", "--crash-dir", str(crash_dir)])
    digest = hashlib.sha256(b"x").hexdigest()
    sidecar = json.loads((crash_dir / f"crash-{digest}.json").read_text(encoding="utf-8"))
    assert (code, (crash_dir / f"crash-{digest}").read_text(encoding="utf-8"), sidecar, capsys.readouterr().err) == (
        1,
        "x",
        {"oracle": "probe", "detail": "broken", "hits": 1, "rng_seed": 0},
        f"FINDING probe sha256={digest}\n",
    )


def test_round_trip_cli_keeps_the_input_and_seed_out_of_the_log(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(ORACLES, _probe(_flag_x), clear=True)
    main(["--minutes", "0.001", "--rng-seed", "918273645", "--crash-dir", str(crash_dir)])
    out, err = capsys.readouterr()
    log = out + err
    assert (err.count("FINDING probe sha256="), "axb" in log, "zxz" in log, "918273645" in log) == (
        1,
        False,
        False,
        False,
    )


def test_round_trip_cli_runs_generated_cases_after_the_seeds(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(ORACLES, _probe(_flag_x), clear=True)
    main(["--minutes", "0.001", "--crash-dir", str(crash_dir)])
    stats = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("probe: "))
    assert int(stats.split("'compared': ")[1].rstrip("}")) > len(_seeds())


def test_round_trip_cli_passes_a_clean_run(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(ORACLES, _probe(lambda _text: None), clear=True)
    code = main(["--minutes", "0", "--crash-dir", str(crash_dir)])
    assert (code, capsys.readouterr().out.splitlines()) == (
        0,
        ["probe: {'compared': 2}", f"0 finding(s) written to {crash_dir}"],
    )


def test_round_trip_cli_fails_a_blind_oracle(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(ORACLES, _probe(_flag_x, controls=_silent), clear=True)
    code = main(["--minutes", "0", "--crash-dir", str(crash_dir)])
    assert (code, capsys.readouterr().err) == (
        2,
        "BLIND ORACLE: the negative control did not fire for probe/control\n",
    )


@pytest.mark.parametrize(
    "check",
    [
        pytest.param(_out_of_scope, id="out-of-scope"),
        pytest.param(_documented_depth, id="documented-depth-error"),
    ],
)
def test_round_trip_cli_fails_a_vacuous_run(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str], check: Callable[[str], str | None]
) -> None:
    mocker.patch.dict(ORACLES, _probe(check), clear=True)
    code = main(["--minutes", "0", "--crash-dir", str(crash_dir)])
    assert (code, capsys.readouterr().err) == (2, "VACUOUS RUN: probe compared below the floor\n")


def test_round_trip_cli_fails_below_the_ratio_floor(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(
        ORACLES, _probe(lambda text: None if "x" in text else _out_of_scope(text), floor=Floor(1, 0.6)), clear=True
    )
    code = main(["--minutes", "0", "--oracle", "probe", "--crash-dir", str(crash_dir)])
    assert (code, capsys.readouterr().err) == (2, "VACUOUS RUN: probe compared below the floor\n")


@pytest.mark.parametrize(
    ("check", "detail"),
    [
        pytest.param(_other_recursion, "raised RecursionError", id="undocumented-recursion"),
        pytest.param(_key_error, "raised KeyError", id="unexpected-exception"),
    ],
)
def test_round_trip_cli_reports_an_escaping_exception(
    mocker: MockerFixture, crash_dir: Path, check: Callable[[str], str | None], detail: str
) -> None:
    mocker.patch.dict(ORACLES, _probe(check), clear=True)
    code = main(["--minutes", "0", "--crash-dir", str(crash_dir)])
    details = [json.loads(path.read_text(encoding="utf-8"))["detail"] for path in crash_dir.glob("crash-*.json")]
    assert (code, details) == (1, [detail])


if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture


def test_fuzz_alloc_sweeps_only_the_inprocess_targets(mocker: MockerFixture, tmp_path: Path) -> None:
    reports = " ".join(
        f"ERROR: AddressSanitizer: {kind}"
        for kind in ("heap-buffer-overflow", "heap-use-after-free", "use-after-poison")
    )

    def run(command: list[str], **_: object) -> MagicMock:
        crashed = "_fuzz_crash" in (command[2] if command[1:2] == ["-c"] else "")
        return mocker.MagicMock(returncode=-6 if crashed else 0, stdout="runtime", stderr=reports)

    calls = mocker.patch("fuzz.fuzz.subprocess.run", autospec=True, side_effect=run).call_args_list
    assert fuzz_alloc_cli_main(["--mode", "alloc", "--crash-dir", str(tmp_path)]) == 0
    workers = [call.args[0][2:4] for call in calls if str(call.args[0][1]).endswith("_targets.py")]
    compiled = [call.args[0] for call in calls if "-fsanitize=address,undefined" in call.args[0]]
    assert (workers, compiled) == ([["--mode", "alloc"], ["--mode", "alloc"]], [])


if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("arguments", "flags"),
    [
        pytest.param(["--sanitizer", "address", "--skip-inprocess"], ["-fsanitize=address,undefined"], id="address"),
        # MemorySanitizer skips the extension without --skip-inprocess
        pytest.param(
            ["--sanitizer", "memory"], ["-fsanitize=memory", "-fsanitize-memory-track-origins=2"], id="memory"
        ),
    ],
)
def test_fuzz_cli_builds_every_standalone_harness_under_the_sanitizer(
    mocker: MockerFixture, tmp_path: Path, arguments: list[str], flags: list[str]
) -> None:
    run: Final = mocker.patch(
        "fuzz.fuzz.subprocess.run",
        autospec=True,
        return_value=mocker.MagicMock(returncode=0, stdout=str(tmp_path / "runtime")),
    )
    status: Final = fuzz_sanitizer_cli_main(["--mode", "smoke", *arguments, "--crash-dir", str(tmp_path / "crashes")])
    commands: Final = [call.args[0] for call in run.call_args_list]
    compiled: Final = [command for command in commands if "-o" in command]
    assert (
        status,
        [command[1] for command in compiled],
        all(command[2 : 2 + len(flags)] == flags for command in compiled),
        sum("_targets.py" in " ".join(command) for command in commands),
    ) == (
        0,
        ["-DTH_IDNA_STANDALONE", "-DTH_PHONE_STANDALONE", "-DJM_STANDALONE", "-DCSS_MINIFY_STANDALONE"],
        True,
        0,
    )


if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import IO
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture


@pytest.fixture
def docker(mocker: MockerFixture) -> MagicMock:
    return mocker.patch("fuzz.cflite.subprocess.run", autospec=True, return_value=mocker.MagicMock(returncode=0))


@pytest.fixture
def build_log(docker: MagicMock, mocker: MockerFixture) -> Callable[[str, int], None]:
    def write(log: str, returncode: int) -> None:
        def run(*_: object, stdout: IO[bytes], **__: object) -> MagicMock:
            stdout.write(log.encode())
            return mocker.MagicMock(returncode=returncode)

        docker.side_effect = run

    return write


def _arguments(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--mode",
        "batch",
        "--sanitizer",
        "address",
        "--seconds",
        "3600",
        "--source",
        str(tmp_path / "turbohtml"),
        "--workspace",
        str(tmp_path / "workspace"),
        *extra,
    ]


def test_cflite_runs_build_then_mode_on_standalone_filestore(docker: MagicMock, tmp_path: Path) -> None:
    assert fuzz_cflite_main(_arguments(tmp_path)) == 0
    workspace: Final = tmp_path / "workspace"
    commands: Final = [call.args[0] for call in docker.call_args_list]
    assert [(command[-1], _environment(command)) for command in commands] == [
        (BUILD_IMAGE, _expected(tmp_path)),
        (RUN_IMAGE, {**_expected(tmp_path), "MODE": "batch", "FUZZ_SECONDS": "3600", "OUTPUT_SARIF": "True"}),
    ]
    assert commands[0][:11] == [
        "docker",
        "run",
        "--rm",
        "--security-opt",
        "label=disable",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-v",
        f"{workspace}:{workspace}",
        "-v",
        f"{tmp_path / 'turbohtml'}:{tmp_path / 'turbohtml'}",
    ]


def test_cflite_replays_build_seeds_with_stored_corpus(
    docker: MagicMock, mocker: MockerFixture, tmp_path: Path
) -> None:
    build: Final = tmp_path / "workspace" / "build-out"

    def run(*_: object, **__: object) -> MagicMock:
        if docker.call_count == 1:
            build.mkdir(parents=True)
            with ZipFile(build / "fuzz_content_detect_seed_corpus.zip", "w") as archive:
                archive.writestr("normal-input", SEED_HEADER + b"abc")
        return mocker.MagicMock(returncode=0)

    docker.side_effect = run
    assert fuzz_cflite_main(_arguments(tmp_path)) == 0
    assert (tmp_path / "workspace" / "storage" / "corpus" / "fuzz_content_detect" / "normal-input").read_bytes() == (
        SEED_HEADER + b"abc"
    )


def test_cflite_writes_logs_not_console(docker: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fuzz_cflite_main(_arguments(tmp_path))
    logs: Final = [call.kwargs["stdout"].name for call in docker.call_args_list]
    assert (logs, json.loads(capsys.readouterr().out)) == (
        [
            str(tmp_path / "workspace" / "logs" / "build-address.log"),
            str(tmp_path / "workspace" / "logs" / "batch-address.log"),
        ],
        {"crashes": [], "status": {"batch": 0, "build": 0}},
    )


@pytest.mark.parametrize(
    ("extra", "variables"),
    [
        pytest.param(("--base-commit", "0123abc"), {"GIT_BASE_COMMIT": "0123abc"}, id="base-commit"),
        pytest.param(("--upload-build",), {"UPLOAD_BUILD": "True"}, id="upload-build"),
    ],
)
def test_cflite_passes_base_build_settings(
    docker: MagicMock, tmp_path: Path, extra: tuple[str, ...], variables: dict[str, str]
) -> None:
    fuzz_cflite_main(_arguments(tmp_path, *extra))
    assert [_environment(call.args[0]) for call in docker.call_args_list] == [
        {**_expected(tmp_path), **variables},
        {**_expected(tmp_path), **variables, "MODE": "batch", "FUZZ_SECONDS": "3600", "OUTPUT_SARIF": "True"},
    ]


def test_cflite_build_failure_skips_fuzzing(
    docker: MagicMock, mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    docker.return_value = mocker.MagicMock(returncode=2)
    assert (
        fuzz_cflite_main(_arguments(tmp_path)),
        docker.call_count,
        json.loads(capsys.readouterr().out)["status"],
    ) == (
        2,
        1,
        {"build": 2},
    )


@pytest.mark.parametrize(
    ("log", "shown"),
    [
        pytest.param(
            "Step 1/4 : FROM base-builder-python\nERROR: meson setup failed\n",
            "Step 1/4 : FROM base-builder-python\nERROR: meson setup failed\n",
            id="build-step",
        ),
        pytest.param(
            "compile done\nBuild check: stdout: INFO: performing bad build checks for /out/fuzz_html_document\n"
            "BAD BUILD: /out/fuzz_html_document seems to have either startup crash or exit:\n"
            "==12==ERROR: AddressSanitizer: heap-buffer-overflow\nBase64: PHNlY3JldD4=\n"
            "2026-10-08 04:20:29,440 - root - ERROR - Build check failed.\n",
            "compile done\nBAD BUILD: /out/fuzz_html_document seems to have either startup crash or exit:\n"
            "2026-10-08 04:20:29,440 - root - ERROR - Build check failed.\n",
            id="bad-build-check-drops-fuzzer-output",
        ),
        pytest.param(
            "".join(f"line {index}\n" for index in range(250)),
            "".join(f"line {index}\n" for index in range(50, 250)),
            id="tail",
        ),
    ],
)
def test_cflite_build_failure_shows_public_build_log(
    build_log: Callable[[str, int], None], tmp_path: Path, capsys: pytest.CaptureFixture[str], log: str, shown: str
) -> None:
    build_log(log, 1)
    fuzz_cflite_main(_arguments(tmp_path))
    assert capsys.readouterr().err == shown


def test_cflite_build_success_keeps_log_private(
    build_log: Callable[[str, int], None], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build_log("compile done\n", 0)
    fuzz_cflite_main(_arguments(tmp_path))
    assert not capsys.readouterr().err


def test_cflite_reports_crashes_by_hash(
    docker: MagicMock, mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts: Final = tmp_path / "workspace" / "out" / "artifacts"
    for target, name, data in (
        ("fuzz_html_document", "crash-8c1ff5bd5bfa04b3c5dd2b9b5bd52bc1a0ea0d1a", b"<secret>"),
        ("fuzz_xml_transform", "crash-2b0f8d36f2e8f9d2e86c4b8a2e43f4e5c0a1b7d9", b"<x/>"),
    ):
        crash = artifacts / target / "address" / name
        crash.parent.mkdir(parents=True)
        crash.write_bytes(data)
        crash.with_name(f"{crash.name}.summary").write_text("==1==ERROR: AddressSanitizer: heap-buffer-overflow\n")
    docker.side_effect = [mocker.MagicMock(returncode=0), mocker.MagicMock(returncode=1)]
    status: Final = fuzz_cflite_main(_arguments(tmp_path))
    output: Final = capsys.readouterr().out
    assert (status, json.loads(output)["crashes"], "secret" in output) == (
        1,
        [
            {"bytes": 8, "fuzzer": "fuzz_html_document", "sha256": hashlib.sha256(b"<secret>").hexdigest()},
            {"bytes": 4, "fuzzer": "fuzz_xml_transform", "sha256": hashlib.sha256(b"<x/>").hexdigest()},
        ],
        False,
    )


def _environment(command: list[str]) -> dict[str, str]:
    return dict(command[index + 1].split("=", 1) for index, argument in enumerate(command) if argument == "-e")


def _expected(tmp_path: Path) -> dict[str, str]:
    return {
        "CFL_PLATFORM": "standalone",
        "WORKSPACE": str(tmp_path / "workspace"),
        "PROJECT_SRC_PATH": str(tmp_path / "turbohtml"),
        "REPOSITORY": "turbohtml",
        "FILESTORE": "filesystem",
        "FILESTORE_ROOT_DIR": str(tmp_path / "workspace" / "storage"),
        "LANGUAGE": "python",
        "SANITIZER": "address",
        "LOW_DISK_SPACE": "True",
    }


if TYPE_CHECKING:
    from unittest.mock import MagicMock


def test_oss_fuzz_targets_skip_only_node_oracle() -> None:
    assert set(atheris_target_names()) == {target.name for target in public_targets()} - {"javascript"}


@pytest.mark.parametrize(
    ("target", "fuzzer"),
    [
        pytest.param("html-document", "fuzz_html_document", id="dashed"),
        pytest.param("javascript", "fuzz_javascript", id="plain"),
    ],
)
def test_oss_fuzz_fuzzer_name(target: str, fuzzer: str) -> None:
    assert fuzzer_name(target) == fuzzer


def test_oss_fuzz_harness_runs_its_target(tmp_path: Path) -> None:
    script: Final = next(path for path in write_harnesses(tmp_path) if path.name == "fuzz_xml_transform.py")
    assert script.read_text(encoding="utf-8") == (
        "import sys\n\nfrom fuzz.atheris_driver import fuzz\n"
        "from fuzz.atheris_targets import MODULES, public_targets\n\n"
        "fuzz(public_targets(), MODULES, 'xml-transform', sys.argv)\n"
    )


def test_oss_fuzz_cli_prints_one_script_per_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert fuzz_oss_fuzz_main(["harnesses", "--output", str(tmp_path)]) == 0
    assert capsys.readouterr().out.split() == [
        str(tmp_path / f"{fuzzer_name(name)}.py") for name in atheris_target_names()
    ]


def test_oss_fuzz_cli_seeds_have_no_failure_header(tmp_path: Path) -> None:
    assert fuzz_oss_fuzz_main(["seeds", "--output", str(tmp_path)]) == 0
    source: Final = Path(__file__).resolve().parents[1] / "tools" / "fuzz" / "corpus" / "minify_css" / "basic.css"
    assert (tmp_path / "tools_fuzz_corpus_minify_css_basic.css").read_bytes() == SEED_HEADER + source.read_bytes()


if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from fuzz.atheris_registry import Target

_TARGETS: Final = {target.name: target for target in public_targets()}


@pytest.mark.parametrize(
    ("target", "names"),
    [
        pytest.param("xml-transform", ["_transform", "Transform", "strparam", "transform"], id="functions-and-class"),
        pytest.param("dom-construction", ["_check", "ElementMaker", "document"], id="partial-and-alias"),
        pytest.param("dom-rewrite", ["_check", "rewrite"], id="protocol-stubs"),
        pytest.param("dom-range", ["_check"], id="c-types"),
        pytest.param("html-incremental", ["_incremental_chunks"], id="chunked-incremental"),
        pytest.param("html-tokenizer", ["_token_chunks"], id="chunked-tokenizer"),
    ],
)
def test_reachability_target_functions_follow_registry(target: str, names: list[str]) -> None:
    assert [function.name for function in target_functions(_TARGETS[target])] == names


def test_reachability_skips_def_line() -> None:
    callback: Final = target_functions(_TARGETS["html-document"])[0]
    assert inspect.getsourcelines(_TARGETS["html-document"].callback)[1] not in callback.lines


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        pytest.param(set(), [], id="all-reached"),
        pytest.param({"_transform"}, ["_transform"], id="callback-unreached"),
        pytest.param({"Transform", "strparam"}, ["Transform", "strparam"], id="exports-unreached"),
    ],
)
def test_reachability_unreached_names_zero_coverage(tmp_path: Path, missing: set[str], expected: list[str]) -> None:
    target: Final = _TARGETS["xml-transform"]
    _record(tmp_path, target, missing)
    assert unreached(target, tmp_path) == expected


def test_reachability_matches_path_suffix_only(tmp_path: Path) -> None:
    target: Final = _TARGETS["xml-transform"]
    data: Final = CoverageData(basename=str(tmp_path / f"coverage_d_{fuzzer_name(target.name)}"))
    data.add_lines({f"/medio/other_{function.path}": sorted(function.lines) for function in target_functions(target)})
    data.write()
    assert unreached(target, tmp_path) == ["_transform", "Transform", "strparam", "transform"]


def test_reachability_missing_data_file(tmp_path: Path) -> None:
    assert unreached(_TARGETS["xml-transform"], tmp_path) == ["no coverage data at coverage_d_fuzz_xml_transform"]


@pytest.mark.parametrize(
    ("unrecorded", "status"),
    [
        pytest.param(None, 0, id="every-floor-met"),
        pytest.param("html-document", 1, id="one-target-without-data"),
    ],
)
def test_reachability_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], unrecorded: str | None, status: int
) -> None:
    for name in atheris_target_names():
        if name != unrecorded:
            _record(tmp_path, _TARGETS[name], set())
    assert (fuzz_reachability_main(["--coverage-dir", str(tmp_path)]), json.loads(capsys.readouterr().out)) == (
        status,
        {
            name: [f"no coverage data at coverage_d_{fuzzer_name(name)}"] if name == unrecorded else []
            for name in atheris_target_names()
        },
    )


def _record(directory: Path, target: Target, missing: set[str]) -> None:
    data: Final = CoverageData(basename=str(directory / f"coverage_d_{fuzzer_name(target.name)}"))
    executed: dict[str, set[int]] = {}
    for function in target_functions(target):
        if function.name not in missing:
            executed.setdefault(f"/pythoncovmergedfiles/medio/{function.path}", set()).update(function.lines)
    data.add_lines({path: sorted(lines) for path, lines in executed.items()})
    data.write()


if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(("round-trip", "none", "clean-url-fixpoint", True), id="first-checkout"),
        pytest.param(("round-trip", "sanitizer", "normalize-url-fixpoint", True), id="extend-old-checkout"),
        pytest.param(("round-trip", "both", "clean-url-fixpoint", False), id="reuse-complete-checkout"),
        pytest.param(("release-diff", "none", "html", False), id="release-does-not-fetch"),
        pytest.param(("round-trip", "none", "html-fixpoint", False), id="unrelated-oracle"),
        pytest.param(("round-trip", "none", None, True), id="all-oracles"),
        pytest.param(("round-trip", "none", "--oracle=normalize-url-fixpoint", True), id="equals-selection"),
    ],
)
def test_fuzz_cli_prepares_url_seeds(
    mocker: MockerFixture, tmp_path: Path, scenario: tuple[str, str, str | None, bool]
) -> None:
    mode, prepared, oracle, fetch = scenario
    if prepared != "none":
        (tmp_path / "tools/fuzz-data/wpt/sanitizer-api").mkdir(parents=True)
    if prepared == "both":
        urls = tmp_path / "tools/fuzz-data/wpt/url/resources/urltestdata.json"
        urls.parent.mkdir(parents=True)
        urls.write_text("[]", encoding="utf-8")
    selection = [] if oracle is None else [oracle] if oracle.startswith("--oracle=") else ["--oracle", oracle]
    mocker.patch("fuzz.fuzz._ROOT", tmp_path)
    mocker.patch.object(
        sys,
        "argv",
        [
            "fuzz.py",
            "--mode",
            mode,
            "--minutes",
            "0",
            "--crash-dir",
            str(tmp_path / "crashes"),
            *selection,
        ],
    )
    run = mocker.patch(
        "fuzz.fuzz.subprocess.run",
        autospec=True,
        return_value=mocker.MagicMock(returncode=2, stdout=str(tmp_path / "runtime")),
    )
    status = fuzz_url_cli_main()
    commands = [call.args[0] for call in run.call_args_list]
    sparse = [command for command in commands if "sparse-checkout" in command]
    child = commands[-1]
    assert (status, sparse, child[1:3], child[7:]) == (
        2,
        [
            [
                "git",
                "-C",
                "tools/fuzz-data/wpt",
                "sparse-checkout",
                "set",
                "--no-cone",
                "/sanitizer-api/",
                "/url/resources/urltestdata.json",
                "/LICENSE.md",
            ]
        ]
        if fetch
        else [],
        ["-m", "fuzz.round_trip_oracles" if mode == "round-trip" else "fuzz.release_diff"],
        selection,
    )


_HEAP_REPORTS: Final = {"heap-buffer-overflow", "heap-use-after-free"}


def _runner(mocker: MockerFixture, crash: tuple[int, str | None], injection: int) -> Callable[..., MagicMock]:
    """Fake the children: a ``None`` crash report gives each kind the ASan report it expects."""
    code, stderr = crash

    def run(command: list[str], **_: object) -> MagicMock:
        script = command[2] if command[1:2] == ["-c"] else ""
        if "_fuzz_crash" in script:
            kind = command[3]
            report = f"ERROR: AddressSanitizer: {kind if kind in _HEAP_REPORTS else 'use-after-poison'} on address"
            return mocker.MagicMock(returncode=code, stdout="", stderr=report if stderr is None else stderr)
        if "_fuzz_inject_failure" in script:
            return mocker.MagicMock(returncode=injection, stdout="", stderr="")
        return mocker.MagicMock(returncode=0, stdout="runtime", stderr="")

    return run


@pytest.mark.parametrize(
    ("crash", "injection", "expected"),
    [
        pytest.param((-6, None), 0, 0, id="every-fault-reported"),
        pytest.param((0, ""), 0, 1, id="sanitizer-silent"),
        pytest.param((-11, "ERROR: AddressSanitizer: SEGV on unknown address"), 0, 1, id="unrelated-crash"),
        pytest.param((-6, None), 3, 3, id="injection-not-raised"),
    ],
)
def test_fuzz_smoke_self_test_gates_the_run(
    mocker: MockerFixture, tmp_path: Path, crash: tuple[int, str | None], injection: int, expected: int
) -> None:
    mocker.patch("fuzz.fuzz.subprocess.run", autospec=True, side_effect=_runner(mocker, crash, injection))
    assert fuzz_smoke_main(["--mode", "smoke", "--crash-dir", str(tmp_path)]) == expected


def test_fuzz_smoke_self_test_skipped_without_inprocess(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch("fuzz.fuzz.subprocess.run", autospec=True, side_effect=_runner(mocker, (0, ""), 1))
    assert fuzz_smoke_main(["--mode", "smoke", "--skip-inprocess", "--crash-dir", str(tmp_path)]) == 0
