"""The evaluation set: composition, grader format and pinned values that must match the real script output."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import make_evals  # noqa: E402
import make_fixtures  # noqa: E402

EVALS = ROOT / "evals"
NAME = "encoding-roundtrip"


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_bytes().decode("utf-8")
    block = text.split("---\n", 2)[1]
    out = {}
    for line in block.strip().split("\n"):
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("'") and v.endswith("'"):
            v = v[1:-1]
        out[k.strip()] = v
    return out


def cases(prefix: str) -> list[Path]:
    return sorted(d for d in EVALS.iterdir() if d.is_dir() and d.name.startswith(prefix))


def test_eval_set_composition():
    trigger = cases("trigger-")
    near = cases("notrigger-near-")
    far = [d for d in cases("notrigger-") if not d.name.startswith("notrigger-near-")]
    tasks = cases("task-")
    errors = [d for d in tasks if d.name.startswith("task-error-")]
    assert len(trigger) >= 10 and len(near) >= 5 and len(near) + len(far) >= 10
    assert len(tasks) >= 5 and len(errors) >= 1
    assert (len(trigger), len(near), len(far), len(tasks), len(errors)) == (12, 6, 5, 6, 2)
    korean = [d for d in trigger if re.search("[가-힣]", (d / "prompt.md").read_bytes().decode("utf-8"))]
    assert len(korean) >= 2
    for d in trigger:
        body = (d / "prompt.md").read_bytes().decode("utf-8").split("---\n", 2)[2]
        assert not re.search(rf"\b{NAME}\b|\bskill\b|스킬", body, re.I), d.name
        g = frontmatter(d / "graders" / "skill-fired.md")
        assert g["tool"] == "Skill" and g["arm"] == "both" and NAME in g["input_match"]
    for d in near + far:
        g = frontmatter(d / "graders" / "skill-not-fired.md")
        assert (g["tool"], g["max"], g["arm"]) == ("Skill", "0", "both")
    for d in tasks:
        fixture = EVALS / "fixtures" / d.name
        assert fixture.is_dir() and any(fixture.rglob("*")), d.name
        ran = frontmatter(d / "graders" / "script-ran.md")
        assert ran["tool"] == "Bash" and ran["arm"] == "with-only" and "scripts/roundtrip" in ran["input_match"]
        regexes = [frontmatter(g) for g in (d / "graders").glob("*.md") if g.name != "script-ran.md"]
        assert regexes and all(r["type"] == "regex" for r in regexes)
        assert any(len(r["pattern"]) > 30 for r in regexes)


def test_regexes_are_javascript_compatible():
    for g in EVALS.glob("*/graders/*.md"):
        fm = frontmatter(g)
        pat = fm.get("pattern") or fm.get("input_match") or ""
        assert not re.search(r"\(\?P[<=]|\\A|\\Z|\(\?#", pat), g
        assert not re.search(r"\\\\[sSdDwWbB.()\[\]]", pat), g
        re.compile(pat)


def test_task_graders_pin_values_only_the_script_prints():
    for d in cases("task-"):
        patterns = [frontmatter(g)["pattern"] for g in (d / "graders").glob("*.md") if g.name != "script-ran.md"]
        joined = " ".join(patterns)
        if d.name == "task-error-euckr-unencodable":
            assert "E_UNENCODABLE" in joined and "U\\+2014@1:7" in joined
        elif d.name == "task-error-ambiguous-short":
            assert "E_AMBIGUOUS" in joined
        else:
            assert re.search(r"[0-9a-f]{64}", joined), d.name
            assert re.search(r"receipt=(?:\(\?:)?[0-9a-f]{16}(?:\|[0-9a-f]{16}\))?", joined), d.name


def test_pinned_graders_match_a_real_run(monkeypatch):
    """Rebuild every eval file from a live run of the script; the files on disk must be identical."""
    monkeypatch.setenv("LANG", "C")
    for rel, text in make_evals.build().items():
        assert (ROOT / rel).read_bytes() == text.encode("utf-8"), f"{rel} is stale: run python3 tools/make_evals.py"


def test_simulated_task_results_satisfy_their_graders(monkeypatch):
    monkeypatch.setenv("LANG", "C")
    for task in make_fixtures.TASKS:
        d = EVALS / task
        for path in make_fixtures.PATHS:       # one-call edit and open -> working copy -> close
            s = make_fixtures.simulate(task, path)
            target = " ".join(f"{k}={v}" for k, v in s.items()
                              if k not in ("open", "exit", "stderr", "result_bytes"))
            line = "ER-SUMMARY " + target
            for g in (d / "graders").glob("*.md"):
                if g.name == "script-ran.md":
                    continue
                pat = frontmatter(g)["pattern"]
                if g.name == "error-code":
                    assert re.search(pat, s.get("stderr", "") + line), (task, path)
                else:
                    assert re.search(pat, line), (task, path, g.name, pat, line)


def test_edit_and_open_close_write_identical_bytes(monkeypatch):
    """Both ways an agent can change an eval fixture end in the same file bytes."""
    monkeypatch.setenv("LANG", "C")
    for task in make_fixtures.TASKS:
        a = make_fixtures.simulate(task, "edit")
        b = make_fixtures.simulate(task, "open-close")
        assert a["result_bytes"] == b["result_bytes"], task
        assert a["exit"] == b["exit"], task
        if a["exit"] != "0":            # a refused change leaves the fixture byte for byte as it was
            rel = make_fixtures.TASKS[task][0]
            assert bytes.fromhex(a["result_bytes"]) == make_fixtures.FILES[f"evals/fixtures/{task}/{rel}"], task


def test_script_does_not_know_the_eval_cases():
    """The skill must solve the cases from its inputs: no case names, fixture paths or fixture texts in the skill."""
    skill = ROOT / "skills" / NAME
    sources = "\n".join(p.read_bytes().decode("utf-8") for p in skill.rglob("*") if p.is_file()
                        and p.suffix in (".py", ".md"))
    needles = {d.name for d in EVALS.iterdir() if d.is_dir() and d.name != "fixtures"}
    for rel, _kind, arg in make_fixtures.TASKS.values():
        needles.add(rel)
        needles.add(Path(rel).name)
        if isinstance(arg, tuple):
            needles.update(x.strip() for x in arg if len(x.strip()) > 3)
        elif isinstance(arg, str):
            needles.add(arg)
    for needle in sorted(needles):
        assert needle not in sources, needle
    for rel, data in make_fixtures.FILES.items():
        if rel.startswith("evals/fixtures/"):
            assert sha256_of(data) not in sources, rel


def sha256_of(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def test_fixtures_match_expected_bytes():
    for rel, data in make_fixtures.FILES.items():
        assert (ROOT / rel).read_bytes() == data, rel


def test_fixtures_have_no_hidden_files_or_state_folders():
    for f in (EVALS / "fixtures").rglob("*"):
        assert not any(part.startswith(".") for part in f.relative_to(EVALS / "fixtures").parts), f
    assert not list((ROOT / "examples").glob(".*"))


def test_task_prompts_name_exact_edits():
    prompts = {d.name: (d / "prompt.md").read_bytes().decode("utf-8") for d in cases("task-")}
    assert "저장 실패" in prompts["task-cp949-edit-string"] and "저장 중 오류" in prompts["task-cp949-edit-string"]
    assert "#define MAX_RETRY 3" in prompts["task-sjis-mixed-eol-add-line"]
    assert '"1.0.1"' in prompts["task-utf8-bom-crlf-edit"]


def test_tool_scripts_have_help():
    for tool in ("make_fixtures.py", "make_evals.py"):
        r = subprocess.run([sys.executable, str(TOOLS / tool), "--help"], capture_output=True)
        assert r.returncode == 0


def test_skill_folder_layout():
    skill = ROOT / "skills" / NAME
    text = (skill / "SKILL.md").read_bytes().decode("utf-8")
    fm = text.split("---\n", 2)[1]
    keys = {line.split(":", 1)[0] for line in fm.strip().split("\n")}
    assert keys == {"name", "description", "license", "compatibility"}
    assert len(text.split("\n")) < 500
    assert "scripts/roundtrip.py" in text and "references/encodings.md" in text
    assert "receipt" not in text.split("---\n", 2)[2].lower().replace("receipt or summary", "")
    for forbidden in (".claude", "CLAUDE.md", "AGENTS.md", ".mcp.json", "hooks"):
        assert not (ROOT / forbidden).exists(), forbidden


def test_skill_instructions_lead_with_one_call_edit():
    """Regression for the 0.1.1 eval: agents pasted the `status` line and edited files with general tools."""
    body = (ROOT / "skills" / NAME / "SKILL.md").read_bytes().decode("utf-8").split("---\n", 2)[2]
    first_command = re.search(r"scripts/roundtrip\.py (\w+)", body)
    assert first_command and first_command.group(1) == "edit"
    assert "Stop early" in body
    assert re.search(r"Never change it with Edit, Write", body)
    assert "printed by the command that wrote the file" in body
    assert not re.search(r"`status` must", body)           # no mandatory status call that ends the transcript
    assert len(body.split()) < 900                          # short enough to act on within a few turns
    for flag in ("--old", "--new", "--append-line", "--set-line", "--insert-after", "--delete-line", "--all"):
        assert flag in body, flag
    help_text = subprocess.run([sys.executable, str(ROOT / "skills" / NAME / "scripts" / "roundtrip.py"),
                                "--lang", "en", "edit", "--help"], capture_output=True).stdout.decode("utf-8")
    for flag in ("--old", "--new", "--append-line", "--set-line", "--insert-after", "--delete-line", "--all"):
        assert flag in help_text, flag
