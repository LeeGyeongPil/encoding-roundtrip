"""Detection: certain signals first, narrow codec on ties, ambiguous means do not write."""
from __future__ import annotations

from pathlib import Path

import pytest

import roundtrip as rt
from conftest import JA_TEXT, KO_TEXT, SAMPLES, write


def test_bom_signals_are_certain():
    info = rt.analyze(b"\xef\xbb\xbfabc\r\n")
    assert (info["encoding"], info["bom"], info["confidence"], info["source"]) == ("utf-8", "utf-8", "certain", "bom")
    le = rt.analyze(b"\xff\xfe" + "a\r\nb".encode("utf-16-le"))
    assert (le["encoding"], le["bom"]) == ("utf-16-le", "utf-16le")
    assert le["eol"]["crlf"] == 1 and not le["final_newline"]
    be = rt.analyze(b"\xfe\xff" + "a\n".encode("utf-16-be"))
    assert be["encoding"] == "utf-16-be" and be["final_newline"]


def test_utf32_and_iso2022_are_unsupported():
    assert rt.analyze(b"\xff\xfe\x00\x00a\x00\x00\x00")["problem"] == "unsupported"
    info = rt.analyze(b"abc \x1b$B$3$s$K$A$O\x1b(B")
    assert info["problem"] == "unsupported" and "ISO-2022" in info["problem_detail"]


def test_ascii_and_strict_utf8():
    assert rt.analyze(b"plain\n")["encoding"] == "ascii"
    info = rt.analyze("héllo wörld 한글\n".encode("utf-8"))
    assert (info["encoding"], info["confidence"], info["plain"]) == ("utf-8", "certain", True)


def test_same_family_prefers_narrow_codec():
    info = rt.analyze(KO_TEXT.encode("euc_kr"))
    assert info["encoding"] == "euc_kr" and info["compatible"] == ["cp949"]
    assert info["confidence"] in ("certain", "likely")
    sj = rt.analyze(JA_TEXT.encode("shift_jis"))
    assert sj["encoding"] == "shift_jis" and "cp932" in sj["compatible"]


def test_cp949_only_syllable_selects_cp949():
    data = "똠방각하 안녕하세요 저장 실패 파일을 찾을 수 없습니다".encode("cp949")
    info = rt.analyze(data)
    assert info["encoding"] == "cp949" and "euc_kr" not in info["candidates"]


@pytest.mark.parametrize("codec", ["cp949", "shift_jis", "gbk", "cp1251", "cp1252"])
def test_each_family_is_recognised(codec):
    info = rt.analyze(SAMPLES[codec])
    assert info["encoding"] in (codec, "euc_kr")
    assert info["confidence"] in ("certain", "likely")


def test_czech_text_is_cp1250():
    info = rt.analyze(SAMPLES["cp1250"])
    assert info["encoding"] == "cp1250"


def test_short_file_ambiguous_refuses_open(cli):
    write(Path("short.txt"), "한글".encode("euc_kr"))
    info = rt.analyze("한글".encode("euc_kr"))
    assert info["confidence"] == "ambiguous" and info["encoding"] is None
    assert {"euc_kr", "cp949", "gbk", "big5"} <= set(info["candidates"])
    r = cli("open", "short.txt")
    assert r.code == 2
    assert "E_AMBIGUOUS" in r.err and "gbk" in r.err and "--encoding" in r.err
    assert r.summary["code"] == "E_AMBIGUOUS"
    assert open("short.txt", "rb").read() == "한글".encode("euc_kr")      # nothing written
    r = cli("open", "short.txt", "--encoding", "cp949")
    assert r.code == 0 and r.summary["confidence"] == "certain"
    one = rt.analyze("펲".encode("cp949"))
    assert one["confidence"] == "ambiguous"


def test_folder_hint_resolves_short_file(cli, tmp_path):
    write(tmp_path / "a.txt", KO_TEXT.encode("cp949"))
    write(tmp_path / "short.txt", "한글".encode("euc_kr"))
    r = cli("inspect", "short.txt")
    assert r.code == 0 and r.summary["confidence"] == "likely"
    assert "from folder" in r.out


def test_gitattributes_hint(cli, tmp_path):
    write(tmp_path / ".gitattributes", b"*.bas working-tree-encoding=CP949\n*.txt text\n")
    write(tmp_path / "x.bas", "한글".encode("cp949"))
    info = cli("inspect", "x.bas")
    assert info.summary["codec"] == "cp949" and info.summary["confidence"] == "certain"
    assert rt.gitattributes_codec(tmp_path / "other.txt") is None
    write(tmp_path / ".gitattributes", b"*.bas working-tree-encoding=NOPE\n")
    assert rt.gitattributes_codec(tmp_path / "x.bas") is None


def test_mixed_encoding_lines():
    data = ("こんにちは、世界。これは設定ファイルです。\r\n".encode("shift_jis") + "追加された行です\r\n".encode("utf-8")
            + "さようなら。またお会いしましょう。\r\n".encode("shift_jis"))
    info = rt.analyze(data)
    assert info["mixed_encoding_lines"] == [2] and info["encoding"] == "shift_jis"
    assert info["mixed_dominant"] == "shift_jis" and not info["plain"]


def test_mixed_with_utf8_majority():
    data = ("첫 번째 줄은 UTF-8 입니다\n".encode("utf-8") * 3 + "이 줄은 CP949 입니다 저장 실패\n".encode("cp949"))
    info = rt.analyze(data)
    assert info["encoding"] == "utf-8" and info["mixed_encoding_lines"] == [4]


def test_open_refuses_mixed_encoding(cli):
    data = ("こんにちは、世界。これは設定ファイルです。\r\n".encode("shift_jis") + "追加された行です\r\n".encode("utf-8")
            + "さようなら。またお会いしましょう。\r\n".encode("shift_jis"))
    write(Path("s.txt"), data)
    r = cli("open", "s.txt")
    assert r.code == 2 and "E_MIXED_ENCODING" in r.err and "2" in r.err
    r = cli("inspect", "s.txt")
    assert r.summary["mixed_encoding_lines"] == "2"
    assert open("s.txt", "rb").read() == data


def test_binary_rejected(cli):
    write(Path("b.bin"), b"abc\x00def\xff")
    r = cli("open", "b.bin")
    assert r.code == 2 and "E_BINARY" in r.err
    assert rt.analyze(b"\x00\x01")["problem"] == "binary"


def test_empty_and_too_large(cli):
    info = rt.analyze(b"")
    assert info["encoding"] == "ascii" and info["eol_style"] == "none" and not info["final_newline"]
    assert rt.analyze(b"a" * (rt.MAX_BYTES + 1))["problem"] == "too_large"
    write(Path("big.txt"), b"a" * (rt.MAX_BYTES + 1))
    r = cli("open", "big.txt")
    assert r.code == 2 and "E_UNSUPPORTED" in r.err


def test_forced_encoding_and_errors(cli):
    data = KO_TEXT.encode("cp949")
    assert rt.analyze(data, forced="cp949")["source"] == "argument"
    assert rt.analyze("똠방각하".encode("cp949"), forced="euc-kr")["problem"] == "decode"
    assert rt.analyze(b"\xef\xbb\xbfa", forced="cp949")["problem"] == "unsupported"
    assert rt.analyze(b"abc", forced="utf-16")["problem"] == "unsupported"
    with pytest.raises(rt.ERError) as e:
        rt.analyze(data, forced="klingon")
    assert e.value.code == "E_UNKNOWN_CODEC"
    write(Path("f.txt"), data)
    r = cli("inspect", "f.txt", "--encoding", "klingon")
    assert r.code == 2 and "E_UNKNOWN_CODEC" in r.err
    r = cli("open", "f.txt", "--encoding", "shift_jis")
    assert r.code in (0, 2)


def test_undecodable_gives_ambiguous_error(cli):
    write(Path("u.txt"), b"\x81\x20\x98")
    info = rt.analyze(b"\x81\x20\x98")
    assert info["problem"] == "undecodable"
    r = cli("open", "u.txt")
    assert r.code == 2 and "E_AMBIGUOUS" in r.err
    assert cli("inspect", "u.txt").code == 0


def test_normalize_codec_aliases():
    assert rt.normalize_codec("EUC-KR") == "euc_kr"
    assert rt.normalize_codec("Shift-JIS") == "shift_jis"
    assert rt.normalize_codec("windows-1252") == "cp1252"
    assert rt.normalize_codec("UTF8") == "utf-8"
    assert rt.normalize_codec("utf-16le") == "utf-16-le"
    assert rt.normalize_codec("gb2312") == "gb2312"


def test_editorconfig_charset(tmp_path):
    (tmp_path / ".editorconfig").write_bytes(b"root = true\n[*.{bas,frm}]\ncharset = utf-8\n[*.txt]\ncharset = latin1\n")
    assert rt.editorconfig_charset(tmp_path / "a.bas") == "utf-8"
    assert rt.editorconfig_charset(tmp_path / "a.txt") == "latin1"
    assert rt.editorconfig_charset(tmp_path / "a.py") is None


def test_editorconfig_warning_in_inspect(cli, tmp_path):
    write(tmp_path / ".editorconfig", b"[*]\ncharset = utf-8\n")
    write(tmp_path / "k.bas", KO_TEXT.encode("cp949"))
    r = cli("inspect", "k.bas")
    assert ".editorconfig says charset=utf-8" in r.out
