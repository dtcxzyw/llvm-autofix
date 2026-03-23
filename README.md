# llvm-autofix

llvm-autofix is an agentic harness of [LLVM](https://github.com/llvm/llvm-project). Its current focus is automatic repair of LLVM bugs and systematic evaluation of agents' ability to resolve LLVM issues. **Longer term, it aims to become an off‑the‑shelf agentic harness for all LLVM tasks that benefit from an agent**. It includes:

+ [llvm tools](./autofix/tools): A collection of agent-friendly LLVM tool wrappers for agents.
+ [llvm skills](./autofix/skills): A collection of LLVM domain knowledge built into agent skills.
+ [llvm-bench (live)](./bench): A continuously updated benchmark of recent LLVM issues, currently focused on middle-end bugs.
+ [llvm-autofix-mini](./autofix): A minimal proof-of-concept agent targeted at fixing LLVM middle-end issues.

## 🔥 News

- 2026-03-20: We released `llvm-autofix`, an agentic harness for real-world compilers.

## 🗺️ Overview

Agents are being increasingly applied to real-world software engineering tasks, but their performance on complex, real-world codebases remains underexplored. With `llvm-autofix`, our evaluation of frontier models, including GPT‑5, Gemini 2.5 Pro, DeepSeek V3.2, and Qwen 3 Max, highlights several findings:

1. Although these models perform well on general SWE-bench Verified, they struggle on `llvm-bench live`: ~60% vs. ~38%.
2. `llvm-autofix-mini` outperforms `mini-SWE-agent`, achieving ~52%.
3. As benchmark splits become more challenging (easy $\to$ medium $\to$ difficult), the performance of frontier models degrades significantly.
4. After code review by LLVM developers, the *true* end-to-end bug-fixing capability of frontier models remains below 22%, even when using `llvm-autofix-mini`.


## 🔨 Build

The simplest way is using docker after editing `environments` and fill in the API keys:

```bash
docker build -t llvm-autofix-base:latest -f .devcontainer/Dockerfile .
docker build -t llvm-autofix:latest -f Dockerfile --build-arg USER_UID=$(id -u) --build-arg USER_GID=$(id -g) .
docker run --rm -it -v $(pwd):/llvm-autofix --cap-add=SYS_PTRACE --security-opt seccomp=unconfined llvm-autofix:latest
# tmux # Optional: spawn a tmux session if you want to see GDB's output.
source ./scripts/upenv.sh
```

Or follow [BUILD.md](./docs/BUILD.md) to install required dependencies and bring up the environment locally.

## 🚀 Launch

Launch the agent on a specific issue with:

```bash
python -m autofix.mini --issue <issue_id> --model <model_name>
```

## 📊 Benchmark

Benchmark the agent on our benchmarks with:

```bash
./bench/benchmark.sh <agent_name> -B <bench_name> -o <output_dir>
```

## 👨‍💻‍ Contributions

Please read guidelines in [CONTRIBUTORS.md](./CONTRIBUTORS.md).

## ✏️ Cite Us

If you found this work helpful, please consider citing our work:

```bibtex
@misc{zheng2026agenticharnessrealworldcompilers,
      title={Agentic Harness for Real-World Compilers},
      author={Yingwei Zheng and Cong Li and Shaohua Li and Yuqun Zhang and Zhendong Su},
      year={2026},
      eprint={2603.20075},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2603.20075},
}
```

Artifacts for the arXiv paper are available at the [experiment](https://github.com/dtcxzyw/llvm-autofix/tree/experiment) branch.
