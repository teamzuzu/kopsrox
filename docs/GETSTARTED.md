# get started

done with [SETUP.md](SETUP.md)? your first cluster is 3 commands away :tada:

## :gear: create a config

```
./kopsrox.py
```

a default `kopsrox.ini` is created - edit it for your setup ( see [SETUP.md](SETUP.md) )

## :package: create the image

```
./kopsrox.py image create
```

builds a microvm template from the configured OCI image ( takes a couple of minutes - watch `kopsrox-image.log` if curious )

## :rocket: create the cluster

```
./kopsrox.py cluster create
```

clones the template into master + worker nodes, installs k3s and exports `<cluster_name>.kubeconfig` + `<cluster_name>.k3stoken` to the current directory - about 2.5 minutes for 1 master + 1 worker

## :mag: use it

```
./kopsrox.py cluster info
./kopsrox.py k3s kubectl get pods -A
kubectl --kubeconfig=<cluster_name>.kubeconfig get nodes
```

## :chart_with_upwards_trend: scale it

edit `kopsrox.ini` - eg set `workers = 3` or `masters = 3` ( 1 or 3 masters only ) - then:

```
./kopsrox.py cluster update
```

with 3 masters the kube api stays up even if you kill the VIP master :muscle:

## :floppy_disk: back it up

configure the `s3_*` settings in `kopsrox.ini` for your provider then:

```
./kopsrox.py etcd snapshot
./kopsrox.py etcd list
```

## :ambulance: restore it

```
./kopsrox.py cluster restore
```

rebuilds the whole cluster from the latest S3 snapshot - even if every node is gone
