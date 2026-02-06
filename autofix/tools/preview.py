from autofix.llvm.lab_env import Environment
from autofix.lms.tool import FuncToolBase, FuncToolSpec


class PreviewTool(FuncToolBase):
  def __init__(self, env: Environment):
    self.env = env

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "preview",
      "Preview the changes made by the current patch.",
      [],
    )

  def _call(self, **kwargs) -> str:
    return self.env.dump_patch()
