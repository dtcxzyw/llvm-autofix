FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

RUN export DEBIAN_FRONTEND=noninteractive \
    && wget -O - https://apt.llvm.org/llvm-snapshot.gpg.key | apt-key add - \
    && apt update \
    && yes | unminimize 2>&1 \
    && apt install -y --no-install-recommends \
        build-essential \
        make \
        cmake \
        ninja-build \
        gdb \
        gdbserver \
        git \
        curl \
        wget \
        zip \
        unzip \
        ripgrep \
        # We use Python 3.12 as the gdb in Ubuntu 24.04 is using the version.
        python3.12 \
        python3.12-dev \
        python3.12-venv \
        python3-pip \
        vim \
        tmux \
        zstd \
    # We use Ollama for development and testing.
    && curl -fsSL https://ollama.com/install.sh | sh \
    && apt autoremove -y && apt clean -y \
    && rm -rf /var/lib/apt/lists/*

# Specify the directory that we'd like to install our dependencies to.
# Note, avoid using home directory as it will make the updating of UID/GID quite slow.
ENV LLVM_AUTOFIX_DEPS_DIR=/llvm-autofix-deps

# Install the dependency installation scripts.
# This will be used by our local feature llvm-autofix-install-deps and the top-level Dockerfile.
ENV LLVM_AUTOFIX_INSTALL_SCRIPT_DIR=/llvm-autofix-install-scripts
COPY scripts /llvm-autofix-install-scripts

ENV DOCKER_BUILDKIT=1

CMD [ "sleep", "infinity" ]
