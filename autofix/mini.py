import json
import os
import time
from argparse import ArgumentParser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import List, Optional, Tuple

from autofix import prompts
from autofix.base.console import get_boxed_console
from autofix.llvm.debugger import DebuggerBase, StackTrace
from autofix.llvm.gdb_support import GDB
from autofix.llvm.lab_env import Environment
from autofix.llvm.llvm import LLVM, Code, CodeSnippet
from autofix.llvm.llvm_helper import (
  get_first_failed_test,
  get_llvm_build_dir,
  git_execute,
  llvm_dir,
  pretty_render_log,
  remove_path_from_output,
  set_llvm_build_dir,
)
from autofix.llvm.llvm_helper import (
  reset as reset_llvm,
)
from autofix.lms.agent import AgentBase
from autofix.tools.code import CodeTool
from autofix.tools.debug import DebugTool
from autofix.tools.docs import DocsTool
from autofix.tools.edit import EditTool
from autofix.tools.eval import EvalTool
from autofix.tools.findn import FindNTool
from autofix.tools.langref import LangRefTool
from autofix.tools.listn import ListNTool
from autofix.tools.preview import PreviewTool
from autofix.tools.readn import ReadNTool
from autofix.tools.reset import ResetTool
from autofix.tools.ripgrepn import RipgrepNTool
from autofix.tools.stop import StopTool
from autofix.tools.test import TestTool

# - ===============================================
# - Agent configurations
# - ===============================================

# We restrict the agent to chat at most 500 rounds for each run
# and consume at most 5 million tokens among all runs.
AGENT_TEMPERATURE = 0
AGENT_TOP_P = 0.95
AGENT_MAX_COMPLETION_TOKENS = 8092
AGENT_REASONINT_EFFORT = "NOT_GIVEN"
AGENT_MAX_CHAT_ROUNDS = 500
AGENT_MAX_CONSUMED_TOKENS = 5_000_000
# We give context gathering tools more budget and restrict the models
# to be careful and think twice when they are editing and testing.
MAX_TCS_GET_CONTEXT = 250
MAX_TCS_EDIT_AND_TEST = 25
MAX_ROLS_PER_TC = 250
MIN_EDIT_POINT_LINES = 1
# We usually do not allow the agent to modify assertions in the code
ALLOW_MODIFY_ASSERTS = False
VALID_EDIT_POINT_REQUIREMENTS = (
  "+ NOTICE (on assertion failure): Assertion failures typically indicate earlier errors in execution. Assume all assertions are correct and investigate preceding code or conditions. Edit points can contain but cannot be limited to assertion statements."
  if not ALLOW_MODIFY_ASSERTS
  else ""
)
VALID_PATCH_REQUIREMENTS = (
  "+ Is valid and does not modify any assertions in the code."
  if not ALLOW_MODIFY_ASSERTS
  else "Is valid."
)

# - ================================================
# - LLVM settings
# - ================================================

ASSERTION_FUNCTION_LIST = [
  "__assert_fail",
  "__GI___assert_fail",
  "llvm::llvm_unreachable_internal",
  "llvm::report_fatal_error",
]

# NOTE: Patterns start with star will be passed into rbreak.
# FIXME: rbreak is slow. Use grep instead?
TRANSFORMATION_FUNCTION_LIST = [
  "Instruction::clone",
  "Instruction::replaceSuccessorWith",
  "Instruction::setSuccessor",
  "User::setOperand",
  "::eraseFromParent",
  "Use::set",
  "Use::operator=",
  "User::replaceUsesOfWith",
  "Value::replaceAllUsesWith",
  "InstCombiner::InsertNewInstBefore",
  "InstCombiner::InsertNewInstWith",
  "InstCombiner::replaceInstUsesWith",
  "InstCombiner::replaceOperand",
  "InstCombiner::replaceUse",
  "SwitchInst::addCase",
  "SwitchInst::removeCase",
  "BinaryOperator::swapOperands",
  "BranchInst::swapSuccessors",
  "BranchInst::setCondition",
  "BranchInst::setSuccessor",
  "SwitchInst::setCondition",
  "SwitchInst::setDefaultDest",
  "SwitchInst::setSuccessor",
  "CmpInst::swapOperands",
  "ICmpInst::swapOperands",
  "FCmpInst::swapOperands",
  "CmpInst::setPredicate",
  "PHINode::addIncoming",
  "PHINode::setIncomingValue",
  "PHINode::addIncoming",
  "*Inst::Create",
  "*Inst::operator new",
  "*BinaryOperator::Create",
  "*IRBuilderBase::Create",
]

COMPILATION_FLAGS = "-O0 -ggdb"
ADDITIONAL_CMAKE_FLAGS = [
  f"-DCMAKE_C_FLAGS_RELWITHDEBINFO={COMPILATION_FLAGS}",
  f"-DCMAKE_CXX_FLAGS_RELWITHDEBINFO={COMPILATION_FLAGS}",
]

# - ================================================
# - Statistis and output
# - ================================================

console = get_boxed_console(debug_mode=False)


def panic(msg: str):
  console.print(f"Error: {msg}", color="red")
  exit(1)


@dataclass
class RunStats:
  # Command to run autofix
  command: dict
  # The generated path for successful runs
  patch: Optional[str] = None
  # The error message for failed runs
  error: Optional[str] = None
  errmsg: Optional[str] = None
  traceback: Optional[str] = None
  # Agent interaction stats
  input_tokens: int = 0
  output_tokens: int = 0
  cached_tokens: int = 0
  total_tokens: int = 0
  chat_rounds: int = 0
  total_time_sec: float = 0.0
  # Fix stats
  trans_point: Tuple[str, str] = ("<not-provided>", "<not-provided>")
  edit_points: List[Tuple[str, int, int]] = field(
    default_factory=lambda *_, **__: [("<not-provided>", -1, -1)]
  )
  reason_thou: str = "<not-provided>"
  test_traj: List[str] = field(
    default_factory=list
  )  # Trajectories of patches ever tried during testing

  def as_dict(self) -> dict:
    return asdict(self)


# - ===============================================
# - Agent's main code
# - ==============================================


class NoAvailablePatchFound(Exception):
  pass


class ReachToolBudget(Exception):
  pass


@dataclass
class Reproducer:
  issue_id: str  # The issue ID
  file_path: Path  # The path to the LLVM IR reproducer file
  command: List[str]  # The command to run the reproducer
  symptom: str  # The observed symptom when running the reproducer
  raw_cmd: str  # The original, unpolished command line to run the reproducer


@dataclass
class PatchEditPoint:
  """A class to represent an edit point in a patch."""

  start: int
  end: int
  file: Path

  def as_tuple(self) -> Tuple[str, int, int]:
    return (str(self.file), self.start, self.end)

  def __str__(self) -> str:
    return f"{self.file}:{self.start}-{self.end}"


def ensure_tools_available(agent: AgentBase, tools: List[str]):
  available_tools = agent.tools.list(ignore_budget=False)
  unavailable_tools = []
  for tool in tools:
    if tool not in available_tools:
      unavailable_tools.append(tool)
  if len(unavailable_tools) > 0:
    raise ReachToolBudget(f"Tools [{', '.join(unavailable_tools)}] are out of budget.")


def extract_code_snippet(
  commit: str,
  file_rel_path: Path,
  file_path: Path,
  start_line: int,
  end_line: int,
  *,
  sourroundings: int = 0,
) -> str:
  if not file_path.exists():
    raise ValueError(f"File {file_path} does not exist.")
  git_execute(["checkout", commit, str(file_rel_path)])
  with file_path.open("r") as f:
    lines = [""] + f.readlines()
  if start_line < 1 or end_line < 1:
    raise ValueError(
      f"Line numbers {start_line} and {end_line} must be positive integers."
    )
  if start_line > end_line:
    raise ValueError(
      f"Start line {start_line} cannot be greater than end line {end_line}."
    )
  if max(start_line, end_line) >= len(lines):
    raise ValueError(
      f"Line numbers {start_line} and {end_line} are out of bounds for file {file_path}"
    )
  start_line = max(1, start_line - sourroundings)
  end_line = min(len(lines) - 1, end_line + sourroundings)
  code = CodeSnippet()
  for line in range(start_line, end_line + 1):
    code.add_line(Code(line, lines[line].rstrip()))
  return code.render()


EDIT_POINT_FORMAT = """\
```cpp
// {file}:{start}-{end}
{code}
```\
"""


def patch_and_fix(
  edit_points: List[PatchEditPoint],
  reason_info: str,
  *,
  rep: Reproducer,
  agent: AgentBase,
  fixenv: Environment,
  llvm: LLVM,
  stats: RunStats,
) -> Optional[str]:
  console.print(
    f"Generating patch for edit points: {', '.join([str(ep) for ep in edit_points])} ..."
  )

  # Reset the LLVM repo to the base commit
  git_execute(["checkout", "."])
  agent.clear_history()

  # Fix: There're chances that the model proposes incorrect edit points
  formatted_edit_points = []
  for ep in edit_points:
    try:
      formatted_edit_points.append(
        EDIT_POINT_FORMAT.format(
          file=ep.file,
          start=ep.start,
          end=ep.end,
          code=extract_code_snippet(
            fixenv.base_commit,
            ep.file,
            llvm.repo / ep.file,
            ep.start,
            ep.end,
            sourroundings=5,
          ),
        )
      )
    except ValueError as e:
      console.print(
        f"Warning: Skip: Failed to extract code snippet for edit point {ep}: {e}",
        color="yellow",
      )

  # Generate the patch according to the information and proposed edit points
  agent.append_user_message(
    prompts.PROMPT_REPAIR.format(
      reprod_code=rep.file_path.read_text(),
      issue_symptom=rep.symptom,
      reason_info=reason_info,
      edit_points="\n".join(formatted_edit_points) or "<not-found>",
      valid_patch_requirements=VALID_PATCH_REQUIREMENTS,
    )
  )

  def response_callback(_: str) -> Tuple[bool, str]:
    ensure_tools_available(agent, ["test", "edit"])
    return True, (
      "Error: You are not calling any tool or your tool call format is incorrect. "
      "You should always continue with tool calling and correct tool call format. "
      "Please continue."
      " If you are done, call the `test` tool to see if it passes the tests."
      " If you already called the `test` tool, please check the feedback, adjust the patch, and try again."
    )

  def tool_call_callback(name: str, _: str, res: str) -> Tuple[bool, str]:
    ensure_tools_available(agent, ["test", "edit"])
    if name == "test":
      patch = fixenv.dump_patch()
      stats.test_traj.append(patch)
      if res == "<success>":
        return False, patch  # Stop the process and return the valid patch
    return True, res  # Continue the process

  return agent.run(
    # TODO: Remove the hardcoded tool names
    [
      # Exlore codebase tools
      "list",
      "read",
      "find",
      "ripgrep",
      "code",
      # Documentation tools
      "docs",
      "langref",
      # Edit tools
      "edit",
      # Test tools
      "reset",
      "test",
      "preview",
    ],
    response_handler=response_callback,
    tool_call_handler=tool_call_callback,
  )


def run_mini_agent(
  rep: Reproducer,
  *,
  # Opt information
  opt_pass: str,
  opt_cmd: str,
  opt_log: str,
  # Debugger information
  debugger: DebuggerBase,
  backtrace: StackTrace,
  # Agent used
  agent: AgentBase,
  # LLVM and fix environem
  fixenv: Environment,
  llvm: LLVM,
  # Statistics
  stats: RunStats,
) -> Optional[str]:
  agent.clear_history()

  #####################################################
  # The agent runs by:
  # 1. Analyze the issue first to reason about the root cause and propose potential edit points.
  # 2. Leverage the provided information to guide the patch generation.
  #####################################################

  # Reason about the root cause and propose potential edit points
  console.print("Analyzing the issue to gather required information ...")
  agent.append_user_message(
    prompts.PROMPT_REASON.format(
      pass_name=opt_pass,
      reprod_code=rep.file_path.read_text(),
      issue_symptom=rep.symptom,
      opt_cmd=opt_cmd,
      opt_log=opt_log,
      trans_point_file=str(backtrace[-1].file),
      trans_point_func=backtrace[-1].func,
      trans_point_stack="\n".join([str(it) for it in reversed(backtrace)]),
      min_edit_point_lines=MIN_EDIT_POINT_LINES,
      valid_edit_point_requirements=VALID_EDIT_POINT_REQUIREMENTS,
    )
  )

  def response_handler(_: str) -> Tuple[bool, str]:
    ensure_tools_available(agent, ["stop"])
    return True, (
      "Error: You are not calling any tool or your tool call format is incorrect. "
      "You should always continue with tool calling and correct tool call format. "
      "Please continue."
      " If you are done, call the `stop` tool with the edit points."
      " If you already called the `stop` tool, please check the format and try again."
    )

  def tool_call_handler(name: str, _: str, res: str) -> Tuple[bool, str]:
    ensure_tools_available(agent, ["stop"])
    if name != "stop":
      return True, res  # Continue the process
    try:
      # The stop tool returns a parseable JSON string
      json.loads(res)
    except Exception:
      return (True, res)  # Continue the process with an error message
    return False, res  # Stop the process with the result

  response = agent.run(
    # TODO: Remove the hardcoded tool names
    [
      # Explore codebase tools
      "list",
      "read",
      "find",
      "ripgrep",
      "code",
      # Documentation tools
      "docs",
      "langref",
      # Debugging tools
      "debug",
      "eval",
      # Stop tool to finish the analysis
      "stop",
    ],
    response_handler=response_handler,
    tool_call_handler=tool_call_handler,
  )

  # Parse the response to get potential edit points
  response = json.loads(response)
  edit_points = response.get("edit_points", [])
  reasoning_thoughts = response.get("thoughts", "")
  fixed_edit_points = []

  for edit_point in edit_points:
    try:
      edit_point_start, edit_point_end, edit_point_file = edit_point
      if not is_interesting_file(edit_point_file):
        console.print(f"Ignore non-interesting file {edit_point_file} for now.")
        continue
      edit_point_file = Path(edit_point_file)
      if edit_point_file.is_absolute():
        edit_point_file = edit_point_file.relative_to(llvm.repo)
      fixed_edit_points.append(
        PatchEditPoint(int(edit_point_start), int(edit_point_end), edit_point_file)
      )
    except Exception as e:
      console.print(
        f"WARNING: skip edit point {edit_point} due to parse failure: {e}",
        color="yellow",
      )

  # if len(fixed_edit_points) == 0:
  #   console.print("No valid edit points found in the response.")
  #   return None  # We are not able to proceed without interesting edit points

  stats.reason_thou = reasoning_thoughts
  stats.edit_points = [ep.as_tuple() for ep in fixed_edit_points]

  # Generate a patch and fix the issue according to the information
  return patch_and_fix(
    fixed_edit_points,
    reasoning_thoughts,
    rep=rep,
    agent=agent,
    fixenv=fixenv,
    llvm=llvm,
    stats=stats,
  )


def is_interesting_file(filename: str) -> bool:
  if "llvm/ADT" in filename or "llvm/Support" in filename:
    return False
  if filename.endswith(".cpp"):
    return True
  # This is not an always-safe operation (some bugs may happen in functions defined in header files)
  if filename.endswith(".h"):
    # Avoid modifying llvm/IR files to reduce the rebuild time.
    return "llvm/Transforms" in filename or "llvm/Analysis" in filename
  return False


def prepare_debugger(
  rep: Reproducer, *, llvm: LLVM, fixenv: Environment
) -> Tuple[DebuggerBase, StackTrace]:
  debugger = GDB(rep.command)

  # Pause the debugger at the first transformation point or crash point
  bug_type = fixenv.get_bug_type()
  breakpints = (
    ASSERTION_FUNCTION_LIST if bug_type == "crash" else TRANSFORMATION_FUNCTION_LIST
  )
  cached_breakpoint_file = os.path.join(
    get_llvm_build_dir(), "autofix_breakpoint_cache.txt"
  )  # We'll cache the breakpoint to speed up future runs
  cached_breakpoint = None
  if os.path.exists(cached_breakpoint_file):
    with open(cached_breakpoint_file, "r") as fin:
      cached_breakpoint = fin.read().strip()
    if cached_breakpoint:
      console.print(f"Using the cached breakpoint function: {cached_breakpoint}")
      breakpints = [cached_breakpoint]
  console.print("Reproducing the issue with debugger...")
  backtrace, breakpoint = debugger.run(
    llvm.repo,
    breakpints,
    bug_type == "miscompilation",
    frame_limit=25,  # 25 frames should be enough
  )
  if not cached_breakpoint and breakpoint:
    console.print(f"The cached breakpoint function: {breakpoint}")
    with open(cached_breakpoint_file, "w") as fou:
      fou.write(breakpoint)

  if bug_type == "miscompilation":
    backtrace.pop()  # Pop out the topmost transformation function
    debugger.select_frame(
      backtrace[-1].func
    )  # Select the topmost frame for miscompilations

  return debugger, backtrace


def run_opt(rep: Reproducer, *, llvm: LLVM, fixenv: Environment, backtrace: StackTrace):
  # We get the transformation pass and its bound analysis passes
  opt_pass, analy_pass = llvm.resolve_pass_name(" ".join(rep.command))
  console.print(f"Transform pass: {opt_pass}")
  console.print(f"Analysis passes: {', '.join([str(ap) for ap in analy_pass])}")

  # We run opt with the reproducer to collect verbose log
  opt_args = rep.command[1:] + llvm.resolve_pass_opts(opt_pass)
  for idx in range(len(opt_args)):
    if opt_args[idx].count("-passes="):
      opt_args[idx] = "--passes=" + ",".join(analy_pass + [opt_pass])
  opt_args.remove(str(rep.file_path))
  for ap in analy_pass:
    opt_args += llvm.resolve_pass_opts(ap)
  opt_args.append(
    "--debug-only="
    + ",".join(llvm.resolve_debug_types(set([frame.file for frame in backtrace])))
  )

  bug_type = fixenv.get_bug_type()

  console.print("Running opt with the reproducer to collect verbose log ...")
  console.print(f"Options: {opt_args}")
  # TODO: `lli` leverages return code to indicate the success or failure, rather than the output.
  opt_cmd, opt_log = llvm.run_opt(
    rep.file_path,
    opt_args,
    check=bug_type != "crash",
    # Run opt with the reproducer and useful options
    env={
      "LLVM_DISABLE_CRASH_REPORT": "1",
      "LLVM_DISABLE_SYMBOLIZATION": "1",
    },
  )
  if bug_type == "crash" and "PLEASE submit a bug report to " in opt_log:
    # Ignore the stack trace from the crash report
    opt_log = opt_log[: opt_log.find("PLEASE submit a bug report to ")]
  opt_log = remove_path_from_output(opt_log)
  console.printb(title="Opt Verbose Log", message=f"$ {opt_cmd}\n{opt_log}")

  return opt_pass, opt_cmd, opt_log


def get_tool_list(fixenv: Environment, llvm: LLVM, debugger: DebuggerBase):
  # The list of our tools and their call limits. 0 means allowing unlimited call.
  # TODO: Manage all tools with a ToolRegistry. Don't share budget across agents.
  return [
    # General tools
    (FindNTool(llvm_dir, n=MAX_ROLS_PER_TC), MAX_TCS_GET_CONTEXT),
    (RipgrepNTool(llvm_dir, n=MAX_ROLS_PER_TC), MAX_TCS_GET_CONTEXT),
    (ListNTool(llvm_dir, n=MAX_ROLS_PER_TC), MAX_TCS_GET_CONTEXT),
    (ReadNTool(llvm_dir, n=MAX_ROLS_PER_TC), MAX_TCS_GET_CONTEXT),
    # FIXME: Redesign the format of the edit tool to avoid the mismatch due to whitespaces
    (EditTool(llvm_dir), MAX_TCS_EDIT_AND_TEST),
    # LLVM-specific tools
    (CodeTool(llvm, debugger), MAX_TCS_GET_CONTEXT),
    (DocsTool(llvm, debugger), MAX_TCS_GET_CONTEXT),
    (LangRefTool(fixenv), MAX_TCS_GET_CONTEXT),
    # (OptTool(llvm), MAX_TCS_GET_CONTEXT),
    # (Alive2Tool(), MAX_TCS_GET_CONTEXT),
    (ResetTool(llvm_dir, fixenv.base_commit), MAX_TCS_EDIT_AND_TEST),
    (PreviewTool(fixenv), MAX_TCS_EDIT_AND_TEST),
    (TestTool(fixenv, ALLOW_MODIFY_ASSERTS), MAX_TCS_EDIT_AND_TEST),
    # Debugging tools
    (DebugTool(debugger), MAX_TCS_GET_CONTEXT),
    (EvalTool(debugger), MAX_TCS_GET_CONTEXT),
    # Stop the agent process
    (StopTool(llvm_dir, MIN_EDIT_POINT_LINES), MAX_TCS_GET_CONTEXT),
  ]


def autofix(
  rep: Reproducer,
  *,
  fixenv: Environment,
  agent: AgentBase,
  llvm: LLVM,
  stats: RunStats,
):
  # We use a debugger to help the agent understand the context
  debugger, backtrace = prepare_debugger(rep, llvm=llvm, fixenv=fixenv)
  stats.trans_point = backtrace[-1].as_tuple()

  # Run opt to get the optimization pass and the verbose log of the reproducer's execution
  # These information will help the agent understand the context better
  opt_pass, opt_cmd, opt_log = run_opt(
    rep, llvm=llvm, fixenv=fixenv, backtrace=backtrace.clone()
  )

  # The list of our tools and their call limits. 0 means allowing unlimited call.
  # TODO: Manage all tools with a ToolRegistry. Don't share budget across agents.
  tools = get_tool_list(fixenv, llvm, debugger)
  for to, th in tools:
    agent.register_tool(to, th)

  # Run the agent with all required information and tools
  return run_mini_agent(
    rep,
    opt_pass=opt_pass,
    opt_cmd=opt_cmd,
    opt_log=opt_log,
    debugger=debugger,
    backtrace=backtrace,
    agent=agent,
    fixenv=fixenv,
    llvm=llvm,
    stats=stats,
  )


def parse_args():
  parser = ArgumentParser(description="llvm-autofix (mini)")
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
    "--driver",
    type=str,
    default="openai",
    help="The LLM api to use (default: openai).",
    choices=["openai", "anthropic"],
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

  # Set up the console for output
  if args.debug:
    global console
    console = get_boxed_console(debug_mode=True)

  # Set up used LLMs and agents
  if args.driver == "openai":
    from autofix.lms.openai import OpenAIAgent

    agent_class = OpenAIAgent
  elif args.driver == "anthropic":
    from autofix.lms.anthropic import ClaudeAgent

    agent = ClaudeAgent
  else:
    panic(f"Unsupported LLM driver: {args.driver}")

  agent = agent_class(
    args.model,
    temperature=AGENT_TEMPERATURE,
    top_p=AGENT_TOP_P,
    max_completion_tokens=AGENT_MAX_COMPLETION_TOKENS,
    reasoning_effort=AGENT_REASONINT_EFFORT,
    token_limit=AGENT_MAX_CONSUMED_TOKENS,
    round_limit=AGENT_MAX_CHAT_ROUNDS,
    debug_mode=args.debug,
  )

  # Set up saved statistics and output
  stats_path = None
  if args.stats:
    stats_path = Path(args.stats)
    if stats_path.exists():
      panic(f"Stats file {stats_path} already exists.")

  # Set up the LLVM environment
  set_llvm_build_dir(os.path.join(get_llvm_build_dir(), args.issue))
  env = Environment(
    args.issue,
    base_model_knowledge_cutoff="2023-12-31Z",
    additional_cmake_args=ADDITIONAL_CMAKE_FLAGS,
    max_build_jobs=os.environ.get("LLVM_AUTOFIX_MAX_BUILD_JOBS"),
    use_entire_regression_test_suite=args.aggressive_testing,
  )

  bug_type = env.get_bug_type()
  if bug_type not in [
    "crash",
    "miscompilation",
  ]:  # We only support crash and miscompilation for now
    panic(f"Unsupported bug type: {bug_type}")

  console.print(f"Issue ID: {args.issue}")
  console.print(f"Issue Type: {bug_type}")
  console.print(f"Issue Commit: {env.get_base_commit()}")
  console.print(f"Issue Title: {env.get_hint_issue()['title']}")
  console.print(f"Issue Labels: {env.get_hint_issue()['labels']}")

  console.print("Checking out the issue's environment ...")
  try:
    env.reset()
  except Exception as e:
    console.print(
      f"Warning: Failed to reset HEAD to {env.get_base_commit()}: {e}", color="yellow"
    )
    console.print("Sync the repository and try again.", color="yellow")
    reset_llvm("main")
    git_execute(["pull", "origin", "main"])
    try:
      env.reset()
    except Exception as e:
      panic(f"Failed to reset HEAD to {env.get_base_commit()}: {e}")

  console.print("Building LLVM and try reproducing the issue ...")
  check_failed, check_log = env.check_fast()
  if check_failed:
    panic(f"Failed to build or reproduce the issue. Please try again.\n\n{check_log}")

  reprod_data = get_first_failed_test(check_log)
  reprod_args = reprod_data["args"]
  reprod_code = reprod_data["body"]
  reprod_log = pretty_render_log(reprod_data["log"])
  console.print("Issue reproduced successfully.")
  console.printb(title="Reproducer", message=reprod_code)
  console.printb(title="Reproducing Log", message=f"$ {reprod_args}\n{reprod_log}")

  # We successfully set up the environment and reproduce the issue.
  llvm = LLVM()
  with NamedTemporaryFile(
    mode="w", suffix=".ll", prefix="reprod_", delete=not args.debug
  ) as reprod_file:  # We keep the file for debugging if needed
    reprod_file.write(reprod_code)
    reprod_file.flush()

    reproducer = Reproducer(
      issue_id=args.issue,
      file_path=Path(reprod_file.name),
      command=[],
      symptom=reprod_log,
      raw_cmd=reprod_args,
    )
    reproducer.command = list(
      filter(
        lambda x: x != "",
        reprod_args.replace("< ", " ")
        .replace("%s", str(reproducer.file_path))
        .replace("2>&1", "")
        .replace("'", "")
        .replace('"', "")
        .replace("opt", str(llvm.opt), 1)
        .strip()
        .split(" "),
      )
    )

    # Start analyzing and repairing the issue
    stats = RunStats(command=vars(args))
    stats.total_time_sec = time.time()
    try:
      stats.patch = autofix(
        reproducer,
        fixenv=env,
        agent=agent,
        llvm=llvm,
        stats=stats,
      )
      if not stats.patch:
        raise NoAvailablePatchFound("All efforts tried yet no available patches found.")
      # Post validation when necessary
      if not env.use_entire_regression_test_suite:
        console.print("Post-validating the generated patch ...")
        env.use_entire_regression_test_suite = True
        passed, errmsg = env.check_regression()
        if passed:
          passed, errmsg = env.check_regression_diff()
        env.use_entire_regression_test_suite = False
        if not passed:
          stats.patch = None
          console.printb(title="Post-validation", message=errmsg)
          raise NoAvailablePatchFound("Post validation failed")
        console.print("Passed")
    except Exception as e:
      import traceback

      stats.error = type(e).__name__
      stats.errmsg = str(e)
      stats.traceback = traceback.format_exc()

      raise e
    finally:
      stats.chat_rounds = agent.chat_stats["chat_rounds"]
      stats.input_tokens = agent.chat_stats["input_tokens"]
      stats.output_tokens = agent.chat_stats["output_tokens"]
      stats.cached_tokens = agent.chat_stats["cached_tokens"]
      stats.total_tokens = agent.chat_stats["total_tokens"]
      stats.total_time_sec = time.time() - stats.total_time_sec
      if stats_path:
        with stats_path.open("w") as fout:
          json.dump(stats.as_dict(), fout, indent=2)
        console.print(f"Generation statistics saved to {stats_path}.")

    console.print("Final Patch")
    console.print("-----------")
    console.print(stats.patch)
    console.print("Reference Patch")
    console.print("---------------")
    console.print(env.get_reference_patch())
    console.print("Statistics")
    console.print("----------")
    console.print(json.dumps(stats.as_dict(), indent=2))


if __name__ == "__main__":
  main()
