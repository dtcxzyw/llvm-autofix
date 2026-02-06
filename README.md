# llvm-autofix

Try fixing LLVM bugs (crashes/mis-compilations) automatically.

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
python -m autofix.main --issue <issue_id> --model <model_name>
```

## 👨‍💻‍ Contributions

Please read guidelines in [CONTRIBUTORS.md](./CONTRIBUTORS.md).
