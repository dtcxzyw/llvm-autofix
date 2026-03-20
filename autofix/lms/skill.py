from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List

import yaml

from autofix.lms.tool import FuncToolBase, FuncToolSpec

if TYPE_CHECKING:
  from autofix.lms.generic import GenericAgent

SKILL_FILE = "SKILL.md"


@dataclass
class Skill:
  name: str
  description: str
  parameters: List[FuncToolSpec.Param]
  tools: List[str]  # Tool names available in the sub-loop
  budget: int  # Max number of calls per tool allowed in the sub-loop
  instructions: str  # Markdown body with {{ param }} placeholders
  path: Path = field(default_factory=lambda: Path("."))  # Skill directory
  references: List[Path] = field(default_factory=list)  # Extra files in skill dir
  scripts: List[Path] = field(default_factory=list)  # Executable scripts in skill dir


class DoneTool(FuncToolBase):
  """Special tool that signals skill sub-loop termination."""

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "skill_done",
      "Signal that the skill has completed and return the result.",
      [
        FuncToolSpec.Param(
          "result",
          "string",
          True,
          "The final result text to return from this skill.",
        ),
      ],
    )

  def _call(self, *, result: str, **kwargs) -> str:
    return result


class SkillTool(FuncToolBase):
  """Wraps a Skill as a FuncToolBase so it can be called like any other tool."""

  def __init__(self, skill: Skill, agent: GenericAgent):
    self.skill = skill
    self.agent = agent

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      self.skill.name,
      self.skill.description,
      self.skill.parameters,
    )

  def _call(self, **kwargs) -> str:
    # Render the prompt template with the provided arguments
    prompt = self.skill.instructions
    for key, value in kwargs.items():
      prompt = prompt.replace("{{ " + key + " }}", str(value))

    # Inject script paths (agent can run them via bash tool)
    if self.skill.scripts:
      scripts_text = "\n".join(
        f"- `{s.resolve().absolute()}`" for s in self.skill.scripts
      )
      prompt += "\n\n# Available Bash Scripts:\n" + scripts_text

    # Inject reference file paths (agent can read them on demand)
    if self.skill.references:
      refs_text = "\n".join(
        f"- `{ref.resolve().absolute()}`" for ref in self.skill.references
      )
      prompt += "\n\n# References:\n" + refs_text

    # Auto-add bash to tools if skill has scripts
    tool_names = list(self.skill.tools)
    if self.skill.scripts and "bash" not in tool_names:
      tool_names.append("bash")

    return self.agent.run_skill(
      skill_name=self.skill.name,
      skill_inst=prompt,
      tool_names=tool_names,
      tool_budget=self.skill.budget,
    )


def load_skill(path: Path) -> Skill:
  """Load a skill from a directory containing SKILL.md."""
  assert path.is_dir(), f"Skill path {path} must be a directory"

  skill_dir = path
  skill_file = skill_dir / SKILL_FILE

  content = skill_file.read_text().strip()

  if not content.startswith("---"):
    raise ValueError(f"Skill file {skill_file} must start with YAML frontmatter (---)")

  end = content.index("---", 3)
  header = yaml.safe_load(content[3:end])
  body = content[end + 3 :].strip()

  params = []
  for p in header.get("parameters", []):
    params.append(
      FuncToolSpec.Param(
        name=p["name"],
        type=p.get("type", "string"),
        req=p.get("required", True),
        desc=p.get("description", ""),
      )
    )

  # Discover references and scripts from subdirectories
  references = []
  refs_dir = skill_dir / "references"
  if refs_dir.exists() and refs_dir.is_dir():
    for child in sorted(refs_dir.iterdir()):
      if child.is_file():
        references.append(child)

  scripts = []
  scripts_dir = skill_dir / "scripts"
  if scripts_dir.exists() and scripts_dir.is_dir():
    for child in sorted(scripts_dir.iterdir()):
      if child.is_file() and child.stat().st_mode & 0o111:
        scripts.append(child)

  return Skill(
    name=header["name"],
    description=header["description"],
    parameters=params,
    tools=header.get("allowed-tools", []),
    budget=header.get("tool-budget", sys.maxsize),
    instructions=body,
    path=skill_dir,
    references=references,
    scripts=scripts,
  )
