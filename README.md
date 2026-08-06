# Ubuntu dotfiles

Personal dotfiles and provisioning scripts for setting up an Ubuntu development machine. The project uses [Dotbot](https://github.com/anishathalye/dotbot) to link configuration files into the home directory and run the setup scripts.

## What it configures

- Bash, Zsh, Git, and profile configuration
- APT packages listed in `apt-packages.txt`
- Package repositories for 1Password, Google Chrome, and Visual Studio Code
- Zsh as the default shell
- [`uv`](https://docs.astral.sh/uv/) for Python package and environment management
- Faster GNOME keyboard repeat settings
- Git LFS filters

The Zsh configuration also adds `~/.local/bin` to `PATH`, automatically activates a `.venv` when entering a project directory, and stores the Hugging Face cache under `/mnt/data/hf_cache`.

## Requirements

- Ubuntu with GNOME
- `git`, `curl`, and `sudo` access
- An internet connection

## Installation

Review the repository before installing. In particular, `gitconfig` contains a personal name, email address, and signing key that should be changed when used by someone else.

```sh
git clone https://github.com/andyjakubowski/dotfiles-ubuntu.git
cd dotfiles-ubuntu
./install
```

The installer initializes the Dotbot submodule, replaces the managed files in your home directory with symlinks to this repository, and runs the provisioning scripts. Some steps prompt for `sudo` access, download third-party signing keys, and install system packages.

Run `./install` again after changing the dotfiles or package list.

## Repository layout

| Path | Purpose |
| --- | --- |
| `install` | Initializes Dotbot and runs the configuration |
| `install.conf.yaml` | Defines symlinks and setup-script order |
| `apt-packages.txt` | Packages installed through APT |
| `provision/apt-repos.d/` | Third-party APT repository setup |
| `setup_apt.sh` | Refreshes repositories and installs packages |
| `setup_zsh.sh` | Sets Zsh as the default shell and prepares the Hugging Face cache |
| `setup_uv.sh` | Installs `uv` |
| `setup_ubuntu.sh` | Applies GNOME desktop preferences |
| `cleanup.sh` | Removes a legacy duplicate 1Password source entry |
| `setup_data_disk.sh` | Optional data-disk partitioning and mount helper |

### Why the `provision` directory exists

Ubuntu installs software from package repositories: trusted online catalogs that APT knows how to contact. The standard Ubuntu repositories do not include every application, so vendors such as 1Password, Google, and Microsoft operate their own repositories.

The scripts in `provision/apt-repos.d/` register those vendor repositories with the system. Each script downloads the vendor's signing key and adds an APT source definition. The signing key lets APT verify that downloaded packages really came from that vendor and were not modified. After registration, applications such as 1Password, Google Chrome, and Visual Studio Code can be installed and updated through the normal APT workflow alongside Ubuntu packages.

## Optional data disk

`setup_data_disk.sh` is not run by the main installer. It prepares a block device, formats it as ext4 when no filesystem exists, mounts it at `/mnt/data`, and adds it to `/etc/fstab`.

> **Warning:** The default device is `/dev/nvme0n1`. Creating its partition table can destroy existing data. Verify the target device first and override the defaults as needed.

For example:

```sh
DEV=/dev/nvme1n1 PART=/dev/nvme1n1p1 MNT=/mnt/data ./setup_data_disk.sh
```

Supported overrides are `DEV`, `PART`, `MNT`, `FS`, `OWNER_USER`, and `OWNER_GROUP`.
