"""Open/close round trips: bytes survive, edits touch only their lines, unwritable characters are refused."""
from __future__ import annotations

from pathlib import Path

import pytest

import roundtrip as rt
from conftest import DE_TEXT, KO_TEXT, SAMPLES, sha, write

MIXED_KO = "첫 줄\r\n둘째 줄\n셋째 줄\r\n넷째"          # CRLF, LF, CRLF, (none)
CR_ONLY = "첫 줄\r둘째 줄\r셋째 줄"


NOOP_CASES = [
    ("cp949", KO_TEXT.encode("cp949"), "cp949"),
    ("euc_kr", KO_TEXT.encode("euc_kr"), "euc_kr"),
    ("shift_jis", "こんにちは\r\n世界\r\n".encode("shift_jis"), "shift_jis"),
    ("gbk", "这是配置文件\r\n请勿修改".encode("gbk"), "gbk"),
    ("cp1250", "Příliš žluťoučký kůň\r\n".encode("cp1250"), "cp1250"),
    ("cp1251", "Привет, мир\r\n".encode("cp1251"), "cp1251"),
    ("cp1252", DE_TEXT.encode("cp1252"), "cp1252"),
    ("utf8_bom_crlf", b"\xef\xbb\xbf" + "using System;\r\n// 한글\r\n".encode("utf-8"), "utf-8"),
    ("utf16_le_bom", b"\xff\xfe" + "a\r\n한글\r\nb".encode("utf-16-le"), "utf-16-le"),
    ("utf16_be_bom", b"\xfe\xff" + "a\nb\n".encode("utf-16-be"), "utf-16-be"),
    ("mixed_eol", MIXED_KO.encode("cp949"), "cp949"),
    ("cr_only", CR_ONLY.encode("cp949"), "cp949"),
    ("no_final_newline", "한글 주석".encode("cp949"), "cp949"),
    ("blank_lines", "가\r\n\r\n\r\n나\r\n".encode("cp949"), "cp949"),
    ("empty", b"", "ascii"),
    ("ascii_lf", b"a\nb\n", "ascii"),
]


@pytest.mark.parametrize("name,data,codec", NOOP_CASES, ids=[c[0] for c in NOOP_CASES])
def test_noop_roundtrip_byte_exact(cli, name, data, codec):
    p = write(Path("f.txt"), data)
    r = cli("open", "f.txt", "--encoding", codec)
    assert r.code == 0, r.err
    working = p.read_bytes()
    assert b"\r" not in working and not working.startswith(b"\xef\xbb\xbf")
    working.decode("utf-8")
    r = cli("close", "f.txt")
    assert r.code == 0, r.err
    assert p.read_bytes() == data
    assert r.summary["changed"] == "0"


def test_noop_roundtrip_with_auto_detection(cli):
    data = KO_TEXT.encode("cp949")
    p = write(Path("a.bas"), data)
    assert cli("open", "a.bas").code == 0
    assert cli("close", "a.bas").code == 0
    assert p.read_bytes() == data


def test_edit_keeps_per_line_eol_and_final_newline(cli):
    p = write(Path("m.txt"), MIXED_KO.encode("cp949"))
    assert cli("open", "m.txt", "--encoding", "cp949").code == 0
    text = p.read_bytes().decode("utf-8").replace("둘째 줄", "둘째 줄 (수정됨)") + "\n다섯째"
    p.write_bytes(text.encode("utf-8"))
    r = cli("close", "m.txt")
    assert r.code == 0, r.err
    expected = "첫 줄\r\n둘째 줄 (수정됨)\n셋째 줄\r\n넷째\r\n다섯째".encode("cp949")
    assert p.read_bytes() == expected
    assert (r.summary["eol"], r.summary["eol_cr"], r.summary["final_newline"]) == ("crlf:3,lf:1", "0", "no")
    assert r.summary["changed"] == "2"
    info = rt.analyze(p.read_bytes(), forced="cp949")
    assert info["eol"] == {"lf": 1, "crlf": 3, "cr": 0} and info["final_newline"] is False


def test_unchanged_lines_keep_original_bytes_even_if_codec_has_duplicates(cli):
    # cp932 maps several byte pairs to one character; copying original bytes keeps them intact.
    original = b"\x87\x54 first\r\n\xfa\x4a second\r\nplain\r\n"          # NEC and IBM codes of the same Roman numeral
    p = write(Path("d.txt"), original)
    assert cli("open", "d.txt", "--encoding", "cp932").code == 0
    text = p.read_bytes().decode("utf-8").replace("plain", "changed")
    p.write_bytes(text.encode("utf-8"))
    assert cli("close", "d.txt").code == 0
    assert p.read_bytes() == original.replace(b"plain", b"changed")


def test_edit_replace_line_keeps_its_ending_and_delete_insert(cli):
    p = write(Path("e.txt"), "하나\r\n둘\n셋\r\n넷\r\n".encode("cp949"))
    cli("open", "e.txt", "--encoding", "cp949")
    p.write_bytes("하나\n둘 수정\n넷\n새 줄\n".encode("utf-8"))      # line 3 deleted, line 2 edited, new last line
    r = cli("close", "e.txt")
    assert r.code == 0
    assert p.read_bytes() == "하나\r\n둘 수정\n넷\r\n새 줄\r\n".encode("cp949")
    assert r.summary["changed"] == "3"


def test_multi_line_replace_blocks(cli):
    p = write(Path("g.txt"), "a\r\nb\nc\r\nd\r\n".encode("cp949"))
    cli("open", "g.txt", "--encoding", "cp949")
    p.write_bytes(b"a\nB1\nB2\nB3\nd\n")                                   # b,c -> B1,B2,B3
    assert cli("close", "g.txt").code == 0
    assert p.read_bytes() == b"a\r\nB1\nB2\r\nB3\r\nd\r\n"


def test_final_newline_options(cli):
    p = write(Path("n.txt"), "가\r\n나".encode("cp949"))
    cli("open", "n.txt", "--encoding", "cp949")
    p.write_bytes("가\n나\n다\n".encode("utf-8"))
    assert cli("close", "n.txt").code == 0                                  # keep: original had no final newline
    assert p.read_bytes() == "가\r\n나\r\n다".encode("cp949")
    cli("open", "n.txt", "--encoding", "cp949")
    assert cli("close", "n.txt", "--final-newline", "add").code == 0
    assert p.read_bytes() == "가\r\n나\r\n다\r\n".encode("cp949")
    cli("open", "n.txt", "--encoding", "cp949")
    assert cli("close", "n.txt", "--final-newline", "remove").code == 0
    assert p.read_bytes() == "가\r\n나\r\n다".encode("cp949")


def test_empty_file_gets_content(cli):
    p = write(Path("z.txt"), b"")
    cli("open", "z.txt")
    p.write_bytes("한글 내용\n".encode("utf-8"))
    r = cli("close", "z.txt")
    assert r.code == 1 and "E_UNENCODABLE" in r.err                       # ascii-only file: refuse, tell how to proceed
    assert "ascii_only" in r.err
    cli("abort", "z.txt")
    cli("open", "z.txt", "--encoding", "cp949")
    p.write_bytes("한글 내용\n".encode("utf-8"))
    assert cli("close", "z.txt").code == 0
    assert p.read_bytes() == "한글 내용\n".encode("cp949")


def test_bom_preserved(cli):
    data = b"\xef\xbb\xbf" + "class A {}\r\n// 주석\r\n".encode("utf-8")
    p = write(Path("a.cs"), data)
    cli("open", "a.cs")
    assert not p.read_bytes().startswith(b"\xef\xbb\xbf")
    p.write_bytes(p.read_bytes().replace(b"class A", b"class B"))
    r = cli("close", "a.cs")
    assert r.code == 0 and r.summary["bom"] == "yes"
    assert p.read_bytes() == b"\xef\xbb\xbf" + "class B {}\r\n// 주석\r\n".encode("utf-8")
    u16 = b"\xff\xfe" + "x = 1\r\ny = 2".encode("utf-16-le")
    q = write(Path("u.txt"), u16)
    cli("open", "u.txt")
    q.write_bytes(b"x = 10\ny = 2")
    assert cli("close", "u.txt").code == 0
    assert q.read_bytes() == b"\xff\xfe" + "x = 10\r\ny = 2".encode("utf-16-le")


def test_bom_in_working_copy_and_crlf_in_working_copy_are_tolerated(cli):
    p = write(Path("t.txt"), "가\r\n나\r\n".encode("cp949"))
    cli("open", "t.txt", "--encoding", "cp949")
    p.write_bytes(b"\xef\xbb\xbf" + "가\r\n다\r\n".encode("utf-8"))
    assert cli("close", "t.txt").code == 0
    assert p.read_bytes() == "가\r\n다\r\n".encode("cp949")


EUC_KR_TEXT = "가격: 1000원 — 확인 ✓\r\n이모지 😀\r\n추가: 똠\r\n"


def _open_euc_kr(cli) -> Path:
    p = write(Path("k.txt"), "가격은 천원입니다 확인해 주세요 저장 실패\r\n".encode("euc_kr"))
    assert cli("open", "k.txt").code == 0
    p.write_bytes(EUC_KR_TEXT.encode("utf-8"))
    return p


def test_unencodable_refused_with_positions(cli):
    p = _open_euc_kr(cli)
    before = sha(p.read_bytes())
    r = cli("close", "k.txt")
    assert r.code == 1
    for item in ("U+2014 at 1:11", "U+2713 at 1:16", "U+1F600 at 2:5"):
        assert item in r.err
    assert r.summary["code"] == "E_UNENCODABLE" and r.summary["status"] == "error"
    assert sha(p.read_bytes()) == before                                  # working copy untouched, nothing written
    assert cli("status").summary["open"] == "1"                           # still open for another try
    report = (Path(".encoding-roundtrip") / "report.json").read_bytes().decode("utf-8")
    assert '"codepoint": "U+2014"' in report


def test_euc_kr_filler_refused(cli):
    p = _open_euc_kr(cli)
    r = cli("close", "k.txt")
    assert "U+B620 at 3:5 (euc_kr_filler)" in r.err
    assert "똠".encode("euc_kr") != "똠".encode("cp949")                     # the trap exists in Python itself
    p.write_bytes("추가: 똠\n".encode("utf-8"))
    r = cli("close", "k.txt")
    assert r.code == 1 and "euc_kr_filler" in r.err
    cli("abort", "k.txt")
    cli("open", "k.txt", "--encoding", "cp949")
    p.write_bytes("추가: 똠\n".encode("utf-8"))
    assert cli("close", "k.txt").code == 0
    assert p.read_bytes() == "추가: 똠\r\n".encode("cp949")


def test_replace_xmlcharref(cli):
    p = _open_euc_kr(cli)
    r = cli("close", "k.txt", "--replace", "xmlcharref")
    assert r.code == 0, r.err
    assert p.read_bytes() == ("가격: 1000원 &#8212; 확인 &#10003;\r\n이모지 &#128512;\r\n추가: &#46624;\r\n").encode("euc_kr")
    assert r.summary["replaced"] == "4" and "replaced" in r.out


def test_replace_question(cli):
    p = _open_euc_kr(cli)
    assert cli("close", "k.txt", "--replace", "question").code == 0
    assert p.read_bytes() == "가격: 1000원 ? 확인 ?\r\n이모지 ?\r\n추가: ?\r\n".encode("euc_kr")


def test_lossy_roundtrip_is_refused(cli):
    p = write(Path("j.txt"), "こんにちは\r\n".encode("shift_jis"))
    cli("open", "j.txt", "--encoding", "shift_jis")
    p.write_bytes("こんにちは ¥100\n".encode("utf-8"))                      # shift_jis writes 0x5C, which reads back as a backslash
    r = cli("close", "j.txt")
    assert r.code == 1 and "lossy_roundtrip" in r.err and "U+00A5 at 1:7" in r.err


def test_unencodable_list_is_capped_in_message(cli):
    p = write(Path("c.txt"), "가\r\n".encode("cp949"))
    cli("open", "c.txt", "--encoding", "cp949")
    p.write_bytes(("😀" * 30 + "\n").encode("utf-8"))
    r = cli("close", "c.txt")
    assert r.code == 1 and "(+10)" in r.err and r.summary["count"] == "30"


def test_close_is_atomic_and_keeps_file_mode(cli):
    p = write(Path("p.txt"), "가나다\r\n".encode("cp949"))
    p.chmod(0o640)
    cli("open", "p.txt", "--encoding", "cp949")
    p.write_bytes("가나다라\n".encode("utf-8"))
    assert cli("close", "p.txt").code == 0
    assert p.stat().st_mode & 0o777 == 0o640
    assert not [x for x in Path(".").iterdir() if x.name.startswith(".er-")]


def test_json_output(cli):
    write(Path("a.bas"), KO_TEXT.encode("cp949"))
    r = cli("inspect", "a.bas", "--json")
    import json
    data = json.loads(r.out)
    assert data["cmd"] == "inspect" and data["files"][0]["encoding"] in ("cp949", "euc_kr")
    assert data["summary"].startswith("ER-SUMMARY ")


def test_small_edit_in_large_repetitive_file_is_fast(cli):
    import time
    line = "Public Const MSG_A = \"저장 실패했습니다\"\r\n"
    data = (line * 20000).encode("cp949")
    p = write(Path("big.bas"), data)
    assert cli("open", "big.bas", "--encoding", "cp949").code == 0
    p.write_bytes(p.read_bytes().replace(b"MSG_A", b"MSG_B", 1))
    started = time.time()
    r = cli("close", "big.bas")
    assert r.code == 0 and time.time() - started < 10
    assert p.read_bytes() == data.replace(b"MSG_A", b"MSG_B", 1)


def test_diff_ops_fallback_for_huge_changed_blocks(cli, monkeypatch):
    monkeypatch.setattr(rt, "MATCH_LIMIT", 0)
    original = "가\r\n나\n다\r\n라\r\n".encode("cp949")
    p = write(Path("h.txt"), original)
    cli("open", "h.txt", "--encoding", "cp949")
    p.write_bytes("하\n나\n새\n라\n마\n".encode("utf-8"))
    r = cli("close", "h.txt")
    assert r.code == 0
    assert p.read_bytes() == "하\r\n나\n새\r\n라\r\n마\r\n".encode("cp949")      # the equal pair keeps its own bytes and ending
    assert r.summary["changed"] == "3"
    assert rt.diff_ops(["a"], ["a", "b"], ) == [("equal", 0, 1, 0, 1), ("insert", 1, 1, 1, 2)]
    monkeypatch.setattr(rt, "MATCH_LIMIT", 0)
    assert rt.diff_ops(["a", "x"], ["a"]) == [("equal", 0, 1, 0, 1), ("delete", 1, 2, 1, 1)]
    assert rt.diff_ops(["a", "x"], ["a", "y"]) == [("equal", 0, 1, 0, 1), ("replace", 1, 2, 1, 2)]


def test_diff_ops_edges():
    assert rt.diff_ops([], []) == [("equal", 0, 0, 0, 0)]
    assert rt.diff_ops(["a"], ["b"]) == [("replace", 0, 1, 0, 1)]
    assert rt.diff_ops(["a", "b", "c"], ["a", "c"]) == [("equal", 0, 1, 0, 1), ("delete", 1, 2, 1, 1), ("equal", 2, 3, 1, 2)]
