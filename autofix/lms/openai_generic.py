# Implementation of OpenAI-compatible agents (Chat Completions API).
# Unlike the OpenAIAgent, this agent does not use the native OpenAI API for tool calling.
# Instead, it uses system prompts to describe the tools.

import os
from typing import Dict, List, Tuple

from openai import NOT_GIVEN, OpenAI

from autofix.lms.agent import ReasoningEffort
from autofix.lms.generic import GenericAgent


class OpenAIGenericAgent(GenericAgent):
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

  def _complete_chat(self, messages: List[Dict]) -> Tuple[str, str]:
    completion = self._completion_api_with_backoff(
      model=self.model,
      messages=messages,
      temperature=self.temperature,
      top_p=self.top_p,
      max_completion_tokens=self.max_completion_tokens,
      reasoning_effort=self.reasoning_effort,
      stream=True,
      stream_options={"include_usage": True},
    )

    reasoning_content = ""
    answer_content = ""

    for chunk in completion:
      # Update tokens that we have consumed
      if chunk.usage:
        if chunk.usage.prompt_tokens:
          self.chat_stats["input_tokens"] += chunk.usage.prompt_tokens
        if (
          chunk.usage.prompt_tokens_details
          and chunk.usage.prompt_tokens_details.cached_tokens
        ):
          self.chat_stats["cached_tokens"] += (
            chunk.usage.prompt_tokens_details.cached_tokens
          )
        if chunk.usage.completion_tokens:
          self.chat_stats["output_tokens"] += chunk.usage.completion_tokens
        if chunk.usage.total_tokens:
          self.chat_stats["total_tokens"] += chunk.usage.total_tokens

      # Get assistant's reasoning and answer from the response content
      if not chunk.choices:
        continue

      delta = chunk.choices[0].delta

      if hasattr(delta, "reasoning_content") and delta.reasoning_content is not None:
        reasoning_content += delta.reasoning_content

      if hasattr(delta, "content") and delta.content:
        answer_content += delta.content

    if (
      not reasoning_content
      and "<think>" in answer_content
      and "</think>" in answer_content
    ):
      think_begin = answer_content.index("<think>") + len("<think>")
      think_end = answer_content.rindex("</think>")
      reasoning_content = answer_content[think_begin:think_end]
      answer_content = (
        answer_content[: think_begin - len("<think>")]
        + "\n"
        + answer_content[think_end + len("</think>") :]
      )

    return reasoning_content, answer_content

  def _completion_api(self, **kwargs):
    return self.client.chat.completions.create(**kwargs)
