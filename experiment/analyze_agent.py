import json
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import dateparser
from unidiff import PatchSet

from autofix.dataset import hints
from autofix.llvm import llvm_helper
from autofix.main import MAX_ROLS_PER_TC

HOME_DIR = Path(__file__).parent.parent

AGENTS = ["autofix", "sweagent"]
MODELS = {
  "gpt4o": {
    "knowledge_cutoff": "2024-07-18T00:00:00Z",
  },
  "gpt5": {
    "knowledge_cutoff": "2025-08-07T00:00:00Z",
  },
  "gemini2p5pro": {
    "knowledge_cutoff": "2025-07-17T00:00:00Z",
  },
  "deepseekv3p2": {
    "knowledge_cutoff": "2025-12-01T00:00:00Z",
  },
  "qwen3max": {
    "knowledge_cutoff": "2025-09-05T00:00:00Z",
  },
}

TOOLS = [
  f"list{MAX_ROLS_PER_TC}",
  f"read{MAX_ROLS_PER_TC}",
  f"find{MAX_ROLS_PER_TC}",
  f"grep{MAX_ROLS_PER_TC}",
  "code",
  "docs",
  "langref",
  "debug",
  "eval",
  "edit",
  "reset",
  "test",
  "preview",
  "stop",
]


def compute_prf(exp, act):
  exp, act = set(exp), set(act)
  true_positives = exp.intersection(act)
  precision = len(true_positives) / len(act) if len(act) > 0 else 0.0
  recall = len(true_positives) / len(exp) if len(exp) > 0 else 0.0
  if precision + recall == 0:
    f1 = 0.0
  else:
    f1 = 2 * precision * recall / (precision + recall)
  return {
    "precision": precision,
    "recall": recall,
    "f1score": f1,
  }


def process_stats_file(stats_file, processed_stats, status):
  issue_id, _, timestamp = stats_file.stem.split("-")
  timestamp = int(timestamp)
  if issue_id in processed_stats:
    if processed_stats[issue_id]["timestamp"] > timestamp:
      return  # Keep the earliest

  with stats_file.open("r") as fin:
    actual_data = json.load(fin)
  assert actual_data["command"]["issue"] == issue_id, (
    f"Issue ID mismatch in {stats_file}. "
    f"Expecting {issue_id}, got {actual_data['command']['issue']}"
  )
  with (HOME_DIR / "dataset" / f"{issue_id}.json").open("r") as fin:
    expected_data = json.load(fin)

  exp_locs = {}
  for fn in expected_data["hints"]["bug_location_funcname"]:
    if fn not in exp_locs:
      exp_locs[fn] = {"functions": [], "linenos": []}
    for func in expected_data["hints"]["bug_location_funcname"][fn]:
      exp_locs[fn]["functions"].append(func)
  for fn in expected_data["hints"]["bug_location_lineno"]:
    if fn not in exp_locs:
      exp_locs[fn] = {"functions": [], "linenos": []}
    for ln in expected_data["hints"]["bug_location_lineno"][fn]:
      exp_locs[fn]["linenos"].append(ln)
  difficulty = expected_data["properties"]["difficulty"]
  processed_stats[issue_id] = {
    "issue": issue_id,
    "knowledge_cutoff": expected_data["knowledge_cutoff"],
    "difficulty": difficulty,
    "type": expected_data["bug_type"],
    "status": status,
    "error": None
    if status == "success"
    else actual_data["error"] or "PostValidationFailed",
    "timestamp": timestamp,
    "expected": {
      "locations": exp_locs,
    },
    "actual": None,
    "metrics": {
      "#tests": len(actual_data["test_traj"]),
      "#total_rounds": actual_data["chat_rounds"],
      "#total_tokens": actual_data["total_tokens"],
      "#input_tokens": actual_data["input_tokens"],
      "#output_tokens": actual_data["output_tokens"],
      "#cached_tokens": actual_data["cached_tokens"],
      "elapsed_time_sec": actual_data["total_time_sec"],
      "file": {"precision": 0.0, "recall": 0.0, "f1score": 0.0},
      "func": {"precision": 0.0, "recall": 0.0, "f1score": 0.0},
    },
  }

  if not actual_data["test_traj"]:
    return  # No patches have been generated once

  act_locs = {}
  patchset = PatchSet(actual_data["test_traj"][-1])  # Use the last generated patch
  for patchfile in patchset:
    if patchfile.path not in act_locs:
      act_locs[patchfile.path] = {"functions": [], "linenos": []}
    act_locs[patchfile.path]["linenos"] += hints.get_line_loc(patchfile)
    if patchfile.is_modified_file:
      source_code = llvm_helper.git_execute(
        ["show", f"{expected_data['base_commit']}:{patchfile.path}"]
      )
      act_locs[patchfile.path]["functions"] += hints.get_funcname_loc(
        patchfile, source_code
      )
    act_locs[patchfile.path]["functions"].sort()
  processed_stats[issue_id]["actual"] = {
    "locations": act_locs,
  }

  # Compute file-granule metrics
  exp_files = set(exp_locs.keys())
  act_files = set(act_locs.keys())
  processed_stats[issue_id]["metrics"]["file"] = compute_prf(exp_files, act_files)

  # Compute function-granule metrics
  exp_funcs = set()
  for fn in exp_locs:
    for func in exp_locs[fn]["functions"]:
      exp_funcs.add((fn, func))
  act_funcs = set()
  for fn in act_locs:
    for func in act_locs[fn]["functions"]:
      act_funcs.add((fn, func))
  processed_stats[issue_id]["metrics"]["func"] = compute_prf(exp_funcs, act_funcs)


def process_agent_stats(agent, model):
  NUM_ISSUES = 229
  processed_stats = {}
  expr_dir = HOME_DIR / "experiment" / f"{agent}-mini-{model}"
  if not expr_dir.exists():
    return
  print(f"Processing stats in {expr_dir}")
  post_valid_data = (expr_dir / "processed_issues.post_validation").read_text()
  post_valid_data_irdiff_regres = (
    expr_dir / "processed_issues.post_validation_diff_regression2"
  ).read_text()
  for sub in ["failure", "success"]:
    subdir = expr_dir / sub
    for file in subdir.iterdir():
      if file.suffixes != [".json"]:
        continue
      issue_id = file.name[: file.name.index("-")]
      process_stats_file(
        file,
        processed_stats,
        "success"
        if sub == "success"
        and f"{issue_id}:success" in post_valid_data
        and f"{issue_id}:success" in post_valid_data_irdiff_regres
        else "failure",
      )
  assert len(processed_stats) == NUM_ISSUES, (
    f"Expected {NUM_ISSUES} issues processed, got {len(processed_stats)}"
  )
  # Calculate overall metrics for the latest N issues
  ordered_stats = [processed_stats[i] for i in sorted(processed_stats.keys())]
  for n in ["all", 100, 50, "knowcut", "easy", "medium", "hard"]:
    if n == "all":
      latest_stats = ordered_stats
      latest_name = "all"
    elif n == "knowcut":
      latest_stats = [
        x
        for x in ordered_stats
        if dateparser.parse(x["knowledge_cutoff"])
        >= dateparser.parse(MODELS[model]["knowledge_cutoff"])
      ]
      latest_name = "knowcut"
    elif n in ["easy", "medium", "hard"]:
      latest_stats = [x for x in ordered_stats if x["difficulty"] == n]
      latest_name = n
    else:
      latest_stats = ordered_stats[NUM_ISSUES - n :]
      latest_name = f"latest{n}"
    for ty in ["bug", "crash", "miscompilation"]:
      # The issues of type ty in the latest N issues
      latest_subset = [x for x in latest_stats if (ty == "bug" or x["type"] == ty)]
      if len(latest_subset) == 0:
        processed_stats[f"metrics@{ty}@{latest_name}"] = {
          "pass@1": "0 (0/0)",
          "#tests": 0,
          "#total_rounds": 0,
          "#total_tokens": 0,
          "#input_tokens": 0,
          "#output_tokens": 0,
          "#cached_tokens": 0,
          "elapsed_time_sec": 0,
          "file_recl": 0,
          "func_recl": 0,
        }
      else:
        num_passed = len([x for x in latest_subset if x["status"] == "success"])
        processed_stats[f"metrics@{ty}@{latest_name}"] = {
          "pass@1": f"{num_passed / len(latest_subset):.4f} ({num_passed}/{len(latest_subset)})",
          "#tests": statistics.mean([x["metrics"]["#tests"] for x in latest_subset]),
          "#total_rounds": statistics.mean(
            [x["metrics"]["#total_rounds"] for x in latest_subset]
          ),
          "#total_tokens": statistics.mean(
            [x["metrics"]["#total_tokens"] for x in latest_subset]
          ),
          "#input_tokens": statistics.mean(
            [x["metrics"]["#input_tokens"] for x in latest_subset]
          ),
          "#output_tokens": statistics.mean(
            [x["metrics"]["#output_tokens"] for x in latest_subset]
          ),
          "#cached_tokens": statistics.mean(
            [x["metrics"]["#cached_tokens"] for x in latest_subset]
          ),
          "elapsed_time_sec": statistics.mean(
            [x["metrics"]["elapsed_time_sec"] for x in latest_subset]
          ),
          "file_recl": statistics.mean(
            [x["metrics"]["file"]["recall"] for x in latest_subset]
          ),
          "func_recl": statistics.mean(
            [x["metrics"]["func"]["recall"] for x in latest_subset]
          ),
        }
  with (expr_dir / "processed_stats.json").open("w") as fou:
    json.dump(
      processed_stats,
      fou,
      indent=2,
      sort_keys=True,
    )
    fou.write("\n")
  print(f"Processed stats for {agent}-{model} saved to {expr_dir}/processed_stats.json")
  return processed_stats


def process_agent_tool_calls(agent, model):
  # This function only works with autofix
  assert agent == "autofix", "Tool call processing is only for autofix agent"

  print(f"Processing tool calls for agent={agent}, model={model}")

  expr_dir = HOME_DIR / "experiment" / f"{agent}-mini-{model}"
  tool_call_data = {}
  for status in ("success", "failure"):
    for case in (expr_dir / status).iterdir():
      if not case.is_file() or case.suffix != ".log":
        continue
      issue = case.name[: case.name.index("-")]
      tool_call_data[issue] = {tool: {"success": 0, "failure": 0} for tool in TOOLS}
      tool_call_data[issue]["unknown"] = {}
      with open(case, "r") as fin:
        for lino, line in enumerate(fin):
          # This is an example of the lines we are looking for:
          # ╭─ Function Call (id = <no-id>) [MainThread@3433734] ─────────────────────────────────╮
          # │ read250({"file": "llvm/lib/Transforms/Scalar/DFAJumpThreading.cpp", "position":     │
          # │ 300})                                                                               │
          # ╰─────────────────────────────────────────────────────────────────────────────────────╯
          # ╭─ Function Call Output (id = <no-id>) [MainThread@3433734] ──────────────────────────╮
          # │ file: llvm/lib/Transforms/Scalar/DFAJumpThreading.cpp:200-450                       │
          # │ -------------------------------------------------------------                       │
          if "╭─ Function Call (id" in line:
            call_line = None
            while True:
              call_line = next(fin)
              if call_line.startswith("│ "):
                break
            assert call_line is not None, (
              f"Cannot find the tool call line for case {case}:{lino}"
            )
            tool_name = call_line[2 : call_line.index("(")]
            # Looking for the output to check success or failure
            while True:
              try:
                output_box_line = next(fin)
              except StopIteration:
                output_box_line = None
                break
              if "╭─ Function Call Output" in output_box_line:
                break
              if "╭─ User " in output_box_line:
                break
            # Judging success or failure of the tool call
            status = "success"
            if output_box_line is not None:
              answer_line = None
              while True:
                answer_line = next(fin)
                if answer_line.startswith("│ "):
                  break
              assert answer_line is not None, (
                f"Cannot find the tool call output line for case {case}:{lino}"
              )
              if answer_line.startswith("│ Error:"):
                status = "failure"
            else:
              status = "failure"
            if tool_name in TOOLS:
              tool_call_data[issue][tool_name][status] += 1
            else:
              # Model called an non-existing tool
              if tool_name not in tool_call_data[issue]["unknown"]:
                tool_call_data[issue]["unknown"][tool_name] = {
                  "success": 0,
                  "failure": 0,
                }
              tool_call_data[issue]["unknown"][tool_name][status] += 1

  with (expr_dir / "processed_tool_calls.json").open("w") as fout:
    json.dump(tool_call_data, fout, indent=2)


def process_wrapper(func, agent, model):
  try:
    return func(agent, model)
  except:  # noqa
    import traceback

    traceback.print_exc()
    return None


def main():
  with ProcessPoolExecutor(max_workers=len(AGENTS) * len(MODELS)) as executor:
    for agent in AGENTS:
      for model in MODELS:
        executor.submit(process_wrapper, process_agent_stats, agent, model)

  with ProcessPoolExecutor(max_workers=len(AGENTS) * len(MODELS)) as executor:
    for model in MODELS:
      executor.submit(process_wrapper, process_agent_tool_calls, "autofix", model)


if __name__ == "__main__":
  main()
