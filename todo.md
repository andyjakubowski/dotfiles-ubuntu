# GPU VM setup checklist

The scripts are implemented; the first real GPU guest still needs verification.
Full commands and troubleshooting: [operating guide](docs/codex-vm.md).

- [ ] **Check prerequisites on ai-box:** Ubuntu 24.04 x86_64, virtualization enabled, `/mnt/data` mounted, enough system-SSD space for a 100 GiB guest, and your Mac's plain SSH public key in `~/.ssh/authorized_keys` — required by the setup defaults.
- [ ] **Install host tools:** from this repo run `DOTFILES_CODEX_VM=1 ./setup_codex_vm.sh` (or `DOTFILES_CODEX_VM=1 ./install` for all dotfiles too), then `sudo codex-vm inspect` — installs virtualization software, networking and firewall; creates no VM.
- [ ] **Change UEFI primary display to integrated AMD graphics and reboot** — frees the RTX 5090 from host display duties; move any monitor cable to the motherboard output.
- [ ] **Reserve the GPU:** run `sudo codex-vm prepare-gpu --acknowledge-display-loss`, reboot, then `sudo codex-vm inspect` — both RTX functions must show `driver=vfio-pci` before a GPU guest can use them.
- [ ] **Create the clean guest:** run `sudo codex-vm create builder` — downloads Ubuntu, boots it and automatically installs drivers, development tools and Codex.
- [ ] **Connect from the Mac:** add the guide's `codex-builder` SSH alias using the printed IP and `ProxyJump ai-box`; run `ssh codex-builder 'cloud-init status --wait'`, then `ssh codex-builder 'sudo reboot'` — waits for provisioning and activates the installed kernel/driver.
- [ ] **Verify before trusting it:** follow the guide's checks for the provisioning marker, Codex, `nvidia-smi`, read-only `/mnt/data`, internet access, blocked host/LAN access and GPU shutdown/start — confirms the real hardware and isolation work.
- [ ] **Save a clean base before adding secrets:** `sudo codex-vm stop builder`; once shut off, `sudo codex-vm template builder ubuntu-gpu`, then `sudo codex-vm create work --base ubuntu-gpu` — makes future disposable VMs quick to create.
- [ ] **Work in the guest:** add a `codex-work` SSH alias using its printed IP; use SSH manually or connect the app to `/home/codex/workspace` and authenticate Codex — both you and the agent use the same isolated GPU environment.

The RTX stays reserved even when no VM runs; host Ollama cannot use it. To return
it to the host, stop GPU guests, run
`sudo codex-vm unprepare-gpu --acknowledge-display-loss`, then reboot.
