# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.2] - 2026-11-13

### Added

- `edit` subcommand: one call finds and replaces text (`--old`/`--new`, `--all`) or changes lines (`--append-line`,
  `--insert-after N:TEXT`, `--set-line N:TEXT`, `--delete-line N`) and writes the file back in its original
  encoding, BOM, per-line line endings and final newline. It gives the same bytes as `open` -> edit -> `close`, and a
  refused change (`E_UNENCODABLE`, `E_NO_MATCH`, `E_MANY_MATCHES`, `E_BAD_EDIT`, `E_AMBIGUOUS`, ...) leaves the
  file untouched, so no `abort` is needed.
- `inspect --text` prints the decoded lines with line numbers (read only, at most 2000 lines).

### Known issues

- `inspect` and `scan` do not change the target file, but they create a `.encoding-roundtrip/` state folder with
  `report.json` (next to the file when there is no git root). In a copy without git, run `uv run pytest -q` before
  trying `inspect` on the sample files, or delete that folder first.

### Changed

- `SKILL.md` is built around `edit`: never change such a file with the agent's own edit tools, stop after one
  successful `edit`, and paste the `ER-SUMMARY` line of the command that wrote the file. The 0.1.1 text asked for a
  final `status` call and then for "the last line of the final command", so agents pasted the `status` line instead
  of the `close` line.
- `close` and `edit` share one rebuild routine.
- `inspect` adds `usable=no problem=<code>` to the `ER-SUMMARY` line when the file cannot be edited (invalid bytes,
  larger than 10 MB, unsupported encoding); `<code>` is the error `edit` and `open` would raise. With several files it
  adds `unusable=<count>`. Lines for usable files are unchanged.
- The receipt of a failed or refused command also covers its `status` and error `code`, so two different refusals
  on the same file no longer share a receipt. Receipts of successful commands are unchanged.
- `--help` in Korean no longer mixes in argparse's English words (`usage:`, section titles, `-h` description).

## [0.1.1] - 2026-11-13

### Changed

- `SKILL.md` is shorter and names the first command to run: `open` to edit, `inspect` alone for read-only
  questions (the garbled-line diagnosis ends after one call), `scan` for a folder.
- On `E_UNENCODABLE` the skill now reports the `line:column:U+XXXX` list from the `close` summary line as
  printed, refuses to write the file any other way, and restores it with `abort`.

## [0.1.0] - 2026-11-13

### Added

- `roundtrip.py` with the subcommands `inspect`, `scan`, `open`, `close`, `abort`, `verify` and `status`
  (standard library only, Python 3.10+).
- Encoding detection for CP949, EUC-KR, Shift_JIS, CP932, GBK, GB18030, Big5 and Windows-1250/1251/1252, with
  BOM, strict UTF-8, `.gitattributes` and sibling-file hints; ambiguous files are refused instead of guessed.
- Byte-exact round trip: per-line line endings, final newline and BOM are preserved; unchanged lines are copied as
  the original bytes.
- Pre-write refusal of characters the target encoding cannot hold (exact line, column and code point), including
  the filler sequences of Python's `euc_kr` codec; `--replace xmlcharref|question` as an explicit opt-in.
- State checks before every write (`E_NOT_OPEN`, `E_ALREADY_OPEN`, `E_STATE_MISMATCH`); `--force` only where the
  message allows it.
- `ER-SUMMARY` last line with a receipt hash, and `report.json` in the state folder.
- English and Korean messages (`--lang`, `ENCODING_ROUNDTRIP_LANG`, `LC_ALL`/`LC_MESSAGES`/`LANG`).
- Agent skill (`SKILL.md`), Claude Code plugin manifests, evaluation set and sample files.
