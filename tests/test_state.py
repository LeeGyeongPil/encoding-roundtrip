"""Regression for the rule 'verify the original state before any write': refusals never touch the target file."""
from __future__ import annotations

from pathlib import Path

import roundtrip as rt
from conftest import KO_TEXT, sha, write

ORIG = KO_TEXT.encode("cp949")


def _session_files(state: Path) -> list[Path]:
    return sorted((state / "sessions").glob("*"))


def _open(cli, name="a.bas") -> Path:
    p = write(Path(name), ORIG)
    assert cli("open", name).code == 0
    return p


def test_close_without_open(cli):
    p = write(Path("a.bas"), ORIG)
    r = cli("close", "a.bas")
    assert r.code == 3 and "E_NOT_OPEN" in r.err and r.summary["code"] == "E_NOT_OPEN"
    assert p.read_bytes() == ORIG
    r = cli("abort", "a.bas")
    assert r.code == 3 and p.read_bytes() == ORIG


def test_double_open(cli):
    p = _open(cli)
    working = p.read_bytes()
    r = cli("open", "a.bas")
    assert r.code == 3 and "E_ALREADY_OPEN" in r.err
    assert p.read_bytes() == working                                       # the working copy was not re-converted
    assert cli("close", "a.bas").code == 0 and p.read_bytes() == ORIG


def test_open_force_restarts_from_current_bytes(cli):
    p = _open(cli)
    state = Path(".encoding-roundtrip")
    r = cli("open", "a.bas", "--force")
    assert r.code == 0
    assert (state / "stale").is_dir() and list((state / "stale").glob("*.orig"))
    assert p.read_bytes().decode("utf-8")


def test_backup_tampered_requires_force(cli):
    p = _open(cli)
    state = Path(".encoding-roundtrip")
    backup = next((state / "sessions").glob("*.orig"))
    backup.write_bytes(backup.read_bytes() + b"x")
    working = p.read_bytes()
    r = cli("close", "a.bas")
    assert r.code == 3 and "E_STATE_MISMATCH" in r.err and "--force" in r.err
    assert p.read_bytes() == working
    r = cli("close", "a.bas", "--force")
    assert r.code == 0
    assert p.read_bytes() == ORIG                                           # the working copy text wins over the tampered backup


def test_backup_missing(cli):
    p = _open(cli)
    working = p.read_bytes()
    next((Path(".encoding-roundtrip") / "sessions").glob("*.orig")).unlink()
    r = cli("close", "a.bas", "--force")
    assert r.code == 3 and "E_STATE_MISMATCH" in r.err and p.read_bytes() == working


def test_working_copy_not_utf8_never_written(cli):
    p = _open(cli)
    p.write_bytes(ORIG)                                                    # something wrote legacy bytes over the working copy
    before = sha(p.read_bytes())
    for extra in ([], ["--force"]):
        r = cli("close", "a.bas", *extra)
        assert r.code == 3 and "E_STATE_MISMATCH" in r.err and "abort" in r.err
        assert sha(p.read_bytes()) == before
    assert cli("status").summary["open"] == "1"


def test_abort_restores_original(cli):
    p = _open(cli)
    p.write_bytes(p.read_bytes() + "추가\n".encode("utf-8"))
    r = cli("abort", "a.bas")
    assert r.code == 0 and p.read_bytes() == ORIG
    assert not _session_files(Path(".encoding-roundtrip"))
    saved = next((Path(".encoding-roundtrip") / "aborted").glob("*.work"))
    assert saved.read_bytes().decode("utf-8").endswith("추가\n")
    assert cli("status").summary["open"] == "0"


def test_abort_when_file_already_original(cli):
    p = _open(cli)
    p.write_bytes(ORIG)
    r = cli("abort", "a.bas")
    assert r.code == 0 and p.read_bytes() == ORIG and not _session_files(Path(".encoding-roundtrip"))


def test_abort_refuses_unknown_content_without_force(cli):
    p = _open(cli)
    p.write_bytes("한글 아님\xff".encode("latin-1", "replace") + b"\xb0\xa1\xff")
    other = p.read_bytes()
    r = cli("abort", "a.bas")
    assert r.code == 3 and "E_STATE_MISMATCH" in r.err and p.read_bytes() == other
    assert cli("abort", "a.bas", "--force").code == 0 and p.read_bytes() == ORIG


def test_status_shows_states(cli):
    p = _open(cli)
    assert "working copy untouched" in cli("status").out
    p.write_bytes(p.read_bytes() + b"x")
    assert "working copy edited" in cli("status").out
    p.write_bytes(ORIG)
    assert "changed outside the session" in cli("status").out
    p.unlink()
    assert "file missing" in cli("status").out
    assert "no open files" not in cli("status").out
    cli("--lang", "ko", "status")


def test_missing_file_and_directory(cli):
    r = cli("open", "nope.txt")
    assert r.code == 2 and "E_NOT_FOUND" in r.err
    Path("d").mkdir()
    assert cli("inspect", "d").code == 2
    assert cli("scan", "missing-dir").code == 2
    assert cli("verify", "nope.txt").code == 2


def test_state_dir_defaults_to_git_root(cli, tmp_path):
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "src" / "legacy"
    sub.mkdir(parents=True)
    write(sub / "a.bas", ORIG)
    assert cli("open", "src/legacy/a.bas").code == 0
    assert (tmp_path / ".encoding-roundtrip" / "sessions").is_dir()
    assert (tmp_path / ".encoding-roundtrip" / ".gitignore").read_bytes() == b"*\n"
    assert not (sub / ".encoding-roundtrip").exists()
    assert cli("close", "src/legacy/a.bas").code == 0


def test_explicit_state_dir(cli, tmp_path):
    write(Path("a.bas"), ORIG)
    custom = tmp_path / "elsewhere"
    assert cli("open", "a.bas", "--state-dir", str(custom)).code == 0
    assert cli("close", "a.bas").code == 3                                    # default state dir has no session
    assert cli("close", "a.bas", "--state-dir", str(custom)).code == 0


def test_session_key_is_stable_per_path(tmp_path):
    assert rt.session_key(tmp_path / "a") == rt.session_key(tmp_path / "a")
    assert rt.session_key(tmp_path / "a") != rt.session_key(tmp_path / "b")


def test_unwritable_state_dir_reports_io_error(cli, tmp_path):
    write(Path("a.bas"), ORIG)
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"file, not a folder")
    r = cli("open", "a.bas", "--state-dir", str(blocker / "state"))
    assert r.code == 2 and "E_IO" in r.err
    assert Path("a.bas").read_bytes() == ORIG
