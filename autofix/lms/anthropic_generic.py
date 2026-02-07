import os
from typing import Dict, List, Tuple

from anthropic import Anthropic, omit

from autofix.lms.agent import (
  ReasoningEffort,
)
from autofix.lms.generic import GenericAgent


class ClaudeGenericAgent(GenericAgent):
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

  def _complete_chat(self, messages: List[Dict]) -> Tuple[str, str]:
    response = self._completion_api_with_backoff(
      model=self.model,
      messages=messages,
      temperature=self.temperature,
      top_p=self.top_p,
      max_tokens=self.max_completion_tokens,
      thinking=self.thinking,
      stream=False,
    )

    # Update tokens that we have consumed
    self.chat_stats["input_tokens"] += response.usage.input_tokens
    self.chat_stats["cached_tokens"] += response.usage.cache_read_input_tokens
    self.chat_stats["output_tokens"] += response.usage.output_tokens
    self.chat_stats["total_tokens"] += (
      response.usage.input_tokens + response.usage.output_tokens
    )

    # Get assistant's reasoning and answer from the response content
    reasoning_content = []
    answer_content = []

    for content in response.content:
      if content.type == "thinking":
        reasoning_content.append(content.text)
      elif content.type == "text":
        answer_content.append(content.text)

    return "\n".join(reasoning_content), "\n".join(answer_content)

  def _completion_api(self, **kwargs):
    return self.client.messages.create(**kwargs)
