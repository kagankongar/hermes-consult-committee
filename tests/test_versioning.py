"""Release hygiene: plugin.yaml version, CHANGELOG sections and compare links agree.

Stdlib only (no YAML parser): plugin.yaml's top-level ``version:`` line is read
with a regex.
"""
from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Plain SemVer core (valid PEP 440 too). Legacy fork strings are grandfathered
# for already-tagged releases only.
_PLAIN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_LEGACY = re.compile(r"^0\.1\.1-fork\.[1-7]$")


def _read(name: str) -> str:
    with open(os.path.join(_ROOT, name), encoding="utf-8") as f:
        return f.read()


def _plugin_version() -> str:
    m = re.search(r"^version:\s*['\"]?([^'\"\s]+)['\"]?\s*$", _read("plugin.yaml"), re.M)
    assert m, "plugin.yaml has no top-level version: line"
    return m.group(1)


def _section_headings(changelog: str) -> list[str]:
    return re.findall(r"^## \[([^\]]+)\]", changelog, re.M)


def _link_refs(changelog: str) -> dict[str, str]:
    return dict(re.findall(r"^\[([^\]]+)\]:\s*(\S+)\s*$", changelog, re.M))


def test_plugin_version_is_plain_semver_or_legacy_fork():
    version = _plugin_version()
    assert _PLAIN.match(version) or _LEGACY.match(version), (
        f"plugin.yaml version {version!r} must be MAJOR.MINOR.PATCH "
        "(new -fork.N versions are not allowed)"
    )


def test_changelog_has_unreleased_first():
    headings = _section_headings(_read("CHANGELOG.md"))
    assert headings and headings[0] == "Unreleased"


def test_changelog_has_section_for_plugin_version():
    version = _plugin_version()
    assert version in _section_headings(_read("CHANGELOG.md")), (
        f"CHANGELOG.md has no '## [{version}]' section for the plugin.yaml version"
    )


def test_every_changelog_section_has_a_link_reference():
    changelog = _read("CHANGELOG.md")
    refs = _link_refs(changelog)
    missing = [h for h in _section_headings(changelog) if h not in refs]
    assert not missing, f"CHANGELOG.md sections without a [x]: link: {missing}"


def test_release_links_point_at_their_tag():
    changelog = _read("CHANGELOG.md")
    refs = _link_refs(changelog)
    for heading in _section_headings(changelog):
        if heading == "Unreleased":
            assert refs[heading].endswith("...HEAD")
            continue
        assert refs[heading].endswith(f"v{heading}"), (
            f"[{heading}] link should end at tag v{heading}: {refs[heading]}"
        )


def test_unreleased_compares_from_latest_release():
    changelog = _read("CHANGELOG.md")
    headings = _section_headings(changelog)
    refs = _link_refs(changelog)
    if len(headings) > 1:
        assert f"/compare/v{headings[1]}...HEAD" in refs["Unreleased"]
