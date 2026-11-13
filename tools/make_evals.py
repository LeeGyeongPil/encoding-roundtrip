#!/usr/bin/env python3
"""Development helper: write the evaluation cases under evals/ (prompts and graders).

Task graders pin values that only the bundled script can print (input and result hashes, receipt). The values are
computed here by running the script on the fixtures, so they always match the current script.

    python3 tools/make_evals.py           # rewrite evals/<case>/prompt.md and evals/<case>/graders/*.md
    python3 tools/make_evals.py --check   # exit 1 if a file on disk differs from what would be written

Not part of the skill.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_fixtures as fx  # noqa: E402

ROOT = fx.ROOT
NAME = "encoding-roundtrip"

TRIGGER = [
    'Edit `src/Form1.frm` (it is a Windows-1252 file) and change the caption to "Café Order". Keep the encoding as it is.',
    "The editor refuses `legacy/report.cbl` with \"file is not valid UTF-8\". Can you still update the header comment in it?",
    "이 CP949 소스 `common/msg.bas` 에서 '저장 실패' 문구를 '저장 중 오류' 로 바꿔 줘. 인코딩 깨지지 않게.",
    "EUC-KR 로 된 `conf/app.properties` 에 `timeout=30` 한 줄 추가해 줘. 줄끝은 CRLF 그대로 둬.",
    "Add a `MAX_RETRY` constant to this Shift_JIS header `inc/const.h` without turning it into UTF-8.",
    "Every time you edit this GBK file the Chinese comments get corrupted. Fix the typo on line 12 of `src/calc.c` safely this time.",
    "What encoding and line endings does `data/legacy.csv` use? When I open it I see �� everywhere.",
    "After the last edit line 4 of `strings_ja.txt` looks garbled. Did that file end up with two encodings mixed together?",
    "Update the copyright year in `units/Main.pas`; it is a cp1250 Delphi source with CRLF line endings and they must stay.",
    "Before you touch anything, list which files in this repo are not plain UTF-8 or have CRLF or a BOM.",
    "Change the version string in `Program.cs` but keep its UTF-8 BOM and CRLF line endings exactly as they are.",
    "레거시 폴더 파일 고치기 전에 어떤 파일이 UTF-8 이 아닌지 먼저 확인해 줘.",
]

NEAR = [
    "Convert every file in this repo from CP949 to UTF-8 permanently and add a .gitattributes for it.",
    "Normalize all line endings in the project to LF and commit the result.",
    "Write a Python function that reads a Shift_JIS CSV file with pandas and returns the rows as dictionaries.",
    "My API returns 'Ã©' instead of 'é' in the JSON responses. Fix the server's Content-Type header.",
    "Explain the difference between UTF-8 and UTF-16 in simple terms.",
    "`README.md` 에 있는 오타 하나만 고쳐 줘. 그 파일은 평범한 UTF-8 이야.",
]

FAR = [
    "Explain how quicksort works and when merge sort is the better choice.",
    "Write a Dockerfile for a Node 22 Express app with a multi-stage build.",
    "Optimize this SQL query: SELECT * FROM orders WHERE YEAR(created_at) = 2025 ORDER BY id;",
    "How do I undo my last three commits but keep the changes staged?",
    "Write a regular expression that matches ISO 8601 dates such as 2026-11-13.",
]

REPORT = ("If any tool you use prints a receipt or summary line for the change, paste that line verbatim at the end "
          "of your answer.")

TASK_PROMPTS = {
    "task-cp949-edit-string": (
        "`legacy/Messages.bas` is an old VB6 module in a legacy encoding. Change the text \"저장 실패\" to "
        "\"저장 중 오류\" (it appears once) and change nothing else. The file must keep its encoding, line endings and "
        "missing final newline exactly. " + REPORT),
    "task-sjis-mixed-eol-add-line": (
        "`inc/const.h` is a Shift_JIS header with mixed line endings. Add the line `#define MAX_RETRY 3` after the "
        "last line. Every existing line must keep its bytes and its line ending, and the file must keep its encoding. "
        + REPORT),
    "task-utf8-bom-crlf-edit": (
        "In `Program.cs` change the version string \"1.0.0\" to \"1.0.1\". The file has a UTF-8 BOM and CRLF line "
        "endings and both must stay exactly as they are. " + REPORT),
    "task-diagnose-mixed-encoding": (
        "Line 2 of `strings_ja.txt` shows up garbled in our editor while the other lines look fine. Find out which "
        "encoding the file uses and which lines do not match it. Do not modify the file. " + REPORT),
    "task-error-euckr-unencodable": (
        "`conf/messages.txt` is an old EUC-KR file. On the first line replace the hyphen between the words with an em "
        "dash (the character U+2014) and save the file in its original encoding. If that is not possible, leave the "
        "file as it was and tell me why. " + REPORT),
    "task-error-ambiguous-short": (
        "`note.txt` is a tiny text file in an old Korean encoding. Change the word 한글 to 한국어 in it and keep the "
        "file's encoding. If you cannot be sure about the encoding, do not write anything and tell me what you need to "
        "know. " + REPORT),
}


def prompt_md(name: str, body: str, turns: int) -> str:
    return f"---\nname: {name}\nmax_turns: {turns}\n---\n\n{body}\n"


def fired_grader() -> str:
    return ("---\ntype: tool_used\ntool: Skill\n"
            f"input_match: '\"skill\"\\s*:\\s*\"(?:[\\w-]+:)?{NAME}\"'\n"
            "arm: both\n---\n")


def not_fired_grader() -> str:
    return "---\ntype: tool_used\ntool: Skill\nmin: 0\nmax: 0\narm: both\n---\n"


def script_ran_grader() -> str:
    return ("---\ntype: tool_used\ntool: Bash\n"
            "input_match: 'scripts/roundtrip\\.py'\n"
            "arm: with-only\n---\n")


def regex_grader(pattern: str) -> str:
    assert "'" not in pattern
    return f"---\ntype: regex\npattern: '{pattern}'\n---\n"


def task_graders(task: str) -> dict[str, str]:
    s = fx.simulate(task)
    g: dict[str, str] = {"script-ran": script_ran_grader()}
    if task == "task-cp949-edit-string":
        g["summary-fields"] = regex_grader(
            r"cmd=(?:close|edit) status=ok path=\S*Messages\.bas codec=cp949 bom=none eol=crlf:5,lf:0 eol_cr=0 "
            r"final_newline=no changed=1 replaced=0")
    elif task == "task-sjis-mixed-eol-add-line":
        g["summary-fields"] = regex_grader(
            r"cmd=(?:close|edit) status=ok path=\S*const\.h codec=shift_jis bom=none eol=crlf:3,lf:1 eol_cr=0 "
            r"final_newline=no changed=1 replaced=0")
    elif task == "task-utf8-bom-crlf-edit":
        g["summary-fields"] = regex_grader(
            r"cmd=(?:close|edit) status=ok path=\S*Program\.cs codec=utf-8 bom=yes eol=crlf:14,lf:0 eol_cr=0 "
            r"final_newline=yes changed=1 replaced=0")
    elif task == "task-diagnose-mixed-encoding":
        g["summary-fields"] = regex_grader(
            r"cmd=inspect status=ok path=\S*strings_ja\.txt codec=shift_jis confidence=certain bom=none "
            r"eol=crlf:3,lf:0 eol_cr=0 final_newline=yes mixed_encoding_lines=2 plain=no")
        g["summary-hash"] = regex_grader(
            f"input_sha256={s['input_sha256']} result_sha256={s['result_sha256']} receipt={s['receipt']}")
        return g
    elif task == "task-error-euckr-unencodable":
        e = fx.simulate(task, "edit")
        g["summary-fields"] = regex_grader(
            r"cmd=(?:close|edit) status=error code=E_UNENCODABLE codec=euc_kr path=\S*messages\.txt count=1 "
            rf"first=U\+2014@1:7 input_sha256={s['input_sha256']} "
            rf"result_sha256=(?:{s['result_sha256']}|{e['result_sha256']}) receipt=(?:{s['receipt']}|{e['receipt']})")
        g["error-code"] = regex_grader(r"E_UNENCODABLE")
        return g
    elif task == "task-error-ambiguous-short":
        insp = fx.simulate_inspect(task)
        g["summary-fields"] = regex_grader(
            r"(?:cmd=(?:open|edit) status=error code=E_AMBIGUOUS path=\S*note\.txt candidates=\S+ "
            rf"input_sha256={s['input_sha256']}|cmd=inspect status=ok path=\S*note\.txt codec=unknown "
            rf"confidence=ambiguous bom=none eol=crlf:0,lf:0 eol_cr=0 final_newline=no mixed_encoding_lines=- "
            rf"plain=no input_sha256={insp['input_sha256']})")
        return g
    # One-call `edit` and open -> working copy -> close must write the same bytes; the receipt names the command.
    e = fx.simulate(task, "edit")
    assert e["result_sha256"] == s["result_sha256"], (task, e, s)
    g["result-hash"] = regex_grader(f"result_sha256={s['result_sha256']} receipt=(?:{s['receipt']}|{e['receipt']})")
    return g


def build() -> dict[str, str]:
    out: dict[str, str] = {}

    def add(case: str, body: str, graders: dict[str, str], turns: int) -> None:
        out[f"evals/{case}/prompt.md"] = prompt_md(case, body, turns)
        for gname, text in graders.items():
            out[f"evals/{case}/graders/{gname}.md"] = text

    for i, body in enumerate(TRIGGER, 1):
        add(f"trigger-{i:02d}", body, {"skill-fired": fired_grader()}, 6)
    for i, body in enumerate(NEAR, 1):
        add(f"notrigger-near-{i:02d}", body, {"skill-not-fired": not_fired_grader()}, 6)
    for i, body in enumerate(FAR, 1):
        add(f"notrigger-{i:02d}", body, {"skill-not-fired": not_fired_grader()}, 6)
    for task, body in TASK_PROMPTS.items():
        add(task, body, task_graders(task), 14)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    bad = 0
    for rel, text in build().items():
        p = ROOT / rel
        data = text.encode("utf-8")
        if p.is_file() and p.read_bytes() == data:
            continue
        if a.check:
            print(f"differs: {rel}")
            bad += 1
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
