from pathlib import Path
from typing import List

from autofix.lms.tool import FuncToolBase, FuncToolCallException, FuncToolSpec
from autofix.tools.llvm_mixins import LlvmDirMixin


class ReadTool(FuncToolBase, LlvmDirMixin):
  def __init__(self, llvm_dir: str):
    self.llvm_dir = Path(llvm_dir).resolve().absolute()

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "read",
      "Read the content of a list of files.",
      [
        FuncToolSpec.Param(
          "files",
          "list[string]",
          True,
          "The list of relative paths to the files to read. Each path should start with llvm/.",
        )
      ],
    )

  def _call(self, *, files: List[str], **kwargs) -> str:
    if len(files) == 0:
      raise FuncToolCallException(
        "No files provided. Please provide a list of file paths to read."
      )

    return "\n\n".join([self.read(file) for file in files])

  def read(self, file: str) -> str:
    file_full_path = self.check_llvm_file(file)
    try:
      lines = file_full_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception as e:
      raise FuncToolCallException(f"Failed to read the file {file}. {e}")
    header = f"file: {file}\n"
    separator = "-" * (len(header) - 1)
    lno_fmt = "{:>" + str(len(str(len(lines)))) + "}"
    return (
      header
      + separator
      + "\n"
      + "".join(
        [lno_fmt.format(lno + 1) + " " + line for lno, line in enumerate(lines)]
      )
      + separator
    )
