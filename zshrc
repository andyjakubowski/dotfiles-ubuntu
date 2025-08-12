echo 'Hello from .zshrc'

# Deepnote-specific variables
# export ESLINT_TYPECHECK_ENABLED=true

# Node setup
# --max-old-space-size sets max heap memory in MB (adjust as needed for your machine)
export NODE_OPTIONS="--max-old-space-size=16384"

# Hugging Face cache on large SSD
export HF_HOME=/mnt/data/hf_cache

# Customize Prompt(s)
ORIGINAL_PROMPT_LINE_1='
'
ORIGINAL_PROMPT_LINE_2="%1~ %L %# " # Restore complete prompt variable
ORIGINAL_PROMPT="${ORIGINAL_PROMPT_LINE_1}${ORIGINAL_PROMPT_LINE_2}"
PROMPT="${ORIGINAL_PROMPT}"

RPROMPT='%*'

# Add Locations to $path Array
typeset -U path

path=(
  # Used by tools like pipx, poetry
  "$HOME/.local/bin"
  $path
)

# Write Handy Functions
function mkcd() {
  mkdir -p "$@" && cd "$_"
}

# Activate Python venv when entering a directory containing a ".venv" subdirectory
activate_venv_if_available() {
  local venv_dir=".venv"
  if [[ -d "$PWD/$venv_dir" ]]; then
    echo "zsh chpwd hook: Detected a Python virtual environment directory (.venv). Activating the virtual environment by running “source .venv/bin/activate”."

    # Disable default venv prompt formatting
    export VIRTUAL_ENV_DISABLE_PROMPT=1

    # Source the venv
    source "$PWD/$venv_dir/bin/activate"

    # Customize prompt with venv name (only if not already present)
    # VIRTUAL_VENV is set by the venv activate script
    export VENV_NAME="(${VIRTUAL_ENV:t})"

    PROMPT="${ORIGINAL_PROMPT_LINE_1}${VENV_NAME} ${ORIGINAL_PROMPT_LINE_2}"
  else
    deactivate 2>/dev/null # Suppress errors if no venv is active
    PROMPT="${ORIGINAL_PROMPT}"
    unset VENV_NAME
  fi
}

autoload -U add-zsh-hook

# Run function when changing directories
add-zsh-hook chpwd activate_venv_if_available
# Run function when starting a new shell
activate_venv_if_available

# Create Aliases
alias ls='eza -lah --classify --git'
alias eza='eza -lah --classify --git'
alias trail='<<<${(F)path}'

# Output PATH segments on separate lines
alias path='echo $path | tr " " \\n'
