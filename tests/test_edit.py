"""One-call `edit`: same bytes as open -> working copy -> close, refusals never touch the file."""
from __future__ import annotations

from pathlib import Path

import pytest

import roundtrip as rt
from conftest import KO_TEXT, SAMPLES, sha, write

MIXED_KO = "첫 줄\r\n둘째 줄\n셋째 줄\r\n넷째"          # CRLF, LF, CRLF, (none)


def open_close(cli, name: str, change) -> bytes:
    """Reference result: open, change the UTF-8 working copy with `change(text)`, close."""
    p = Path(name)
    assert cli("open", name).code == 0
    text = p.read_bytes().decode("utf-8")
    p.write_bytes(change(text).encode("utf-8"))
    r = cli("close", name)
    assert r.code == 0, r.err
    return p.read_bytes()


@pytest.mark.parametrize("codec", sorted(SAMPLES))
def test_edit_matches_open_close_bytes(cli, codec):
    data = SAMPLES[codec]
    text = data.decode(codec)
    first_line = text.split("\r\n")[0]
    word = first_line.strip("' ")[:2]
    write(Path("a.txt"), data)
    write(Path("b.txt"), data)
    r = cli("edit", "a.txt", "--encoding", codec, "--old", word, "--new", word + word, "--append-line", word)
    assert r.code == 0, r.err
    rt_bytes = Path("a.txt").read_bytes()

    def change(t: str) -> str:
        t = t.replace(word, word + word, 1)
        return t + word + "\n" if t.endswith("\n") else t + "\n" + word
    assert cli("open", "b.txt", "--encoding", codec).code == 0
    p = Path("b.txt")
    p.write_bytes(change(p.read_bytes().decode("utf-8")).encode("utf-8"))
    assert cli("close", "b.txt").code == 0
    assert rt_bytes == p.read_bytes()
    assert r.summary["cmd"] == "edit" and r.summary["changed"] == "2"
    assert r.summary["result_sha256"] == sha(rt_bytes)


def test_edit_replace_keeps_other_lines_bytes(cli):
    data = MIXED_KO.encode("cp949")
    p = write(Path("m.txt"), data)
    r = cli("edit", "m.txt", "--encoding", "cp949", "--old", "둘째 줄", "--new", "둘째 줄 (수정됨)")
    assert r.code == 0, r.err
    out = p.read_bytes()
    assert out == data.replace("둘째 줄".encode("cp949"), "둘째 줄 (수정됨)".encode("cp949"))
    s = r.summary
    assert (s["eol"], s["final_newline"], s["changed"], s["bom"]) == ("crlf:2,lf:1", "no", "1", "none")


def test_edit_append_line_after_line_without_newline(cli):
    data = MIXED_KO.encode("cp949")
    p = write(Path("m.txt"), data)
    r = cli("edit", "m.txt", "--encoding", "cp949", "--append-line", "다섯째")
    assert r.code == 0, r.err
    assert p.read_bytes() == data + "\r\n다섯째".encode("cp949")      # majority ending, still no final newline
    assert r.summary["eol"] == "crlf:3,lf:1" and r.summary["final_newline"] == "no"


def test_edit_line_operations(cli):
    data = "가\r\n나\r\n다\r\n".encode("cp949")
    p = write(Path("l.txt"), data)
    r = cli("edit", "l.txt", "--encoding", "cp949", "--set-line", "2:둘", "--delete-line", "3",
            "--insert-after", "0:영", "--insert-after", "1:하나 반")
    assert r.code == 0, r.err
    assert p.read_bytes() == "영\r\n가\r\n하나 반\r\n둘\r\n".encode("cp949")


def test_edit_keeps_bom_and_crlf(cli):
    data = b"\xef\xbb\xbf" + "using System;\r\n// 버전 1.2\r\n".encode("utf-8")
    p = write(Path("p.cs"), data)
    r = cli("edit", "p.cs", "--old", "1.2", "--new", "1.3")
    assert r.code == 0, r.err
    assert p.read_bytes() == data.replace(b"1.2", b"1.3")
    assert r.summary["bom"] == "yes" and r.summary["codec"] == "utf-8"


def test_edit_all_and_many_matches(cli):
    data = "가 가 가\r\n".encode("cp949") + KO_TEXT.encode("cp949")
    p = write(Path("x.txt"), data)
    r = cli("edit", "x.txt", "--encoding", "cp949", "--old", "가", "--new", "나")
    assert r.code == 1 and "E_MANY_MATCHES" in r.err and r.summary["code"] == "E_MANY_MATCHES"
    assert p.read_bytes() == data
    r = cli("edit", "x.txt", "--encoding", "cp949", "--old", "가", "--new", "나", "--all")
    assert r.code == 0, r.err
    assert "가".encode("cp949") not in p.read_bytes()


@pytest.mark.parametrize("argv,code,err", [
    (["--old", "없는 문구", "--new", "x"], 1, "E_NO_MATCH"),
    (["--old", "주석"], 2, "E_BAD_EDIT"),
    (["--set-line", "99:x"], 2, "E_BAD_EDIT"),
    (["--set-line", "x"], 2, "E_BAD_EDIT"),
    (["--delete-line", "0"], 2, "E_BAD_EDIT"),
    (["--set-line", "1:x", "--delete-line", "1"], 2, "E_BAD_EDIT"),
    (["--old", "", "--new", "x"], 2, "E_BAD_EDIT"),
    ([], 2, "E_BAD_EDIT"),
])
def test_edit_refusals_leave_file_untouched(cli, argv, code, err):
    data = KO_TEXT.encode("cp949")
    p = write(Path("k.bas"), data)
    r = cli("edit", "k.bas", "--encoding", "cp949", *argv)
    assert r.code == code and err in r.err, r.err
    assert r.summary["code"] == err and r.summary["path"] == "k.bas"
    assert sha(p.read_bytes()) == sha(data)
    assert not (Path(".encoding-roundtrip") / "sessions").exists()


def test_edit_unencodable_refused_then_replace(cli):
    data = "가격 안내 - 환불 불가\r\n둘째 줄\r\n".encode("euc_kr")
    p = write(Path("e.txt"), data)
    r = cli("edit", "e.txt", "--encoding", "euc_kr", "--old", " - ", "--new", " — ")
    assert r.code == 1 and "E_UNENCODABLE" in r.err and "U+2014 at 1:7" in r.err
    s = r.summary
    assert (s["cmd"], s["code"], s["codec"], s["count"], s["first"]) == ("edit", "E_UNENCODABLE", "euc_kr", "1",
                                                                        "U+2014@1:7")
    assert p.read_bytes() == data and s["result_sha256"] == sha(data)
    r = cli("edit", "e.txt", "--encoding", "euc_kr", "--old", " - ", "--new", " — ", "--replace", "xmlcharref")
    assert r.code == 0 and b"&#8212;" in p.read_bytes() and r.summary["replaced"] == "1"


def test_edit_euc_kr_filler_refused(cli):
    data = "가나다라 마바사 아자차\r\n".encode("euc_kr")
    p = write(Path("f.txt"), data)
    r = cli("edit", "f.txt", "--encoding", "euc_kr", "--append-line", "똠")
    assert r.code == 1 and "euc_kr_filler" in r.err
    assert p.read_bytes() == data


def test_edit_refused_while_open(cli):
    p = write(Path("o.bas"), KO_TEXT.encode("cp949"))
    assert cli("open", "o.bas").code == 0
    working = p.read_bytes()
    r = cli("edit", "o.bas", "--old", "Dim", "--new", "Rem")
    assert r.code == 3 and r.summary["code"] == "E_ALREADY_OPEN"
    assert p.read_bytes() == working
    assert cli("abort", "o.bas").code == 0


def test_edit_ambiguous_needs_encoding(cli):
    data = "한글".encode("euc_kr")
    p = write(Path("n.txt"), data)
    r = cli("edit", "n.txt", "--old", "한글", "--new", "한국어")
    assert r.code == 2 and r.summary["code"] == "E_AMBIGUOUS" and "--encoding" in r.err
    assert p.read_bytes() == data
    r = cli("edit", "n.txt", "--encoding", "cp949", "--old", "한글", "--new", "한국어")
    assert r.code == 0 and p.read_bytes() == "한국어".encode("cp949")


def test_edit_mixed_encoding_refused(cli):
    data = "こんにちは世界です\r\n".encode("shift_jis") + "追加された行です\r\n".encode("utf-8") + \
        "さようなら世界\r\n".encode("shift_jis")
    p = write(Path("j.txt"), data)
    r = cli("edit", "j.txt", "--append-line", "x")
    assert r.code == 2 and r.summary["code"] == "E_MIXED_ENCODING"
    assert p.read_bytes() == data


def test_edit_noop_does_not_write_and_verify_after_edit(cli):
    data = KO_TEXT.encode("cp949")
    p = write(Path("v.bas"), data)
    before = p.stat().st_mtime_ns
    r = cli("edit", "v.bas", "--old", "Dim", "--new", "Dim")
    assert r.code == 0 and r.summary["changed"] == "0" and p.read_bytes() == data
    assert p.stat().st_mtime_ns == before
    assert cli("edit", "v.bas", "--old", "Dim", "--new", "Rem").code == 0
    r = cli("verify", "v.bas")
    assert r.code == 0 and r.summary["drift"] == "0" and r.summary["changed"] == "1"


def test_edit_final_newline_policy(cli):
    p = write(Path("z.txt"), "가나다라 마바사\r\n".encode("cp949"))
    r = cli("edit", "z.txt", "--encoding", "cp949", "--append-line", "아자", "--final-newline", "remove")
    assert r.code == 0 and p.read_bytes() == "가나다라 마바사\r\n아자".encode("cp949")


def test_edit_summary_is_language_independent(cli):
    data = KO_TEXT.encode("cp949")
    lines = {}
    for lang in ("en", "ko"):
        write(Path(f"{lang}.bas"), data)
        r = cli("--lang", lang, "edit", f"{lang}.bas", "--old", "Dim", "--new", "Rem")
        assert r.code == 0
        lines[lang] = r.summary
    assert lines["en"]["receipt"] == lines["ko"]["receipt"]
    assert {k: v for k, v in lines["en"].items() if k != "path"} == \
        {k: v for k, v in lines["ko"].items() if k != "path"}


def test_inspect_text_prints_decoded_numbered_lines(cli):
    write(Path("t.bas"), KO_TEXT.encode("cp949"))
    r = cli("inspect", "t.bas", "--text")
    assert r.code == 0
    assert "1| ' 사용자 이름 확인 모듈입니다 저장 실패 처리" in r.out
    assert "5| End If" in r.out
    plain = cli("inspect", "t.bas")
    assert plain.summary == r.summary


def test_inspect_text_is_bounded(cli, monkeypatch):
    monkeypatch.setattr(rt, "TEXT_LINES_MAX", 3)
    write(Path("long.txt"), "".join(f"가나다 {i}\r\n" for i in range(10)).encode("cp949"))
    r = cli("inspect", "long.txt", "--encoding", "cp949", "--text")
    assert "3| 가나다 2" in r.out and "4| " not in r.out and "7 more line(s)" in r.out


def test_edit_help_localized(capsys):
    for lang, word in (("en", "original encoding"), ("ko", "원래 인코딩")):
        with pytest.raises(SystemExit):
            rt.main(["--lang", lang, "edit", "--help"])
        assert word in capsys.readouterr().out
