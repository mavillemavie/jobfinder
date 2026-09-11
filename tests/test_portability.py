"""Windows portability guards. The suite runs on windows-latest in CI; these catch the two
classic regressions before they get there."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from jobfinder import paths
from jobfinder.llm import claude_code

SRC = paths.repo_root() / "src" / "jobfinder"
# Text-mode file access that would default to cp1252 on Windows (French accents break).
PATTERN = re.compile(
    r"""(?:\.open\(\s*\)|\.open\(\s*["'][rwa]["']\s*\)|(?<![\w.])open\([^)]*\)"""
    r"""|\.read_text\(\s*\)|\.write_text\((?:[^()]|\([^()]*\))*\))"""
)


def test_every_text_file_access_names_utf8() -> None:
    offenders = []
    for py in SRC.rglob("*.py"):
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            m = PATTERN.search(line)
            if m and "encoding=" not in line and not re.search(r"""["'][rwa]b["']""", line):
                offenders.append(f"{py.relative_to(paths.repo_root())}:{n}: {line.strip()}")
    assert not offenders, "text I/O without encoding='utf-8':\n" + "\n".join(offenders)


def test_claude_binary_is_resolved_through_path(monkeypatch, tmp_path: Path) -> None:
    """npm installs claude.cmd on Windows; a bare 'claude' is not found by CreateProcess."""
    monkeypatch.setenv("JOBFINDER_HOME", str(tmp_path))
    seen: dict = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    monkeypatch.setattr(claude_code.shutil, "which", lambda name: r"C:\npm\claude.cmd")
    monkeypatch.setattr(claude_code.subprocess, "run", fake_run)
    claude_code._default_runner(["claude", "-p"], "hi", 5)
    assert seen["argv"][0] == r"C:\npm\claude.cmd" and seen["argv"][1:] == ["-p"]
    assert seen["kw"]["encoding"] == "utf-8"


def test_unresolvable_binary_is_passed_through(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOBFINDER_HOME", str(tmp_path))
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    seen: dict = {}
    monkeypatch.setattr(
        claude_code.subprocess, "run",
        lambda argv, **kw: seen.update(argv=argv) or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    claude_code._default_runner(["claude", "-p"], "hi", 5)
    assert seen["argv"][0] == "claude"
