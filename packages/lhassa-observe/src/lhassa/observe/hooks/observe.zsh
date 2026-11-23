# lhassa observe: zsh hook (source it from an interactive ~/.zshrc).
#
# Same record and same rules as the bash hook: builtins only, first word of the
# command, full line only with LHASSA_OBSERVE_FULL=1, silent when the receiver is
# not running. NOTE: written against the zsh manual, not exercised in the
# project's tests (no zsh on the development machine).

[[ -o interactive ]] || return 0
(( ${+__lhassa_hook_loaded} )) && return 0
typeset -g __lhassa_hook_loaded=1
zmodload zsh/datetime 2>/dev/null || return 0
autoload -Uz add-zsh-hook

typeset -g __lhassa_dir=${LHASSA_OBSERVE_DIR:-${XDG_RUNTIME_DIR:+$XDG_RUNTIME_DIR/lhassa-observe}}
: ${__lhassa_dir:=/tmp/lhassa-observe-$UID}
typeset -g __lhassa_t0= __lhassa_tok= __lhassa_full=

__lhassa_preexec() {
  __lhassa_t0=$EPOCHREALTIME
  __lhassa_tok=${1%%[[:space:]]*}
  if [[ -n ${LHASSA_OBSERVE_FULL-} ]]; then
    __lhassa_full=${1//$'\n'/ }
    __lhassa_full=${__lhassa_full//$'\037'/}
  else
    __lhassa_full=
  fi
}

__lhassa_precmd() {
  local rc=$?
  [[ -n $__lhassa_t0 ]] || return $rc
  local t0=$__lhassa_t0 tok=$__lhassa_tok full=$__lhassa_full pid
  __lhassa_t0= __lhassa_tok= __lhassa_full=
  { read -r pid < "$__lhassa_dir/shell.pid"; } 2>/dev/null; [[ -n $pid ]] || return $rc
  [[ -d /proc/$pid && -p $__lhassa_dir/shell.fifo ]] || return $rc
  printf 'L1\037zsh\037%s\037%d\037%d\037%s\037%s\n' \
    "$PWD" "$rc" $(( int((EPOCHREALTIME - t0) * 1000) )) "$tok" "$full" 2>/dev/null 1<>"$__lhassa_dir/shell.fifo"
  return $rc
}

add-zsh-hook preexec __lhassa_preexec
add-zsh-hook precmd __lhassa_precmd
