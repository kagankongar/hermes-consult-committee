"""Docs consistency: where the tier prompts live, README size, links, pinning advice."""
from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOCS = ("README.md", "GUIDE.md", "escalation-prompts.md", "COMPARISON.md", "CHANGELOG.md", "AGENTS.md")
_README_MAX_LINES = 150


def _read(name: str) -> str:
    with open(os.path.join(_ROOT, name), encoding="utf-8") as f:
        return f.read()


def _prompt_blocks(text: str) -> list[str]:
    return re.findall(r"```\n(## Committee Escalation[^\n]*\n.*?)\n```", text, re.S)


def _without_code(text: str) -> str:
    """Drop fenced code blocks and inline code, so examples aren't parsed as links/headings."""
    text = re.sub(r"^```.*?^```", "", text, flags=re.S | re.M)
    return re.sub(r"`[^`\n]*`", "", text)


def _slug(heading: str) -> str:
    """GitHub-style heading anchor."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def _anchors(text: str) -> set[str]:
    seen: dict[str, int] = {}
    anchors = set()
    for match in re.finditer(r"^#{1,6} +(.+?) *#*$", _without_code(text), re.M):
        slug = _slug(match.group(1))
        count = seen.get(slug, 0)
        anchors.add(slug if count == 0 else f"{slug}-{count}")
        seen[slug] = count + 1
    return anchors


# ---------------------------------------------------------------------------
# Tier prompts: escalation-prompts.md is the single source
# ---------------------------------------------------------------------------
def test_three_tier_blocks_each_with_heading_inside_fence():
    blocks = _prompt_blocks(_read("escalation-prompts.md"))
    assert [b.splitlines()[0] for b in blocks] == [
        "## Committee Escalation (high)",
        "## Committee Escalation (medium)",
        "## Committee Escalation (low)",
    ]


def test_prompt_blocks_have_no_stray_quote_markers():
    for block in _prompt_blocks(_read("escalation-prompts.md")):
        assert not any(line.startswith(">") for line in block.splitlines()), block[:80]


def test_every_tier_block_handles_exhausted_budget():
    for block in _prompt_blocks(_read("escalation-prompts.md")):
        assert "If the budget is exhausted, proceed solo and state it." in block, block.splitlines()[0]


def test_tier_table_lives_in_prompts_file():
    text = _read("escalation-prompts.md")
    for row in ("| Pick when |", "| Suggested budget"):
        assert row in text, row


def test_no_copies_of_prompts_or_table_elsewhere():
    for name in ("README.md", "GUIDE.md"):
        text = _read(name)
        assert not _prompt_blocks(text), f"{name} copies a tier prompt block; link escalation-prompts.md"
        assert "| Pick when |" not in text, f"{name} copies the tier table"


# ---------------------------------------------------------------------------
# README stays a short front page
# ---------------------------------------------------------------------------
def test_readme_is_short():
    lines = _read("README.md").count("\n") + 1
    assert lines <= _README_MAX_LINES, f"README.md has {lines} lines (max {_README_MAX_LINES}); move detail to GUIDE.md"


def test_readme_links_guide_and_prompts():
    text = _read("README.md")
    assert "](GUIDE.md" in text and "](escalation-prompts.md" in text


# ---------------------------------------------------------------------------
# Links and pinning advice
# ---------------------------------------------------------------------------
def test_relative_links_resolve():
    problems = []
    for name in _DOCS:
        for target in re.findall(r"\]\(([^)\s]+)\)", _without_code(_read(name))):
            if re.match(r"[a-z]+:", target):  # http(s):, mailto:
                continue
            path, _, anchor = target.partition("#")
            path = path or name
            if not os.path.isfile(os.path.join(_ROOT, path)):
                problems.append(f"{name}: missing file {target}")
            elif anchor and path.endswith(".md") and anchor not in _anchors(_read(path)):
                problems.append(f"{name}: missing anchor {target}")
    assert not problems, "\n".join(problems)


def test_ref_is_never_given_a_tag():
    """`hermes plugins install --ref` takes only a full 40-character commit SHA."""
    for name in _DOCS:
        bad = re.findall(r"--ref[ =]+v\d[\w.]*", _read(name))
        assert not bad, f"{name}: {bad} (use the tag's commit SHA)"
