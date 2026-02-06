from autofix.base.console import get_boxed_console
from autofix.lms.tool import FuncToolBase, FuncToolSpec


class AskQuestionTool(FuncToolBase):
  def __init__(self):
    self.console = get_boxed_console(box_title="Agent Question", debug_mode=True)

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "ask",
      "Ask the user a question to clarify requirements, get feedback, or request missing information.",
      [
        FuncToolSpec.Param(
          "question",
          "string",
          True,
          "The question to ask the user.",
        ),
      ],
    )

  def _call(self, *, question: str, **kwargs) -> str:
    self.console.printb(message=question)
    answer = input("> ")
    return answer
