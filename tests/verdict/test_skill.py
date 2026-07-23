"""The deep-verdict skill installer writes the file and backs up existing ones."""

from llmux.verdict.skill import (
    install_skill,
    render_mcp_registration,
    skill_dir,
)


def test_install_skill_writes_and_backs_up(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    paths = install_skill()
    target = skill_dir() / "SKILL.md"
    assert paths == [target]
    content = target.read_text(encoding="utf-8")
    assert "name: deep-verdict" in content

    # A second install backs the previous file up rather than clobbering it.
    install_skill()
    assert (skill_dir() / "SKILL.md.bak").exists()


def test_mcp_registration_snippet_mentions_server():
    snippet = render_mcp_registration()
    assert "llmux-verdict serve-mcp" in snippet
