import json
import os
import shlex
import shutil
import threading
import time
from argparse import ArgumentParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from uuid import uuid4 as uuid

from autofix.llvm.lab_env import Environment as FixEnvironment
from autofix.llvm.llvm_helper import (
  get_first_failed_test,
  get_llvm_build_dir,
  llvm_alive_tv,
  llvm_dir,
  pretty_render_log,
  set_llvm_build_dir,
)
from autofix.lms.tool import FuncToolCallException
from autofix.mini import ADDITIONAL_CMAKE_FLAGS, NoAvailablePatchFound, RunStats
from autofix.tools.test import TestTool
from autofix.utils import cmdline

_TEST_SERVER_ADDR = "127.0.0.1"
_TEST_SERVER_PORT = 3921

LLVM_AUTOFIX_HOME_DIR = os.environ.get("LLVM_AUTOFIX_HOME_DIR")

PROMPT_TEMPLATE = """You are an expert LLVM developer. Please solve this LLVM issue:

------ BEGIN ISSUE ------
Type: {issue_type}

Reproducer (LLVM IR): ```bash
cat {issue_rep_path}
{issue_rep_code}
```

LLVM's Symptom: ```bash
{issue_command}
{issue_symptom}
```
------  END ISSUE  ------

## Recommended Workflow

This workflows should be done step-by-step so that you can iterate on your changes and any possible problems.

1. Analyze the codebase by finding and reading relevant files
2. Edit the source code to resolve the issue
3. Verify your fix works by running the `submit-patch` command
4. Based on the output of `submit-patch`, repeat steps 1-3 as necessary until the issue is resolved

## Necessary Information

- The root directory of the LLVM project is: {workdir}
- The build directory is: {builddir}
- You may use `submit-patch` command to test your patch or use existing LLVM tools. For example:
  - For miscompilation bugs, you may use alive2, a translation verification tool, which is available at: {llvm_alive_tv}.
  - For crash bugs, you may use the built opt.
  - You may also use built lli, llvm-lit, etc.

## Disallowed Behaviors

+ Accessing the Internet or any external resources. You should only rely on the information provided in the issue description and your existing knowledge to solve the issue.
+ Accessing the files other than LLVM source/build directories.
+ Using Git to checkout other commits in the LLVM repository. You should only modify the code based on the current state of the repository.
+ Modifying regression tests.
+ Using workarounds or temporary fixes that don't address the root cause of the issue. Your fix should be a proper solution to the problem. For example, modifying assertions in the code is not allowed. You should not weaken any assertion checks in the code. If you think an assertion is too strict and causes the issue, you should analyze why this assertion is there and find a way to fix the issue without removing or weakening the assertion.
"""


def panic(msg: str):
  print(f"Error: {msg}")
  exit(1)


def parse_args():
  parser = ArgumentParser(description="Wrapper of XXX CLI/Agent (llvm-autofix)")
  parser.add_argument(
    "--issue",
    type=str,
    required=True,
    help="The issue ID to fix.",
  )
  parser.add_argument(
    "--xcli",
    type=str,
    required=True,
    choices=["claudecode", "codex", "geminicli"],
    help="The XXX CLI/Agent to use for fixing the issue.",
  )
  parser.add_argument(
    "--model",
    type=str,
    default=None,
    help="The LLM model to use for the agent.",
  )
  parser.add_argument(
    "--stats",
    type=str,
    required=True,
    help="Path to save the generation statistics as a JSON file.",
  )
  parser.add_argument(
    "--aggressive-testing",
    action="store_true",
    default=False,
    help="Use all Transforms and Analysis tests for testing patches (default: False).",
  )
  return parser.parse_args()


def start_test_server(fixenv: FixEnvironment, stats: RunStats):
  """
  Start HTTP server to serve the test tool and return the commands to request the server.
  The server is started in a daemon thread on port _TEST_SERVER_ADDR:_TEST_SERVER_PORT and will call the test tool whenever receiving a POST request.
  Whenver received any POST request, the server should call the test tool and return the result.
  """

  tester = TestTool(fixenv, allow_alt_asserts=True)

  def do_test():
    # Save the test trajectory
    patch = fixenv.dump_patch()
    stats.test_traj.append(patch)
    try:
      res = tester.call()
    except FuncToolCallException as e:
      return f"FAILURE\n\n{e}"  # Return the error message
    if res == "<success>":
      # We are successful, save the patch
      stats.patch = patch
      return "SUCCESS"  # Success
    return res  # Return the error message

  class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
      result = do_test()
      self.send_response(200)
      self.send_header("Content-Type", "text/plain")
      self.end_headers()
      self.wfile.write(result.encode())

    def log_message(self, format, *args):
      pass  # Suppress request logging

  server = HTTPServer((_TEST_SERVER_ADDR, _TEST_SERVER_PORT), Handler)
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()

  script = f"""#!/bin/bash
RESULT=$(curl -s -X POST http://{_TEST_SERVER_ADDR}:{_TEST_SERVER_PORT}/)
if [ "$RESULT" = "SUCCESS" ]; then
  exit 0
else
  echo "$RESULT"
  exit 1
fi
"""
  return script, server


def ensure_xcli_exists(xcli: str):
  bin = {
    "claudecode": "claude",
  }.get(xcli, "unknown")
  if bin == "unknown":
    panic(f"Unsupported X-CLI: {xcli}")
  if not shutil.which(bin):
    panic(f"The `{bin}` command is not found.")


def render_xcli_command(
  xcli: str,
  *,
  prompt: str,
  session: Optional[str] = None,
  model: Optional[str] = None,
) -> str:
  # TODO: Output the trajectory in a structured format
  if xcli == "claudecode":
    model_arg = f"--model {model}" if model else ""
    session_arg = f"--session-id {session}" if session else ""
    return f"claude --dangerously-skip-permissions -p --output-format json {model_arg} {session_arg} {shlex.quote(prompt)}"
  # TODO: Support Codex and Gemini CLI
  raise ValueError(f"Unsupported X-CLI: {xcli}")


def save_xcli_trajectory(
  xcli: str, *, session: str, summary: str, stats: RunStats, stats_path: Path
):
  assert xcli == "claudecode", (
    f"Support for other X-CLI ({xcli}) has not been implemented"
  )

  sum_dict = json.loads(summary)
  # Update stats
  stats.chat_rounds = sum_dict["num_turns"]
  stats.output_tokens = sum_dict["usage"]["output_tokens"]
  stats.cached_tokens = (
    sum_dict["usage"]["cache_creation_input_tokens"]
    + sum_dict["usage"]["cache_read_input_tokens"]
  )
  stats.input_tokens = sum_dict["usage"]["input_tokens"] + stats.cached_tokens
  stats.total_tokens = stats.input_tokens + stats.output_tokens
  # Save the summary
  with stats_path.with_suffix(".summary.json").open("w") as fou:
    json.dump(sum_dict, fou, indent=2)

  # Find and save the trajectory
  proj_name = "-".join(str(Path(LLVM_AUTOFIX_HOME_DIR).resolve().absolute()).split("/"))
  # Trajectory of CC is saved at:
  # - ~/.claude/projects/{proj_name}/{session} or
  # ~/.claude/projects/{proj_name}/{session}.jsonl
  proj_dir = Path.home() / ".claude" / "projects" / proj_name

  traj_jsonl = proj_dir / (session + ".jsonl")
  if traj_jsonl.exists() and traj_jsonl.is_file():
    shutil.copy2(traj_jsonl, stats_path.with_suffix(".traj.jsonl"))
    return

  traj_dir = proj_dir / session
  if traj_dir.exists() and traj_dir.is_dir():
    shutil.copy2(traj_dir, stats_path.with_suffix(".traj"))
    return

  print(
    f"[WARNING] No trajectory file found for X-CLI {xcli}: neither {traj_jsonl} nor {traj_dir}"
  )


def main():
  if LLVM_AUTOFIX_HOME_DIR is None:
    panic("The llvm-autofix environment has not been brought up.")

  args = parse_args()

  ensure_xcli_exists(args.xcli)
  print(f"Preparing {args.xcli} command to fix the LLVM issue ...")

  stats_path = Path(args.stats).resolve().absolute()
  if stats_path.exists():
    panic(f"Stats file {args.stats} already exists.")

  issue = args.issue
  set_llvm_build_dir(os.path.join(get_llvm_build_dir(), issue))
  fixenv = FixEnvironment(
    issue,
    base_model_knowledge_cutoff="2023-12-31Z",
    additional_cmake_args=ADDITIONAL_CMAKE_FLAGS,
    max_build_jobs=os.environ.get("LLVM_AUTOFIX_MAX_BUILD_JOBS"),
    use_entire_regression_test_suite=args.aggressive_testing,
  )
  fixenv.reset()

  print("Building LLVM and try reproducing the issue ...")
  check_failed, check_log = fixenv.check_fast()
  if check_failed:
    panic(f"Failed to build or reproduce the issue. Please try again.\n\n{check_log}")

  reprod_data = get_first_failed_test(check_log)
  reprod_args = reprod_data["args"]
  reprod_code = reprod_data["body"]
  reprod_log = pretty_render_log(reprod_data["log"])
  print("Issue reproduced successfully.")
  reprod_file = os.path.join("/", "tmp", f"test_{issue}.ll")
  with open(reprod_file, "w") as fou:
    fou.write(reprod_code)

  prompt = PROMPT_TEMPLATE.format(
    issue_type=fixenv.get_bug_type(),
    issue_rep_path=reprod_file,
    issue_rep_code=reprod_code,
    issue_command=" ".join(
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
    issue_symptom=reprod_log,
    workdir=llvm_dir,
    builddir=get_llvm_build_dir(),
    llvm_alive_tv=llvm_alive_tv,
  )

  session = str(uuid())
  command = render_xcli_command(
    args.xcli,
    prompt=prompt,
    model=args.model,
    session=session,
  )
  print(f"Agent command prepared: {command[:80]} ...")

  # The generation statistics
  stats = RunStats(command=vars(args))

  # Start the test server in a daemon thread to serve the test tool
  test_commands, test_server = start_test_server(fixenv, stats)

  # Write submit-patch directly into a temp bin dir and add it to PATH
  tmp_bin = os.path.join("/", "tmp", "llvm-autofix-bin")
  os.makedirs(tmp_bin, exist_ok=True)
  submit_patch_script = os.path.join(tmp_bin, "submit-patch")
  with open(submit_patch_script, "w") as fou:
    fou.write(test_commands)
  os.chmod(submit_patch_script, 0o755)
  env = os.environ.copy()
  env["PATH"] = tmp_bin + ":" + env.get("PATH", "")

  # Run the agent command to generate patches and test them until a patch passes the test tool or all attempts are exhausted.
  print("Starting to fix the issue ...")
  stats.total_time_sec = time.time()
  try:
    summary = cmdline.check_output(command, timeout=1800, cwd=llvm_dir, env=env)
    save_xcli_trajectory(
      args.xcli, session=session, summary=summary, stats=stats, stats_path=stats_path
    )
    if not stats.patch:
      raise NoAvailablePatchFound("All efforts tried yet no available patches found.")
    if not fixenv.use_entire_regression_test_suite:
      print("Post-validating the generated patch ...")
      fixenv.use_entire_regression_test_suite = True
      passed, errmsg = fixenv.check_midend()
      if passed:
        passed, errmsg = fixenv.check_regression_diff()
      fixenv.use_entire_regression_test_suite = False
      if not passed:
        stats.patch = None
        print("Post-validation failed:", errmsg)
        raise NoAvailablePatchFound("Post validation failed")
      print("Passed")
  except Exception as e:
    import traceback

    stats.error = type(e).__name__
    stats.errmsg = str(e)
    stats.traceback = traceback.format_exc()

    raise e
  finally:
    test_server.shutdown()
    stats.total_time_sec = time.time() - stats.total_time_sec
    with stats_path.open("w") as fou:
      json.dump(stats.as_dict(), fou, indent=2)
    print(f"Generation statistics saved to {stats_path}.")

  print("\n\nFinal Patch")
  print("-----------")
  print(stats.patch)
  print("Reference Patch")
  print("---------------")
  print(fixenv.get_reference_patch())
  print("Statistics")
  print("----------")
  print(json.dumps(stats.as_dict(), indent=2))


if __name__ == "__main__":
  main()
