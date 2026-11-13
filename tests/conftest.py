"""Shared helpers: run the CLI in-process and build legacy-encoded sample files."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

import roundtrip as rt


@pytest.fixture(autouse=True)
def _english_by_default(monkeypatch):
    """Tests must not depend on the developer's locale."""
    for name in ("ENCODING_ROUNDTRIP_LANG", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "C")


class Result:
    def __init__(self, code: int, out: str, err: str):
        self.code, self.out, self.err = code, out, err

    @property
    def summary(self) -> dict[str, str]:
        last = self.out.rstrip("\n").split("\n")[-1]
        assert last.startswith("ER-SUMMARY "), last
        return dict(p.split("=", 1) for p in last.split(" ")[1:] if "=" in p)


@pytest.fixture
def cli(capsys, tmp_path, monkeypatch):
    """cli('open', 'a.txt') runs roundtrip.main inside tmp_path and returns Result(code, out, err)."""
    monkeypatch.chdir(tmp_path)

    def run(*argv: str) -> Result:
        code = rt.main(list(argv))
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return run


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def crlf(*lines: str, final: bool = True) -> str:
    return "\r\n".join(lines) + ("\r\n" if final else "")


KO_TEXT = ("' 사용자 이름 확인 모듈입니다 저장 실패 처리\r\nDim name ' 이름\r\nIf name = \"\" Then\r\n"
           "  MsgBox \"이름을 입력하세요 다시 시도해 주세요\"\r\nEnd If")
JA_TEXT = "こんにちは、世界。これは設定ファイルです。\r\nさようなら。またお会いしましょう。\r\n"
ZH_TEXT = "这是一个配置文件，请不要修改其中的内容。\r\n错误处理：读取文件失败时返回错误。\r\n"
RU_TEXT = "Привет, мир! Это тестовый файл для проверки кодировки.\r\n"
DE_TEXT = "Größe und Prüfung der Änderungen. Straße: Bitte prüfen Sie die Änderung.\r\n"
CZ_TEXT = "Příliš žluťoučký kůň úpěl ďábelské ódy a řekl něco důležitého.\r\n"

SAMPLES = {
    "cp949": KO_TEXT.encode("cp949"),
    "euc_kr": KO_TEXT.encode("euc_kr"),
    "shift_jis": JA_TEXT.encode("shift_jis"),
    "gbk": ZH_TEXT.encode("gbk"),
    "cp1250": CZ_TEXT.encode("cp1250"),
    "cp1251": RU_TEXT.encode("cp1251"),
    "cp1252": DE_TEXT.encode("cp1252"),
}


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def summary_value(text: str, key: str) -> str:
    m = re.search(rf"\b{key}=(\S+)", text)
    assert m, (key, text)
    return m.group(1)
