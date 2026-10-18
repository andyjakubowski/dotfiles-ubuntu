# Reproducible defaults; environment overrides apply when host setup is run.
CODEX_VM_CPUS=${CODEX_VM_CPUS:-8}
CODEX_VM_MEMORY_MIB=${CODEX_VM_MEMORY_MIB:-16384}
CODEX_VM_DISK_GIB=${CODEX_VM_DISK_GIB:-100}
CODEX_VM_SSH_PUBLIC_KEY=${CODEX_VM_SSH_PUBLIC_KEY:-$HOME/.ssh/authorized_keys}
CODEX_VM_NVIDIA_PACKAGE=${CODEX_VM_NVIDIA_PACKAGE:-nvidia-driver-575-open}
# Empty selects the only NVIDIA display controller; ambiguity is an error.
CODEX_VM_GPU_BDF=${CODEX_VM_GPU_BDF:-}
