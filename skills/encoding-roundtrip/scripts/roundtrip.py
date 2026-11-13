#!/usr/bin/env python3
"""encoding-roundtrip: edit legacy-encoded text files without changing their bytes elsewhere.

Subcommands: inspect, scan, edit, open, close, abort, verify, status.

`edit` changes a file in one call (find/replace, set/insert/delete/append lines) and writes it back in
its original format. `open` replaces a legacy file (CP949, EUC-KR, Shift_JIS, GBK, Windows-125x, BOM, CRLF ...) with a
UTF-8/LF working copy and remembers the original bytes. `close` writes the edited text back in the
original encoding, line endings, BOM and final-newline state; unchanged lines are copied as the
original bytes. Characters the target encoding cannot represent are refused before anything is
written. Standard library only; every file is read and written as bytes.
"""
from __future__ import annotations

import argparse
import codecs
import difflib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Callable

VERSION = "0.1.2"
TOOL = "encoding-roundtrip"
MAX_BYTES = 10 * 1024 * 1024
SAMPLE_CHARS = 100_000          # scoring looks at this many characters at most
STATE_DIRNAME = ".encoding-roundtrip"
LANG_ENV = "ENCODING_ROUNDTRIP_LANG"

# Auto-detection candidates. Order = preference when two codecs decode to the same text
# (the narrower codec first: euc_kr < cp949, shift_jis < cp932, gbk < gb18030).
CANDIDATES = ["euc_kr", "cp949", "shift_jis", "cp932", "gbk", "gb18030", "big5", "cp1252", "cp1250", "cp1251"]
PREFER = {c: i for i, c in enumerate(CANDIDATES)}
# Extra codecs accepted only through --encoding.
EXPLICIT_ONLY = ["gb2312", "cp1253", "cp1254", "cp1255", "cp1256", "cp1257", "cp1258"]
SUPPORTED = set(CANDIDATES) | set(EXPLICIT_ONLY) | {"utf-8", "ascii", "utf-16-le", "utf-16-be"}
FAMILY = {"euc_kr": "ko", "cp949": "ko", "shift_jis": "ja", "cp932": "ja", "gbk": "zh", "gb18030": "zh",
          "big5": "tw", "cp1252": "c1252", "cp1250": "c1250", "cp1251": "c1251"}

EOL_LF, EOL_CRLF, EOL_CR = b"\n", b"\r\n", b"\r"
_EOL_BYTES = re.compile(rb"\r\n|\r|\n")
_EOL_TEXT = re.compile(r"\r\n|\r|\n")
_ISO2022 = re.compile(rb"\x1b[$(][)@ABCDIJ]")

# ---------------------------------------------------------------------------------------------
# Messages (English / Korean). Error codes (E_...) and the ER-SUMMARY keys are never translated.
# A token {name|은/는} appends the correct Korean particle after the value of `name`.
# ---------------------------------------------------------------------------------------------

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "help_description": "Edit legacy-encoded text files (CP949, EUC-KR, Shift_JIS, GBK, Windows-125x, BOM, CRLF) "
                            "without changing their encoding, line endings, BOM or final newline.",
        "help_epilog": "The last stdout line of every command is an ER-SUMMARY line; report it as is.",
        "help_lang": "message language: en or ko (default: ENCODING_ROUNDTRIP_LANG, then LC_ALL/LC_MESSAGES/LANG, else en)",
        "help_state_dir": "folder for sessions and report.json (default: <git root or file folder>/.encoding-roundtrip)",
        "help_json": "print JSON instead of text",
        "help_usage": "usage: ",
        "help_positionals": "positional arguments",
        "help_options": "options",
        "help_help": "show this help message and exit",
        "help_version": "show the version and exit",
        "help_inspect": "show encoding, BOM, line endings, final newline and mixed-encoding lines",
        "help_scan": "list files in a folder that are not plain UTF-8/LF",
        "help_edit": "change a file in one call and write it back in its original encoding, BOM, line endings and "
                     "final newline",
        "help_old": "exact text to find (pair with --new; may repeat; must match once unless --all)",
        "help_new": "replacement for the --old text given in the same position",
        "help_all": "replace every match of --old instead of requiring exactly one",
        "help_append_line": "add this line after the last line (may repeat)",
        "help_insert_after": "N:TEXT - insert a line after line N (0 = before the first line; may repeat)",
        "help_set_line": "N:TEXT - replace the whole of line N (may repeat)",
        "help_delete_line": "N - delete line N (may repeat)",
        "help_text": "also print the decoded text with line numbers (read only)",
        "help_open": "turn a file into a UTF-8/LF working copy and remember the original bytes",
        "help_close": "write the working copy back in the original format",
        "help_abort": "restore the original bytes and drop the working copy",
        "help_verify": "compare a result with the original: encoding, BOM, line endings, final newline",
        "help_status": "list files that are open (not yet closed)",
        "help_paths": "file path(s)",
        "help_path": "file path",
        "help_dir": "folder to scan",
        "help_encoding": "use this encoding instead of detecting it (for example cp949, shift_jis, cp1252)",
        "help_force": "bypass the safety check that is allowed to be bypassed (see the error message)",
        "help_replace": "what to do with characters the encoding cannot hold: error (default), xmlcharref or question",
        "help_final_newline": "final newline of the result: keep (default), add or remove",
        "help_against": "original file to compare with (default: the backup of the last closed file)",
        "help_max_files": "stop after this many files (default 2000)",
        "yes": "yes", "no": "no", "none": "none", "dash": "-",
        "insp_file": "{path}",
        "insp_enc": "  encoding: {encoding} ({confidence}, from {source})   compatible with: {compatible}",
        "insp_bom": "  BOM: {bom}   line endings: {eol_style} (CRLF {crlf}, LF {lf}, CR {cr})   final newline: {final_newline}",
        "insp_mixed_eol": "  lines with a different line ending: {lines}",
        "insp_mixed_enc": "  lines in a different encoding ({dominant} is the majority): {lines}",
        "insp_ffd": "  U+FFFD replacement characters: {n}",
        "insp_candidates": "  candidates: {candidates}",
        "insp_warn_editorconfig": "  note: .editorconfig says charset={charset}, the file is {encoding}",
        "insp_next_plain": "  next: plain UTF-8 with LF - edit it with your normal tools",
        "insp_next_open": "  next: change it with edit (or open, edit the working copy, then close)",
        "insp_next_ambiguous": "  next: ask which encoding it is, then pass --encoding to open",
        "insp_next_mixed": "  next: do not open; the lines above use another encoding, ask the user how to proceed",
        "insp_problem": "  cannot use this file: {problem}",
        "insp_text_more": "  ... {n} more line(s) not shown",
        "prob_binary": "it contains NUL bytes (binary)",
        "prob_unsupported": "unsupported encoding ({detail})",
        "prob_too_large": "larger than 10 MB",
        "prob_decode": "bytes are not valid {detail}",
        "prob_undecodable": "none of the supported encodings decodes it",
        "open_ok": "opened {path}: the working copy is UTF-8 with LF line endings. Edit it, then run close "
                   "(or abort to restore the original).",
        "open_codec": "  original encoding: {codec} ({confidence}), BOM: {bom}, line endings: {eol_style}, "
                      "final newline: {final_newline}",
        "close_ok": "closed {path}: written back as {codec}; {changed} changed line(s); BOM, line endings of unchanged "
                    "lines and the final newline are preserved.",
        "close_replaced": "  {n} character(s) were replaced ({mode}).",
        "close_noop": "closed {path}: nothing changed, the original bytes are back.",
        "edit_ok": "edited {path}: written as {codec}; {changed} changed line(s); BOM, line endings of unchanged lines "
                   "and the final newline are preserved.",
        "edit_noop": "edited {path}: the change gives the same text, the file was not touched.",
        "edit_unencodable": "{count} character(s) cannot be written as {codec}: {items} - nothing was written; ask "
                            "the user for a replacement and run edit again, or add --replace xmlcharref",
        "err_E_NO_MATCH": "the text was not found: {text} - check it with inspect --text (the file was not touched)",
        "err_E_MANY_MATCHES": "the text appears {count} times: {text} - give a longer --old or add --all "
                              "(the file was not touched)",
        "err_E_BAD_EDIT": "invalid edit: {detail} - see edit --help (the file was not touched)",
        "abort_ok": "restored {path} to its original bytes.",
        "abort_saved": "  the edited working copy was saved to {saved}",
        "abort_already": "{path} already has its original bytes; the session was removed.",
        "verify_ok": "verified {path}: same {codec}, BOM and line endings as the original; changed lines: {lines}",
        "verify_fail": "verification failed for {path}:",
        "verify_item_decode": "  - not valid {codec} on lines {lines}",
        "verify_item_utf8": "  - the file looks like a UTF-8 working copy (run close, or abort)",
        "verify_item_bom": "  - BOM differs from the original",
        "verify_item_eol": "  - line ending changed on unchanged lines: {lines}",
        "verify_item_final": "  - final newline differs from the original",
        "verify_item_ffd": "  - U+FFFD characters increased from {before} to {after}",
        "verify_item_mixed": "  - lines in a different encoding: {lines}",
        "status_none": "no open files",
        "status_row": "{path}: {codec}, {state}",
        "state_unchanged": "working copy untouched",
        "state_edited": "working copy edited",
        "state_other": "file changed outside the session",
        "state_missing": "file missing",
        "scan_summary": "scanned {files} file(s): {legacy} legacy-encoded, {bom} with BOM, {crlf} CRLF, {mixed_eol} mixed "
                        "line endings, {mixed_enc} mixed encodings, {ambiguous} undecided, {skipped} skipped",
        "scan_majority": "most common legacy encoding: {codec}",
        "scan_none": "all scanned files are plain UTF-8/ASCII with LF endings",
        "scan_more": "... {n} more files are listed in report.json",
        "scan_open": "open (not closed) sessions: {paths}",
        "col_path": "path", "col_encoding": "encoding", "col_confidence": "confidence", "col_bom": "BOM",
        "col_eol": "line endings", "col_final": "final NL", "col_flags": "flags",
        "err_E_NOT_FOUND": "file not found or not readable: {path} - check the path (it is relative to the current folder)",
        "err_E_BINARY": "{path} contains NUL bytes and looks binary - do not edit it as text",
        "err_E_AMBIGUOUS": "cannot tell the encoding of {path} (candidates: {candidates}) - ask the user which "
                           "encoding it is and run the command again with --encoding NAME",
        "err_E_UNKNOWN_CODEC": "unknown or unsupported encoding name {name} - use one of: {supported}",
        "err_E_UNSUPPORTED": "{path} is not supported: {detail} - edit it with another tool",
        "err_E_DECODE_FAILED": "{path} is not valid {codec} ({detail}) - check the encoding name or inspect the file first",
        "err_E_MIXED_ENCODING": "{path} mixes encodings on lines {lines} - fix those lines by hand first; "
                                "opening would corrupt them",
        "err_E_UNENCODABLE": "{count} character(s) cannot be written as {codec}: {items} - ask the user for a "
                             "replacement, edit the working copy and run close again, or use --replace xmlcharref",
        "err_E_DRIFT": "verification found a difference: {detail} - run close again or restore with abort",
        "err_E_NOT_OPEN": "{path} is not open - run open first (use status to list open files, or pass the same "
                          "--state-dir you used for open)",
        "err_E_ALREADY_OPEN": "{path} is already open - run close or abort first (--force starts a new session "
                              "from the current bytes)",
        "err_E_STATE_MISMATCH": "{detail} - {fix}",
        "verify_mixed_err": "{path} now has lines in another encoding ({lines}) - the edit added text that is not "
                            "{detail}; restore the file or fix those lines",
        "err_E_IO": "cannot read or write {path}: {detail} - check permissions and free space",
        "mm_backup": "the saved original of {path} does not match its recorded hash",
        "mm_backup_fix": "restore the backup from the state folder or pass --force to use it as is",
        "mm_not_utf8": "the working copy of {path} is not valid UTF-8, so it was changed outside the session",
        "mm_not_utf8_fix": "run abort {path} to restore the original, then open again",
        "mm_other": "{path} was changed outside the session and is not the saved original",
        "mm_other_fix": "pass --force to overwrite it with the saved original",
    },
    "ko": {
        "help_description": "레거시 인코딩(CP949, EUC-KR, Shift_JIS, GBK, Windows-125x, BOM, CRLF) 텍스트 파일을 "
                            "인코딩·줄끝·BOM·마지막 줄바꿈을 바꾸지 않고 고칩니다.",
        "help_epilog": "모든 명령의 stdout 마지막 줄은 ER-SUMMARY 줄입니다. 그대로 보고하세요.",
        "help_lang": "메시지 언어: en 또는 ko (기본: ENCODING_ROUNDTRIP_LANG, 다음 LC_ALL/LC_MESSAGES/LANG, 그 밖에는 en)",
        "help_state_dir": "세션과 report.json 을 둘 폴더 (기본: <git 루트 또는 파일 폴더>/.encoding-roundtrip)",
        "help_json": "텍스트 대신 JSON 을 출력",
        "help_usage": "사용법: ",
        "help_positionals": "위치 인자",
        "help_options": "옵션",
        "help_help": "이 도움말을 보여 주고 끝냅니다",
        "help_version": "버전을 보여 주고 끝냅니다",
        "help_inspect": "인코딩·BOM·줄끝·마지막 줄바꿈·섞인 인코딩 줄을 보여 줍니다",
        "help_scan": "폴더에서 순수 UTF-8/LF 가 아닌 파일을 나열합니다",
        "help_edit": "한 번의 호출로 파일을 고치고 원래 인코딩·BOM·줄끝·마지막 줄바꿈으로 되돌려 씁니다",
        "help_old": "찾을 정확한 텍스트 (--new 와 짝, 여러 번 가능, --all 이 없으면 한 번만 나와야 함)",
        "help_new": "같은 순서의 --old 텍스트를 바꿀 내용",
        "help_all": "--old 가 한 번만 나와야 한다는 조건 없이 모두 바꿉니다",
        "help_append_line": "마지막 줄 뒤에 이 줄을 추가 (여러 번 가능)",
        "help_insert_after": "N:텍스트 - N번 줄 뒤에 줄을 끼워 넣음 (0 = 첫 줄 앞, 여러 번 가능)",
        "help_set_line": "N:텍스트 - N번 줄 전체를 바꿈 (여러 번 가능)",
        "help_delete_line": "N - N번 줄을 지움 (여러 번 가능)",
        "help_text": "디코딩한 내용을 줄 번호와 함께 출력 (읽기 전용)",
        "help_open": "파일을 UTF-8/LF 작업본으로 바꾸고 원본 바이트를 기억합니다",
        "help_close": "작업본을 원래 형식으로 되돌려 씁니다",
        "help_abort": "원본 바이트를 복원하고 작업본을 버립니다",
        "help_verify": "결과를 원본과 비교합니다: 인코딩·BOM·줄끝·마지막 줄바꿈",
        "help_status": "열려 있는(아직 닫지 않은) 파일을 나열합니다",
        "help_paths": "파일 경로(여러 개 가능)",
        "help_path": "파일 경로",
        "help_dir": "검사할 폴더",
        "help_encoding": "판별하지 않고 이 인코딩을 사용 (예: cp949, shift_jis, cp1252)",
        "help_force": "우회가 허용된 안전 검사를 건너뜁니다 (오류 메시지 참고)",
        "help_replace": "인코딩이 담을 수 없는 문자 처리: error(기본), xmlcharref, question",
        "help_final_newline": "결과의 마지막 줄바꿈: keep(기본), add, remove",
        "help_against": "비교할 원본 파일 (기본: 마지막으로 닫은 파일의 백업)",
        "help_max_files": "이 개수까지만 검사 (기본 2000)",
        "yes": "있음", "no": "없음", "none": "없음", "dash": "-",
        "insp_file": "{path}",
        "insp_enc": "  인코딩: {encoding} ({confidence}, 근거: {source})   호환: {compatible}",
        "insp_bom": "  BOM: {bom}   줄끝: {eol_style} (CRLF {crlf}, LF {lf}, CR {cr})   마지막 줄바꿈: {final_newline}",
        "insp_mixed_eol": "  줄끝이 다른 줄: {lines}",
        "insp_mixed_enc": "  인코딩이 다른 줄 (다수는 {dominant}): {lines}",
        "insp_ffd": "  U+FFFD 대체 문자: {n}개",
        "insp_candidates": "  후보: {candidates}",
        "insp_warn_editorconfig": "  참고: .editorconfig 는 charset={charset} 인데 파일은 {encoding} 입니다",
        "insp_next_plain": "  다음: 평범한 UTF-8/LF 파일 - 평소 도구로 고치면 됩니다",
        "insp_next_open": "  다음: edit 로 고치세요 (또는 open, 작업본 수정, close 순서)",
        "insp_next_ambiguous": "  다음: 인코딩이 무엇인지 사용자에게 묻고 open 에 --encoding 을 주세요",
        "insp_next_mixed": "  다음: open 하지 마세요. 위 줄들이 다른 인코딩이라 사용자에게 진행 방법을 물으세요",
        "insp_problem": "  이 파일은 쓸 수 없습니다: {problem}",
        "insp_text_more": "  ... 나머지 {n}줄은 생략했습니다",
        "prob_binary": "NUL 바이트가 있어 바이너리로 보입니다",
        "prob_unsupported": "지원하지 않는 인코딩입니다 ({detail})",
        "prob_too_large": "10 MB 보다 큽니다",
        "prob_decode": "바이트가 올바른 {detail} 가 아닙니다",
        "prob_undecodable": "지원하는 인코딩 중 어느 것으로도 읽히지 않습니다",
        "open_ok": "{path|을/를} 열었습니다: 작업본은 UTF-8 이고 줄끝은 LF 입니다. 고친 뒤 close 를 실행하세요 "
                   "(원본으로 되돌리려면 abort).",
        "open_codec": "  원본 인코딩: {codec} ({confidence}), BOM: {bom}, 줄끝: {eol_style}, 마지막 줄바꿈: {final_newline}",
        "close_ok": "{path|을/를} 닫았습니다: {codec|으로/로} 되돌려 썼고 바뀐 줄은 {changed}개이며, BOM·바뀌지 않은 줄의 줄끝·"
                    "마지막 줄바꿈은 그대로입니다.",
        "close_replaced": "  문자 {n}개를 바꿔 썼습니다 ({mode}).",
        "close_noop": "{path|을/를} 닫았습니다: 바뀐 것이 없어 원본 바이트로 돌아갔습니다.",
        "edit_ok": "{path|을/를} 고쳤습니다: {codec|으로/로} 썼고 바뀐 줄은 {changed}개이며, BOM·바뀌지 않은 줄의 줄끝·"
                   "마지막 줄바꿈은 그대로입니다.",
        "edit_noop": "{path}: 고친 결과가 원래 내용과 같아 파일을 건드리지 않았습니다.",
        "edit_unencodable": "{codec|으로/로} 쓸 수 없는 문자가 {count}개 있습니다: {items} - 아무것도 쓰지 않았습니다. "
                            "사용자에게 대체 문자를 묻고 edit 를 다시 실행하거나 --replace xmlcharref 를 붙이세요",
        "err_E_NO_MATCH": "텍스트를 찾지 못했습니다: {text} - inspect --text 로 내용을 확인하세요 (파일은 그대로입니다)",
        "err_E_MANY_MATCHES": "텍스트가 {count}번 나옵니다: {text} - --old 를 더 길게 주거나 --all 을 붙이세요 "
                              "(파일은 그대로입니다)",
        "err_E_BAD_EDIT": "잘못된 수정 지시: {detail} - edit --help 를 보세요 (파일은 그대로입니다)",
        "abort_ok": "{path|을/를} 원본 바이트로 복원했습니다.",
        "abort_saved": "  고치던 작업본은 {saved} 에 보관했습니다",
        "abort_already": "{path|은/는} 이미 원본 바이트라 세션만 지웠습니다.",
        "verify_ok": "{path|을/를} 검증했습니다: 인코딩 {codec}, BOM, 줄끝이 원본과 같습니다. 바뀐 줄: {lines}",
        "verify_fail": "{path|의} 검증이 실패했습니다:",
        "verify_item_decode": "  - {codec|으로/로} 읽을 수 없는 줄: {lines}",
        "verify_item_utf8": "  - 파일이 UTF-8 작업본처럼 보입니다 (close 하거나 abort 하세요)",
        "verify_item_bom": "  - BOM 이 원본과 다릅니다",
        "verify_item_eol": "  - 바뀌지 않은 줄의 줄끝이 달라졌습니다: {lines}",
        "verify_item_final": "  - 마지막 줄바꿈이 원본과 다릅니다",
        "verify_item_ffd": "  - U+FFFD 가 {before}개에서 {after}개로 늘었습니다",
        "verify_item_mixed": "  - 인코딩이 다른 줄: {lines}",
        "status_none": "열려 있는 파일이 없습니다",
        "status_row": "{path}: {codec}, {state}",
        "state_unchanged": "작업본 그대로",
        "state_edited": "작업본 수정됨",
        "state_other": "세션 밖에서 파일이 바뀜",
        "state_missing": "파일 없음",
        "scan_summary": "파일 {files}개 검사: 레거시 인코딩 {legacy}, BOM {bom}, CRLF {crlf}, 줄끝 혼합 {mixed_eol}, "
                        "인코딩 혼합 {mixed_enc}, 판별 보류 {ambiguous}, 건너뜀 {skipped}",
        "scan_majority": "가장 많은 레거시 인코딩: {codec}",
        "scan_none": "검사한 파일이 모두 LF 줄끝의 순수 UTF-8/ASCII 입니다",
        "scan_more": "... 나머지 {n}개는 report.json 에 있습니다",
        "scan_open": "열려 있는(닫지 않은) 세션: {paths}",
        "col_path": "경로", "col_encoding": "인코딩", "col_confidence": "확신", "col_bom": "BOM",
        "col_eol": "줄끝", "col_final": "끝 줄바꿈", "col_flags": "표시",
        "err_E_NOT_FOUND": "파일이 없거나 읽을 수 없습니다: {path} - 경로를 확인하세요 (현재 폴더 기준)",
        "err_E_BINARY": "{path|에} NUL 바이트가 있어 바이너리로 보입니다 - 텍스트로 고치지 마세요",
        "err_E_AMBIGUOUS": "{path|의} 인코딩을 확정할 수 없습니다 (후보: {candidates}) - 사용자에게 인코딩을 묻고 "
                           "--encoding 이름 을 붙여 같은 명령을 다시 실행하세요",
        "err_E_UNKNOWN_CODEC": "알 수 없거나 지원하지 않는 인코딩 이름 {name} - 다음 중에서 고르세요: {supported}",
        "err_E_UNSUPPORTED": "{path|은/는} 지원하지 않습니다: {detail} - 다른 도구로 고치세요",
        "err_E_DECODE_FAILED": "{path|은/는} 올바른 {codec} 가 아닙니다 ({detail}) - 인코딩 이름을 확인하거나 먼저 inspect 하세요",
        "err_E_MIXED_ENCODING": "{path|은/는} {lines} 줄의 인코딩이 다릅니다 - 그 줄을 먼저 손으로 고치세요. "
                                "그대로 열면 그 줄이 깨집니다",
        "err_E_UNENCODABLE": "{codec|으로/로} 쓸 수 없는 문자가 {count}개 있습니다: {items} - 사용자에게 대체 문자를 묻고 "
                             "작업본을 고쳐 close 를 다시 실행하거나 --replace xmlcharref 를 쓰세요",
        "err_E_DRIFT": "검증에서 차이를 찾았습니다: {detail} - close 를 다시 실행하거나 abort 로 되돌리세요",
        "err_E_NOT_OPEN": "{path|은/는} 열려 있지 않습니다 - 먼저 open 을 실행하세요 (status 로 열린 파일을 보거나 "
                          "open 때와 같은 --state-dir 를 주세요)",
        "err_E_ALREADY_OPEN": "{path|은/는} 이미 열려 있습니다 - 먼저 close 나 abort 를 실행하세요 "
                              "(--force 는 현재 바이트로 새 세션을 시작합니다)",
        "err_E_STATE_MISMATCH": "{detail} - {fix}",
        "verify_mixed_err": "{path|에} 다른 인코딩의 줄이 생겼습니다 ({lines}) - 고친 텍스트의 인코딩이 {detail|이/가} 아닙니다. "
                            "파일을 복원하거나 그 줄을 고치세요",
        "err_E_IO": "{path|을/를} 읽거나 쓸 수 없습니다: {detail} - 권한과 남은 공간을 확인하세요",
        "mm_backup": "{path|의} 저장된 원본이 기록된 해시와 다릅니다",
        "mm_backup_fix": "상태 폴더의 백업을 복구하거나 --force 로 그대로 쓰세요",
        "mm_not_utf8": "{path|의} 작업본이 올바른 UTF-8 이 아니라 세션 밖에서 바뀐 것입니다",
        "mm_not_utf8_fix": "abort {path} 로 원본을 복원한 뒤 다시 open 하세요",
        "mm_other": "{path|은/는} 세션 밖에서 바뀌어 저장된 원본이 아닙니다",
        "mm_other_fix": "--force 로 저장된 원본을 덮어쓰세요",
    },
}

_TOKEN = re.compile(r"\{(\w+)(?:\|([^}]+))?\}")
CURRENT_LANG = "en"


def _batchim(word: str) -> int:
    """Final consonant index of the last syllable of `word` (0 = none, 8 = rieul)."""
    digits = {"0": 21, "1": 8, "2": 0, "3": 16, "4": 0, "5": 0, "6": 1, "7": 8, "8": 8, "9": 0}
    letters = {"l": 8, "m": 16, "n": 4, "r": 8}
    for ch in reversed(word):
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:
            return (o - 0xAC00) % 28
        if ch in digits:
            return digits[ch]
        if ch.isascii() and ch.isalpha():
            return letters.get(ch.lower(), 0)
    return 0


def josa(word: str, pair: str) -> str:
    """Korean particle for `word`: pair is 'topic/with-vowel' such as 은/는, 이/가, 을/를, 으로/로, 의 (fixed)."""
    if "/" not in pair:
        return pair
    with_final, without = pair.split("/", 1)
    t = _batchim(word)
    if with_final == "으로":
        return with_final if t not in (0, 8) else without
    return with_final if t else without


def msg(key: str, **kw: Any) -> str:
    """Localized text for `key` in the current language."""
    template = MESSAGES[CURRENT_LANG][key]

    def repl(m: re.Match) -> str:
        value = str(kw[m.group(1)])
        return value + (josa(value, m.group(2)) if m.group(2) else "")

    return _TOKEN.sub(repl, template)


def resolve_lang(cli_value: str | None, environ: dict[str, str] | None = None) -> str:
    """--lang > ENCODING_ROUNDTRIP_LANG > LC_ALL > LC_MESSAGES > LANG; anything but Korean is English."""
    env = os.environ if environ is None else environ
    if cli_value in ("en", "ko"):
        return cli_value
    for name in (LANG_ENV, "LC_ALL", "LC_MESSAGES", "LANG"):
        value = (env.get(name) or "").strip().lower()
        if not value:
            continue
        if name == LANG_ENV and value not in ("en", "ko"):
            continue
        return "ko" if value.startswith("ko") else "en"
    return "en"


# ---------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------

EXIT_FOR = {"E_NOT_FOUND": 2, "E_BINARY": 2, "E_AMBIGUOUS": 2, "E_UNKNOWN_CODEC": 2, "E_UNSUPPORTED": 2,
            "E_DECODE_FAILED": 2, "E_MIXED_ENCODING": 2, "E_IO": 2,
            "E_UNENCODABLE": 1, "E_DRIFT": 1, "E_NO_MATCH": 1, "E_MANY_MATCHES": 1, "E_BAD_EDIT": 2,
            "E_NOT_OPEN": 3, "E_ALREADY_OPEN": 3, "E_STATE_MISMATCH": 3}


class ERError(Exception):
    """A user-facing failure: `code` (E_...), exit code, message parameters and summary extras."""

    def __init__(self, code: str, exit_code: int | None = None, extra: dict[str, Any] | None = None,
                 key: str | None = None, **params: Any):
        super().__init__(code)
        self.code = code
        self.key = key
        self.exit_code = EXIT_FOR[code] if exit_code is None else exit_code
        self.params = params
        self.extra = extra or {}

    def text(self) -> str:
        return msg(self.key or "err_" + self.code, **self.params)


# ---------------------------------------------------------------------------------------------
# Small helpers: hashing, display, tables, files
# ---------------------------------------------------------------------------------------------

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def receipt_for(cmd: str, input_sha: str, result_sha: str, codec: str, status: str = "ok", code: str = "") -> str:
    """Short digest that only this script computes; it ties a summary line to real input and output bytes.

    ok lines keep the original er1 form (receipts already printed stay valid). Other lines also bind the
    status and error code, so a hand-edited status= or code= no longer matches its receipt."""
    base = f"er1|{cmd}|{input_sha}|{result_sha}|{codec}"
    if status != "ok" or code:
        base += f"|{status}|{code}"      # 실패·오류 줄은 상태와 오류 코드까지 묶는다
    return sha256_hex(base.encode("utf-8"))[:16]


def dwidth(s: str) -> int:
    """Display width: East Asian wide/fullwidth characters take two columns, combining marks none."""
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - dwidth(s))


def render_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [max(dwidth(h), *(dwidth(r[i]) for r in rows)) if rows else dwidth(h) for i, h in enumerate(headers)]
    lines = ["  ".join(pad(h, widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for r in rows:
        lines.append("  ".join(pad(c, widths[i]) for i, c in enumerate(r)).rstrip())
    return lines


def display_path(p: Path) -> str:
    """Path relative to the current folder when it lies below it, else absolute; always with slashes."""
    try:
        return p.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except (ValueError, OSError):
        return p.as_posix()


def sv(value: Any) -> str:
    """Value for an ER-SUMMARY key=value pair (no spaces)."""
    s = str(value)
    return re.sub(r"\s", lambda m: "%%%02X" % ord(m.group()), s) or "-"


def read_file(p: Path, shown: str) -> bytes:
    try:
        return p.read_bytes()
    except OSError as e:
        raise ERError("E_NOT_FOUND", path=shown) from e


def atomic_write(target: Path, data: bytes) -> None:
    """Write bytes next to `target`, then replace it in one step; keeps the file mode."""
    directory = target.parent
    fd, tmp = tempfile.mkstemp(prefix=".er-", suffix=".tmp", dir=str(directory))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if target.exists():
            shutil.copymode(str(target), tmp)
        os.replace(tmp, str(target))
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ERError("E_IO", path=display_path(target), detail=str(e)) from e


# ---------------------------------------------------------------------------------------------
# Lines, line endings, BOM
# ---------------------------------------------------------------------------------------------

def split_bom(data: bytes) -> tuple[str, bytes, bytes]:
    """(codec, bom bytes, body). codec is '' when there is no BOM and 'utf-32' for the unsupported UTF-32 BOMs."""
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return "utf-32", data[:4], data[4:]
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8", codecs.BOM_UTF8, data[3:]
    if data.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le", codecs.BOM_UTF16_LE, data[2:]
    if data.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be", codecs.BOM_UTF16_BE, data[2:]
    return "", b"", data


def split_lines(body: bytes, codec: str) -> list[tuple[bytes, bytes]]:
    """Split into (content bytes, line ending bytes). The last line may have an empty line ending."""
    if codec.startswith("utf-16"):
        text = body.decode(codec)
        out: list[tuple[bytes, bytes]] = []
        pos = 0
        for m in _EOL_TEXT.finditer(text):
            out.append((text[pos:m.start()].encode(codec), m.group().encode(codec)))
            pos = m.end()
        if pos < len(text):
            out.append((text[pos:].encode(codec), b""))
        return out
    out = []
    pos = 0
    for m in _EOL_BYTES.finditer(body):
        out.append((body[pos:m.start()], m.group()))
        pos = m.end()
    if pos < len(body):
        out.append((body[pos:], b""))
    return out


def eol_name(eol: bytes, codec: str = "") -> str:
    try:
        text = eol.decode(codec) if codec.startswith("utf-16") else eol.decode("ascii")
    except UnicodeDecodeError:
        return "?"
    return {"\n": "lf", "\r\n": "crlf", "\r": "cr"}.get(text, "")


def eol_summary(lines: list[tuple[bytes, bytes]], codec: str = "") -> dict[str, Any]:
    counts = {"lf": 0, "crlf": 0, "cr": 0}
    names = []
    for _, e in lines:
        n = eol_name(e, codec)
        names.append(n)
        if n in counts:
            counts[n] += 1
    kinds = [k for k, v in counts.items() if v]
    style = "none" if not kinds else (kinds[0] if len(kinds) == 1 else "mixed")
    majority = max(kinds, key=lambda k: (counts[k], -["lf", "crlf", "cr"].index(k))) if kinds else ""
    if style == "mixed":   # ties go to the first line ending that appears in the file
        top = max(counts.values())
        tied = [k for k in kinds if counts[k] == top]
        majority = next(n for n in names if n in tied)
    odd = [i + 1 for i, n in enumerate(names) if style == "mixed" and n and n != majority]
    return {"eol": counts, "eol_style": style, "mixed_eol_lines": odd,
            "final_newline": bool(lines) and lines[-1][1] != b""}


def majority_eol(lines: list[tuple[bytes, bytes]], codec: str) -> bytes:
    """Most common non-empty line ending (first one on ties); LF when the file has none."""
    seen: dict[bytes, int] = {}
    for _, e in lines:
        if e:
            seen[e] = seen.get(e, 0) + 1
    if not seen:
        return "\n".encode(codec) if codec.startswith("utf-16") else EOL_LF
    top = max(seen.values())
    for _, e in lines:
        if e and seen[e] == top:
            return e
    return EOL_LF


def find_mixed(lines: list[tuple[bytes, bytes]]) -> tuple[list[int], list[int]]:
    """Among lines with non-ASCII bytes: (1-based numbers of strict UTF-8 lines, numbers of the other lines)."""
    utf8_lines: list[int] = []
    other: list[int] = []
    for i, (c, _) in enumerate(lines, 1):
        if c.isascii():
            continue
        try:
            c.decode("utf-8")
            utf8_lines.append(i)
        except UnicodeDecodeError:
            other.append(i)
    return utf8_lines, other


MATCH_LIMIT = 4_000_000      # longest middle block (old lines x new lines) handed to difflib


def diff_ops(old: list[str], new: list[str]) -> list[tuple[str, int, int, int, int]]:
    """difflib-style opcodes. Common leading and trailing lines are matched directly, so a small edit in a large
    file stays fast; a huge changed middle falls back to one positional replace block."""
    if old == new:
        return [("equal", 0, len(old), 0, len(new))]
    shortest = min(len(old), len(new))
    head = 0
    while head < shortest and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < shortest - head and old[len(old) - 1 - tail] == new[len(new) - 1 - tail]:
        tail += 1
    ops: list[tuple[str, int, int, int, int]] = []
    if head:
        ops.append(("equal", 0, head, 0, head))
    o_end, n_end = len(old) - tail, len(new) - tail
    if head < o_end or head < n_end:
        if (o_end - head) * (n_end - head) > MATCH_LIMIT:
            tag = "replace" if head < o_end and head < n_end else ("delete" if head < o_end else "insert")
            ops.append((tag, head, o_end, head, n_end))
        else:
            matcher = difflib.SequenceMatcher(None, old[head:o_end], new[head:n_end], autojunk=False)
            ops.extend((t, i1 + head, i2 + head, j1 + head, j2 + head) for t, i1, i2, j1, j2 in matcher.get_opcodes())
    if tail:
        ops.append(("equal", o_end, len(old), n_end, len(new)))
    return ops


def short_list(nums: list[int], limit: int = 12) -> str:
    if not nums:
        return "-"
    shown = ",".join(str(n) for n in nums[:limit])
    return shown + (f",...(+{len(nums) - limit})" if len(nums) > limit else "")


# ---------------------------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------------------------

KO_COMMON = frozenset("이다는의에을를은가고하지서로도한과와으며들나기수그리것아자인사대시어일해라여전만있되면때부제정국년중"
                      "보같위상선원공미우주연동소두산신관성개간방장명경조행세회용없입니습저실오문류름존파글데터버설값읽쓰열닫패처"
                      "목록확")
JA_COMMON = frozenset("日本語人大年出中国会時事自社者地業方新場員立開手力問代明動京目通言理体田主題意不作用度強公持野以思家世"
                      "多正安院心界教文元重近考画海売知道集別物使品計死特私始朝運終台広住真有口少町料工建空急止送切転研足究楽起"
                      "着店病質待試族銀早映親験英医仕去味写字答夜音注帰古歌買悪図週室歩風紙黒花春赤青館屋色走秋夏習駅洋旅服夕借"
                      "曜飲肉貸堂鳥飯勉冬昼茶弟牛魚兄犬妹姉漢設定読込書保存失敗処理確認結果値名前一覧追加削除更新表示選択")
ZH_COMMON = frozenset("的一是不了人我在有他这中大来上个们到说国和地也子时道出而要于就下得可你年生自会那后能对着事其里所去行过家十用"
                      "发天如然作方成者多日都三小军二无同么经法当起与好看学进种将还分此心前面又定见只主没公从错误文件读取写入失败"
                      "保存配置设置注释函数变量返回处理数据")
TW_COMMON = frozenset("的一是不了人我在有他這中大來上個們到說國和地也子時道出而要於就下得可你年生自會那後能對著事其裡所去行過家十用"
                      "發天如然作方成者多日都三小軍二無同麼經法當起與好看學進種將還分此心前面又定見只主沒公從錯誤檔案讀取寫入失敗"
                      "儲存設定註解函數變數返回處理資料")
LATIN1_RARE = frozenset("ðþÐÞøØìÌòÒ×÷")
LATIN1_OK = frozenset("«»¿¡°·º ª§©®€µ")
TYPOGRAPHIC = frozenset("–—‘’‚“”„†‡•…‰‹›€™№")


def _ascii_alpha_next_to(text: str, i: int) -> bool:
    return (i > 0 and text[i - 1].isascii() and text[i - 1].isalpha()) or \
           (i + 1 < len(text) and text[i + 1].isascii() and text[i + 1].isalpha())


def _is_cyrillic(ch: str) -> bool:
    return "Ѐ" <= ch <= "ӿ"


def score_text(codec: str, text: str) -> float:
    """How plausible `text` is as real writing in the script this codec exists for (higher = more plausible)."""
    fam = FAMILY[codec]
    s = 0.0
    for i, ch in enumerate(text):
        o = ord(ch)
        if o < 0x80:
            if (o < 32 and ch not in "\t\r\n\f") or o == 127:
                s -= 3
            continue
        if 0x80 <= o <= 0x9F or o == 0xFFFD or 0xE000 <= o <= 0xF8FF:
            s -= 3
            continue
        if fam == "ko":
            if 0xAC00 <= o <= 0xD7A3:
                s += 3 if ch in KO_COMMON else 1
            elif 0x3131 <= o <= 0x318E:
                s += 0.5
            elif 0x4E00 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF \
                    or 0x2000 <= o <= 0x27BF:
                s += 0.3
            elif 0x3040 <= o <= 0x30FF:
                s += 0.2
            else:
                s -= 0.5
        elif fam == "ja":
            if 0x3040 <= o <= 0x30FF:
                s += 2.5
            elif 0x4E00 <= o <= 0x9FFF:
                s += 2.5 if ch in JA_COMMON else 0.5
            elif 0xFF61 <= o <= 0xFF9F:
                s += 0.2
            elif 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFF60 or 0xFFE0 <= o <= 0xFFE6 or 0x2000 <= o <= 0x27BF:
                s += 0.3
            elif 0xAC00 <= o <= 0xD7A3:
                s -= 1
            else:
                s -= 0.5
        elif fam in ("zh", "tw"):
            common = ZH_COMMON if fam == "zh" else TW_COMMON
            if 0x4E00 <= o <= 0x9FFF:
                s += 3 if ch in common else 0.5
            elif 0x3400 <= o <= 0x4DBF:
                s += 0.1
            elif 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF or 0x2000 <= o <= 0x27BF:
                s += 0.3
            elif 0x3040 <= o <= 0x30FF:
                s += 0.2
            elif 0xAC00 <= o <= 0xD7A3:
                s -= 1
            else:
                s -= 0.5
        elif fam in ("c1252", "c1250"):
            if 0xC0 <= o <= 0xFF:
                if ch in "×÷":
                    s -= 0.5
                elif _ascii_alpha_next_to(text, i):
                    s += 0.5 if (fam == "c1252" and ch in LATIN1_RARE) else 1.5
                else:
                    s -= 0.5
            elif fam == "c1250" and 0x100 <= o <= 0x17F:
                s += 1.4 if _ascii_alpha_next_to(text, i) else -0.5
            elif fam == "c1252" and o in (0x152, 0x153, 0x160, 0x161, 0x17D, 0x17E, 0x178):
                s += 0.5
            elif ch in TYPOGRAPHIC:
                s += 0.5
            elif 0xA0 <= o <= 0xBF:
                s += 0.3 if ch in LATIN1_OK else -1
            else:
                s -= 1
        else:  # c1251
            if _is_cyrillic(ch) and ch.isalpha():
                near = (i > 0 and _is_cyrillic(text[i - 1])) or (i + 1 < len(text) and _is_cyrillic(text[i + 1]))
                if ch.islower():
                    s += 2 if near else 0
                else:
                    word_start = i == 0 or not _is_cyrillic(text[i - 1])
                    s += 1.5 if word_start and near else -1
            elif ch in TYPOGRAPHIC:
                s += 0.5
            elif 0xA0 <= o <= 0xBF:
                s += 0.3 if ch in LATIN1_OK else -1
            else:
                s -= 1
    return s


def _try_decode(data: bytes, codec: str) -> str | None:
    try:
        return data.decode(codec)
    except UnicodeDecodeError:
        return None


def detect_content(body: bytes, gitattr: str | None = None, folder: str | None = None) -> dict[str, Any]:
    """Pick a legacy codec for `body` (non-ASCII, not valid UTF-8). Returns encoding, confidence, candidates,
    compatible and source. Confidence: certain, likely or ambiguous (ambiguous = do not write)."""
    texts: dict[str, str] = {}
    for c in CANDIDATES:
        t = _try_decode(body, c)
        if t is not None:
            texts[c] = t
    res: dict[str, Any] = {"encoding": None, "confidence": "none", "candidates": [], "compatible": [], "source": "content"}
    if not texts:
        return res
    groups: dict[str, list[str]] = {}
    for c, t in texts.items():
        groups.setdefault(t, []).append(c)
    ranked = []
    for t, cs in groups.items():
        cs.sort(key=PREFER.__getitem__)
        sample = t[:SAMPLE_CHARS]
        ranked.append({"codecs": cs, "score": max(score_text(c, sample) for c in cs),
                       "ev": sum(1 for ch in sample if ord(ch) > 127)})
    ranked.sort(key=lambda g: (-g["score"], PREFER[g["codecs"][0]]))
    res["candidates"] = [c for g in ranked for c in g["codecs"]]

    def pick(group: dict[str, Any], chosen: str | None, confidence: str, source: str) -> dict[str, Any]:
        enc = chosen or group["codecs"][0]
        res.update(encoding=enc, confidence=confidence, source=source,
                   compatible=[c for c in group["codecs"] if c != enc])
        return res

    if gitattr:
        for g in ranked:
            if gitattr in g["codecs"]:
                return pick(g, gitattr, "certain", "gitattributes")
    top = ranked[0]
    second = ranked[1]["score"] if len(ranked) > 1 else 0.0
    if top["score"] >= 20 and top["ev"] >= 8 and second <= 0.4 * top["score"]:
        return pick(top, None, "certain", "content")
    if top["score"] >= 8 and top["ev"] >= 4 and second <= 0.7 * top["score"]:
        return pick(top, None, "likely", "content")
    if folder:
        for g in ranked:
            if folder in g["codecs"]:
                return pick(g, folder, "likely", "folder")
    res["confidence"] = "ambiguous"
    return res


def normalize_codec(name: str) -> str:
    """Canonical supported codec name for a user-supplied name, or raise E_UNKNOWN_CODEC."""
    try:
        found = codecs.lookup(name).name
    except LookupError:
        found = ""
    if found not in ("utf-8", "ascii", "utf-16", "utf-16-le", "utf-16-be"):
        found = found.replace("-", "_")
    if found == "utf-16":
        found = "utf-16-le" if name.lower().replace("_", "-").endswith("le") else "utf-16"
    if found not in SUPPORTED and found != "utf-16":
        raise ERError("E_UNKNOWN_CODEC", name=name, supported=", ".join(sorted(SUPPORTED)))
    return found


def analyze(data: bytes, forced: str | None = None,
            hint_fn: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    """Everything `inspect` reports about one file. `hint_fn('gitattributes'|'folder')` supplies optional codec hints."""
    info: dict[str, Any] = {
        "encoding": None, "confidence": "none", "candidates": [], "compatible": [], "source": "", "bom": "none",
        "bom_hex": "", "eol": {"lf": 0, "crlf": 0, "cr": 0}, "eol_style": "none", "mixed_eol_lines": [],
        "final_newline": False, "mixed_encoding_lines": [], "mixed_dominant": "", "replacement_chars": 0,
        "sha256": sha256_hex(data), "size": len(data), "plain": False, "problem": "", "problem_detail": "", "lines": 0}
    if len(data) > MAX_BYTES:
        info["problem"] = "too_large"
        return info
    forced_codec = normalize_codec(forced) if forced else None
    bom_codec, bom_bytes, body = split_bom(data)
    if bom_codec == "utf-32":
        info.update(problem="unsupported", problem_detail="UTF-32")
        return info
    codec = ""
    if bom_codec:
        if forced_codec and forced_codec not in (bom_codec, "utf-16"):
            info.update(problem="unsupported", problem_detail=f"BOM says {bom_codec} but --encoding is {forced_codec}")
            return info
        info.update(encoding=bom_codec, confidence="certain", source="bom", bom_hex=bom_bytes.hex(),
                    bom={"utf-8": "utf-8", "utf-16-le": "utf-16le", "utf-16-be": "utf-16be"}[bom_codec])
        if _try_decode(body, bom_codec) is None:
            info.update(problem="decode", problem_detail=bom_codec)
            return info
        codec = bom_codec
    elif b"\x00" in body:
        info["problem"] = "binary"
        return info
    elif forced_codec:
        if forced_codec == "utf-16":
            info.update(problem="unsupported", problem_detail="UTF-16 without BOM")
            return info
        info.update(encoding=forced_codec, confidence="certain", source="argument")
        if _try_decode(body, forced_codec) is None:
            info.update(problem="decode", problem_detail=forced_codec)
            return info
        codec = forced_codec
    lines = split_lines(body, codec if codec.startswith("utf-16") else "")
    if not codec:
        if body.isascii():
            if _ISO2022.search(body):
                info.update(problem="unsupported", problem_detail="ISO-2022 escape sequences")
                return info
            hint = (hint_fn("gitattributes") if hint_fn else None) or (hint_fn("folder") if hint_fn else None)
            info.update(encoding="ascii", confidence="certain", source="ascii", compatible=[hint] if hint else [])
            codec = "ascii"
        elif _try_decode(body, "utf-8") is not None:
            info.update(encoding="utf-8", confidence="certain", source="utf8")
            codec = "utf-8"
        else:
            utf8_lines, other = find_mixed(lines)
            if utf8_lines and other and len(utf8_lines) > len(other):
                info.update(encoding="utf-8", confidence="likely", source="content", mixed_dominant="utf-8",
                            mixed_encoding_lines=other)
                codec = "utf-8"
            elif utf8_lines and other:
                legacy_part = b"\n".join(lines[i - 1][0] for i in other)
                info.update(_detect_with_hints(legacy_part, hint_fn))
                info.update(mixed_dominant=info["encoding"] or "legacy", mixed_encoding_lines=utf8_lines)
                codec = info["encoding"] or ""
            else:
                info.update(_detect_with_hints(body, hint_fn))
                codec = info["encoding"] or ""
            if not codec and not info["candidates"]:
                info["problem"] = "undecodable"
    elif codec not in ("utf-8", "ascii") and not codec.startswith("utf-16"):
        utf8_lines, other = find_mixed(lines)
        if utf8_lines and other:
            info.update(mixed_encoding_lines=utf8_lines, mixed_dominant=codec)
    if codec:
        text = _try_decode(body, codec)
        if text is not None:
            info["replacement_chars"] = text.count("�")
    info.update(eol_summary(lines, codec if codec.startswith("utf-16") else ""))
    info["lines"] = len(lines)
    info["plain"] = (info["encoding"] in ("utf-8", "ascii") and info["bom"] == "none"
                     and info["eol_style"] in ("lf", "none") and not info["mixed_encoding_lines"])
    return info


def _detect_with_hints(body: bytes, hint_fn: Callable[[str], str | None] | None) -> dict[str, Any]:
    """Run content detection; ask for the folder hint only when the content alone cannot decide."""
    gitattr = hint_fn("gitattributes") if hint_fn else None
    det = detect_content(body, gitattr=gitattr)
    if det["confidence"] == "ambiguous" and hint_fn:
        folder = hint_fn("folder")
        if folder:
            det = detect_content(body, gitattr=gitattr, folder=folder)
    return det


# ---------------------------------------------------------------------------------------------
# Hints from the project: .gitattributes, .editorconfig, sibling files
# ---------------------------------------------------------------------------------------------

def _walk_up(start: Path, names: tuple[str, ...], limit: int = 12) -> list[Path]:
    found = []
    cur = start
    for _ in range(limit):
        for n in names:
            if (cur / n).is_file():
                found.append(cur / n)
        if (cur / ".git").exists() or cur.parent == cur:
            break
        cur = cur.parent
    return found


def gitattributes_codec(path: Path) -> str | None:
    """Codec named by `working-tree-encoding=` in the nearest .gitattributes that matches this file."""
    for ga in _walk_up(path.parent, (".gitattributes",)):
        try:
            text = ga.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[0].startswith("#"):
                continue
            wte = next((p.split("=", 1)[1] for p in parts[1:] if p.startswith("working-tree-encoding=")), None)
            if wte and fnmatch.fnmatch(path.name, parts[0].lstrip("/")):
                try:
                    c = normalize_codec(wte)
                except ERError:
                    return None
                return c if c in CANDIDATES else None
    return None


def editorconfig_charset(path: Path) -> str | None:
    """`charset` that the nearest .editorconfig sets for this file (informational only)."""
    for ec in _walk_up(path.parent, (".editorconfig",)):
        try:
            text = ec.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        match_section = False
        value = None
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                pat = line[1:-1]
                alts = [pat]
                m = re.fullmatch(r"(.*)\{([^}]*)\}(.*)", pat)
                if m:
                    alts = [m.group(1) + a + m.group(3) for a in m.group(2).split(",")]
                match_section = any(fnmatch.fnmatch(path.name, a.lstrip("/")) for a in alts)
            elif match_section and "=" in line and not line.startswith(("#", ";")):
                k, v = (x.strip().lower() for x in line.split("=", 1))
                if k == "charset":
                    value = v
        if value:
            return value
    return None


def folder_majority(path: Path, limit: int = 60) -> str | None:
    """Most common certain/likely legacy codec among the other files in the same folder."""
    counts: dict[str, int] = {}
    try:
        siblings = sorted(p for p in path.parent.iterdir() if p.is_file() and p != path)[:limit]
    except OSError:
        return None
    for p in siblings:
        try:
            if p.stat().st_size > 1024 * 1024:
                continue
            data = p.read_bytes()
        except OSError:
            continue
        if data.isascii() or b"\x00" in data:
            continue
        info = analyze(data)
        if info["confidence"] in ("certain", "likely") and info["encoding"] in CANDIDATES and not info["mixed_encoding_lines"]:
            counts[info["encoding"]] = counts.get(info["encoding"], 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda c: (counts[c], -PREFER[c]))


def hints_for(path: Path) -> Callable[[str], str | None]:
    cache: dict[str, str | None] = {}

    def hint(kind: str) -> str | None:
        if kind not in cache:
            cache[kind] = gitattributes_codec(path) if kind == "gitattributes" else folder_majority(path)
        return cache[kind]

    return hint


# ---------------------------------------------------------------------------------------------
# State folder and sessions
# ---------------------------------------------------------------------------------------------

def git_root(start: Path) -> Path | None:
    cur = start
    for _ in range(16):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def state_dir_for(explicit: str | None, anchor: Path) -> Path:
    """--state-dir, else <git root>/.encoding-roundtrip, else <folder of the file>/.encoding-roundtrip."""
    if explicit:
        return Path(explicit).resolve()
    folder = anchor if anchor.is_dir() else anchor.parent
    return (git_root(folder) or folder) / STATE_DIRNAME


def ensure_state(state: Path) -> None:
    try:
        state.mkdir(parents=True, exist_ok=True)
        gi = state / ".gitignore"
        if not gi.exists():
            gi.write_bytes(b"*\n")
    except OSError as e:
        raise ERError("E_IO", path=display_path(state), detail=str(e)) from e


def session_key(target: Path) -> str:
    return sha256_hex(str(target).encode("utf-8", "surrogateescape"))[:16]


def session_paths(state: Path, key: str, folder: str = "sessions") -> tuple[Path, Path]:
    return state / folder / f"{key}.json", state / folder / f"{key}.orig"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def load_session(state: Path, key: str, folder: str = "sessions") -> dict[str, Any] | None:
    jp, _ = session_paths(state, key, folder)
    try:
        return json.loads(jp.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------
# Command context and summary line
# ---------------------------------------------------------------------------------------------

class Ctx:
    def __init__(self, cmd: str, args: argparse.Namespace):
        self.cmd = cmd
        self.args = args
        self.lines: list[str] = []
        self.payload: dict[str, Any] = {"tool": TOOL, "version": VERSION, "cmd": cmd, "status": "ok"}
        self.summary_fields: list[tuple[str, Any]] = []
        self.input_sha = "-"
        self.result_sha = "-"
        self.codec = "-"
        self.state: Path | None = None
        self.error_code = ""          # set when the command ends with an ERError

    def say(self, text: str) -> None:
        self.lines.append(text)

    def receipt(self, status: str) -> str:
        return receipt_for(self.cmd, self.input_sha, self.result_sha, self.codec, status, self.error_code)

    def summary(self, status: str) -> str:
        fields = [("cmd", self.cmd), ("status", status)] + self.summary_fields + [
            ("input_sha256", self.input_sha), ("result_sha256", self.result_sha),
            ("receipt", self.receipt(status))]
        return "ER-SUMMARY " + " ".join(f"{k}={sv(v)}" for k, v in fields)


def resolve_file(raw: str) -> tuple[Path, str]:
    """(real path, display path) of a file argument; E_NOT_FOUND when it is not a readable file."""
    p = Path(raw)
    shown = raw.replace("\\", "/")
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ERError("E_NOT_FOUND", path=shown) from None
    if not real.is_file():
        raise ERError("E_NOT_FOUND", path=shown)
    return real, shown


def file_entry(path: str, info: dict[str, Any]) -> dict[str, Any]:
    keys = ["encoding", "confidence", "candidates", "compatible", "source", "bom", "eol", "eol_style",
            "mixed_eol_lines", "final_newline", "mixed_encoding_lines", "replacement_chars", "sha256"]
    entry = {"path": path}
    entry.update({k: info[k] for k in keys})
    return entry


def eol_field(info: dict[str, Any]) -> str:
    e = info["eol"]
    return f"crlf:{e['crlf']},lf:{e['lf']}"


def yes_no(flag: bool) -> str:
    return msg("yes") if flag else msg("no")


def problem_error(info: dict[str, Any], shown: str) -> ERError | None:
    """Translate an analysis problem or an unusable result into the error `open` would raise."""
    extra = {"path": shown, "input_sha256": info["sha256"]}
    p = info["problem"]
    if p == "binary":
        return ERError("E_BINARY", extra=extra, path=shown)
    if p in ("unsupported", "too_large"):
        detail = info["problem_detail"] or msg("prob_too_large")
        return ERError("E_UNSUPPORTED", extra=extra, path=shown, detail=detail)
    if p == "decode":
        return ERError("E_DECODE_FAILED", extra=extra, path=shown, codec=info["problem_detail"],
                       detail="invalid byte sequence")
    if p == "undecodable":
        return ERError("E_AMBIGUOUS", extra=extra, path=shown, candidates="-")
    if info["mixed_encoding_lines"]:
        return ERError("E_MIXED_ENCODING", extra=extra, path=shown, lines=short_list(info["mixed_encoding_lines"]))
    if info["confidence"] == "ambiguous":
        return ERError("E_AMBIGUOUS", extra={**extra, "candidates": ",".join(info["candidates"])}, path=shown,
                       candidates=", ".join(info["candidates"]))
    return None


# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------

def describe(ctx: Ctx, shown: str, info: dict[str, Any], editorconfig: str | None = None) -> None:
    ctx.say(msg("insp_file", path=shown))
    if info["problem"]:
        key = {"too_large": "prob_too_large", "binary": "prob_binary", "unsupported": "prob_unsupported",
               "decode": "prob_decode", "undecodable": "prob_undecodable"}[info["problem"]]
        ctx.say(msg("insp_problem", problem=msg(key, detail=info["problem_detail"])))
        return
    compat = ", ".join(info["compatible"]) or msg("dash")
    ctx.say(msg("insp_enc", encoding=info["encoding"] or "?", confidence=info["confidence"],
                source=info["source"], compatible=compat))
    ctx.say(msg("insp_bom", bom=msg("none") if info["bom"] == "none" else info["bom"], eol_style=info["eol_style"],
                crlf=info["eol"]["crlf"], lf=info["eol"]["lf"], cr=info["eol"]["cr"],
                final_newline=yes_no(info["final_newline"])))
    if info["mixed_eol_lines"]:
        ctx.say(msg("insp_mixed_eol", lines=short_list(info["mixed_eol_lines"])))
    if info["mixed_encoding_lines"]:
        ctx.say(msg("insp_mixed_enc", dominant=info["mixed_dominant"], lines=short_list(info["mixed_encoding_lines"])))
    if info["replacement_chars"]:
        ctx.say(msg("insp_ffd", n=info["replacement_chars"]))
    if info["confidence"] == "ambiguous":
        ctx.say(msg("insp_candidates", candidates=", ".join(info["candidates"])))
    if editorconfig and info["encoding"] and not _charset_matches(editorconfig, info):
        ctx.say(msg("insp_warn_editorconfig", charset=editorconfig, encoding=info["encoding"]))
    if info["mixed_encoding_lines"]:
        ctx.say(msg("insp_next_mixed"))
    elif info["confidence"] == "ambiguous":
        ctx.say(msg("insp_next_ambiguous"))
    elif info["plain"]:
        ctx.say(msg("insp_next_plain"))
    else:
        ctx.say(msg("insp_next_open"))


def _charset_matches(charset: str, info: dict[str, Any]) -> bool:
    enc = info["encoding"]
    if charset in ("utf-8", "utf-8-bom"):
        return enc in ("utf-8", "ascii")
    if charset in ("utf-16le", "utf-16be"):
        return enc.startswith("utf-16")
    return True


TEXT_LINES_MAX = 2000     # inspect --text prints at most this many lines


def show_text(ctx: Ctx, data: bytes, info: dict[str, Any]) -> None:
    """Decoded lines with numbers, so an agent can read a legacy file without opening it."""
    codec = info["encoding"]
    lines = split_lines(data[len(bytes.fromhex(info["bom_hex"])):], codec)
    width = len(str(len(lines)))
    for n, (content, _) in enumerate(lines[:TEXT_LINES_MAX], 1):
        ctx.say(f"{n:>{width}}| " + content.decode(codec, errors="replace"))
    if len(lines) > TEXT_LINES_MAX:
        ctx.say(msg("insp_text_more", n=len(lines) - TEXT_LINES_MAX))


def cmd_inspect(ctx: Ctx, a: argparse.Namespace) -> int:
    files = []
    shas = []
    codecs_seen: list[str] = []
    infos = []
    anchor = None
    for raw in a.paths:
        real, shown = resolve_file(raw)
        anchor = anchor or real
        data = read_file(real, shown)
        info = analyze(data, forced=a.encoding, hint_fn=hints_for(real))
        infos.append((shown, info))
        files.append(file_entry(shown, info))
        shas.append(info["sha256"])
        if info["encoding"] and info["encoding"] not in codecs_seen:
            codecs_seen.append(info["encoding"])
        describe(ctx, shown, info, editorconfig_charset(real))
        if a.text and info["encoding"] and not info["problem"]:
            show_text(ctx, data, info)
    ctx.state = state_dir_for(a.state_dir, anchor) if anchor else None
    ctx.payload["files"] = files
    combined = shas[0] if len(shas) == 1 else sha256_hex("\n".join(shas).encode("ascii"))
    ctx.input_sha = ctx.result_sha = combined
    ctx.codec = ",".join(codecs_seen) or "-"
    if len(infos) == 1:
        shown, info = infos[0]
        ctx.summary_fields = [("path", shown), ("codec", info["encoding"] or "unknown"),
                              ("confidence", info["confidence"]), ("bom", info["bom"]),
                              ("eol", eol_field(info)), ("eol_cr", info["eol"]["cr"]),
                              ("final_newline", "yes" if info["final_newline"] else "no"),
                              ("mixed_encoding_lines", short_list(info["mixed_encoding_lines"]).replace(",", "+")),
                              ("plain", "yes" if info["plain"] else "no")]
        # 쓸 수 없는 파일(깨진 바이트·10 MB 초과 등)은 요약 줄에도 표시한다 — edit/open 이 낼 오류 코드와 같다.
        # 쓸 수 있는 파일은 필드를 붙이지 않아 기존 요약 줄이 그대로다.
        if info["problem"]:
            ctx.summary_fields += [("usable", "no"), ("problem", problem_error(info, shown).code)]
    else:
        ctx.summary_fields = [("files", len(infos)), ("codec", ctx.codec)]
        unusable = sum(1 for _, info in infos if info["problem"])
        if unusable:
            ctx.summary_fields.append(("unusable", unusable))
    return 0


def cmd_scan(ctx: Ctx, a: argparse.Namespace) -> int:
    try:
        root = Path(a.dir).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ERError("E_NOT_FOUND", path=a.dir) from None
    if not root.is_dir():
        raise ERError("E_NOT_FOUND", path=a.dir)
    state = state_dir_for(a.state_dir, root)
    ctx.state = state
    paths = list_files(root, state)[: a.max_files]
    entries: dict[Path, dict[str, Any]] = {}
    skipped = 0
    for p in paths:
        try:
            if p.stat().st_size > MAX_BYTES:
                skipped += 1
                continue
            data = p.read_bytes()
        except OSError:
            skipped += 1
            continue
        if b"\x00" in data and not data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            skipped += 1
            continue
        entries[p] = analyze(data, hint_fn=lambda kind, p=p: gitattributes_codec(p) if kind == "gitattributes" else None)
    per_dir: dict[Path, dict[str, int]] = {}
    for p, info in entries.items():
        if info["confidence"] in ("certain", "likely") and info["encoding"] in CANDIDATES and not info["mixed_encoding_lines"]:
            d = per_dir.setdefault(p.parent, {})
            d[info["encoding"]] = d.get(info["encoding"], 0) + 1
    overall: dict[str, int] = {}
    for counts in per_dir.values():
        for c, n in counts.items():
            overall[c] = overall.get(c, 0) + n
    for p, info in list(entries.items()):
        if info["confidence"] == "ambiguous" and p.parent in per_dir:
            folder = max(per_dir[p.parent], key=lambda c: (per_dir[p.parent][c], -PREFER[c]))
            entries[p] = analyze(p.read_bytes(), hint_fn=lambda kind, folder=folder: folder if kind == "folder" else None)
    counts = {"files": len(entries), "legacy": 0, "bom": 0, "crlf": 0, "mixed_eol": 0, "mixed_enc": 0, "ambiguous": 0,
              "skipped": skipped}
    rows = []
    report_files = []
    for p, info in entries.items():
        legacy = info["encoding"] not in (None, "utf-8", "ascii") and not info["encoding"].startswith("utf-16")
        flags = []
        if legacy:
            counts["legacy"] += 1
        if info["bom"] != "none":
            counts["bom"] += 1
        if info["eol"]["crlf"]:
            counts["crlf"] += 1
        if info["eol_style"] == "mixed":
            counts["mixed_eol"] += 1
            flags.append("mixed-eol")
        if info["mixed_encoding_lines"]:
            counts["mixed_enc"] += 1
            flags.append("mixed-enc")
        if info["confidence"] == "ambiguous":
            counts["ambiguous"] += 1
            flags.append("ambiguous")
        if info["problem"]:
            flags.append(info["problem"])
        notable = legacy or info["bom"] != "none" or info["eol"]["crlf"] or info["eol"]["cr"] or flags
        rel = display_path(p)
        report_files.append(file_entry(rel, info))
        if notable:
            rank = (0 if legacy else 1 if info["mixed_encoding_lines"] or info["confidence"] == "ambiguous" else 2
                    if info["bom"] != "none" else 3 if info["eol_style"] == "mixed" else 4)
            rows.append((rank, rel, [rel, info["encoding"] or "?", info["confidence"],
                                      msg("none") if info["bom"] == "none" else info["bom"], info["eol_style"],
                                      yes_no(info["final_newline"]), ",".join(flags) or msg("dash")]))
    rows.sort(key=lambda r: (r[0], r[1]))
    ctx.say(msg("scan_summary", **counts))
    if overall:
        top = max(overall, key=lambda c: (overall[c], -PREFER[c]))
        ctx.say(msg("scan_majority", codec=top))
    if rows:
        shown_rows = [r[2] for r in rows[:200]]
        for line in render_table([msg("col_path"), msg("col_encoding"), msg("col_confidence"), msg("col_bom"),
                                  msg("col_eol"), msg("col_final"), msg("col_flags")], shown_rows):
            ctx.say(line)
        if len(rows) > 200:
            ctx.say(msg("scan_more", n=len(rows) - 200))
    else:
        ctx.say(msg("scan_none"))
    open_sessions = list_sessions(state)
    if open_sessions:
        ctx.say(msg("scan_open", paths=", ".join(s["display"] for s in open_sessions)))
    ctx.payload.update(files=report_files, summary_counts=counts, majority_codec=(max(overall, key=lambda c: (overall[c], -PREFER[c])) if overall else None),
                       open_sessions=[s["display"] for s in open_sessions])
    digest = sha256_hex("\n".join(f"{f['path']}\0{f['sha256']}" for f in sorted(report_files, key=lambda f: f["path"])).encode("utf-8"))
    ctx.input_sha = ctx.result_sha = digest
    ctx.codec = max(overall, key=lambda c: (overall[c], -PREFER[c])) if overall else "-"
    ctx.summary_fields = [("path", display_path(root)), ("files", counts["files"]), ("legacy", counts["legacy"]),
                          ("bom", counts["bom"]), ("crlf", counts["crlf"]), ("mixed_eol", counts["mixed_eol"]),
                          ("mixed_enc", counts["mixed_enc"]), ("ambiguous", counts["ambiguous"]),
                          ("codec", ctx.codec), ("open_sessions", len(open_sessions))]
    return 0


BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf", ".zip", ".gz", ".tar", ".7z", ".jar",
                   ".class", ".exe", ".dll", ".so", ".dylib", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".mov",
                   ".pyc", ".o", ".a", ".bin", ".lock"}
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", STATE_DIRNAME}


def list_files(root: Path, state: Path) -> list[Path]:
    """Files to scan: `git ls-files` in a git checkout, otherwise a walk that skips VCS, venv and state folders."""
    out: list[Path] = []
    if (root / ".git").exists():
        try:
            r = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                               capture_output=True, timeout=60)
            if r.returncode == 0:
                out = [root / f for f in r.stdout.decode("utf-8", "replace").split("\0") if f]
        except (OSError, subprocess.TimeoutExpired):
            out = []
    if not out:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for f in sorted(filenames):
                out.append(Path(dirpath) / f)
    result = []
    for p in sorted(out):
        if p.suffix.lower() in BINARY_SUFFIXES or p.is_symlink() or not p.is_file():
            continue
        if state in p.parents:
            continue
        result.append(p)
    return result


def list_sessions(state: Path) -> list[dict[str, Any]]:
    sessions = []
    folder = state / "sessions"
    if not folder.is_dir():
        return sessions
    for jp in sorted(folder.glob("*.json")):
        try:
            sessions.append(json.loads(jp.read_bytes().decode("utf-8")))
        except (OSError, ValueError):
            continue
    return sessions


def cmd_open(ctx: Ctx, a: argparse.Namespace) -> int:
    real, shown = resolve_file(a.path)
    state = state_dir_for(a.state_dir, real)
    ctx.state = state
    key = session_key(real)
    ctx.summary_fields = [("path", shown)]
    existing = load_session(state, key)
    if existing and not a.force:
        raise ERError("E_ALREADY_OPEN", path=shown, extra={"path": shown, "codec": existing.get("codec", "-")})
    data = read_file(real, shown)
    info = analyze(data, forced=a.encoding, hint_fn=hints_for(real))
    err = problem_error(info, shown)
    if err:
        raise err
    codec = info["encoding"]
    bom_bytes = bytes.fromhex(info["bom_hex"])
    lines = split_lines(data[len(bom_bytes):], codec)
    texts = [c.decode(codec) for c, _ in lines]
    work_text = "".join(t + ("\n" if e else "") for t, (_, e) in zip(texts, lines))
    work = work_text.encode("utf-8")
    ensure_state(state)
    if existing:   # --force: keep the previous record once, then start from the current bytes
        for suffix in (".json", ".orig"):
            old = state / "sessions" / f"{key}{suffix}"
            if old.exists():
                (state / "stale").mkdir(exist_ok=True)
                os.replace(old, state / "stale" / f"{key}{suffix}")
    jp, bp = session_paths(state, key)
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_bytes(data)
    record = {"version": 1, "key": key, "path": str(real), "display": shown, "codec": codec, "bom_hex": info["bom_hex"],
              "confidence": info["confidence"], "compatible": info["compatible"], "eol": info["eol"],
              "eol_style": info["eol_style"], "final_newline": info["final_newline"], "lines": len(lines),
              "orig_sha256": info["sha256"], "work_sha256": sha256_hex(work)}
    write_json(jp, record)
    atomic_write(real, work)
    ctx.input_sha, ctx.result_sha, ctx.codec = info["sha256"], record["work_sha256"], codec
    ctx.say(msg("open_ok", path=shown))
    ctx.say(msg("open_codec", codec=codec, confidence=info["confidence"],
                bom=msg("none") if info["bom"] == "none" else info["bom"], eol_style=info["eol_style"],
                final_newline=yes_no(info["final_newline"])))
    ctx.payload.update(files=[file_entry(shown, info)], input_sha256=info["sha256"], result_sha256=record["work_sha256"])
    ctx.summary_fields = [("path", shown), ("codec", codec), ("confidence", info["confidence"]),
                          ("bom", info["bom"]), ("eol", eol_field(info)), ("eol_cr", info["eol"]["cr"]),
                          ("final_newline", "yes" if info["final_newline"] else "no")]
    return 0


def load_open_session(ctx: Ctx, a: argparse.Namespace, real: Path, shown: str) -> tuple[dict[str, Any], bytes]:
    """Session record and the saved original bytes; raises E_NOT_OPEN / E_STATE_MISMATCH before anything is written."""
    state = state_dir_for(a.state_dir, real)
    ctx.state = state
    key = session_key(real)
    ctx.summary_fields = [("path", shown)]
    rec = load_session(state, key)
    if rec is None:
        raise ERError("E_NOT_OPEN", path=shown, extra={"path": shown})
    _, bp = session_paths(state, key)
    try:
        orig = bp.read_bytes()
        have_backup = True
    except OSError:
        orig, have_backup = b"", False
    ctx.codec = rec["codec"]
    ctx.input_sha = sha256_hex(orig)
    if sha256_hex(orig) != rec["orig_sha256"] and not getattr(a, "force", False):
        raise ERError("E_STATE_MISMATCH", detail=msg("mm_backup", path=shown), fix=msg("mm_backup_fix"),
                      extra={"path": shown, "codec": rec["codec"], "input_sha256": ctx.input_sha})
    if not have_backup:
        raise ERError("E_STATE_MISMATCH", detail=msg("mm_backup", path=shown), fix=msg("mm_backup_fix"),
                      extra={"path": shown, "codec": rec["codec"]})
    return rec, orig


def unencodable_in(line: str, codec: str) -> list[tuple[int, str, str]]:
    """(1-based column, char, reason) for every character of `line` that `codec` cannot hold safely."""
    bad = []
    for col, ch in enumerate(line, 1):
        try:
            raw = ch.encode(codec)
        except UnicodeEncodeError:
            bad.append((col, ch, "ascii_only" if codec == "ascii" else "unencodable"))
            continue
        if codec == "euc_kr" and len(raw) > 2:
            bad.append((col, ch, "euc_kr_filler"))
        elif raw.decode(codec, errors="replace") != ch:
            bad.append((col, ch, "lossy_roundtrip"))
    return bad


def replace_chars(line: str, bad: list[tuple[int, str, str]], mode: str) -> str:
    cols = {col for col, _, _ in bad}
    out = []
    for col, ch in enumerate(line, 1):
        if col in cols:
            out.append(f"&#{ord(ch)};" if mode == "xmlcharref" else "?")
        else:
            out.append(ch)
    return "".join(out)


def rebuild(orig: bytes, codec: str, bom: bytes, orig_final_newline: bool, work_text: str,
            replace_mode: str, final_policy: str) -> dict[str, Any]:
    """Write `work_text` (UTF-8 text, LF endings) back in the format of `orig`.

    Unchanged lines are copied as the original bytes with their original line ending; new lines get the majority
    line ending. Nothing is written here: the caller gets the bytes, or the list of characters the codec cannot
    hold (`problems`), and decides. Shared by `close` and `edit` so both give byte-identical results.
    """
    orig_lines = split_lines(orig[len(bom):], codec)
    old_texts = [c.decode(codec) for c, _ in orig_lines]     # UnicodeDecodeError: the caller reports it
    new_texts = work_text.split("\n")
    work_final_newline = work_text.endswith("\n")
    if work_text == "" or work_final_newline:
        new_texts.pop()
    ops = diff_ops(old_texts, new_texts)
    maj = majority_eol(orig_lines, codec)
    entries: list[list[Any]] = []        # [content bytes or str, eol bytes or b"", needs_encoding]
    changed = 0
    changed_lines: list[int] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                entries.append([orig_lines[i1 + k][0], orig_lines[i1 + k][1], False])
            continue
        n_old, n_new = i2 - i1, j2 - j1
        for k in range(n_new):
            if k < n_old and old_texts[i1 + k] == new_texts[j1 + k]:     # equal pair inside a large fallback block
                entries.append([orig_lines[i1 + k][0], orig_lines[i1 + k][1], False])
                continue
            eol = orig_lines[i1 + k][1] if k < n_old else b""
            entries.append([new_texts[j1 + k], eol, True])
            changed_lines.append(j1 + k + 1)
            changed += 1
        changed += max(0, n_old - n_new)
    problems = []
    replaced = 0
    changed_iter = iter(changed_lines)
    for ent in entries:
        if not ent[2]:
            continue
        n = next(changed_iter)
        bad = unencodable_in(ent[0], codec)
        if not bad:
            ent[0] = ent[0].encode(codec)
            continue
        if replace_mode == "error":
            problems.extend({"line": n, "col": col, "codepoint": f"U+{ord(ch):04X}", "reason": reason}
                            for col, ch, reason in bad)
            continue
        replaced += len(bad)
        ent[0] = replace_chars(ent[0], bad, replace_mode).encode(codec)
    want_final = {"keep": orig_final_newline if orig_lines else work_final_newline,
                  "add": True, "remove": False}[final_policy]
    result = b""
    if not problems:
        for idx, ent in enumerate(entries):
            last = idx == len(entries) - 1
            if not last:
                ent[1] = ent[1] or maj          # the old last line gets an ending when a line follows it
            elif want_final:
                ent[1] = ent[1] or maj
            else:
                ent[1] = b""
        result = bom + b"".join(ent[0] + ent[1] for ent in entries)
    return {"result": result, "changed": changed, "replaced": replaced, "changed_lines": changed_lines,
            "want_final": want_final, "problems": problems}


def raise_unencodable(ctx: Ctx, built: dict[str, Any], codec: str, extra: dict[str, Any], key: str | None) -> None:
    """E_UNENCODABLE with every `U+XXXX at line:col`; called before anything is written."""
    problems = built["problems"]
    items = ", ".join(f"{p['codepoint']} at {p['line']}:{p['col']}" + (f" ({p['reason']})" if p["reason"] != "unencodable" else "")
                      for p in problems[:20]) + (f", ... (+{len(problems) - 20})" if len(problems) > 20 else "")
    ctx.payload.update(status="error", unencodable=problems, changed_lines=built["changed_lines"])
    extra.update(count=len(problems), first=f"{problems[0]['codepoint']}@{problems[0]['line']}:{problems[0]['col']}")
    raise ERError("E_UNENCODABLE", extra=extra, key=key, count=len(problems), codec=codec, items=items)


def write_result(ctx: Ctx, real: Path, shown: str, rec: dict[str, Any], orig: bytes, built: dict[str, Any],
                 final_policy: str, replace_mode: str, extra: dict[str, Any], ok_key: str, noop_key: str) -> None:
    """Write the rebuilt bytes, re-read them, keep the original as the closed record and fill the summary."""
    codec = rec["codec"]
    bom = bytes.fromhex(rec["bom_hex"])
    result, changed, replaced = built["result"], built["changed"], built["replaced"]
    if final_policy == "keep" and not changed and not replaced and result != orig:
        raise ERError("E_DRIFT", detail="no-op round trip changed bytes", extra=extra)
    if result != real.read_bytes():
        atomic_write(real, result)
    after = real.read_bytes()
    result_sha = sha256_hex(result)
    if after != result or _try_decode(after[len(bom):], codec) is None:
        raise ERError("E_DRIFT", detail="re-read of the written file differs", extra=extra)
    # `verify <file>` later compares against this closed record and the saved original bytes.
    state = ctx.state
    key = rec["key"]
    jp, bp = session_paths(state, key)
    closed_j, closed_b = session_paths(state, key, "closed")
    closed_j.parent.mkdir(parents=True, exist_ok=True)
    rec.update(result_sha256=result_sha, result_final_newline=built["want_final"], closed_changed=changed)
    write_json(closed_j, rec)
    closed_b.write_bytes(orig)
    for p in (jp, bp):
        try:
            p.unlink()
        except OSError:
            pass
    res_lines = split_lines(result[len(bom):], codec)
    res_sum = eol_summary(res_lines, codec if codec.startswith("utf-16") else "")
    ctx.result_sha = result_sha
    if changed or replaced:
        ctx.say(msg(ok_key, path=shown, codec=codec, changed=changed))
    else:
        ctx.say(msg(noop_key, path=shown))
    if replaced:
        ctx.say(msg("close_replaced", n=replaced, mode=replace_mode))
    ctx.payload.update(changed_lines=built["changed_lines"], unencodable=[], input_sha256=ctx.input_sha,
                       result_sha256=result_sha, replaced=replaced,
                       files=[{"path": shown, "encoding": codec, "bom": rec["bom_hex"] and "yes" or "none",
                               "eol": res_sum["eol"], "eol_style": res_sum["eol_style"],
                               "final_newline": res_sum["final_newline"], "sha256": result_sha}])
    ctx.summary_fields = [("path", shown), ("codec", codec), ("bom", "yes" if bom else "none"),
                          ("eol", f"crlf:{res_sum['eol']['crlf']},lf:{res_sum['eol']['lf']}"), ("eol_cr", res_sum["eol"]["cr"]),
                          ("final_newline", "yes" if res_sum["final_newline"] else "no"), ("changed", changed),
                          ("replaced", replaced)]


def cmd_close(ctx: Ctx, a: argparse.Namespace) -> int:
    real, shown = resolve_file(a.path)
    rec, orig = load_open_session(ctx, a, real, shown)
    codec = rec["codec"]
    cur = read_file(real, shown)
    cur_sha = sha256_hex(cur)
    extra = {"path": shown, "codec": codec, "input_sha256": ctx.input_sha, "result_sha256": cur_sha}
    ctx.result_sha = cur_sha
    body_cur = cur[3:] if cur.startswith(codecs.BOM_UTF8) else cur
    try:
        work_text = body_cur.decode("utf-8")
    except UnicodeDecodeError:
        raise ERError("E_STATE_MISMATCH", detail=msg("mm_not_utf8", path=shown), fix=msg("mm_not_utf8_fix", path=shown),
                      extra=extra) from None
    work_text = work_text.replace("\r\n", "\n").replace("\r", "\n")
    bom = bytes.fromhex(rec["bom_hex"])
    try:
        built = rebuild(orig, codec, bom, rec["final_newline"], work_text, a.replace, a.final_newline)
    except UnicodeDecodeError:
        raise ERError("E_STATE_MISMATCH", detail=msg("mm_backup", path=shown), fix=msg("mm_backup_fix"), extra=extra) from None
    if built["problems"]:
        raise_unencodable(ctx, built, codec, extra, None)
    write_result(ctx, real, shown, rec, orig, built, a.final_newline, a.replace, extra, "close_ok", "close_noop")
    return 0


# ---------------------------------------------------------------------------------------------
# edit: open + change + close in one call (nothing on disk changes unless the whole edit fits)
# ---------------------------------------------------------------------------------------------

def _line_arg(spec: str, n_lines: int, allow_zero: bool, with_text: bool) -> tuple[int, str]:
    """'N:TEXT' (or 'N') -> (N, TEXT); E_BAD_EDIT when N is not a line of the file."""
    num, sep, text = spec.partition(":") if with_text else (spec, "", "")
    if not num.strip().isdigit() or (with_text and not sep):
        raise ERError("E_BAD_EDIT", detail=spec)
    n = int(num)
    if n > n_lines or (n == 0 and not allow_zero):
        raise ERError("E_BAD_EDIT", detail=f"{spec} (1..{n_lines})")
    return n, text


def apply_edits(lines: list[str], a: argparse.Namespace) -> list[str]:
    """Apply the edit options to the decoded lines (no line endings).

    Order: --set-line / --delete-line / --insert-after (line numbers of the file before the edit), then each
    --old/--new pair in the order given, then --append-line.
    """
    olds, news = a.old or [], a.new or []
    sets, deletes, inserts, appends = a.set_line or [], a.delete_line or [], a.insert_after or [], a.append_line or []
    if len(olds) != len(news):
        raise ERError("E_BAD_EDIT", detail="--old and --new must come in pairs")
    if not (olds or sets or deletes or inserts or appends):
        raise ERError("E_BAD_EDIT", detail="no change given (--old/--new, --append-line, --insert-after, "
                                           "--set-line or --delete-line)")
    n = len(lines)
    new_text: dict[int, str] = {}
    gone: set[int] = set()
    after: dict[int, list[str]] = {}
    for spec in sets:
        k, text = _line_arg(spec, n, False, True)
        new_text[k] = text
    for spec in deletes:
        k, _ = _line_arg(spec, n, False, False)
        gone.add(k)
    for spec in inserts:
        k, text = _line_arg(spec, n, True, True)
        after.setdefault(k, []).append(text)
    if gone & set(new_text):
        raise ERError("E_BAD_EDIT", detail="--set-line and --delete-line name the same line")
    out = list(after.get(0, []))
    for k, text in enumerate(lines, 1):
        if k not in gone:
            out.append(new_text.get(k, text))
        out.extend(after.get(k, []))
    had_lines = bool(out)
    text = "\n".join(out)
    for old, new in zip(olds, news):
        if old == "":
            raise ERError("E_BAD_EDIT", detail="--old is empty")
        count = text.count(old)
        if count == 0:
            raise ERError("E_NO_MATCH", text=old)
        if count > 1 and not a.all:
            raise ERError("E_MANY_MATCHES", text=old, count=count)
        text = text.replace(old, new)
    out = text.split("\n") if (text or had_lines) else []
    return out + list(appends)


def cmd_edit(ctx: Ctx, a: argparse.Namespace) -> int:
    real, shown = resolve_file(a.path)
    state = state_dir_for(a.state_dir, real)
    ctx.state = state
    key = session_key(real)
    ctx.summary_fields = [("path", shown)]
    if load_session(state, key):     # the file on disk is a UTF-8 working copy right now
        raise ERError("E_ALREADY_OPEN", path=shown, extra={"path": shown})
    data = read_file(real, shown)
    info = analyze(data, forced=a.encoding, hint_fn=hints_for(real))
    err = problem_error(info, shown)
    if err:
        raise err
    codec = info["encoding"]
    ctx.codec, ctx.input_sha, ctx.result_sha = codec, info["sha256"], info["sha256"]
    bom = bytes.fromhex(info["bom_hex"])
    lines = [c.decode(codec) for c, _ in split_lines(data[len(bom):], codec)]
    try:
        new_lines = apply_edits(lines, a)
    except ERError as e:
        e.extra = {"path": shown, **e.extra}
        raise
    # Same working text `open` would hand over, then the same rebuild `close` runs.
    work_text = "\n".join(new_lines) + ("\n" if new_lines and info["final_newline"] else "")
    built = rebuild(data, codec, bom, info["final_newline"], work_text, a.replace, a.final_newline)
    extra = {"path": shown, "codec": codec, "input_sha256": info["sha256"], "result_sha256": info["sha256"]}
    if built["problems"]:
        raise_unencodable(ctx, built, codec, extra, "edit_unencodable")
    ensure_state(state)
    rec = {"version": 1, "key": key, "path": str(real), "display": shown, "codec": codec, "bom_hex": info["bom_hex"],
           "confidence": info["confidence"], "compatible": info["compatible"], "eol": info["eol"],
           "eol_style": info["eol_style"], "final_newline": info["final_newline"],
           "lines": len(lines), "orig_sha256": info["sha256"], "via": "edit"}
    write_result(ctx, real, shown, rec, data, built, a.final_newline, a.replace, extra, "edit_ok", "edit_noop")
    return 0


def cmd_abort(ctx: Ctx, a: argparse.Namespace) -> int:
    real, shown = resolve_file(a.path)
    rec, orig = load_open_session(ctx, a, real, shown)
    cur = read_file(real, shown)
    ctx.result_sha = sha256_hex(orig)
    extra = {"path": shown, "codec": rec["codec"], "input_sha256": ctx.input_sha}
    state = ctx.state
    jp, bp = session_paths(state, rec["key"])
    saved = None
    if cur == orig:
        ctx.say(msg("abort_already", path=shown))
    else:
        if _try_decode(cur, "utf-8") is None and not a.force:
            raise ERError("E_STATE_MISMATCH", detail=msg("mm_other", path=shown), fix=msg("mm_other_fix"), extra=extra)
        aborted = state / "aborted"
        aborted.mkdir(exist_ok=True)
        saved_path = aborted / f"{rec['key']}.work"
        saved_path.write_bytes(cur)
        saved = display_path(saved_path)
        atomic_write(real, orig)
        ctx.say(msg("abort_ok", path=shown))
        ctx.say(msg("abort_saved", saved=saved))
    for p in (jp, bp):
        try:
            p.unlink()
        except OSError:
            pass
    ctx.payload.update(input_sha256=ctx.input_sha, result_sha256=ctx.result_sha, saved_working_copy=saved)
    ctx.summary_fields = [("path", shown), ("codec", rec["codec"]), ("restored", "yes")]
    return 0


def cmd_verify(ctx: Ctx, a: argparse.Namespace) -> int:
    real, shown = resolve_file(a.path)
    ctx.state = state_dir_for(a.state_dir, real)
    cur = read_file(real, shown)
    ctx.result_sha = sha256_hex(cur)
    ctx.summary_fields = [("path", shown)]
    expect_final: bool | None = None
    if a.against:
        oreal, oshown = resolve_file(a.against)
        orig = read_file(oreal, oshown)
        oinfo = analyze(orig, forced=a.encoding, hint_fn=hints_for(oreal))
        err = problem_error(oinfo, oshown)
        if err:
            raise err
        codec, bom_hex = oinfo["encoding"], oinfo["bom_hex"]
    else:
        rec = load_session(ctx.state, session_key(real), "closed")
        if rec is None:
            raise ERError("E_NOT_FOUND", path=shown + " (no closed session; pass --against ORIGINAL)")
        _, bp = session_paths(ctx.state, rec["key"], "closed")
        orig = read_file(bp, display_path(bp))
        codec, bom_hex = rec["codec"], rec["bom_hex"]
        expect_final = rec.get("result_final_newline")
    ctx.codec = codec
    ctx.input_sha = sha256_hex(orig)
    bom = bytes.fromhex(bom_hex)
    problems: list[dict[str, Any]] = []
    if cur[:len(bom)] != bom or (not bom and split_bom(cur)[0]):
        problems.append({"kind": "bom"})
    body = cur[len(bom):] if cur[:len(bom)] == bom else cur
    o_lines = split_lines(orig[len(bom):], codec)
    try:
        c_lines = split_lines(body, codec)
    except UnicodeDecodeError:
        c_lines = []
        problems.append({"kind": "decode", "lines": [1]})
    bad_lines = []
    c_texts = []
    for i, (c, _) in enumerate(c_lines, 1):
        t = _try_decode(c, codec)
        if t is None:
            bad_lines.append(i)
            t = c.decode("latin-1")
        c_texts.append(t)
    if bad_lines:
        problems.append({"kind": "decode", "lines": bad_lines})
        if _try_decode(body, "utf-8") is not None and codec not in ("utf-8", "ascii"):
            problems.append({"kind": "utf8_copy"})
    o_texts = [c.decode(codec) for c, _ in o_lines]
    ops = diff_ops(o_texts, c_texts)
    changed_lines: list[int] = []
    eol_drift: list[int] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                oi, ci = i1 + k, j1 + k
                last_o = oi == len(o_lines) - 1
                last_c = ci == len(c_lines) - 1
                if o_lines[oi][1] != c_lines[ci][1] and not (last_o or last_c):
                    eol_drift.append(ci + 1)
        else:
            changed_lines.extend(range(j1 + 1, j2 + 1))
    if eol_drift:
        problems.append({"kind": "eol", "lines": eol_drift})
    want_final = bool(o_lines) and o_lines[-1][1] != b"" if expect_final is None else expect_final
    if (bool(c_lines) and c_lines[-1][1] != b"") != want_final and (c_lines or o_lines):
        problems.append({"kind": "final_newline"})
    o_ffd = sum(t.count("�") for t in o_texts)
    c_ffd = sum(t.count("�") for t in c_texts)
    if c_ffd > o_ffd:
        problems.append({"kind": "ffd", "before": o_ffd, "after": c_ffd})
    if codec not in ("utf-8", "ascii") and not codec.startswith("utf-16"):
        cu, co = find_mixed(c_lines)
        ou, oo = find_mixed(o_lines)
        if cu and co and not (ou and oo):
            problems.append({"kind": "mixed", "lines": cu if len(cu) <= len(co) else co})
    ctx.payload.update(codec=codec, changed_lines=changed_lines, problems=problems, input_sha256=ctx.input_sha,
                       result_sha256=ctx.result_sha)
    if not problems:
        ctx.say(msg("verify_ok", path=shown, codec=codec, lines=short_list(changed_lines)))
        ctx.summary_fields = [("path", shown), ("codec", codec), ("changed", len(changed_lines)), ("drift", 0)]
        return 0
    ctx.say(msg("verify_fail", path=shown))
    has_mixed = any(p["kind"] == "mixed" for p in problems)
    for p in problems:
        k = p["kind"]
        if k == "decode":
            ctx.say(msg("verify_item_decode", codec=codec, lines=short_list(p["lines"])))
        elif k == "utf8_copy":
            ctx.say(msg("verify_item_utf8"))
        elif k == "bom":
            ctx.say(msg("verify_item_bom"))
        elif k == "eol":
            ctx.say(msg("verify_item_eol", lines=short_list(p["lines"])))
        elif k == "final_newline":
            ctx.say(msg("verify_item_final"))
        elif k == "ffd":
            ctx.say(msg("verify_item_ffd", before=p["before"], after=p["after"]))
        elif k == "mixed":
            ctx.say(msg("verify_item_mixed", lines=short_list(p["lines"])))
    kinds = ",".join(sorted({p["kind"] for p in problems}))
    code = "E_MIXED_ENCODING" if has_mixed else "E_DRIFT"
    mixed_lines = next((p["lines"] for p in problems if p["kind"] == "mixed"), [])
    raise ERError(code, exit_code=1, key="verify_mixed_err" if has_mixed else None,
                  extra={"path": shown, "codec": codec, "input_sha256": ctx.input_sha,
                         "result_sha256": ctx.result_sha, "drift": len(problems), "kinds": kinds},
                  path=shown, lines=short_list(mixed_lines), detail=codec if has_mixed else kinds)


def cmd_status(ctx: Ctx, a: argparse.Namespace) -> int:
    state = state_dir_for(a.state_dir, Path.cwd())
    ctx.state = state
    sessions = list_sessions(state)
    rows = []
    for s in sessions:
        try:
            cur = Path(s["path"]).read_bytes()
            sha = sha256_hex(cur)
            st = "state_unchanged" if sha == s["work_sha256"] else ("state_edited" if _try_decode(cur, "utf-8") is not None
                                                                     else "state_other")
        except OSError:
            st = "state_missing"
        rows.append({"path": s["display"], "codec": s["codec"], "state": st})
        ctx.say(msg("status_row", path=s["display"], codec=s["codec"], state=msg(st)))
    if not rows:
        ctx.say(msg("status_none"))
    ctx.payload["open"] = rows
    digest = sha256_hex("\n".join(s["key"] for s in sessions).encode("ascii"))
    ctx.input_sha = ctx.result_sha = digest
    ctx.summary_fields = [("open", len(rows))]
    return 0


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

class LocalizedHelpFormatter(argparse.HelpFormatter):
    """argparse prints 'usage: ' itself; take the prefix from the message table instead."""

    def add_usage(self, usage, actions, groups, prefix=None):  # type: ignore[no-untyped-def]
        if prefix is None:
            prefix = msg("help_usage")
        super().add_usage(usage, actions, groups, prefix)


def localize_titles(parser: argparse.ArgumentParser) -> None:
    # argparse 가 직접 찍는 절 제목("positional arguments", "options")도 고른 언어로 바꾼다.
    parser._positionals.title = msg("help_positionals")
    parser._optionals.title = msg("help_options")


def build_parser(lang: str) -> argparse.ArgumentParser:
    global CURRENT_LANG
    CURRENT_LANG = lang
    # -h 는 argparse 기본(영어 설명) 대신 직접 등록해 도움말 설명도 번역한다.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help=msg("help_help"))
    common.add_argument("--lang", choices=["en", "ko"], default=argparse.SUPPRESS, help=msg("help_lang"))
    common.add_argument("--state-dir", default=argparse.SUPPRESS, metavar="DIR", help=msg("help_state_dir"))
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=msg("help_json"))
    kw: dict[str, Any] = {"parents": [common], "add_help": False, "formatter_class": LocalizedHelpFormatter}
    p = argparse.ArgumentParser(prog="roundtrip.py", description=msg("help_description"), epilog=msg("help_epilog"),
                                add_help=False, formatter_class=LocalizedHelpFormatter)
    p.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help=msg("help_help"))
    p.add_argument("--version", action="version", version=f"{TOOL} {VERSION}", help=msg("help_version"))
    p.add_argument("--lang", choices=["en", "ko"], default=None, help=msg("help_lang"))
    p.add_argument("--state-dir", default=None, metavar="DIR", help=msg("help_state_dir"))
    p.add_argument("--json", action="store_true", help=msg("help_json"))
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{inspect,scan,edit,open,close,abort,verify,status}")
    s = sub.add_parser("inspect", **kw, help=msg("help_inspect"), description=msg("help_inspect"))
    s.add_argument("paths", nargs="+", metavar="PATH", help=msg("help_paths"))
    s.add_argument("--encoding", metavar="ENC", help=msg("help_encoding"))
    s.add_argument("--text", action="store_true", help=msg("help_text"))
    s = sub.add_parser("edit", **kw, help=msg("help_edit"), description=msg("help_edit"))
    s.add_argument("path", metavar="PATH", help=msg("help_path"))
    s.add_argument("--old", action="append", metavar="TEXT", help=msg("help_old"))
    s.add_argument("--new", action="append", metavar="TEXT", help=msg("help_new"))
    s.add_argument("--all", action="store_true", help=msg("help_all"))
    s.add_argument("--append-line", action="append", metavar="TEXT", help=msg("help_append_line"))
    s.add_argument("--insert-after", action="append", metavar="N:TEXT", help=msg("help_insert_after"))
    s.add_argument("--set-line", action="append", metavar="N:TEXT", help=msg("help_set_line"))
    s.add_argument("--delete-line", action="append", metavar="N", help=msg("help_delete_line"))
    s.add_argument("--encoding", metavar="ENC", help=msg("help_encoding"))
    s.add_argument("--replace", choices=["error", "xmlcharref", "question"], default="error", help=msg("help_replace"))
    s.add_argument("--final-newline", choices=["keep", "add", "remove"], default="keep", help=msg("help_final_newline"))
    s = sub.add_parser("scan", **kw, help=msg("help_scan"), description=msg("help_scan"))
    s.add_argument("dir", metavar="DIR", help=msg("help_dir"))
    s.add_argument("--max-files", type=int, default=2000, metavar="N", help=msg("help_max_files"))
    s = sub.add_parser("open", **kw, help=msg("help_open"), description=msg("help_open"))
    s.add_argument("path", metavar="PATH", help=msg("help_path"))
    s.add_argument("--encoding", metavar="ENC", help=msg("help_encoding"))
    s.add_argument("--force", action="store_true", help=msg("help_force"))
    s = sub.add_parser("close", **kw, help=msg("help_close"), description=msg("help_close"))
    s.add_argument("path", metavar="PATH", help=msg("help_path"))
    s.add_argument("--replace", choices=["error", "xmlcharref", "question"], default="error", help=msg("help_replace"))
    s.add_argument("--final-newline", choices=["keep", "add", "remove"], default="keep", help=msg("help_final_newline"))
    s.add_argument("--force", action="store_true", help=msg("help_force"))
    s = sub.add_parser("abort", **kw, help=msg("help_abort"), description=msg("help_abort"))
    s.add_argument("path", metavar="PATH", help=msg("help_path"))
    s.add_argument("--force", action="store_true", help=msg("help_force"))
    s = sub.add_parser("verify", **kw, help=msg("help_verify"), description=msg("help_verify"))
    s.add_argument("path", metavar="PATH", help=msg("help_path"))
    s.add_argument("--against", metavar="ORIG", help=msg("help_against"))
    s.add_argument("--encoding", metavar="ENC", help=msg("help_encoding"))
    sub.add_parser("status", **kw, help=msg("help_status"), description=msg("help_status"))
    for parser in [p, *sub.choices.values()]:
        localize_titles(parser)
    return p


def pre_scan_lang(argv: list[str]) -> str | None:
    """Find --lang before argparse runs so that --help is already localized."""
    for i, tok in enumerate(argv):
        if tok == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith("--lang="):
            return tok.split("=", 1)[1]
    return None


COMMANDS: dict[str, Callable[[Ctx, argparse.Namespace], int]] = {
    "inspect": cmd_inspect, "scan": cmd_scan, "edit": cmd_edit, "open": cmd_open, "close": cmd_close, "abort": cmd_abort,
    "verify": cmd_verify, "status": cmd_status}


def emit(ctx: Ctx, status: str, as_json: bool) -> None:
    summary = ctx.summary(status)
    ctx.payload["summary"] = summary
    if as_json:
        print(json.dumps(ctx.payload, ensure_ascii=False, indent=2))
        return
    for line in ctx.lines:
        print(line)
    print(summary)


def write_report(ctx: Ctx, status: str) -> None:
    """report.json in the state folder; a failed write never hides the command result."""
    if ctx.state is None or ctx.cmd == "status":
        return
    ctx.payload["status"] = status
    ctx.payload.setdefault("input_sha256", ctx.input_sha)
    ctx.payload.setdefault("result_sha256", ctx.result_sha)
    ctx.payload["receipt"] = ctx.receipt(status)
    ordered = {k: ctx.payload[k] for k in ("tool", "version", "cmd", "status") if k in ctx.payload}
    ordered.update({k: v for k, v in ctx.payload.items() if k not in ordered})
    try:
        ensure_state(ctx.state)
        write_json(ctx.state / "report.json", ordered)
    except (OSError, ERError):
        pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    lang = resolve_lang(pre_scan_lang(argv))
    parser = build_parser(lang)
    args = parser.parse_args(argv)
    for attr, default in (("lang", None), ("state_dir", None), ("json", False)):
        if not hasattr(args, attr):
            setattr(args, attr, default)
    lang = resolve_lang(args.lang)
    build_parser(lang)     # sets CURRENT_LANG for the messages below
    ctx = Ctx(args.cmd, args)
    try:
        code = COMMANDS[args.cmd](ctx, args)
        status = "ok" if code == 0 else "fail"
        if code == 0 and args.cmd in ("inspect", "scan") and ctx.payload.get("files"):
            pass
        write_report(ctx, status)
        emit(ctx, status, args.json)
        return code
    except ERError as e:
        ctx.payload.update(status="error", error=e.code, message=e.text())
        ctx.error_code = e.code
        for k, v in e.extra.items():
            if k == "input_sha256":
                ctx.input_sha = v
            elif k == "result_sha256":
                ctx.result_sha = v
            elif k == "codec":
                ctx.codec = v
        ctx.summary_fields = [("code", e.code)] + [(k, v) for k, v in e.extra.items()
                                                  if k not in ("input_sha256", "result_sha256", "codec")]
        if ctx.codec != "-":
            ctx.summary_fields.insert(1, ("codec", ctx.codec))
        print(f"{TOOL}: error {e.code}: {e.text()}", file=sys.stderr)
        write_report(ctx, "error")
        emit(ctx, "error", args.json)
        return e.exit_code
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
