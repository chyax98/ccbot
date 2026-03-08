"""Workspace manager: builds system prompt from memory, skills, and bootstrap files."""

from __future__ import annotations

import json
import platform
import re
import shutil
from datetime import datetime
from pathlib import Path

from loguru import logger

_BUILTIN_SKILLS = Path(__file__).parent / "skills"
_BUILTIN_TEMPLATES = Path(__file__).parent / "templates"
_BOOTSTRAP_FILES = ["SOUL.md", "AGENTS.md", "USER.md", "TOOLS.md", "IDENTITY.md"]


class WorkspaceManager:
    """
    Manages the ccbot workspace directory.

    Workspace layout:
        memory/MEMORY.md    — long-term facts (always in system_prompt)
        memory/HISTORY.md   — append-only grep-searchable log
        HEARTBEAT.md        — periodic tasks
        SOUL.md / AGENTS.md / USER.md / TOOLS.md — personality & instructions
        skills/<name>/SKILL.md — custom user skills (override builtins)
    """

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self._ensure_dirs()
        self._init_templates()

    def _ensure_dirs(self) -> None:
        (self.path / "memory").mkdir(parents=True, exist_ok=True)
        (self.path / "skills").mkdir(parents=True, exist_ok=True)

    def _init_templates(self) -> None:
        """Copy builtin templates to workspace on first run."""
        if not _BUILTIN_TEMPLATES.exists():
            return
        for tmpl in _BUILTIN_TEMPLATES.glob("*.md"):
            dest = self.path / tmpl.name
            if not dest.exists():
                shutil.copy2(tmpl, dest)
                logger.debug("初始化模板: {}", tmpl.name)

    # ---- Properties ----

    @property
    def memory_file(self) -> Path:
        return self.path / "memory" / "MEMORY.md"

    @property
    def history_file(self) -> Path:
        return self.path / "memory" / "HISTORY.md"

    @property
    def heartbeat_file(self) -> Path:
        return self.path / "HEARTBEAT.md"

    def read_memory(self) -> str:
        return self.memory_file.read_text("utf-8") if self.memory_file.exists() else ""

    # ---- System prompt ----

    def build_system_prompt(self) -> str:
        """Build complete system prompt: identity + bootstrap + memory + always-skills + skills summary."""
        parts = [self._identity()]

        # Bootstrap files (SOUL.md, AGENTS.md, USER.md, TOOLS.md, IDENTITY.md)
        bootstrap: list[str] = []
        for fname in _BOOTSTRAP_FILES:
            p = self.path / fname
            if p.exists():
                bootstrap.append(f"## {fname}\n\n{p.read_text('utf-8')}")
        if bootstrap:
            parts.append("\n\n".join(bootstrap))

        # Long-term memory (always loaded)
        memory = self.read_memory()
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        # Always-on skills (e.g. memory skill — always=true)
        always = self._get_always_skills()
        if always:
            content = self._load_skills_for_context(always)
            if content:
                parts.append(f"# Active Skills\n\n{content}")

        # Skills summary (agent reads SKILL.md on demand via Read tool)
        summary = self._build_skills_summary()
        if summary:
            parts.append(
                "# Skills\n\n"
                "The following skills extend your capabilities. "
                "To use a skill, read its SKILL.md file using the Read tool.\n\n" + summary
            )

        return "\n\n---\n\n".join(parts)

    def _identity(self) -> str:
        ws = str(self.path)
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        return (
            "# ccbot 🐈\n\n"
            "You are ccbot, a helpful AI assistant.\n\n"
            f"## Runtime\n{runtime}\nCurrent time: {now}\n\n"
            f"## Workspace\nYour workspace is at: {ws}\n"
            f"- Long-term memory: {ws}/memory/MEMORY.md (write important facts here)\n"
            f"- History log: {ws}/memory/HISTORY.md (grep-searchable, entries start with [YYYY-MM-DD HH:MM])\n"
            f"- Custom skills: {ws}/skills/{{skill-name}}/SKILL.md\n"
            f"- Heartbeat tasks: {ws}/HEARTBEAT.md\n\n"
            "## Guidelines\n"
            "- State intent before tool calls, but NEVER predict results before receiving them.\n"
            "- Before modifying a file, read it first.\n"
            "- After writing or editing a file, re-read it if accuracy matters.\n"
            "- If a tool call fails, analyze the error before retrying.\n"
            "- Ask for clarification when the request is ambiguous."
        )

    # ---- Skill management ----

    def _list_all_skills(self) -> list[dict]:
        """List all skills; workspace skills override builtins of same name."""
        skills: list[dict] = []

        ws_skills = self.path / "skills"
        if ws_skills.exists():
            for d in sorted(ws_skills.iterdir()):
                if d.is_dir() and (d / "SKILL.md").exists():
                    skills.append(
                        {"name": d.name, "path": str(d / "SKILL.md"), "source": "workspace"}
                    )

        if _BUILTIN_SKILLS.exists():
            for d in sorted(_BUILTIN_SKILLS.iterdir()):
                if (
                    d.is_dir()
                    and (d / "SKILL.md").exists()
                    and not any(s["name"] == d.name for s in skills)
                ):
                    skills.append(
                        {"name": d.name, "path": str(d / "SKILL.md"), "source": "builtin"}
                    )

        return skills

    def _list_available_skills(self) -> list[dict]:
        return [
            s
            for s in self._list_all_skills()
            if self._check_requirements(self._get_skill_meta(s["name"]))
        ]

    def _get_skill_content(self, name: str) -> str | None:
        for base in [self.path / "skills", _BUILTIN_SKILLS]:
            p = base / name / "SKILL.md"
            if p.exists():
                return p.read_text("utf-8")
        return None

    def _parse_frontmatter(self, content: str) -> dict:
        if not content.startswith("---"):
            return {}
        m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not m:
            return {}
        meta: dict = {}
        for line in m.group(1).split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip("\"'")
        return meta

    def _get_skill_meta(self, name: str) -> dict:
        """Return ccbot/nanobot/openclaw metadata block from skill frontmatter."""
        content = self._get_skill_content(name)
        if not content:
            return {}
        fm = self._parse_frontmatter(content)
        raw = fm.get("metadata", "")
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    result: dict = data.get("ccbot", data.get("nanobot", data.get("openclaw", {})))  # type: ignore[assignment]
                    return result
            except Exception:
                pass
        return fm

    def _check_requirements(self, skill_meta: dict) -> bool:
        import os

        requires = skill_meta.get("requires", {})
        return all(shutil.which(b) for b in requires.get("bins", [])) and all(
            os.environ.get(env) for env in requires.get("env", [])
        )

    def _get_always_skills(self) -> list[str]:
        result = []
        for s in self._list_available_skills():
            fm = self._parse_frontmatter(self._get_skill_content(s["name"]) or "")
            skill_meta = self._get_skill_meta(s["name"])
            if skill_meta.get("always") in (True, "true") or fm.get("always") in ("true", True):
                result.append(s["name"])
        return result

    def _strip_frontmatter(self, content: str) -> str:
        if not content.startswith("---"):
            return content
        m = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
        return content[m.end() :].strip() if m else content

    def _load_skills_for_context(self, names: list[str]) -> str:
        parts = []
        for name in names:
            content = self._get_skill_content(name)
            if content:
                parts.append(f"### Skill: {name}\n\n{self._strip_frontmatter(content)}")
        return "\n\n---\n\n".join(parts)

    def _build_skills_summary(self) -> str:
        all_skills = self._list_all_skills()
        if not all_skills:
            return ""

        def esc(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for s in all_skills:
            fm = self._parse_frontmatter(self._get_skill_content(s["name"]) or "")
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)
            desc = esc(fm.get("description", s["name"]))
            lines += [
                f'  <skill available="{str(available).lower()}">',
                f"    <name>{esc(s['name'])}</name>",
                f"    <description>{desc}</description>",
                f"    <location>{s['path']}</location>",
                "  </skill>",
            ]
        lines.append("</skills>")
        return "\n".join(lines)
