#!/usr/bin/env bash

set -eux


if [[ $(id -u) -ne 0 ]]; then
    echo -e 'USER is not root. This script must be run as root. Use sudo, su, or add "USER root" to Dockerfile before running this script.'
    exit 1
fi

if [[ -z ${LLVM_AUTOFIX_DEPS_DIR} ]]; then
    echo 'LLVM_AUTOFIX_DEPS_DIR is not set. Set it in Dockerfile to point to a location for saving dependencies that''s going to be installed by this script using for example "ENV LLVM_AUTOFIX_DEPS_DIR=<location>."'
    exit 1
fi

if [[ -z ${LLVM_AUTOFIX_INSTALL_SCRIPT_DIR} ]]; then
    echo 'LLVM_AUTOFIX_INSTALL_SCRIPT_DIR is not set. Set it in Dockerfile to point to a location which is saving the script for installing dependencies using for example "ENV LLVM_AUTOFIX_INSTALL_SCRIPT_DIR=<location>."'
    exit 1
fi

if [[ ! -d ${LLVM_AUTOFIX_INSTALL_SCRIPT_DIR} ]]; then
    echo "LLVM_AUTOFIX_INSTALL_SCRIPT_DIR does not exist or is not a directory which is saving the installation script: ${LLVM_AUTOFIX_INSTALL_SCRIPT_DIR}"
    exit 1
fi


export DEBIAN_FRONTEND=noninteractive


# Create an admin user for installing dependencies
export LLVM_AUTOFIX_ADMIN_USERNAME=autofix-admin
export LLVM_AUTOFIX_ADMIN_USER_UID=11011
export LLVM_AUTOFIX_ADMIN_USER_GID=11011

groupadd -g $LLVM_AUTOFIX_ADMIN_USER_GID $LLVM_AUTOFIX_ADMIN_USERNAME
useradd -u $LLVM_AUTOFIX_ADMIN_USER_UID -g $LLVM_AUTOFIX_ADMIN_USER_GID -m $LLVM_AUTOFIX_ADMIN_USERNAME
# Add the user to the sudo group without asking him to input password for his sudo operation
# Note: Avoid using usermod as it always requires the user to input password
echo $LLVM_AUTOFIX_ADMIN_USERNAME ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/$LLVM_AUTOFIX_ADMIN_USERNAME
chmod 0440 /etc/sudoers.d/$LLVM_AUTOFIX_ADMIN_USERNAME

# Install dependencies for llvm-autofix by the admin user
mkdir -p $LLVM_AUTOFIX_DEPS_DIR
chown $LLVM_AUTOFIX_ADMIN_USERNAME:$LLVM_AUTOFIX_ADMIN_USERNAME $LLVM_AUTOFIX_DEPS_DIR
chmod 775 $LLVM_AUTOFIX_DEPS_DIR # Grant users in our group the same permission as the admin user
sudo -E -u $LLVM_AUTOFIX_ADMIN_USERNAME /usr/bin/env bash $LLVM_AUTOFIX_INSTALL_SCRIPT_DIR/install.sh

# Clean up the installation script directory
rm -rf $LLVM_AUTOFIX_INSTALL_SCRIPT_DIR
