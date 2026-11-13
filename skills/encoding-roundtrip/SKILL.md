---
name: encoding-roundtrip
description: Edits text files that are not plain UTF-8 with LF (CP949, EUC-KR, Shift_JIS, GBK, Windows-1250/1251/1252, UTF-8 with BOM, CRLF or mixed line endings) byte-exactly. A bundled script applies a find/replace or line change in one call and writes the file back in its original encoding, BOM, per-line line endings and final newline, copies untouched lines as the original bytes, refuses characters the encoding cannot hold with the exact line, column and code point, and reports which files or lines use another encoding. Use when the user asks to change, add to or inspect a legacy-encoded, CRLF or BOM source, config or text file, when an edit is refused as not valid UTF-8, when a file shows replacement characters or garbled Korean, Japanese, Chinese or accented text, when a diff suddenly covers a whole file, or to list which files in a folder or repository are not plain UTF-8 or use CRLF or a BOM. Not for converting files to UTF-8 permanently or normalizing line endings.
license: MIT
compatibility: Python 3.10+ (standard library only). No network access.
---

# Edit legacy-encoded files without changing their bytes

Agents read and write UTF-8 with LF. On a CP949, Shift_JIS, BOM or CRLF file that corrupts text or rewrites every
line. The bundled script does the byte work. `scripts/roundtrip.py` below is relative to this skill's folder: use
its full path and run it from the user's folder with the file path as the user gave it. `--lang ko` or `--lang en`
picks the message language.

## Rules

- Only the script writes such a file. Never change it with Edit, Write, MultiEdit, `sed -i`, `perl -i`, `iconv`,
  `unix2dos` or `dos2unix`, and do not Read or `cat` it to see the text (that shows garbled bytes).
- Stop early: one `edit` call normally finishes the task. After it succeeds do not run `verify`, `status`,
  `inspect` or `git diff`, and do not explore other files. If the named file or folder does not exist, say so and stop.
- End your answer with the `ER-SUMMARY` line printed by the command that wrote the file or refused to, copied
  exactly as one line, then one or two plain sentences.

## Change a file: one command

```
python3 scripts/roundtrip.py edit FILE --old 'exact old text' --new 'new text'
python3 scripts/roundtrip.py edit FILE --append-line 'new last line'
python3 scripts/roundtrip.py edit FILE --set-line '12:the whole new text of line 12'
python3 scripts/roundtrip.py edit FILE --insert-after '3:a new line after line 3'
python3 scripts/roundtrip.py edit FILE --delete-line 7
```

- `edit` detects the encoding, applies the change and writes the file once. BOM, the line ending of every unchanged
  line and the final newline stay as they were; new lines get the file's usual ending.
- `--old` must match exactly once (add `--all` to change every match). Options may repeat and combine. Write
  `--old='-text'` when the text starts with `-`. `--insert-after '0:...'` inserts before line 1.
- Need to see the text first (a line number, a typo): run `inspect --text FILE` once, then `edit`.
- Many scattered changes: `open FILE` (the file becomes a UTF-8/LF working copy), change it with your normal tools,
  then `close FILE`, and paste the `close` summary line. A file left open stays UTF-8: finish with `close` or `abort FILE`.

## Only look, do not change

- One file ("which encoding", "which lines are garbled", "do not modify"): run `inspect FILE` once. Name the
  encoding and the line numbers in `mixed_encoding_lines` (joined by `+`), then stop.
- A folder or the repository ("which files are not UTF-8, have CRLF or a BOM"): run `scan FOLDER` once (`scan .`
  when no folder is named or the named one does not exist). Answer from its table; do not inspect files one by one.

## When the script says no

A non-zero exit means nothing was written. Show the error line and the `ER-SUMMARY` line as printed, then:

- `E_UNENCODABLE`: list every `U+XXXX at line:column` exactly as printed and propose replacements (`-` for an em
  dash, `[OK]` for a check mark). Do not remove, replace or escape the character yourself; use
  `--replace xmlcharref` or `--replace question` only after the user agrees. Reason `euc_kr_filler` in a file the
  user calls CP949: run the same command with `--encoding cp949`. Reason `ascii_only`: ask which encoding to use.
- `E_AMBIGUOUS`: do not guess; ask for the encoding, then repeat the command with `--encoding NAME`.
- `E_MIXED_ENCODING`: list the lines from the output and ask how to proceed.
- `E_NO_MATCH`, `E_MANY_MATCHES`: run `inspect --text FILE` once and retry with the exact or a longer `--old`.
- `E_BINARY`, `E_UNSUPPORTED`: do not edit the file as text.
- `E_ALREADY_OPEN`, `E_NOT_OPEN`, `E_STATE_MISMATCH`: a session from `open` is involved; follow the message. Use
  `--force` only when the message offers it and the user agrees.

`verify FILE` compares a result with the original later if the user asks. Codec pitfalls (CP949 versus EUC-KR,
Shift_JIS versus CP932, Windows-125x) are in references/encodings.md.
