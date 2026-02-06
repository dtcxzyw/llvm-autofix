import json
import os
import shlex
import time
from argparse import ArgumentParser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

os.environ["MSWEA_SILENT_STARTUP"] = "1"  # Silent startup
os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = "3"  # Retry 3 times
import yaml
from minisweagent import Model
from minisweagent.agents.default import DefaultAgent, Submitted
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.run.utils.save import save_traj

from autofix.base.console import get_boxed_console
from autofix.llvm.lab_env import Environment as FixEnvironment
from autofix.llvm.llvm_helper import (
  get_first_failed_test,
  get_llvm_build_dir,
  git_execute,
  llvm_dir,
  pretty_render_log,
  set_llvm_build_dir,
)
from autofix.llvm.llvm_helper import (
  reset as reset_llvm,
)
from autofix.lms.agent import ReachRoundLimit, ReachTokenLimit
from autofix.lms.tool import FuncToolCallException
from autofix.mini import (
  ADDITIONAL_CMAKE_FLAGS,
  AGENT_MAX_CHAT_ROUNDS,
  AGENT_MAX_CONSUMED_TOKENS,
  ALLOW_MODIFY_ASSERTS,
  MAX_TCS_EDIT_AND_TEST,
  NoAvailablePatchFound,
  ReachToolBudget,
  RunStats,
)
from autofix.tools.test import TestTool

# TODO: remove duplicates with main.py
console = get_boxed_console(debug_mode=False)


def panic(msg: str):
  console.print(f"Error: {msg}", color="red")
  exit(1)


# TODO: add other tools that do not require permission
FORBIDDEN_TOOLS = [
  "which",
  "sudo",
  "rm",
  "curl",
  "wget",
  "git",
  "ssh",
  "scp",
  "ftp",
  "telnet",
  "ping",
  "traceroute",
  "nslookup",
  "dig",
  "nmap",
  "apt",
  "apt-get",
  "dpkg",
]


# TODO: Python etc can also edit files ...
EDITING_TOOLS = [
  "sed",
  "awk",
]


@dataclass
class Issue:
  type: str
  rep_path: str
  rep_code: str
  command: str
  symptom: str

  def as_dict(self) -> dict:
    return {
      "issue_type": self.type,
      "issue_rep_path": self.rep_path,
      "issue_rep_code": self.rep_code,
      "issue_command": self.command,
      "issue_symptom": self.symptom,
    }


class MyModel(LitellmModel):
  def __init__(self, model: str, *, token_limit=-1, round_limit=-1):
    super().__init__(
      model_name=model,
      model_kwargs={
        "custom_llm_provider": "openai",
        "api_base": os.environ.get("LLVM_AUTOFIX_LM_API_ENDPOINT"),
        "api_key": os.environ.get("LLVM_AUTOFIX_LM_API_KEY"),
        "temperature": 0,
        "top_p": 0.95,
        "max_completion_tokens": 4096,
        "drop_params": True,
      },
      cost_tracking="ignore_errors",  # Ignore cost tracking errors, we have our own
    )
    self.token_limit = token_limit
    self.round_limit = round_limit
    self.chat_stats = {
      "chat_rounds": 0,
      "input_tokens": 0,
      "cached_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0,
    }

  def _query(self, messages, **kwargs):
    if self.round_limit > 0 and self.chat_stats["chat_rounds"] >= self.round_limit:
      raise ReachRoundLimit()
    if self.token_limit > 0 and self.chat_stats["total_tokens"] >= self.token_limit:
      raise ReachTokenLimit()

    response = super()._query(messages, **kwargs)

    console.print(
      f"Executing round #{self.chat_stats['chat_rounds']}, chat statistics so far: {self.chat_stats}"
    )
    self.chat_stats["chat_rounds"] += 1
    usage = getattr(response, "usage", None)
    if usage:
      self.chat_stats["input_tokens"] += usage.prompt_tokens
      if usage.prompt_tokens_details:
        self.chat_stats["cached_tokens"] += usage.prompt_tokens_details.cached_tokens
      self.chat_stats["output_tokens"] += usage.completion_tokens
      self.chat_stats["total_tokens"] += usage.total_tokens

    return response

  def query(self, messages, **kwargs):
    console.printb(message=messages[-1]["content"], title=messages[-1]["role"])
    response = super().query(messages, **kwargs)
    console.printb(message=response["content"], title="assistant")
    return response


class MyEnvironment(LocalEnvironment):
  def __init__(self, *, cwd: str):
    super().__init__(cwd=cwd)
    self.shim_path = os.path.join("/", "tmp", "mswe_myenv_shim.sh")
    self._create_shim()

  def _create_shim(self):
    # TODO: How to defend the models from accessing /usr/bin/xxx directly?
    # Shim script to set up the execution environment for mini-swe-agent
    shim_content = "#!/bin/bash\n"
    for cmd in FORBIDDEN_TOOLS:
      shim_content += f"""
{cmd}() {{
  echo "Error: You do not have perssion to access the command '{cmd}'."
  return 1
}}
"""
    with open(self.shim_path, "w") as f:
      f.write(shim_content)
    os.chmod(self.shim_path, 0o755)

  def execute(
    self, command: str, cwd: str = "", *, timeout: int | None = None
  ) -> dict[str, str]:
    command = shlex.join(["bash", "-c", f". {self.shim_path} && {command}"])
    return super().execute(command, cwd, timeout=timeout)


class MyAgent(DefaultAgent):
  def __init__(self, model: Model, stats: RunStats, workdir: str) -> None:
    super().__init__(
      model=MyModel(
        model=model,
        token_limit=AGENT_MAX_CONSUMED_TOKENS,
        round_limit=AGENT_MAX_CHAT_ROUNDS,
      ),
      env=MyEnvironment(cwd=workdir),
      # IMPORTANT: Configurations except for `agent` should be configured programmatically.
      **yaml.safe_load(
        Path(
          os.path.join(os.environ.get("LLVM_AUTOFIX_HOME_DIR"), "autofix", "mswe.yaml")
        ).read_text()
      )["agent"],
    )
    self.stats = stats
    self.issue = None
    self.fixenv = None
    self.tester = None
    self.test_budget = MAX_TCS_EDIT_AND_TEST
    self.edit_budget = MAX_TCS_EDIT_AND_TEST

  def setup_llvm(self, issue: str, aggressive_testing: bool = False):
    console.print("Setting up the buggy LLVM environment ...")
    self.issue = issue
    set_llvm_build_dir(os.path.join(get_llvm_build_dir(), issue))
    self.fixenv = FixEnvironment(
      issue,
      base_model_knowledge_cutoff="2023-12-31Z",
      additional_cmake_args=ADDITIONAL_CMAKE_FLAGS,
      max_build_jobs=os.environ.get("LLVM_AUTOFIX_MAX_BUILD_JOBS"),
      use_entire_regression_test_suite=aggressive_testing,
    )
    bug_type = self.fixenv.get_bug_type()
    if bug_type not in [
      "crash",
      "miscompilation",
    ]:  # We only support crash and miscompilation for now
      panic(f"Unsupported bug type: {bug_type}")
    try:
      self.fixenv.reset()
    except Exception as e:
      console.print(
        f"Warning: Failed to reset HEAD to {self.fixenv.get_base_commit()}: {e}",
        color="yellow",
      )
      console.print("Sync the repository and try again.", color="yellow")
      reset_llvm("main")
      git_execute(["pull", "origin", "main"])
      try:
        self.fixenv.reset()
      except Exception as e:
        panic(f"Failed to reset HEAD to {self.fixenv.get_base_commit()}: {e}")
    self.tester = TestTool(self.fixenv, allow_alt_asserts=ALLOW_MODIFY_ASSERTS)

  def setup_issue(self) -> Issue:
    console.print("Building LLVM and try reproducing the issue ...")
    check_failed, check_log = self.fixenv.check_fast()
    if check_failed:
      panic(f"Failed to build or reproduce the issue. Please try again.\n\n{check_log}")

    reprod_data = get_first_failed_test(check_log)
    reprod_args = reprod_data["args"]
    reprod_code = reprod_data["body"]
    reprod_log = pretty_render_log(reprod_data["log"])
    console.print("Issue reproduced successfully.")

    reprod_file = os.path.join("/", "tmp", f"test_{self.issue}.ll")
    with open(reprod_file, "w") as fou:
      fou.write(reprod_code)

    return Issue(
      type=self.fixenv.get_bug_type(),
      rep_path=reprod_file,
      rep_code=reprod_code,
      command=" ".join(
        list(
          filter(
            lambda x: x != "",
            reprod_args.replace("< ", " ")
            .replace("%s", reprod_file)
            .replace("2>&1", "")
            .replace("'", "")
            .replace('"', "")
            .replace("opt", os.path.join(get_llvm_build_dir(), "bin", "opt"), 1)
            .strip()
            .split(" "),
          )
        )
      ),
      symptom=reprod_log,
    )

  def _test_submission(self) -> Optional[str]:
    # Save the test trajectory
    patch = self.fixenv.dump_patch()
    self.stats.test_traj.append(patch)
    try:
      res = self.tester.call()
    except FuncToolCallException as e:
      return f"FAILURE\n\n{e}"  # Return the error message
    if res == "<success>":
      # We are successful, save the patch
      self.stats.patch = patch
      return None  # Success
    return res  # Return the error message

  def execute_action(self, action: dict) -> dict:
    if self.test_budget == 0:
      raise ReachToolBudget("test")
    if self.edit_budget == 0:
      raise ReachToolBudget("edit")
    tool = (action["action"] or "").split(" ", maxsplit=1)[0]
    if tool in FORBIDDEN_TOOLS:
      return {
        "output": "Error: You do not have permission to use this tool.",
        "returncode": 1,
      }
    if tool == "submit-patch":
      self.test_budget -= 1
      errmsg = self._test_submission()
      if errmsg:
        return {"output": errmsg, "returncode": 1}
      raise Submitted("Patch generated successfully.")
    if tool in EDITING_TOOLS:
      # Fix: Our edit tools sed/awk may also not change anything
      self.edit_budget -= 0  # We do not decrease the budget for now
    return super().execute_action(action)

  def step(self):
    console.print(
      f"Remaining tools: [edit[{self.edit_budget}], test[{self.test_budget}]]"
    )
    return super().step()


def parse_args():
  parser = ArgumentParser(description="mini-swe-agent (llvm-autofix)")
  parser.add_argument(
    "--issue",
    type=str,
    required=True,
    help="The issue ID to fix.",
  )
  parser.add_argument(
    "--model",
    type=str,
    required=True,
    help="The LLM model to use for the agent.",
  )
  parser.add_argument(
    "--stats",
    type=str,
    default=None,
    help="Path to save the generation statistics as a JSON file (default: None).",
  )
  parser.add_argument(
    "--debug",
    action="store_true",
    default=False,
    help="Enable debug mode for more verbose output (default: False).",
  )
  parser.add_argument(
    "--aggressive-testing",
    action="store_true",
    default=False,
    help="Use all Transforms and Analysis tests for testing patches (default: False).",
  )
  return parser.parse_args()


def main():
  if os.environ.get("LLVM_AUTOFIX_HOME_DIR") is None:
    panic("The llvm-autofix environment has not been brought up.")

  args = parse_args()

  if args.debug:
    global console
    console = get_boxed_console(debug_mode=True)

  if args.stats:
    if Path(args.stats).exists():
      panic(f"Stats file {args.stats} already exists.")

  try:
    stats = RunStats(command=vars(args))
    agent = MyAgent(args.model, stats, workdir=llvm_dir)
    agent.setup_llvm(args.issue, aggressive_testing=args.aggressive_testing)
    issue = agent.setup_issue()
    stats.total_time_sec = time.time()
    console.print("Starting to fix the issue ...")
    exit_status, result = agent.run(
      "",
      **issue.as_dict(),
      forbidden_tools=", ".join(FORBIDDEN_TOOLS),
      workdir=llvm_dir,
    )
    if not stats.patch:
      raise NoAvailablePatchFound("All efforts tried yet no available patches found.")
    if not agent.fixenv.use_entire_regression_test_suite:
      console.print("Post-validating the generated patch ...")
      agent.fixenv.use_entire_regression_test_suite = True
      passed, errmsg = agent.fixenv.check_regression()
      if passed:
        passed, errmsg = agent.fixenv.check_regression_diff()
      agent.fixenv.use_entire_regression_test_suite = False
      if not passed:
        stats.patch = None
        console.printb(title="Post-validation", message=errmsg)
        raise NoAvailablePatchFound("Post validation failed")
      console.print("Passed")
    extra_info = {}
  except Exception as e:
    import traceback

    stats.error = type(e).__name__
    stats.errmsg = str(e)
    stats.traceback = traceback.format_exc()

    exit_status = stats.error
    result = stats.errmsg
    extra_info = {"traceback": stats.traceback}

    raise e
  finally:
    stats.chat_rounds = agent.model.chat_stats["chat_rounds"]
    stats.input_tokens = agent.model.chat_stats["input_tokens"]
    stats.output_tokens = agent.model.chat_stats["output_tokens"]
    stats.cached_tokens = agent.model.chat_stats["cached_tokens"]
    stats.total_tokens = agent.model.chat_stats["total_tokens"]
    stats.total_time_sec = time.time() - stats.total_time_sec
    if args.stats:
      with open(args.stats, "w") as fou:
        json.dump(stats.as_dict(), fou, indent=2)
      console.print(f"Generation statistics saved to {args.stats}.")
      agent.model.config.model_kwargs["api_base"] = "hidden"
      agent.model.config.model_kwargs["api_key"] = "hidden"
      save_traj(
        agent,
        Path(args.stats).with_suffix(".traj.json"),
        exit_status=exit_status,
        result=result,
        extra_info=extra_info,
      )

  console.print("Final Patch")
  console.print("-----------")
  console.print(stats.patch)
  console.print("Reference Patch")
  console.print("---------------")
  console.print(agent.fixenv.get_reference_patch())
  console.print("Statistics")
  console.print("----------")
  console.print(json.dumps(stats.as_dict(), indent=2))


if __name__ == "__main__":
  main()
