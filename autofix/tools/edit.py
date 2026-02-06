from pathlib import Path

from autofix.lms.tool import FuncToolBase, FuncToolCallException, FuncToolSpec
from autofix.tools.llvm_mixins import LlvmDirMixin


class EditTool(FuncToolBase, LlvmDirMixin):
  def __init__(self, llvm_dir: str):
    self.llvm_dir = Path(llvm_dir).resolve().absolute()

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "edit",
      "Edit a file to replace text within a file with new text.",
      [
        FuncToolSpec.Param(
          "file",
          "string",
          True,
          "The relative path of the file to edit (starting with llvm/).",
        ),
        FuncToolSpec.Param(
          "old", "string", True, "The *exact* code snippet to be replaced in the file."
        ),
        FuncToolSpec.Param("new", "string", True, "The new code snippet."),
      ],
    )

  def _call(self, *, file: str, old: str, new: str, **kwargs) -> str:
    full_path = self.check_llvm_file(file)
    content = full_path.read_text(encoding="utf-8")
    if old not in content:
      raise FuncToolCallException("The `old` text is not found in file.")
    content = content.replace(old, new)
    full_path.write_text(content, encoding="utf-8")
    return "Replaced successfully."
