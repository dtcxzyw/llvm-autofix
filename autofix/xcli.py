import os
from argparse import ArgumentParser
from pathlib import Path

from autofix.llvm.lab_env import Environment as FixEnvironment
from autofix.llvm.llvm_helper import (
  get_first_failed_test,
  get_llvm_build_dir,
  llvm_alive_tv,
  llvm_dir,
  pretty_render_log,
  set_llvm_build_dir,
)
from autofix.mini import (
  ADDITIONAL_CMAKE_FLAGS,
)

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

Please analyze the issue and provide a fix. The fix should be done by modifying the LLVM source code.
Make sure the crash no longer happens with your fix, and the fix doesn't break the original regression tests.
After fixing the issue, please generate two files under the directory of this markdown file:
+ A patch file named "fix.patch" that contains the code changes you made to fix the issue.
+ A markdown file named "fix.md" that contains the detailed explanation of your fix, including the root cause of the issue, how you fixed it, and why your fix works.

The root directory of the LLVM project is: {workdir}
The build directory is: {builddir}
For miscompilation bugs, you are allowed to use alive2, a translation verification tool, which is available at: {llvm_alive_tv}.

**DO NOT**
+ Accessing the Internet or any external resources. You should only rely on the information provided in the issue description and your existing knowledge to solve the issue.
+ Accessing the files other than the ones you generated (fix.patch and fix.md) and LLVM source/build directories.
+ Using Git to checkout other commits in the LLVM repository. You should only modify the code based on the current state of the repository.
+ Using workarounds or temporary fixes that don't address the root cause of the issue. Your fix should be a proper solution to the problem.
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
    "--output",
    type=str,
    required=True,
    help="The file to output the generated prompt.",
  )
  return parser.parse_args()


def main():
  if os.environ.get("LLVM_AUTOFIX_HOME_DIR") is None:
    panic("The llvm-autofix environment has not been brought up.")

  args = parse_args()
  issue = args.issue
  set_llvm_build_dir(os.path.join(get_llvm_build_dir(), issue))
  fixenv = FixEnvironment(
    issue,
    base_model_knowledge_cutoff="2023-12-31Z",
    additional_cmake_args=ADDITIONAL_CMAKE_FLAGS,
    max_build_jobs=os.environ.get("LLVM_AUTOFIX_MAX_BUILD_JOBS"),
  )
  fixenv.reset()
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
  Path(args.output).write_text(prompt)
  print(f"Prompt generated and saved to {args.output}.")


if __name__ == "__main__":
  main()
