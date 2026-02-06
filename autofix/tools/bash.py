import shlex
from pathlib import Path
from subprocess import CalledProcessError

from autofix.lms.tool import FuncToolBase, FuncToolCallException, FuncToolSpec
from autofix.tools.llvm_mixins import LlvmDirMixin
from autofix.utils import cmdline


class BashTool(FuncToolBase, LlvmDirMixin):
  def __init__(self, llvm_dir: str, max_output_length: int = 4096):
    self.llvm_dir = Path(llvm_dir).resolve().absolute()
    self.max_output_length = max_output_length

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "bash",
      "Execute a bash command in the llvm/ directory. "
      "This tool should be used for commands that are not provided by other tools, "
      "such as building the project, running specific tests, or using complex shell commands with pipes and redirections.",
      [
        FuncToolSpec.Param(
          "command",
          "string",
          True,
          "The bash command to execute.",
        ),
        FuncToolSpec.Param(
          "timeout",
          "integer",
          False,
          "Optional timeout in seconds for the command execution. Default is 60 seconds.",
        ),
      ],
    )

  def _call(self, *, command: str, timeout: int = 60, **kwargs) -> str:
    if not command:
      raise FuncToolCallException(
        "No command provided. Please specify the bash command to execute."
      )

    # We use 'bash -c' to ensure full shell support (pipes, redirects, etc.)
    bash_cmd = f"bash -c {shlex.quote(command)}"

    try:
      # Use getoutput with check=True to catch and handle errors,
      # but we capture stdout/stderr together via cmdline.getoutput.
      output = cmdline.getoutput(
        bash_cmd, cwd=self.llvm_dir, check=True, timeout=timeout
      )
      output = output.decode("utf-8")
      # Check the length of the output, if is too long, we retain
      # the header and footer and truncate the middle part.
      if len(output) > self.max_output_length:
        header = output[:500]  # Keep the first 500 characters
        footer = output[-500:]  # Keep the last 500 characters
        output = header + "\n...[output truncated]...\n" + footer
      return output
    except CalledProcessError as e:
      # If the command failed, return the combined output and error message.
      error_output = e.stdout.decode("utf-8") if e.stdout else ""
      raise FuncToolCallException(
        f"Command failed with exit code {e.returncode}.\n\nOutput:\n{error_output}"
      )
    except Exception as e:
      raise FuncToolCallException(f"Failed to execute command: {str(e)}")
