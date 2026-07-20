#!/usr/bin/env bash

# bash only - the ERR trap and [[ ]] below break silently under plain sh
[ -n "$BASH_VERSION" ] || exec bash "$0" "$@"

start_time=$(date +%s)

# kopsrox aliases
CFG="kopsrox.ini"
K="./kopsrox.py"
KC="$K cluster"
KCI="$KC info"
KCC="$KC create"
KCU="$KC update"
KCD="$KC destroy"
KCR="$KC restore"
KI="$K image"
KID="$KI destroy"
KIC="$KI create"
KII="$KI info"
KE="$K etcd"
KEL="$KE list"
KES="$KE snapshot"
KER="$KE restore"

# change kopsrox config item
kc() {
  sed -i /"$1 =/c\\$1 = $2" $CFG
}

# get pods
get_pods="$KC kubectl get pods -A"

# format seconds as 3m21s
fmt() {
  local s=$1
  [[ $s -ge 60 ]] && echo "$((s / 60))m$((s % 60))s" || echo "${s}s"
}

# phase banner
total_phases=6
phase_num=0
phase() {
  phase_num=$((phase_num + 1))
  echo
  echo "🧪 [${phase_num}/${total_phases}] $1"
  echo "──────────────────────────────────────────────"
}

# run a step: prints label, runs command, prints time taken
current_step="startup"
run() {
  current_step="$1"; shift
  local t0=$(date +%s)
  echo "▶️  $current_step"
  "$@"
  echo "✅ $current_step ($(fmt $(($(date +%s) - t0))))"
}

# remove any generated files - best effort so runs before the ERR trap is set
rm \
lib/manifests/config.yaml \
lib/manifests/server.yaml \
lib/manifests/kopsrox* \
lib/scripts/* \
*.kubeconfig \
*.k3stoken \
> /dev/null 2>&1

# report which step failed and total elapsed time
trap 'echo; echo "❌ FAILED: $current_step (after $(fmt $(($(date +%s) - start_time))))"' ERR
# -E so the ERR trap fires inside run()
set -eE

echo "🚀 kopsrox release test - $(date '+%F %T')"

phase "clean slate 🧹"
run "destroy existing cluster" $KCD
kc workers 0 ; kc masters 1
echo "⚙️  config: 1 master, 0 workers"

phase "image + 1 master cluster 🖼️"
run "image create" $KIC
run "cluster create" $KCC
run "cluster update" $KCU

phase "etcd snapshot + restore 📸"
run "etcd snapshot" $KES
run "cluster destroy" $KCD
run "cluster create" $KCC
# restore the newest snapshot - name parsed from the etcd list output
current_step="parse latest etcd snapshot"
latest=$($KEL | awk '/^  kopsrox-/ {print $1}' | sort | tail -1)
[[ -n $latest ]]
run "etcd restore $latest" $KER "$latest"

phase "worker scaling 👷"
kc workers 1 ; run "scale workers 0 -> 1" $KCU
kc workers 0 ; run "scale workers 1 -> 0" $KCU
kc workers 2 ; run "scale workers 0 -> 2" $KCU

phase "master scaling 👑"
kc masters 3 ; run "scale masters 1 -> 3" $KCU
kc masters 1 ; run "scale masters 3 -> 1" $KCU

# change back to 1 node
kc masters 1 ; kc workers 0

phase "full cluster restore ♻️"
run "cluster destroy" $KCD
run "cluster restore" $KCR

echo
echo "🎉 ALL TESTS PASSED in $(fmt $(($(date +%s) - start_time)))"
