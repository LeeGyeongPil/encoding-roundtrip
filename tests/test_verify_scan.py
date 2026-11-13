"""verify finds drift; scan lists notable files in a table aligned by display width."""
from __future__ import annotations

import subprocess
import shutil
from pathlib import Path

import pytest

import roundtrip as rt
from conftest import JA_TEXT, KO_TEXT, write

ORIG = KO_TEXT.encode("cp949")


def _closed_file(cli, edit=b"") -> Path:
    p = write(Path("a.bas"), ORIG)
    assert cli("open", "a.bas").code == 0
    p.write_bytes(p.read_bytes() + edit)
    assert cli("close", "a.bas").code == 0
    return p


def test_verify_ok_after_close(cli):
    _closed_file(cli, "\n추가 줄".encode("utf-8"))
    r = cli("verify", "a.bas")
    assert r.code == 0 and r.summary["drift"] == "0" and r.summary["changed"] == "1"
    assert "changed lines: 6" in r.out


def test_verify_against_original_file(cli):
    write(Path("orig.bas"), ORIG)
    write(Path("new.bas"), ORIG.replace("이름".encode("cp949"), "성명".encode("cp949")))
    r = cli("verify", "new.bas", "--against", "orig.bas")
    assert r.code == 0 and r.summary["changed"] == "3"


def test_verify_detects_line_ending_drift(cli):
    p = _closed_file(cli)
    p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
    r = cli("verify", "a.bas")
    assert r.code == 1 and "E_DRIFT" in r.err and "eol" in r.summary["kinds"]
    assert "line ending changed on unchanged lines" in r.out


def test_verify_detects_final_newline_and_bom_drift(cli):
    p = _closed_file(cli)
    p.write_bytes(p.read_bytes() + b"\r\n")
    r = cli("verify", "a.bas")
    assert r.code == 1 and "final_newline" in r.summary["kinds"]
    p.write_bytes(b"\xef\xbb\xbf" + ORIG)
    r = cli("verify", "a.bas")
    assert r.code == 1 and "bom" in r.summary["kinds"]


def test_verify_detects_utf8_working_copy_left_behind(cli):
    write(Path("a.bas"), ORIG)
    cli("open", "a.bas")
    r = cli("verify", "a.bas", "--against", ".encoding-roundtrip/sessions/" + next(
        (Path(".encoding-roundtrip") / "sessions").glob("*.orig")).name, "--encoding", "cp949")
    assert r.code == 1 and "UTF-8 working copy" in r.out and "decode" in r.summary["kinds"]


def test_verify_detects_replacement_characters_and_mixed_encoding(cli):
    p = _closed_file(cli)
    p.write_bytes(p.read_bytes() + "\r\n�".encode("utf-8"))
    r = cli("verify", "a.bas")
    assert r.code == 1 and "decode" in r.summary["kinds"]            # U+FFFD in UTF-8 is not valid cp949 either
    write(Path("s.txt"), JA_TEXT.encode("shift_jis"))
    cli("open", "s.txt")
    cli("close", "s.txt")
    p2 = Path("s.txt")
    p2.write_bytes(p2.read_bytes() + "追加された行です\r\n".encode("utf-8"))
    r = cli("verify", "s.txt")
    assert r.code == 1 and r.summary["code"] == "E_MIXED_ENCODING" and "mixed" in r.summary["kinds"]


def test_verify_without_closed_session(cli):
    write(Path("a.bas"), ORIG)
    r = cli("verify", "a.bas")
    assert r.code == 2 and "--against" in r.err


def test_scan_table_and_summary(cli, tmp_path):
    write(tmp_path / "legacy" / "메시지.bas", ORIG)
    write(tmp_path / "legacy" / "b.bas", KO_TEXT.encode("euc_kr"))
    write(tmp_path / "legacy" / "bom.cs", b"\xef\xbb\xbfclass A {}\n")
    write(tmp_path / "win.txt", b"a\r\nb\r\n")
    write(tmp_path / "plain.txt", b"hello\n")
    write(tmp_path / "bin.dat", b"\x00\x01\x02")
    write(tmp_path / "logo.png", b"\x89PNG")
    mixed = ("こんにちは、世界。これは設定ファイルです。\r\n".encode("shift_jis") + "追加された行です\r\n".encode("utf-8")
             + "さようなら。またお会いしましょう。\r\n".encode("shift_jis"))
    write(tmp_path / "ja.txt", mixed)
    write(tmp_path / "eol.txt", b"a\r\nb\nc\r\n")
    r = cli("scan", ".")
    assert r.code == 0
    s = r.summary
    assert (s["legacy"], s["bom"], s["mixed_enc"], s["mixed_eol"]) == ("3", "1", "1", "1")
    assert int(s["crlf"]) >= 4 and "mixed-enc" in r.out and "mixed-eol" in r.out
    assert "plain.txt" not in r.out and "logo.png" not in r.out
    table = [ln for ln in r.out.split("\n") if ln.startswith(("path", "-----", "legacy", "ja.txt", "win.txt"))]
    header_end = table[0].index("encoding")
    assert all(rt.dwidth(ln[:ln.index("  ")]) <= header_end for ln in table[2:] if "  " in ln)
    first_cols = [ln for ln in r.out.split("\n") if "메시지.bas" in ln][0]
    plain_cols = [ln for ln in r.out.split("\n") if ln.startswith("legacy/b.bas")][0]
    # the column after the path starts at the same display width in both rows
    assert rt.dwidth(first_cols[:first_cols.index("euc_kr")]) ==rt.dwidth(plain_cols[:plain_cols.index("euc_kr")])
    report = (tmp_path / ".encoding-roundtrip" / "report.json").read_bytes().decode("utf-8")
    assert '"cmd": "scan"' in report


def test_scan_ambiguous_file_uses_folder_majority(cli, tmp_path):
    write(tmp_path / "d" / "a.txt", KO_TEXT.encode("cp949"))
    write(tmp_path / "d" / "short.txt", "한글".encode("euc_kr"))
    r = cli("scan", "d")
    assert r.summary["ambiguous"] == "0"
    assert "most common legacy encoding" in r.out


def test_scan_reports_open_sessions_and_plain_repo(cli, tmp_path):
    write(tmp_path / "ok.txt", b"hello\n")
    r = cli("scan", ".")
    assert "plain UTF-8/ASCII with LF" in r.out
    write(tmp_path / "a.bas", ORIG)
    cli("open", "a.bas")
    r = cli("scan", ".")
    assert "open (not closed) sessions: a.bas" in r.out and r.summary["open_sessions"] == "1"


def test_scan_respects_max_files_and_skips_oversize(cli, tmp_path):
    for i in range(5):
        write(tmp_path / f"f{i}.txt", b"x\r\n")
    r = cli("scan", ".", "--max-files", "2")
    assert r.summary["files"] == "2"
    write(tmp_path / "huge.txt", b"a" * (rt.MAX_BYTES + 1))
    assert cli("scan", ".").code == 0


def test_scan_in_git_checkout_uses_git_listing(cli, tmp_path):
    if not shutil.which("git"):
        pytest.skip("git not installed")
    init = subprocess.run(["git", "init", "-q", str(tmp_path)], capture_output=True)
    if init.returncode != 0:
        pytest.skip("git init is not possible here")
    write(tmp_path / "keep.bas", ORIG)
    write(tmp_path / "ignored.bas", ORIG)
    write(tmp_path / ".gitignore", b"ignored.bas\n")
    r = cli("scan", ".")
    assert r.code == 0 and "keep.bas" in r.out and "ignored.bas" not in r.out


def test_table_rendering_is_width_aware():
    lines = rt.render_table(["path", "enc"], [["한글.txt", "cp949"], ["abcdef.txt", "euc_kr"]])
    starts = [rt.dwidth(ln[:ln.index(ln.split()[-1])]) for ln in lines if not ln.startswith("-")]
    assert len(set(starts)) == 1
    assert rt.dwidth("한a") == 3 and rt.dwidth("é") == 1
