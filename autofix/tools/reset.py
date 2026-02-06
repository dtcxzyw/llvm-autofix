import subprocess

from autofix.llvm.llvm_helper import decode_output
from autofix.lms.tool import FuncToolBase, FuncToolCallException, FuncToolSpec


class ResetTool(FuncToolBase):
  def __init__(self, llvm_dir: str, base_commit: str):
    self.llvm_dir = llvm_dir
    self.base_commit = base_commit

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "reset",
      "Checkout the original file from the repository.",
      [
        FuncToolSpec.Param(
          "file",
          "string",
          True,
          "The relative path of the file to edit (starting with llvm/).",
        )
      ],
    )

  def _call(self, *, file: str, **kwargs) -> str:
    try:
      subprocess.check_call(
        ["git", "checkout", self.base_commit, file],
        cwd=self.llvm_dir,
      )
    except subprocess.CalledProcessError as e:
      raise FuncToolCallException(
        f"Failed to checkout {file}: "
        + str(e)
        + "\n"
        + decode_output(e.output)
        + "\n"
        + decode_output(e.stderr),
      )
    return f"Checked out {file} from commit {self.base_commit}."
