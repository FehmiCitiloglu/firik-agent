from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field
import yaml

from firik_agent.core.types import ToolDefinition


class Skill(BaseModel):
    name: str
    description: str
    instructions: str
    version: str = "1.0.0"
    is_active: bool = False
    source_dir: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    @classmethod
    def load_from_directory(cls, dir_path: Union[str, Path]) -> "Skill":
        path = Path(dir_path)
        if not path.is_dir():
            raise NotADirectoryError(f"Skill directory not found: {dir_path}")

        name = path.name
        description = ""
        instructions = ""
        tags = []

        # Check for skill.yaml or skill.json
        yaml_file = path / "skill.yaml"
        if yaml_file.exists():
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                name = data.get("name", name)
                description = data.get("description", "")
                tags = data.get("tags", [])

        # Check for SKILL.md
        md_file = path / "SKILL.md"
        if md_file.exists():
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read()

            # Check for frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    fm = yaml.safe_load(parts[1]) or {}
                    name = fm.get("name", name)
                    description = fm.get("description", description)
                    instructions = parts[2].strip()
                else:
                    instructions = content.strip()
            else:
                instructions = content.strip()
                if not description and instructions:
                    description = instructions.split("\n")[0][:100]

        return cls(
            name=name,
            description=description or f"Skill {name}",
            instructions=instructions,
            source_dir=str(path.resolve()),
            tags=tags,
        )

    def save(self, target_dir: Union[str, Path]) -> None:
        path = Path(target_dir) / self.name
        path.mkdir(parents=True, exist_ok=True)

        # Write skill.yaml
        yaml_data = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "tags": self.tags,
        }
        with open(path / "skill.yaml", "w", encoding="utf-8") as f:
            yaml.dump(yaml_data, f, default_flow_style=False)

        # Write SKILL.md
        md_content = f"""---
name: {self.name}
description: {self.description}
---

# {self.name}

{self.instructions}
"""
        with open(path / "SKILL.md", "w", encoding="utf-8") as f:
            f.write(md_content)

        self.source_dir = str(path.resolve())
