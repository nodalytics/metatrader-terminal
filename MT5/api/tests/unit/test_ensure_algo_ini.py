"""`ensure_algo_ini.py`: set one value in a file that also holds the account credentials.

The script exists because `run-mt5.sh` wrote `[Experts] Enabled=1` only inside its install branch,
so it was set once and never reasserted - and MT5 rewrites `common.ini` when it exits, so a terminal
that exited with AutoTrading off left it off for every launch afterwards. That refused every order
with retcode 10027 on 2026-09-24.

**The test that matters most is `test_credentials_survive`.** The obvious fix - rewrite the file the
way the installer does - destroys the account, because a live `common.ini` holds MT5's encrypted
`Environment` blob along with `Login` and `Server`. Everything else here is boundary work; that one
is the reason the script is 200 lines instead of two.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent.parent.parent / "assets" / "ensure_algo_ini.py"

BOM = b"\xff\xfe"


def load():
    """Import the script by path - it lives in `assets/`, which is not a package."""
    spec = importlib.util.spec_from_file_location("ensure_algo_ini", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["ensure_algo_ini"] = module
    spec.loader.exec_module(module)
    return module


mod = load()


#: A realistic file: what MT5 actually writes back, not what the installer wrote.
LIVE = [
    "[Experts]",
    "Enabled=0",
    "AllowDllImport=0",
    "Account=1",
    "Profile=1",
    "[Common]",
    "Environment=F008C7293503CED0045B07842FC57A638AA2FD5F10DEE8EA7AD19815A339B9A2",
    "Login=6258778",
    "Server=Deriv-Demo",
    "NewsEnable=1",
    "[Charts]",
    "MaxBars=100000",
]


def write(path: Path, lines: list[str]) -> None:
    path.write_bytes(BOM + "\r\n".join(lines).encode("utf-16-le") + "\r\n".encode("utf-16-le"))


def read(path: Path) -> list[str]:
    return path.read_bytes().decode("utf-16").splitlines()


@pytest.fixture()
def ini(tmp_path):
    path = tmp_path / "common.ini"
    write(path, LIVE)
    return path


# --------------------------------------------------------- the important one


def test_credentials_survive(ini):
    """Every line but the one being changed comes back byte-identical."""
    assert mod.main(["prog", str(ini)]) == 0
    after = read(ini)
    assert "Environment=F008C7293503CED0045B07842FC57A638AA2FD5F10DEE8EA7AD19815A339B9A2" in after
    assert "Login=6258778" in after
    assert "Server=Deriv-Demo" in after
    # And nothing else moved: same lines, same order, one value different.
    expected = ["Enabled=1" if line == "Enabled=0" else line for line in LIVE]
    assert after == expected


def test_it_changes_exactly_one_value(ini):
    before = read(ini)
    mod.main(["prog", str(ini)])
    after = read(ini)
    differing = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert differing == [("Enabled=0", "Enabled=1")]


# ------------------------------------------------------------------ encoding


def test_the_file_stays_utf16le_with_a_mark(ini):
    """MT5 silently ignores a file in any other encoding, which reports as success."""
    mod.main(["prog", str(ini)])
    raw = ini.read_bytes()
    assert raw.startswith(BOM)
    assert raw.decode("utf-16").startswith("[Experts]")


def test_line_endings_stay_crlf(ini):
    mod.main(["prog", str(ini)])
    text = ini.read_bytes().decode("utf-16")
    assert "\r\n" in text
    assert "\n" not in text.replace("\r\n", "")


# ----------------------------------------------------------------- behaviour


def test_an_already_correct_file_is_left_alone(tmp_path):
    """Idempotent, so running it on every launch accumulates nothing."""
    path = tmp_path / "common.ini"
    good = ["Enabled=1" if line == "Enabled=0" else line for line in LIVE]
    write(path, good)
    before = path.read_bytes()
    assert mod.main(["prog", str(path)]) == 0
    assert path.read_bytes() == before
    assert not (tmp_path / "common.ini.before-algo-fix").exists()


def test_a_backup_is_kept_when_something_changes(ini):
    mod.main(["prog", str(ini)])
    backup = Path(str(ini) + ".before-algo-fix")
    assert backup.exists()
    assert "Enabled=0" in read(backup)


def test_the_backup_is_not_overwritten_by_a_later_run(ini):
    mod.main(["prog", str(ini)])
    backup = Path(str(ini) + ".before-algo-fix")
    first = backup.read_bytes()
    write(ini, LIVE)  # knocked back to off
    mod.main(["prog", str(ini)])
    assert backup.read_bytes() == first


def test_a_missing_key_is_added_to_the_right_section(tmp_path):
    path = tmp_path / "common.ini"
    write(path, ["[Experts]", "AllowDllImport=0", "[Common]", "Login=1"])
    assert mod.main(["prog", str(path)]) == 0
    after = read(path)
    assert after.index("Enabled=1") < after.index("[Common]")


def test_a_missing_section_is_appended(tmp_path):
    path = tmp_path / "common.ini"
    write(path, ["[Common]", "Login=1"])
    assert mod.main(["prog", str(path)]) == 0
    after = read(path)
    assert "[Experts]" in after
    assert "Enabled=1" in after
    assert "Login=1" in after


def test_a_missing_file_is_created(tmp_path):
    path = tmp_path / "common.ini"
    assert mod.main(["prog", str(path)]) == 0
    assert read(path) == ["[Experts]", "Enabled=1"]


def test_the_wrong_sections_enabled_is_not_touched(tmp_path):
    """`Enabled` is a common key name; setting the wrong one is a silent no-op."""
    path = tmp_path / "common.ini"
    write(path, ["[News]", "Enabled=0", "[Experts]", "Enabled=0"])
    mod.main(["prog", str(path)])
    after = read(path)
    assert after == ["[News]", "Enabled=0", "[Experts]", "Enabled=1"]


def test_section_names_are_matched_case_insensitively(tmp_path):
    path = tmp_path / "common.ini"
    write(path, ["[experts]", "enabled=0"])
    assert mod.main(["prog", str(path)]) == 0
    assert "Enabled=1" in read(path)


def test_a_utf8_file_is_repaired_rather_than_refused(tmp_path):
    """Some other tool having rewritten it is not a reason to leave AutoTrading off."""
    path = tmp_path / "common.ini"
    path.write_text("[Experts]\r\nEnabled=0\r\n", encoding="utf-8")
    assert mod.main(["prog", str(path)]) == 0
    assert path.read_bytes().startswith(BOM)
    assert "Enabled=1" in read(path)


def test_bad_usage_is_reported(capsys):
    assert mod.main(["prog"]) == 1


def test_an_unwritable_path_fails_rather_than_claims_success(tmp_path):
    """Exit status is what `run-mt5.sh` branches on, so it has to be honest."""
    directory = tmp_path / "nope"
    assert mod.main(["prog", str(directory / "common.ini")]) == 1
