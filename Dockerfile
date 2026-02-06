FROM llvm-autofix-base:latest

ARG USERNAME=autofix
ARG USER_UID
ARG USER_GID

RUN groupadd -g $USER_GID $USERNAME \
    && useradd -u $USER_UID -g $USER_GID -m $USERNAME \
    # Add the user to the sudo group without asking him to input password for his sudo operation
    # Note: Avoid using usermod as it always requires the user to input password
    && echo $USERNAME ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

USER $USERNAME
ENV SHELL=/bin/zsh

RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" \
    && sudo mkdir -p $LLVM_AUTOFIX_DEPS_DIR \
    && sudo chown $USERNAME:$USERNAME $LLVM_AUTOFIX_DEPS_DIR \
    && $LLVM_AUTOFIX_INSTALL_SCRIPT_DIR/install.sh \
    && sudo rm -rf $LLVM_AUTOFIX_INSTALL_SCRIPT_DIR

VOLUME ["/llvm-autofix"]
WORKDIR /llvm-autofix

ENTRYPOINT [ "/bin/zsh" ]
