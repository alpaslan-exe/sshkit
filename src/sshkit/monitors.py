"""Remote monitor shell scripts.

These run on the *remote* host, which is assumed to be POSIX. They are plain
POSIX sh with awk, so they work on any Linux box without extra tooling.
"""

from __future__ import annotations

MONITOR_SCRIPTS = {
    "gpu": r"""export TERM=${TERM:-xterm-256color}
while true; do
  cols=${COLUMNS:-80}
  rows=${LINES:-12}
  gpu_rows=$((rows / 2))
  task_rows=$((rows - gpu_rows - 2))
  [ "$gpu_rows" -lt 1 ] && gpu_rows=1
  [ "$task_rows" -lt 1 ] && task_rows=1
  frame=$(
  echo "GPU $(date +%H:%M:%S)"
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw --format=csv,noheader,nounits 2>/dev/null |
      awk -F, '{
        for (i = 1; i <= NF; i++) gsub(/^ +| +$/, "", $i)
        printf "#%s u%s%% m%s/%s t%sC p%sW\n", $1, $2, $3, $4, $5, $6
      }' | head -"$gpu_rows"
    echo "gpu pid sm mem cmd"
    nvidia-smi pmon -c 1 -s um 2>/dev/null |
      awk '
        $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ {
          cmd=$8
          if (cmd == "" || cmd == "-") cmd=$7
          printf "%s %s %s%% %s%% %.10s\n", $1, $2, $4, $5, cmd
        }
      ' | head -"$task_rows"
  else
    echo "nvidia-smi missing"
  fi
  )
  printf '\033[H'
  awk -v cols="$cols" -v rows="$rows" '
    NR <= rows { printf "%-*.*s\n", cols, cols, $0; count++ }
    END { for (; count < rows; count++) printf "%*s\n", cols, "" }
  ' <<EOF
$frame
EOF
  sleep 2
done""",
    "cpu": r"""export TERM=${TERM:-xterm-256color}
while true; do
  cols=${COLUMNS:-80}
  rows=${LINES:-12}
  proc_rows=$((rows - 6))
  [ "$proc_rows" -lt 1 ] && proc_rows=1
  frame=$(
  echo "CPU $(date +%H:%M:%S)"
  if [ -r /proc/loadavg ]; then
    echo "load $(cut -d' ' -f1-3 /proc/loadavg)"
  else
    uptime
  fi
  if [ -r /proc/stat ]; then
    read _ u1 n1 s1 i1 w1 q1 r1 t1 _ < /proc/stat
    idle1=$((i1 + w1)); total1=$((u1 + n1 + s1 + i1 + w1 + q1 + r1 + t1))
    sleep 0.2
    read _ u2 n2 s2 i2 w2 q2 r2 t2 _ < /proc/stat
    idle2=$((i2 + w2)); total2=$((u2 + n2 + s2 + i2 + w2 + q2 + r2 + t2))
    totald=$((total2 - total1)); idled=$((idle2 - idle1))
    [ "$totald" -gt 0 ] && echo "cpu $((100 * (totald - idled) / totald))%"
  fi
  free -h 2>/dev/null | awk '/^Mem:/ {print "mem " $3 "/" $2}'
  echo
  ps -eo pcpu=,pmem=,comm= --sort=-pcpu 2>/dev/null |
    head -"$proc_rows" |
    awk '{printf "%5s%% %4s%% %.12s\n", $1, $2, $3}'
  )
  printf '\033[H'
  awk -v cols="$cols" -v rows="$rows" '
    NR <= rows { printf "%-*.*s\n", cols, cols, $0; count++ }
    END { for (; count < rows; count++) printf "%*s\n", cols, "" }
  ' <<EOF
$frame
EOF
  sleep 3
done""",
    "users": r"""export TERM=${TERM:-xterm-256color}
while true; do
  printf '\033[H\033[J'
  cols=${COLUMNS:-80}
  rows=${LINES:-12}
  user_rows=$((rows - 4))
  [ "$user_rows" -lt 1 ] && user_rows=1
  trim() { cut -c 1-"$cols"; }
  echo "USERS $(date +%H:%M:%S)" | trim
  count=$(who 2>/dev/null | wc -l | tr -d ' ')
  echo "logged in: ${count:-0}" | trim
  echo
  who 2>/dev/null |
    awk '{print $1 " " $2 " " $3 " " $4}' |
    head -"$user_rows" | trim
  sleep 5
done""",
    "processes": r"""export TERM=${TERM:-xterm-256color}
while true; do
  cols=${COLUMNS:-80}
  rows=${LINES:-12}
  proc_rows=$((rows - 2))
  [ "$proc_rows" -lt 1 ] && proc_rows=1
  frame=$(
    echo "PROC CPU $(date +%H:%M:%S)"
    echo " CPU  MEM PID CMD"
    ps -eo pcpu=,pmem=,pid=,comm= --sort=-pcpu 2>/dev/null |
      head -"$proc_rows" |
      awk '{printf "%5s %4s %s %.12s\n", $1"%", $2"%", $3, $4}'
  )
  printf '\033[H'
  awk -v cols="$cols" -v rows="$rows" '
    NR <= rows { printf "%-*.*s\n", cols, cols, $0; count++ }
    END { for (; count < rows; count++) printf "%*s\n", cols, "" }
  ' <<EOF
$frame
EOF
  sleep 3
done""",
}

COMBINED_SECTIONS = {
    "gpu": r"""echo
echo "=== GPU ==="
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi
else
  echo "nvidia-smi not found"
fi""",
    "cpu": r"""echo
echo "=== CPU ==="
uptime || true
if command -v top >/dev/null 2>&1; then
  top -bn1 | head -12
else
  ps -eo pid,user,pcpu,pmem,comm,args --sort=-pcpu | head -12
fi""",
    "users": r"""echo
echo "=== LOGGED IN USERS ==="
who || true
w -h 2>/dev/null || true""",
    "processes": r"""echo
echo "=== PROCESSES BY CPU ==="
ps -eo pid,user,pcpu,pmem,etime,comm,args --sort=-pcpu | head -18""",
}


def remote_monitor_script(name: str) -> str:
    return MONITOR_SCRIPTS[name]


def combined_monitor_script(monitors: list[str]) -> str:
    body = "\n".join(COMBINED_SECTIONS[name] for name in monitors if name in COMBINED_SECTIONS)
    return f"""export TERM=${{TERM:-xterm-256color}}
while true; do
  printf '\\033[H\\033[J'
  echo "[sshkit monitors] $(hostname)  $(date)"
{body}
  sleep 3
done"""
