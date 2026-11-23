# lhassa observe: bash hook (source it from an interactive ~/.bashrc).
#
# Sends one metadata record per command to the observer's FIFO: shell, cwd, exit
# code, duration in ms and the FIRST WORD of the command. Only shell builtins run
# (no fork, no exec): the DEBUG trap stamps the start, PROMPT_COMMAND sends.
# The full command line is sent only with LHASSA_OBSERVE_FULL=1 (and the receiver
# drops it unless its own configuration also opts in).
#
# Nothing is sent unless the receiver is alive (`lhassa observe run` with the
# shell observer), so a stopped observer costs one stat and one read per prompt.

[[ $- == *i* ]] || return 0 2>/dev/null
[[ -n ${__lhassa_hook_loaded-} ]] && return 0
__lhassa_hook_loaded=1

__lhassa_dir=${LHASSA_OBSERVE_DIR:-${XDG_RUNTIME_DIR:+$XDG_RUNTIME_DIR/lhassa-observe}}
__lhassa_dir=${__lhassa_dir:-/tmp/lhassa-observe-$UID}
__lhassa_armed=
__lhassa_t0=
__lhassa_tok=

# DEBUG trap: runs before every simple command; only the first one after a prompt counts.
__lhassa_preexec() {
  [[ -n $__lhassa_armed ]] || return 0
  __lhassa_armed=
  __lhassa_t0=${EPOCHREALTIME/[.,]/}
  __lhassa_tok=${BASH_COMMAND%%[[:space:]]*}
}

# First element of PROMPT_COMMAND: it must read $? before anything else does.
__lhassa_precmd() {
  local rc=$?
  [[ -n $__lhassa_t0 ]] || return $rc
  local t0=$__lhassa_t0 tok=$__lhassa_tok t1=${EPOCHREALTIME/[.,]/} full= pid
  __lhassa_t0= __lhassa_tok=
  { read -r pid < "$__lhassa_dir/shell.pid"; } 2>/dev/null; [[ -n $pid ]] || return $rc
  [[ -d /proc/$pid && -p $__lhassa_dir/shell.fifo ]] || return $rc
  if [[ -n ${LHASSA_OBSERVE_FULL-} ]]; then
    full=$(HISTTIMEFORMAT= history 1)
    [[ $full =~ ^[[:space:]]*[0-9]+[[:space:]]+(.*)$ ]] && full=${BASH_REMATCH[1]}
    full=${full//$'\n'/ }
    full=${full//$'\037'/}
  fi
  # 1<> opens the FIFO read-write: it can never block on a missing reader.
  printf 'L1\037bash\037%s\037%d\037%d\037%s\037%s\n' \
    "$PWD" "$rc" $(( (t1 - t0) / 1000 )) "$tok" "$full" 2>/dev/null 1<>"$__lhassa_dir/shell.fifo"
  return $rc
}

# Last element of PROMPT_COMMAND: arms the DEBUG trap for the next command line.
__lhassa_arm() { __lhassa_armed=1; }

PROMPT_COMMAND="__lhassa_precmd${PROMPT_COMMAND:+;$PROMPT_COMMAND};__lhassa_arm"
trap '__lhassa_preexec' DEBUG
