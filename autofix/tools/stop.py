import json
from pathlib import Path

from autofix.lms.tool import FuncToolBase, FuncToolCallException, FuncToolSpec
from autofix.tools.llvm_mixins import LlvmDirMixin


class StopTool(FuncToolBase, LlvmDirMixin):
  def __init__(self, llvm_dir: str, min_edit_point_lines: int):
    self.llvm_dir = Path(llvm_dir).resolve().absolute()
    self.min_edit_point_lines = min_edit_point_lines

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "stop",
      "Stop process and return the found edit points for fixing the issue",
      [
        FuncToolSpec.Param(
          "edit_points",
          "list[tuple[int,int,string]]",
          True,
          "A list of edit points with each being a tuple of the one-indexed starting line number (included)"
          ", the ending line number (included), and the relative path of the file to edit (starting with llvm/).",
        ),
        FuncToolSpec.Param(
          "thoughts",
          "string",
          True,
          'The detailed thoughts for diagnosing the issue including step-by-step "'
          '1. Understanding the Issue", '
          '2. "Analyzing `opt`\'s Log", '
          '3. "Root Cause Analysis", '
          '4. "Proposed Edit Points(s)", and '
          '5. "Conclusion".',
        ),
      ],
    )

  def _call(self, *, edit_points: list[tuple[int, int, str]], thoughts: str) -> str:
    # Check and fix the model-provided edit points
    fixed_edit_points = []
    for ind, edit in enumerate(edit_points):
      if len(edit) != 3:
        raise FuncToolCallException(
          f"Each edit point must be a tuple of 3 elements (starting line number, ending line number, and the relative path of the file to edit): {edit}"
        )
      fixed_edit = []
      try:
        start_line = int(edit[0])
      except Exception:
        raise FuncToolCallException(
          f"The starting line number must be an integer, got {edit[0]} at edit_points[{ind}]: {edit}"
        )
      if start_line < 1:
        raise FuncToolCallException(
          f"The starting line number must be an one-indexed integer, got {start_line} at edit_points[{ind}]: {edit}"
        )
      fixed_edit.append(start_line)
      try:
        end_line = int(edit[1])
      except Exception:
        raise FuncToolCallException(
          f"The ending line number must be an integer, got {edit[1]} at edit_points[{ind}]: {edit}"
        )
      if end_line < 1:
        raise FuncToolCallException(
          f"The ending line number must be an one-indexed integer, got {end_line} at edit_points[{ind}]: {edit}"
        )
      if end_line - start_line + 1 < self.min_edit_point_lines:
        raise FuncToolCallException(
          f"An edit point must be at least 5 lines long, got {end_line - start_line + 1} lines at edit_points[{ind}]: {edit}"
        )
      fixed_edit.append(end_line)
      try:
        fixed_edit.append(str(self.check_llvm_file(edit[2]).relative_to(self.llvm_dir)))
      except FuncToolCallException as e:
        raise FuncToolCallException(
          f"Invalid file path for detected at edit_points[{ind}]: {edit}. {e}"
        )
      fixed_edit_points.append(tuple(fixed_edit))
    return json.dumps(
      {
        "edit_points": fixed_edit_points,
        "thoughts": thoughts,
      },
      indent=2,
    )
