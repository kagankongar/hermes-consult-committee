"""Docs consistency: escalation-prompts.md mirrors the README recall tiers."""
from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name: str) -> str:
    with open(os.path.join(_ROOT, name), encoding="utf-8") as f:
        return f.read()


def _prompt_blocks(text: str) -> list[str]:
    return re.findall(r"```\n(## Committee Escalation[^\n]*\n.*?)\n```", text, re.S)


def _tier_table(text: str) -> list[str]:
    rows = [line.removeprefix("> ") for line in text.splitlines()]
    return [row for row in rows if row.startswith("| ")]


def test_three_tier_blocks_each_with_heading_inside_fence():
    blocks = _prompt_blocks(_read("README.md"))
    assert [b.splitlines()[0] for b in blocks] == [
        "## Committee Escalation - High",
        "## Committee Escalation (medium)",
        "## Committee Escalation (low)",
    ]


def test_prompt_blocks_have_no_stray_quote_markers():
    for block in _prompt_blocks(_read("README.md")):
        assert not any(line.startswith(">") for line in block.splitlines()), block[:80]


def test_escalation_prompts_mirror_readme():
    readme, mirror = _read("README.md"), _read("escalation-prompts.md")
    assert _prompt_blocks(mirror) == _prompt_blocks(readme)
    assert _tier_table(mirror) == _tier_table(readme)
