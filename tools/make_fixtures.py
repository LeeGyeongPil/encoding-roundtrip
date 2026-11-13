#!/usr/bin/env python3
"""Development helper: (re)create the sample files and eval fixtures byte for byte, and compute eval pins.

    python3 tools/make_fixtures.py            # write files that are missing or different
    python3 tools/make_fixtures.py --check    # exit 1 if a file on disk differs from the expected bytes
    python3 tools/make_fixtures.py --pins     # run the simulated eval tasks and print the pinned values

Not part of the skill: the skill folder does not depend on this file.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "encoding-roundtrip" / "scripts" / "roundtrip.py"


def crlf(*lines: str, final: bool = True) -> str:
    return "\r\n".join(lines) + ("\r\n" if final else "")


BAS_TEXT = crlf(
    "' 메시지 모듈: 사용자에게 보여 주는 문구를 모아 둔 파일입니다",
    'Public Const MSG_HELLO = "똠방각하 안녕하세요"',
    'Public Const MSG_SAVE_FAIL = "저장 실패"',
    'Public Const MSG_NOT_FOUND = "파일을 찾을 수 없습니다"',
    'Public Const MSG_CONFIRM = "정말로 삭제하시겠습니까?"',
    'Public Const MSG_DONE = "작업이 끝났습니다"',
    final=False)

SJIS_MIXED = (
    "こんにちは、世界。これは設定ファイルです。\r\n".encode("shift_jis")
    + "追加された行です（この行だけUTF-8）\r\n".encode("utf-8")
    + "さようなら。またお会いしましょう。\r\n".encode("shift_jis"))

PROPERTIES_TEXT = crlf(
    "# 가격 안내 - 환불 불가 상품 설정 파일입니다",
    "app.name=가격 안내 시스템",
    "app.timeout=30")

FRM_TEXT = crlf(
    "VERSION 5.00",
    "Begin VB.Form frmGroesse",
    '   Caption         =   "Größe und Prüfung der Änderungen"',
    '   Begin VB.Label lblStrasse',
    '      Caption         =   "Straße: Bitte prüfen Sie die Änderung"',
    "   End",
    "End")

CONST_H_LINES = [
    ("/* 定数定義ヘッダ: 設定値をまとめています */", "\r\n"),
    ("#define MAX_USERS 100", "\n"),
    ("/* 再試行回数の上限を決める定数です */", "\r\n"),
    ("#define TIMEOUT_SEC 30", ""),
]
CONST_H = "".join(t + e for t, e in CONST_H_LINES).encode("shift_jis")

PROGRAM_CS = (b"\xef\xbb\xbf" + crlf(
    "using System;",
    "",
    "namespace Demo",
    "{",
    "    public static class Program",
    "    {",
    '        public const string Version = "1.0.0";',
    "",
    "        public static void Main()",
    "        {",
    '            Console.WriteLine("Demo " + Version);',
    "        }",
    "    }",
    "}").encode("ascii"))

MESSAGES_TXT = crlf(
    "가격 안내 - 환불 불가 상품입니다 확인 부탁드립니다",
    "배송은 영업일 기준 사흘 안에 도착합니다",
    "문의는 고객센터로 연락해 주세요").encode("euc_kr")

FILES: dict[str, bytes] = {
    "examples/messages-cp949.bas": BAS_TEXT.encode("cp949"),
    "examples/strings-sjis-mixed.txt": SJIS_MIXED,
    "examples/app-euckr.properties": PROPERTIES_TEXT.encode("euc_kr"),
    "examples/form-cp1252.frm": FRM_TEXT.encode("cp1252"),
    "evals/fixtures/task-cp949-edit-string/legacy/Messages.bas": BAS_TEXT.encode("cp949"),
    "evals/fixtures/task-sjis-mixed-eol-add-line/inc/const.h": CONST_H,
    "evals/fixtures/task-utf8-bom-crlf-edit/Program.cs": PROGRAM_CS,
    "evals/fixtures/task-diagnose-mixed-encoding/strings_ja.txt": SJIS_MIXED,
    "evals/fixtures/task-error-euckr-unencodable/conf/messages.txt": MESSAGES_TXT,
    "evals/fixtures/task-error-ambiguous-short/note.txt": "한글".encode("euc_kr"),
}

# Simulated eval tasks: fixture file, command sequence, and an exact edit of the UTF-8 working copy.
TASKS = {
    "task-cp949-edit-string": ("legacy/Messages.bas", "replace", ("저장 실패", "저장 중 오류")),
    "task-sjis-mixed-eol-add-line": ("inc/const.h", "append", "#define MAX_RETRY 3"),
    "task-utf8-bom-crlf-edit": ("Program.cs", "replace", ('"1.0.0"', '"1.0.1"')),
    "task-diagnose-mixed-encoding": ("strings_ja.txt", "inspect", None),
    "task-error-euckr-unencodable": ("conf/messages.txt", "replace", (" - ", " — ")),
    "task-error-ambiguous-short": ("note.txt", "open", ("한글", "한국어")),
}
# An agent may change a file in one `edit` call or with open -> edit the working copy -> close. Both must give the
# same bytes; the graders accept the summary line of either path.
PATHS = ("edit", "open-close")


def edit_args(kind: str, arg) -> list[str]:
    """Options of the one-call `edit` command for a task kind."""
    if kind == "append":
        return ["--append-line", arg]
    old, new = arg
    return ["--old", old, "--new", new]


def run(args: list[str], cwd: Path) -> tuple[int, str, str]:
    r = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True)
    return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")


def last_line(text: str) -> str:
    return text.rstrip("\n").split("\n")[-1]


def simulate(task: str, path: str = "open-close") -> dict[str, str]:
    """Run the commands an agent would run for `task` on a temporary copy of its fixture; return ER-SUMMARY values.

    path="edit" uses the one-call `edit` command, path="open-close" edits the UTF-8 working copy between open and
    close. The returned dict also holds `result_bytes` (hex of the file afterwards) for byte comparisons.
    """
    rel, kind, arg = TASKS[task]
    with tempfile.TemporaryDirectory(prefix="er-pin-") as tmp:
        work = Path(tmp) / "ws"
        shutil.copytree(ROOT / "evals" / "fixtures" / task, work)
        out: dict[str, str] = {}
        if kind == "inspect":
            code, so, _ = run(["inspect", rel], work)
            out.update(pairs(last_line(so)), exit=str(code))
        elif path == "edit":
            code, so, se = run(["edit", rel, *edit_args(kind, arg)], work)
            out.update(pairs(last_line(so)), exit=str(code), stderr=se.strip())
        elif kind == "open":
            code, so, se = run(["open", rel], work)
            out.update(pairs(last_line(so)), exit=str(code), stderr=se.strip())
        else:
            code, so, _ = run(["open", rel], work)
            out["open"] = last_line(so)
            target = work / rel
            text = target.read_bytes().decode("utf-8")
            if kind == "replace":
                old, new = arg
                assert old in text, (task, old)
                text = text.replace(old, new, 1)
            else:
                text = text.rstrip("\n") + "\n" + arg
            target.write_bytes(text.encode("utf-8"))
            code, so, se = run(["close", rel], work)
            out.update(pairs(last_line(so)), exit=str(code), stderr=se.strip())
            if code != 0:          # the skill restores a refused file with abort
                run(["abort", rel], work)
        out["result_bytes"] = (work / rel).read_bytes().hex()
        return out


def simulate_inspect(task: str) -> dict[str, str]:
    """ER-SUMMARY values of `inspect <fixture file>` for `task`."""
    rel = TASKS[task][0]
    with tempfile.TemporaryDirectory(prefix="er-pin-") as tmp:
        work = Path(tmp) / "ws"
        shutil.copytree(ROOT / "evals" / "fixtures" / task, work)
        code, so, _ = run(["inspect", rel], work)
        return dict(pairs(last_line(so)), exit=str(code))


def pairs(summary: str) -> dict[str, str]:
    return dict(p.split("=", 1) for p in summary.split(" ")[1:] if "=" in p)


def write_all(check: bool) -> int:
    bad = 0
    for rel, data in FILES.items():
        p = ROOT / rel
        if p.is_file() and p.read_bytes() == data:
            continue
        if check:
            print(f"differs: {rel}")
            bad += 1
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        print(f"wrote {rel} ({len(data)} bytes)")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--pins", action="store_true")
    a = ap.parse_args()
    if a.pins:
        for task in TASKS:
            print(task, simulate(task))
        return 0
    return write_all(a.check)


if __name__ == "__main__":
    sys.exit(main())
