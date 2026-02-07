# Implementation of OpenAI-compatible agents (Chat Completions API).

import json
import os
from typing import List

from openai import NOT_GIVEN, OpenAI
from tenacity import (
  retry,
  stop_after_attempt,
  wait_random_exponential,
)  # for exponential backoff

from autofix.lms.agent import (
  AgentBase,
  ChatMessageFunctionCall,
  ChatMessageFunctionCallOutput,
  ChatMessageMessage,
  ReachRoundLimit,
  ReachTokenLimit,
  ReasoningEffort,
  ResponseHandler,
  ToolUseHandler,
)


class OpenAIAgent(AgentBase):
  def __init__(
    self,
    model: str,
    *,
    temperature: float = 0,
    top_p: float = 0.95,
    max_completion_tokens: int = 8092,
    reasoning_effort: ReasoningEffort = "NOT_GIVEN",
    token_limit: int = -1,
    round_limit: int = -1,
    debug_mode: bool = False,
  ):
    super().__init__(
      model,
      temperature=temperature,
      top_p=top_p,
      max_completion_tokens=max_completion_tokens,
      reasoning_effort=reasoning_effort,
      token_limit=token_limit,
      round_limit=round_limit,
      debug_mode=debug_mode,
    )
    if self.reasoning_effort == "NOT_GIVEN":
      self.reasoning_effort = NOT_GIVEN
    api_key = os.environ.get("LLVM_AUTOFIX_LM_API_KEY")
    base_url = os.environ.get("LLVM_AUTOFIX_LM_API_ENDPOINT") or None
    self.client = OpenAI(api_key=api_key, base_url=base_url)

  def render_message_list(self) -> List[dict]:
    messages = []
    for message in self.history:
      if isinstance(message, ChatMessageMessage):
        messages.append(
          {
            "role": message.role,
            "content": message.content,
          }
        )
      elif isinstance(message, ChatMessageFunctionCall):
        messages.append(
          {
            "role": "assistant",
            "content": "",
            "tool_calls": [
              {
                "id": message.call_id,
                "function": {
                  "arguments": message.arguments,
                  "name": message.name,
                },
                "type": "function",
                "index": 0,
              }
            ],
          }
        )
      elif isinstance(message, ChatMessageFunctionCallOutput):
        messages.append(
          {
            "role": "tool",
            "tool_call_id": message.call_id,
            "content": message.output,
          }
        )

    return messages

  @retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(3))
  def _completion_with_backoff(self, **kwargs):
    return self.client.chat.completions.create(**kwargs)

  def run(
    self,
    activated_tools: List[str],
    response_handler: ResponseHandler,
    tool_call_handler: ToolUseHandler,
  ) -> str:
    while self.round_limit <= 0 or self.chat_stats["chat_rounds"] <= self.round_limit:
      self.console.print(
        f"Executing round #{self.chat_stats['chat_rounds']}, chat statistics so far: {self.chat_stats}"
      )
      self.chat_stats["chat_rounds"] += 1
      if self.token_limit > 0 and self.chat_stats["total_tokens"] >= self.token_limit:
        raise ReachTokenLimit()
      remaining_tools = self._get_remaining_tools_from(activated_tools)
      completion = self._completion_with_backoff(
        model=self.model,
        messages=self.render_message_list(),
        temperature=self.temperature,
        top_p=self.top_p,
        max_completion_tokens=self.max_completion_tokens,
        reasoning_effort=self.reasoning_effort,
        tools=(
          [tool.spec().render_in_openai_format() for tool in remaining_tools]
          or NOT_GIVEN
        ),
        tool_choice="auto",
        parallel_tool_calls=False,
      )

      if completion.usage:
        self.chat_stats["input_tokens"] += completion.usage.prompt_tokens
        if completion.usage.prompt_tokens_details:
          self.chat_stats["cached_tokens"] += (
            completion.usage.prompt_tokens_details.cached_tokens
          )
        self.chat_stats["output_tokens"] += completion.usage.completion_tokens
        self.chat_stats["total_tokens"] += completion.usage.total_tokens

      response = completion.choices[0].message

      if not response.tool_calls:
        # Handle normal response
        self.append_assistant_message(response.content)
        cont_exec, content = response_handler(response.content)
        if cont_exec:
          self.append_user_message(content)
          continue
        else:
          return content

      # Handle tool calls
      for tool_call in response.tool_calls:
        name = tool_call.function.name
        args = tool_call.function.arguments
        self.append_function_tool_call(
          call_id=tool_call.id,
          name=name,
          arguments=args,
        )
        arguments = json.loads(args)
        result = self.perform_tool_call(name, arguments)
        cont_exec, result = tool_call_handler(name, args, result)
        if not cont_exec:
          self.append_user_message(result)
          return result
        self.append_function_tool_call_output(call_id=tool_call.id, result=result)

    raise ReachRoundLimit()
