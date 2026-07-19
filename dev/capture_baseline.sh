#!/usr/bin/env bash
# capture pre-change behavior baselines - re-run on the OLD code only
set -e
B=.baseline
mkdir -p $B

cap() {  # cap <name> <cmd...>
    local name=$1; shift
    set +e
    "$@" > $B/$name.txt 2>&1
    echo $? > $B/$name.exit
    set -e
}

cap help            ./kopsrox.py
cap help-cluster    ./kopsrox.py cluster
cap help-image      ./kopsrox.py image
cap help-etcd       ./kopsrox.py etcd
cap help-k3s        ./kopsrox.py k3s
cap help-node       ./kopsrox.py node
cap bad-verb        ./kopsrox.py bogus
cap bad-cmd         ./kopsrox.py etcd restore-latest
cap missing-arg     ./kopsrox.py etcd restore
cap image-info      ./kopsrox.py image info
cap cluster-info    ./kopsrox.py cluster info

# default ini from the schema renderer
./dev/gen_config.sh && mv kopsrox.ini.default $B/default.ini

# rendered artifacts for the live config ( argv hack needed on old code only )
python3 - <<'EOF'
import sys
sys.argv = ['kopsrox.py', 'k3s', 'kubeconfig']
sys.path.insert(0, 'lib')
from kopsrox_artifacts import kopsrox_manifest, k3s_server_config, kopsrox_sh
open('.baseline/artifact-manifest.yaml', 'w').write(kopsrox_manifest())
open('.baseline/artifact-config.yaml', 'w').write(k3s_server_config())
open('.baseline/artifact-kopsrox.sh', 'w').write(kopsrox_sh())
EOF
echo baseline captured
