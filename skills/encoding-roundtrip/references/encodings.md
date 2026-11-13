# Encoding pitfalls

Short notes for the cases where `roundtrip.py` stops or reports something unexpected.

## Contents

- CP949 versus EUC-KR
- Shift_JIS versus CP932
- GBK versus GB18030
- Windows-1250, 1251 and 1252
- BOM and UTF-16
- Line endings and the final newline
- Why detection says ambiguous

## CP949 versus EUC-KR

CP949 is a superset of EUC-KR. Text that uses only the 2,350 syllables of KS X 1001 has the same bytes in both, so the script reports the narrower `euc_kr` and lists `cp949` under `compatible`. A syllable outside KS X 1001 (for example U+B620) cannot be written to an `euc_kr` file. Python's `euc_kr` codec does not raise an error for it: it silently writes an 8-byte filler sequence that other tools show as four jamo. The script refuses these characters with reason `euc_kr_filler`. If the user confirms the file is really CP949, run the same `edit` again with `--encoding cp949` (after `open`, run `abort`, then `open --encoding cp949`).

## Shift_JIS versus CP932

CP932 (Windows code page 932) adds NEC and IBM extensions and maps a few symbols differently (for example the wave dash and the full-width tilde). For Japanese text that avoids those symbols the bytes are identical; the script reports `shift_jis` with `cp932` as compatible. Unchanged lines are copied as the original bytes, so existing symbols are never remapped. New characters are checked against the reported codec, and a character that would not decode back to itself is refused (reason `lossy_roundtrip`).

## GBK versus GB18030

GB18030 is a superset of GBK. GBK text is reported as `gbk` with `gb18030` as compatible. Short Chinese, Japanese and Korean files can decode as each other, which is why short files are reported as ambiguous.

## Windows-1250, 1251 and 1252

These single-byte encodings accept almost every byte, so a file that is valid in one is usually valid in the others and only the letters differ. The script scores letters in context (accented letters next to ASCII letters, Cyrillic words, Central European letters). When the text is the same in several of them, or too short to tell, the result is ambiguous and `edit` and `open` refuse until you pass `--encoding`. Windows-1253 to 1258 are accepted through `--encoding` only.

## BOM and UTF-16

A UTF-8 BOM (EF BB BF) and UTF-16 LE/BE BOMs are detected first and written back unchanged. UTF-16 without a BOM contains NUL bytes and is treated as binary. UTF-32, ISO-2022-JP and other stateful encodings are reported as unsupported.

## Line endings and the final newline

The working copy always uses LF. On close (and in `edit`), each unchanged line gets its original ending back; a line that replaces an original line keeps that line's ending; new lines get the most common ending of the file (the first one on a tie). The final-newline state of the original is kept unless you pass `--final-newline add` or `--final-newline remove`.

## Why detection says ambiguous

The script prefers a certain answer (BOM, valid UTF-8, a `working-tree-encoding` entry in `.gitattributes`) and otherwise scores the text of every codec that decodes it. With only a handful of non-ASCII characters several codecs fit equally well, for example two Korean syllables are also valid GBK and Big5. A sibling file in the same folder with a clear encoding is used as a tie-breaker; otherwise the file is not opened.
