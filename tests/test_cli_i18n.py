"""CLI contract: exit codes, ER-SUMMARY line and receipt, English/Korean messages and language precedence."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import roundtrip as rt
from conftest import KO_TEXT, write

SCRIPT = Path(rt.__file__).resolve()
SUMMARY_RE = re.compile(r"^ER-SUMMARY cmd=\w+ status=\w+ .*receipt=[0-9a-f]{16}$")
HANGUL = re.compile("[가-힣]")


def test_summary_line_and_receipt_deterministic(cli):
    sample = Path(rt.__file__).resolve().parents[3] / "examples" / "messages-cp949.bas"
    write(Path("a.bas"), sample.read_bytes())
    r1 = cli("inspect", "a.bas")
    r2 = cli("inspect", "a.bas")
    last = r1.out.rstrip("\n").split("\n")[-1]
    assert SUMMARY_RE.match(last)
    assert re.match(r"^ER-SUMMARY cmd=inspect status=ok .*codec=cp949 .*eol=crlf:\d+,lf:\d+ .*receipt=[0-9a-f]{16}$", last)
    assert r1.summary["receipt"] == r2.summary["receipt"]
    # one changed byte in the input changes the receipt
    write(Path("a.bas"), sample.read_bytes() + b" ")
    assert cli("inspect", "a.bas").summary["receipt"] != r1.summary["receipt"]


def test_receipt_formula_and_binding():
    a = rt.receipt_for("close", "1" * 64, "2" * 64, "cp949")
    assert a == rt.receipt_for("close", "1" * 64, "2" * 64, "cp949")
    assert len({a, rt.receipt_for("open", "1" * 64, "2" * 64, "cp949"), rt.receipt_for("close", "3" * 64, "2" * 64, "cp949"),
                rt.receipt_for("close", "1" * 64, "3" * 64, "cp949"), rt.receipt_for("close", "1" * 64, "2" * 64, "euc_kr")}) == 5


def test_every_command_ends_with_a_summary_line(cli):
    write(Path("a.bas"), KO_TEXT.encode("cp949"))
    write(Path("b.txt"), b"x\n")
    runs = [("inspect", "a.bas"), ("scan", "."), ("status",), ("open", "a.bas"), ("close", "a.bas"), ("verify", "a.bas"),
            ("open", "a.bas"), ("abort", "a.bas"), ("close", "nope"), ("open", "a.bas", "--encoding", "x")]
    for argv in runs:
        r = cli(*argv)
        last = r.out.rstrip("\n").split("\n")[-1]
        assert SUMMARY_RE.match(last), (argv, last)


def test_report_json_has_hashes_and_receipt(cli):
    import json
    write(Path("a.bas"), KO_TEXT.encode("cp949"))
    r = cli("open", "a.bas")
    report = json.loads((Path(".encoding-roundtrip") / "report.json").read_bytes().decode("utf-8"))
    assert report["tool"] == "encoding-roundtrip" and report["cmd"] == "open"
    assert report["input_sha256"] == r.summary["input_sha256"] and report["receipt"] == r.summary["receipt"]
    assert len(report["result_sha256"]) == 64
    assert list(report)[:4] == ["tool", "version", "cmd", "status"]


def test_exit_codes_table(cli):
    write(Path("a.bas"), KO_TEXT.encode("cp949"))
    assert cli("inspect", "a.bas").code == 0
    assert cli("open", "missing").code == 2
    assert cli("close", "a.bas").code == 3
    assert rt.EXIT_FOR["E_UNENCODABLE"] == 1 and rt.EXIT_FOR["E_DRIFT"] == 1


def test_message_keys_match():
    assert set(rt.MESSAGES["en"]) == set(rt.MESSAGES["ko"])
    for key in rt.MESSAGES["en"]:
        en_tokens = sorted(set(re.findall(r"\{(\w+)", rt.MESSAGES["en"][key])))
        ko_tokens = sorted(set(re.findall(r"\{(\w+)", rt.MESSAGES["ko"][key])))
        assert en_tokens == ko_tokens, key


def test_every_error_code_has_a_message_and_exit_code():
    codes = set(rt.EXIT_FOR)
    for code in codes:
        assert f"err_{code}" in rt.MESSAGES["en"], code
    assert {k[4:] for k in rt.MESSAGES["en"] if k.startswith("err_")} == codes


def test_lang_precedence(monkeypatch):
    env = {"LANG": "ko_KR.UTF-8"}
    assert rt.resolve_lang(None, env) == "ko"
    assert rt.resolve_lang("en", env) == "en"
    assert rt.resolve_lang(None, {"LANG": "ko_KR.UTF-8", "LC_ALL": "en_US.UTF-8"}) == "en"
    assert rt.resolve_lang(None, {"LC_MESSAGES": "ko_KR", "LANG": "en_US"}) == "ko"
    assert rt.resolve_lang(None, {"ENCODING_ROUNDTRIP_LANG": "en", "LC_ALL": "ko_KR.UTF-8"}) == "en"
    assert rt.resolve_lang(None, {"ENCODING_ROUNDTRIP_LANG": "ko", "LANG": "en_US"}) == "ko"
    assert rt.resolve_lang(None, {"ENCODING_ROUNDTRIP_LANG": "fr", "LANG": "ko_KR"}) == "ko"
    assert rt.resolve_lang("ko", {"ENCODING_ROUNDTRIP_LANG": "en"}) == "ko"
    assert rt.resolve_lang(None, {"LANG": "fr_FR.UTF-8"}) == "en"
    assert rt.resolve_lang(None, {}) == "en"


def test_language_switch_changes_output_but_not_summary_or_codes(cli):
    write(Path("a.bas"), KO_TEXT.encode("cp949"))
    en = cli("inspect", "a.bas")
    ko = cli("--lang", "ko", "inspect", "a.bas")
    assert "encoding:" in en.out and not HANGUL.search(en.out.split("ER-SUMMARY")[0])
    assert "인코딩:" in ko.out
    assert en.summary == ko.summary
    assert "E_NOT_OPEN" in cli("close", "a.bas").err and "E_NOT_OPEN" in cli("--lang", "ko", "close", "a.bas").err
    assert HANGUL.search(cli("--lang", "ko", "close", "a.bas").err)
    assert not HANGUL.search(cli("--lang", "en", "close", "a.bas").err)


def test_lang_flag_after_subcommand_and_env(cli, monkeypatch):
    write(Path("a.bas"), KO_TEXT.encode("cp949"))
    assert "인코딩:" in cli("inspect", "a.bas", "--lang", "ko").out
    monkeypatch.setenv("ENCODING_ROUNDTRIP_LANG", "ko")
    assert "인코딩:" in cli("inspect", "a.bas").out
    assert "encoding:" in cli("inspect", "a.bas", "--lang", "en").out


def test_help_localized():
    def help_text(*argv):
        r = subprocess.run([sys.executable, str(SCRIPT), *argv, "--help"], capture_output=True,
                           env={"PATH": "/usr/bin:/bin", "LANG": "C"})
        assert r.returncode == 0
        return r.stdout.decode("utf-8")

    en = help_text("--lang", "en")
    ko = help_text("--lang", "ko")
    assert HANGUL.search(ko) and not HANGUL.search(en)
    assert "inspect" in en and "close" in ko
    assert HANGUL.search(help_text("--lang", "ko", "close")) and not HANGUL.search(help_text("close"))
    assert not HANGUL.search(help_text("close", "--lang", "en"))


def test_help_is_localized_in_process(capsys):
    with pytest.raises(SystemExit) as e:
        rt.main(["--lang", "ko", "--help"])
    assert e.value.code == 0
    assert HANGUL.search(capsys.readouterr().out)
    with pytest.raises(SystemExit):
        rt.main(["--lang=ko", "open", "--help"])
    assert HANGUL.search(capsys.readouterr().out)


def test_version_and_missing_command(capsys):
    with pytest.raises(SystemExit) as e:
        rt.main(["--version"])
    assert e.value.code == 0 and rt.VERSION in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        rt.main([])
    assert e.value.code == 2


def test_korean_particles():
    assert rt.josa("Messages.bas", "을/를") == "를"
    assert rt.josa("파일", "을/를") == "을"
    assert rt.josa("cp949", "으로/로") == "로"          # 9 = 구, no final consonant
    assert rt.josa("gb18030", "으로/로") == "으로"      # 0 = 영, final ieung
    assert rt.josa("euc_kr", "으로/로") == "로"         # r = 알, rieul takes 로
    assert rt.josa("note.txt", "은/는") == "는"
    assert rt.josa("읽기.cfg", "이/가") == "가"
    assert rt.josa("cp1251", "이/가") == "이"           # 1 = 일, final rieul
    assert rt.josa("a.in", "은/는") == "은"             # n = 엔
    assert rt.josa("x", "의") == "의"
    assert rt.josa("...", "을/를") == "를"


def test_korean_messages_use_the_right_particle(cli):
    write(Path("a.bas"), KO_TEXT.encode("cp949"))
    out = cli("--lang", "ko", "open", "a.bas").out
    assert "a.bas를 열었습니다" in out
    write(Path("파일"), KO_TEXT.encode("cp949"))
    out = cli("--lang", "ko", "open", "파일").out
    assert "파일을 열었습니다" in out


def test_script_reads_and_writes_bytes_only():
    source = SCRIPT.read_bytes().decode("utf-8")
    assert ".read_text(" not in source and ".write_text(" not in source


def test_no_forbidden_constructs():
    source = SCRIPT.read_bytes().decode("utf-8")
    for needle in ("shell=True", "os.system", "eval(", "exec(", "pickle", "import socket", "import urllib", "requests"):
        assert needle not in source, needle


def test_script_runs_as_a_standalone_process(tmp_path):
    f = tmp_path / "a.bas"
    f.write_bytes(KO_TEXT.encode("cp949"))
    r = subprocess.run([sys.executable, str(SCRIPT), "--lang", "en", "inspect", str(f)], capture_output=True,
                       env={"PATH": "/usr/bin:/bin", "LANG": "C", "PYTHONIOENCODING": "ascii"})
    assert r.returncode == 0
    assert r.stdout.decode("utf-8").rstrip("\n").split("\n")[-1].startswith("ER-SUMMARY cmd=inspect status=ok")
    r = subprocess.run([sys.executable, str(SCRIPT), "close", str(f)], capture_output=True,
                       env={"PATH": "/usr/bin:/bin", "LANG": "C"})
    assert r.returncode == 3 and b"E_NOT_OPEN" in r.stderr


# --- fix round 4: QA minor findings Q-5, Q-6, Q-7 -------------------------------------------------

def test_inspect_summary_marks_unusable_files(cli):
    """Q-5: an agent that reads only the ER-SUMMARY line must see that the file cannot be edited."""
    write(Path("broken.txt"), b"\xff\xfe\xfa\x80abc\r\n")             # UTF-16 BOM, then invalid UTF-16
    write(Path("big.txt"), b"a" * (rt.MAX_BYTES + 1))
    write(Path("ok.bas"), KO_TEXT.encode("cp949"))
    broken = cli("inspect", "broken.txt")
    assert broken.code == 0                                           # inspect only diagnoses
    assert broken.summary["usable"] == "no" and broken.summary["problem"] == "E_DECODE_FAILED"
    big = cli("inspect", "big.txt")
    assert big.summary["usable"] == "no" and big.summary["problem"] == "E_UNSUPPORTED"
    # the error code named in inspect is the one edit really raises
    assert "E_DECODE_FAILED" in cli("edit", "broken.txt", "--old", "a", "--new", "b").err
    # a usable file keeps the old summary line unchanged (no usable/problem fields)
    good = cli("inspect", "ok.bas")
    assert "usable" not in good.summary and "problem" not in good.summary
    both = cli("inspect", "ok.bas", "broken.txt")
    assert both.summary["unusable"] == "1"
    assert "unusable" not in cli("inspect", "ok.bas", "ok.bas").summary


@pytest.mark.parametrize("argv", [["--help"], ["edit", "--help"], ["close", "--help"], ["inspect", "--help"]])
def test_korean_help_has_no_english_argparse_boilerplate(capsys, argv):
    """Q-6: argparse's own words (usage, section titles, -h help) follow the chosen language."""
    with pytest.raises(SystemExit):
        rt.main(["--lang", "ko", *argv])
    ko = capsys.readouterr().out
    for english in ("usage:", "positional arguments", "optional arguments", "options:", "show this help message",
                    "show program's version"):
        assert english not in ko, english
    assert ko.startswith("사용법:")
    with pytest.raises(SystemExit):
        rt.main(["--lang", "en", *argv])
    en = capsys.readouterr().out
    assert en.startswith("usage:") and "show this help message and exit" in en and not HANGUL.search(en)


def test_receipt_binds_status_and_error_code(cli):
    """Q-7: two different refusals on the same file must not share a receipt."""
    write(Path("e.properties"), "a - b\r\n".encode("euc_kr"))
    unencodable = cli("edit", "e.properties", "--append-line", "a — b")
    no_match = cli("edit", "e.properties", "--old", "zzzz", "--new", "q")
    assert unencodable.summary["code"] == "E_UNENCODABLE" and no_match.summary["code"] == "E_NO_MATCH"
    assert unencodable.summary["receipt"] != no_match.summary["receipt"]
    # ok lines keep the er1 formula, so receipts already printed in docs and graders stay valid
    assert rt.receipt_for("close", "1" * 64, "2" * 64, "cp949") == \
        rt.sha256_hex(("er1|close|" + "1" * 64 + "|" + "2" * 64 + "|cp949").encode("utf-8"))[:16]
    ok = rt.receipt_for("edit", "1" * 64, "2" * 64, "cp949")
    assert ok != rt.receipt_for("edit", "1" * 64, "2" * 64, "cp949", status="error", code="E_NO_MATCH")
    assert rt.receipt_for("edit", "1" * 64, "2" * 64, "cp949", status="error", code="E_NO_MATCH") != \
        rt.receipt_for("edit", "1" * 64, "2" * 64, "cp949", status="error", code="E_UNENCODABLE")
