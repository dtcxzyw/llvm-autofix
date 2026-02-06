# Contributor Guidelines

## Step 0. Optional Configurations

We recommend developing this project in a powerful server since we need to frequently build LLVM with different commits. You may use VS Code to maintain a streamlined development experience. However, this is optional.

After cloning this project into your server:

1. Install [Remote-SSH](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-ssh) and [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) plugins in VS Code.
2. Reopen VS Code and call the Command Pallete up by `Ctrl+Shift+P`.
3. Type `Remote-SSH` and select `Remote-SSH: Connect to Host...`.
4. Enter your server address and your password to connect to your server.
5. Open the llvm-autofix project in VS Code.
6. Type `Dev Containers` and select `Dev Containers: Rebuild and Reopen in Container`.
7. Wait until VS Code build our devcontainer and import the project successfully.
8. Call the terminal up to check if we are successful by typing `whoami`. If it shows `autofix`, we are successful.

## Step 1. Install Dependencies

If you chose to execute Step 0, you can edit `environments` to fill in you API key and then run the following command and goto Step 2:

```shell
# tmux # Optional: spawn a tmux session if you want to see GDB's output.
source ./scripts/upenv.sh
```

Otherwise, please follow [BUILD.md](./docs/BUILD.md) to install required dependencies and bring up the environment, then goto Step 2.


## Step 2. Install Dev Dependencies

Install development-specific dependencies:

```shell
pip install -r requirements.dev.txt
```

## Step 3. Install Pre-Commit Checks

We enforce a series of pre-commit checks that our contributors should follow. Before contributing to this project, developers are required to install our checkers:

```shell
pre-commit install  # install pre-commit itself
pre-commit install-hooks  # install our pre-commit checkers
```

Below are checkers/hooks we have enabled:
+ Python: We use Ruff's lint and format the code; check [all rules](https://docs.astral.sh/ruff/rules/) if your commits fail. Check [ruff.md](./docs/ruff.md) to configure Ruff in PyCharm.
+ Commit: We apply Conventional Commits to format all commit messages; check [the rules](https://www.conventionalcommits.org/) to configure its format.
+ MISC: We also apply some misc checkers for example YAML.

## Step 4. We are Done.
