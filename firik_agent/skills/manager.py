from __future__ import annotations
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from firik_agent.skills.skill import Skill

logger = logging.getLogger(__name__)


class SkillDict(dict):
    """Dictionary that transparently handles hyphen and underscore equivalence for skill names."""

    def _resolve_key(self, key: Any) -> Any:
        if isinstance(key, str):
            if super().__contains__(key):
                return key
            alt1 = key.replace("-", "_")
            if super().__contains__(alt1):
                return alt1
            alt2 = key.replace("_", "-")
            if super().__contains__(alt2):
                return alt2
        return key

    def __getitem__(self, key: str) -> Skill:
        resolved = self._resolve_key(key)
        return super().__getitem__(resolved)

    def __setitem__(self, key: str, value: Skill) -> None:
        super().__setitem__(key, value)

    def __delitem__(self, key: str) -> None:
        resolved = self._resolve_key(key)
        super().__delitem__(resolved)

    def __contains__(self, key: object) -> bool:
        if super().__contains__(key):
            return True
        if isinstance(key, str):
            alt1 = key.replace("-", "_")
            if super().__contains__(alt1):
                return True
            alt2 = key.replace("_", "-")
            if super().__contains__(alt2):
                return True
        return False

    def get(self, key: str, default: Any = None) -> Any:
        resolved = self._resolve_key(key)
        return super().get(resolved, default)

    def pop(self, key: str, *args: Any) -> Any:
        resolved = self._resolve_key(key)
        return super().pop(resolved, *args)


def _is_root_or_root_child(path: Path) -> bool:
    try:
        resolved = path.resolve()
        return resolved.parent == resolved or resolved.parent.parent == resolved.parent
    except Exception:
        return True


def _resolve_safe_skills_dir(skills_dir: Optional[Union[str, Path]] = None) -> Path:
    target = Path(skills_dir or "skills")
    try:
        if _is_root_or_root_child(target):
            return Path.home() / ".firik" / "skills"
        return target.resolve()
    except Exception:
        return Path.home() / ".firik" / "skills"


class SkillManager:
    """Manages skill discovery, dynamic creation, and prompt injection."""

    def __init__(self, skills_dir: Optional[Union[str, Path]] = None):
        self.skills_dir = _resolve_safe_skills_dir(skills_dir)
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            fallback = Path.home() / ".firik" / "skills"
            if self.skills_dir != fallback:
                logger.warning(
                    "Failed to create skills directory at %s (%s). Falling back to %s",
                    self.skills_dir, e, fallback,
                )
                self.skills_dir = fallback
                try:
                    self.skills_dir.mkdir(parents=True, exist_ok=True)
                except OSError as err_fb:
                    logger.warning("Failed to create fallback skills directory %s: %s", fallback, err_fb)
            else:
                logger.warning("Failed to create skills directory %s: %s", self.skills_dir, e)
        self.skills: Dict[str, Skill] = SkillDict()

    def discover_skills(self, additional_dirs: Optional[List[Union[str, Path]]] = None) -> int:
        """Scan skills directory and optional additional dirs (e.g. builtin skills)."""
        search_dirs = [self.skills_dir]
        if additional_dirs:
            search_dirs.extend([Path(d).resolve() for d in additional_dirs if Path(d).exists()])

        count = 0
        for sdir in search_dirs:
            if not sdir.is_dir():
                continue
            for item in sdir.iterdir():
                if item.is_dir() and (item / "SKILL.md").exists() or (item / "skill.yaml").exists():
                    try:
                        skill = Skill.load_from_directory(item)
                        if skill.name not in self.skills:
                            self.skills[skill.name] = skill
                            count += 1
                            logger.info("Discovered skill '%s' at %s", skill.name, item)
                    except Exception as e:
                        logger.error("Failed loading skill from %s: %s", item, e)
        return count

    def add_skill(
        self,
        name: str,
        description: str,
        instructions: str,
        tags: Optional[List[str]] = None,
        activate: bool = True,
    ) -> Skill:
        """Dynamically create and persist a new skill."""
        skill = Skill(
            name=name,
            description=description,
            instructions=instructions,
            tags=tags or [],
            is_active=activate,
        )
        skill.save(self.skills_dir)
        self.skills[name] = skill
        logger.info("Added and persisted skill '%s'", name)
        return skill

    def activate_skill(self, name: str) -> bool:
        if name in self.skills:
            self.skills[name].is_active = True
            logger.info("Activated skill '%s'", name)
            return True
        return False

    def deactivate_skill(self, name: str) -> bool:
        if name in self.skills:
            self.skills[name].is_active = False
            logger.info("Deactivated skill '%s'", name)
            return True
        return False

    def get_skill(self, name: str) -> Optional[Skill]:
        return self.skills.get(name)

    def list_skills(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "is_active": s.is_active,
                "tags": s.tags,
                "source_dir": s.source_dir,
            }
            for s in self.skills.values()
        ]

    def get_prompt_augmentation(self) -> str:
        """Concatenate instructions from all currently active skills."""
        active = [s for s in self.skills.values() if s.is_active and s.instructions]
        if not active:
            return ""

        parts = ["\n\n--- ACTIVE SKILLS & SPECIALIZED INSTRUCTIONS ---"]
        for s in active:
            parts.append(f"\n### [Skill: {s.name}]\n{s.description}\n{s.instructions}\n")
        parts.append("--- END ACTIVE SKILLS ---\n")
        return "\n".join(parts)
