# Implementation of Claude-based agents.

import json
import os
from typing import List

from anthropic import Anthropic, omit

from autofix.lms.agent import (
  AgentBase,
  ChatMessageMessage,
  ReachRoundLimit,
  ReachTokenLimit,
  ReasoningEffort,
  ResponseHandler,
  ToolUseHandler,
)


class ClaudeAgent(AgentBase):
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
      self.reasoning_effort = omit
      self.thinking = omit
    elif self.reasoning_effort == "none":
      self.thinking = "disabled"
    else:
      self.thinking = "adaptive"
    api_key = os.environ.get("LLVM_AUTOFIX_LM_API_KEY")
    base_url = os.environ.get("LLVM_AUTOFIX_LM_API_ENDPOINT") or None
    self.client = Anthropic(api_key=api_key, base_url=base_url)

  def run(
    self,
    activated_tools: List[str],
    response_handler: ResponseHandler,
    tool_call_handler: ToolUseHandler,
  ) -> str:
    messages = []
    for message in self.history:
      if isinstance(message, ChatMessageMessage):
        messages.append(
          {
            "role": message.role,
            "content": message.content,
          }
        )
    while self.round_limit <= 0 or self.chat_stats["chat_rounds"] <= self.round_limit:
      self.console.print(
        f"Executing round #{self.chat_stats['chat_rounds']}, chat statistics so far: {self.chat_stats}"
      )
      self.chat_stats["chat_rounds"] += 1
      if self.token_limit > 0 and self.chat_stats["total_tokens"] >= self.token_limit:
        raise ReachTokenLimit()

      remaining_tools = self._get_remaining_tools_from(activated_tools)
      response = self._completion_api_with_backoff(
        model=self.model,
        messages=messages,
        temperature=self.temperature,
        top_p=self.top_p,
        max_tokens=self.max_completion_tokens,
        thinking=self.thinking,
        tools=(
          [tool.spec().render_in_claude_format() for tool in remaining_tools] or omit
        ),
        tool_choice={
          "type": "auto",
          "disable_parallel_tool_use": True,
        },
      )

      # Update tokens that we have consumed
      self.chat_stats["input_tokens"] += response.usage.input_tokens
      self.chat_stats["cached_tokens"] += response.usage.cache_read_input_tokens
      self.chat_stats["output_tokens"] += response.usage.output_tokens
      self.chat_stats["total_tokens"] += (
        response.usage.input_tokens + response.usage.output_tokens
      )
      messages.append({"role": "assistant", "content": response.content})

      if response.stop_reason == "tool_use":
        for content in response.content:
          if content.type == "text":
            self.append_assistant_message(content.text)
          elif content.type == "tool_use":
            name = content.name
            call_id = content.id
            args = content.input
            args_text = json.dumps(args)
            self.append_function_tool_call(
              call_id=call_id,
              name=name,
              arguments=args_text,
            )
            result = self.perform_tool_call(name, args)
            cont_exec, result = tool_call_handler(name, args_text, result)
            if not cont_exec:
              self.append_user_message(result)
              return result
            self.append_function_tool_call_output(call_id=call_id, result=result)
            messages.append(
              {
                "role": "user",
                "content": [
                  {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": result,
                  }
                ],
              }
            )
      elif response.stop_reason == "stop_sequence":
        self.append_assistant_message(response.content[0].text)
        cont_exec, content = response_handler(response.content[0].text)
        if cont_exec:
          self.append_user_message(content)
          messages.append(
            {
              "role": "user",
              "content": content,
            }
          )
        else:
          return content

    raise ReachRoundLimit()

  def _completion_api(self, **kwargs):
    return self.client.messages.create(**kwargs)
