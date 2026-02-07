# llvm-autofix

llvm-autofix is a research project focused on automatically repairing LLVM bugs and systematically evaluating an agent’s capability to resolve LLVM issues. The current scope is limited to issues in LLVM's middle-end. It includes:

+ [llvm tooling](./autofix/llvm): A collection of agent-friendly LLVM tool wrappers for agents.
+ [llvm-bench (live)](./bench): A continuously updated benchmark containing the latest LLVM middle-end issues.
+ [llvm-autofix-mini](./autofix): A minimal, proof-of-concept agent designed to fix LLVM middle-end issues.


## 🔨 Build

The simplest way is using docker after editing `environments` and fill in your API keys:

```shell
docker build -t llvm-autofix-base:latest -f .devcontainer/Dockerfile .
docker build -t llvm-autofix:latest -f Dockerfile --build-arg USER_UID=$(id -u) --build-arg USER_GID=$(id -g) .
docker run --rm -it -v $(pwd):/llvm-autofix --cap-add=SYS_PTRACE --security-opt seccomp=unconfined llvm-autofix:latest
# tmux # Optional: spawn a tmux session if you want to see GDB's output.
source ./scripts/upenv.sh
```

Or you can follow [BUILD.md](./docs/BUILD.md) to install required dependencies and bring up the environment locally.

## 🚀 Launch

```shell
python -m autofix.mini --issue <issue_id> --model <model_name>
```

## 🔥 Benchmark

```shell
./bench/benchmark.sh <agent_name> -B <bench_name> -o <output_dir>
```


## 👨‍💻‍ Contributions

Please read guidelines in [CONTRIBUTORS.md](./CONTRIBUTORS.md).
