#!/usr/bin/env bash

set -eux


if [[ $(id -u) -ne 0 ]]; then
    echo -e 'USER is not root. This script must be run as root. Use sudo, su, or add "USER root" to Dockerfile before running this script.'
    exit 1
fi

if [[ -z ${USERNAME} ]]; then
    echo 'USERNAME is not set. Set it as an argument to this feature.'
    exit 1
fi

if ! id -u ${USERNAME} > /dev/null 2>&1; then
    echo "User ${USERNAME} does not exist. The script requires ${USERNAME} to be created and will grant dependency-access permissions to that user. Create ${USERNAME} as a non-root, perhaps sudo-permitted user by commit-utils."
    exit 1
fi

# The admin user information for installing dependencies
# Note, this should match the one in llvm-autofix-install-deps feature
export LLVM_AUTOFIX_ADMIN_USERNAME=autofix-admin
export LLVM_AUTOFIX_ADMIN_USER_UID=11011
export LLVM_AUTOFIX_ADMIN_USER_GID=11011

if ! id -u ${LLVM_AUTOFIX_ADMIN_USERNAME} > /dev/null 2>&1; then
    echo "User ${LLVM_AUTOFIX_ADMIN_USERNAME} does not exist. The script requires ${LLVM_AUTOFIX_ADMIN_USERNAME} to be created and dependencies installed. Either you did not install llvm-autofix-install-deps feature first, or the llvm-autofix-install-deps feature defines a different admin user name."
    exit 1
fi

# Add the user to the admin group
usermod -aG $LLVM_AUTOFIX_ADMIN_USERNAME $USERNAME
