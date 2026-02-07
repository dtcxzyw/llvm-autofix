import sys
from abc import abstractmethod
from dataclasses import dataclass
from typing import Callable, List, Literal, Tuple, Union

from tenacity import (
  retry,
  stop_after_attempt,
  wait_random_exponential,
)  # for exponential backoff

from autofix.base.console import get_boxed_console
from autofix.llvm.llvm_helper import remove_path_from_output
from autofix.lms.tool import FuncToolBase, ToolRegistry


@dataclass
class ChatMessage:
  type: Union[
    Literal["message"], Literal["function_call"], Literal["function_call_output"]
  ]


@dataclass
class ChatMessageMessage(ChatMessage):
  role: str = Union[Literal["system"], Literal["user"], Literal["assistant"]]
  content: str = ""
  type: str = "message"


@dataclass
class ChatMessageFunctionCall(ChatMessage):
  call_id: str = ""
  name: str = ""
  arguments: str = ""
  type: str = "function_call"


@dataclass
class ChatMessageFunctionCallOutput(ChatMessage):
  call_id: str = ""
  output: str = ""
  type: str = "function_call_output"


# Input: response content
# Output: (flag, content)
# If flag is True, the content is passed as user prompt for the next round.
# Otherwise, the content is returned as the final output.
ResponseHandler = Callable[[str], Tuple[bool, str]]
# Input: (tool name, tool arguments, tool result)
# Output: (flag, content)
# If flag is True, the content is passed to the assistant.
# Otherwise, the content is returned as the final output.
ToolUseHandler = Callable[[str, str, str], Tuple[bool, str]]


class ReachRoundLimit(StopIteration):
  def __init__(self):
    super().__init__("Reached the round limit for agent execution.")


class ReachTokenLimit(StopIteration):
  def __init__(self):
    super().__init__("Reached the token limit for agent execution.")


ReasoningEffort = Literal[
  "NOT_GIVEN", "none", "minimal", "low", "medium", "high", "xhigh"
]


class AgentBase:
  def __init__(
    self,
    model: str,
    *,
    temperature: float = 0,  # Higher temperature means more randomness
    top_p: float = 0.95,  # Top cumulative probability for nucleus sampling
    max_completion_tokens: int = 8092,  # Max completion tokens (including reasoning and tool calls)
    reasoning_effort: ReasoningEffort = "NOT_GIVEN",  # Reasoning effort level: NOT_GIVEN, none, minimal, low, medium, high, and xhigh
    token_limit: int = -1,  # Max total tokens (including input and output) for the entire conversation.
    round_limit: int = -1,  # Max rounds of conversation (one round includes one assistant message and tool calls)
    debug_mode: bool = False,
  ):
    self.model = model
    self.history = []
    self.temperature = temperature
    self.top_p = top_p
    self.max_completion_tokens = max_completion_tokens
    self.reasoning_effort = reasoning_effort
    assert reasoning_effort in [
      "NOT_GIVEN",
      "none",
      "minimal",
      "low",
      "medium",
      "high",
      "xhigh",
    ], (
      f"Invalid reasoning_effort: {reasoning_effort}; "
      f"must be one of NOT_GIVEN, none, minimal, low, medium, high, and xhigh."
    )
    self.tools = ToolRegistry()
    self.debug_mode = debug_mode
    self.token_limit = token_limit
    self.round_limit = round_limit
    self.chat_stats = {
      "chat_rounds": 0,
      "input_tokens": 0,
      "cached_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0,
    }
    self.console = get_boxed_console(debug_mode=debug_mode)

  def is_debug_mode(self):
    return self.debug_mode

  def enable_debug_mode(self):
    self.debug_mode = True
    self.console = get_boxed_console(debug_mode=True)

  def disable_debug_mode(self):
    self.debug_mode = False
    self.console = get_boxed_console(debug_mode=False)

  def register_tool(self, tool: FuncToolBase, budget: int = sys.maxsize):
    self.console.print(
      "Registering tool: " + tool.name() + " (budget=" + str(budget) + ")"
    )
    self.tools.register(tool, budget)

  @abstractmethod
  def run(
    self,
    activated_tools: List[str],
    response_handler: ResponseHandler,
    tool_call_handler: ToolUseHandler,
  ) -> str:
    """
    Call to LLMs and execute all function calls until the model stops.
    """
    ...

  def get_history(self):
    return self.history

  def clear_history(self):
    self.history = []

  def append_system_message(self, content: str):
    content = remove_path_from_output(content)
    self.history.append(ChatMessageMessage(role="system", content=content))
    self.console.printb(title="System", message=content)

  def append_user_message(self, content: str):
    content = remove_path_from_output(content)
    self.history.append(ChatMessageMessage(role="user", content=content))
    self.console.printb(title="User", message=content)

  def append_assistant_message(self, content: str):
    self.history.append(ChatMessageMessage(role="assistant", content=content))
    self.console.printb(title="Assistant", message=content)

  def append_function_tool_call(self, call_id: str, name: str, arguments: str):
    self.history.append(
      ChatMessageFunctionCall(call_id=call_id, name=name, arguments=arguments)
    )
    self.console.printb(
      title=f"Function Call (id = {call_id})",
      message=f"{name}({arguments})",
    )

  def append_function_tool_call_output(self, call_id: str, result: str):
    self.history.append(ChatMessageFunctionCallOutput(call_id=call_id, output=result))
    self.console.printb(
      title=f"Function Call Output (id = {call_id})",
      message=result,
    )

  def perform_tool_call(self, tool_name: str, tool_args: dict) -> str:
    MAX_TOOL_CALL_OUTPUT_LINES = 500
    res = remove_path_from_output(self.tools.call(tool_name, **tool_args))
    lines = res.splitlines()
    if len(lines) > MAX_TOOL_CALL_OUTPUT_LINES:
      res = "\n".join(lines[:MAX_TOOL_CALL_OUTPUT_LINES])
      res += f"\n... (truncated, total {len(lines)} lines)"
    return res

  def _get_remaining_tools_from(self, activated_tools: List[str]) -> List[str]:
    # Ensure we always pass the correct tool names
    for tool in activated_tools:
      self.tools.get(tool)  # This will raise if the tool is not registered
    remaining = [
      self.tools.get(tool)
      for tool in self.tools.list(ignore_budget=False)
      if tool in activated_tools
    ]
    self.console.print(
      "Remaining tools: "
      + str(
        [
          f"{tool.name()}[{self.tools.get_remaining_budget(tool.name())}]"
          for tool in remaining
        ]
      )
    )
    return remaining

  @retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(3))
  def _completion_api_with_backoff(self, **kwargs):
    return self._completion_api(**kwargs)

  @abstractmethod
  def _completion_api(self, **kwargs):
    """
    Call the provider API to get the completion.
    """
    ...
