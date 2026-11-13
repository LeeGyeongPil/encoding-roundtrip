---
name: task-sjis-mixed-eol-add-line
max_turns: 14
---

`inc/const.h` is a Shift_JIS header with mixed line endings. Add the line `#define MAX_RETRY 3` after the last line. Every existing line must keep its bytes and its line ending, and the file must keep its encoding. If any tool you use prints a receipt or summary line for the change, paste that line verbatim at the end of your answer.
