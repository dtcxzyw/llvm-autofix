from pathlib import Path

from autofix.lms.tool import FuncToolBase, FuncToolCallException, FuncToolSpec
from autofix.tools.llvm_mixins import LlvmDirMixin


class WriteTool(FuncToolBase, LlvmDirMixin):
  def __init__(self, llvm_dir: str):
    self.llvm_dir = Path(llvm_dir).resolve().absolute()

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "write",
      "Write the content to a file, overwriting its existing content or creating it if it doesn't exist.",
      [
        FuncToolSpec.Param(
          "file",
          "string",
          True,
          "The relative path of the file to write (starting with llvm/).",
        ),
        FuncToolSpec.Param(
          "content", "string", True, "The content to write to the file."
        ),
      ],
    )

  def _call(self, *, file: str, content: str, **kwargs) -> str:
    full_path = self.check_llvm_file(file, should_exist=False)
    try:
      # Ensure the parent directory exists
      full_path.parent.mkdir(parents=True, exist_ok=True)
      full_path.write_text(content, encoding="utf-8")
      return f"File {file} written successfully."
    except Exception as e:
      raise FuncToolCallException(f"Failed to write to file {file}. {e}")
