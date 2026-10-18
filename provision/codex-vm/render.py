#!/usr/bin/env python3
"""Pure renderers: no host changes, device assignment or external dependencies."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def mac(slot):
    return f"52:54:00:ca:de:{slot:02x}"


def element(parent, tag, text=None, **attributes):
    node = ET.SubElement(parent, tag, attributes)
    if text is not None:
        node.text = str(text)
    return node


def domain(config, name, slot, disk, seed, gpu=True):
    root = ET.Element("domain", {"type": "kvm"})
    element(root, "name", name)
    element(root, "description", "Managed by dotfiles codex-vm; guest root does not manage the host")
    element(root, "memory", config["memory_mib"], unit="MiB")
    element(root, "vcpu", config["cpus"], placement="static")
    os = element(root, "os", firmware="efi")
    element(os, "type", "hvm", arch="x86_64", machine="q35")
    element(element(os, "firmware"), "feature", name="secure-boot", enabled="no")
    element(os, "boot", dev="hd")
    features = element(root, "features")
    element(features, "acpi")
    element(features, "apic")
    element(root, "cpu", mode="host-passthrough", check="none", migratable="off")
    element(root, "clock", offset="utc")
    element(root, "on_poweroff", "destroy")
    element(root, "on_reboot", "restart")
    element(root, "on_crash", "destroy")
    devices = element(root, "devices")
    element(devices, "emulator", "/usr/bin/qemu-system-x86_64")
    for path, kind, fmt, target, bus in [(disk, "disk", "qcow2", "vda", "virtio"),
                                         (seed, "cdrom", "raw", "sda", "sata")]:
        node = element(devices, "disk", type="file", device=kind)
        element(node, "driver", name="qemu", type=fmt)
        element(node, "source", file=str(path))
        element(node, "target", dev=target, bus=bus)
        if kind == "cdrom":
            element(node, "readonly")
    net = element(devices, "interface", type="network")
    element(net, "source", network="codex-vm")
    element(net, "mac", address=mac(slot))
    element(net, "model", type="virtio")
    element(net, "port", isolated="yes")
    share = element(devices, "filesystem", type="mount", accessmode="squash")
    element(share, "driver", type="path")
    element(share, "source", dir="/mnt/data")
    element(share, "target", dir="host-data")
    element(share, "readonly")  # QEMU backend refuses writes, even from guest root.
    if gpu:
        for device in config["gpu"]:
            node = element(devices, "hostdev", mode="subsystem", type="pci", managed="no")
            source = element(node, "source")
            busdomain, bus, slotfunc = device["bdf"].split(":")
            pcislot, function = slotfunc.split(".")
            element(source, "address", domain="0x" + busdomain, bus="0x" + bus,
                    slot="0x" + pcislot, function="0x" + function)
    serial = element(devices, "serial", type="pty")
    element(serial, "target", port="0")
    console = element(devices, "console", type="pty")
    element(console, "target", type="serial", port="0")
    element(element(devices, "video"), "model", type="none")
    element(devices, "memballoon", model="none")
    ET.indent(root)
    return ET.tostring(root, encoding="unicode") + "\n"


def cloud_init(config, name, instance_id, slot, guest_script):
    data = {
        "hostname": name, "manage_etc_hosts": True, "disable_root": True,
        "ssh_pwauth": False,
        "users": [{"name": "codex", "shell": "/bin/bash", "lock_passwd": True,
                   "sudo": "ALL=(ALL) NOPASSWD:ALL", "ssh_authorized_keys": config["public_keys"]}],
        "mounts": [["host-data", "/mnt/data", "9p",
                    "ro,trans=virtio,version=9p2000.L,msize=1048576,cache=mmap,nodev,nosuid,noexec", "0", "0"]],
        "write_files": [
            {"path": "/etc/codex-vm-guest", "content": instance_id + "\n"},
            {"path": "/etc/codex-vm-nvidia-package", "content": config["nvidia_package"] + "\n"},
            {"path": "/usr/local/sbin/provision-codex-vm", "permissions": "0755", "content": guest_script},
            {"path": "/etc/ssh/sshd_config.d/00-codex-vm.conf", "content":
             "PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\nAllowUsers codex\nAllowAgentForwarding no\n"},
            {"path": "/etc/profile.d/codex-path.sh", "content": 'export PATH="$HOME/.local/bin:$PATH"\n'},
            {"path": "/etc/sysctl.d/90-codex-vm.conf", "content":
             "net.ipv6.conf.all.disable_ipv6=1\nnet.ipv6.conf.default.disable_ipv6=1\n"}],
        "runcmd": [["sysctl", "--system"], ["systemctl", "restart", "ssh"],
                    ["/usr/local/sbin/provision-codex-vm"]],
    }
    metadata = {"instance-id": instance_id, "local-hostname": name}
    network = {"version": 2, "ethernets": {"main": {
        "match": {"macaddress": mac(slot)}, "set-name": "ens3", "dhcp4": True,
        "dhcp6": False, "accept-ra": False}}}
    return {"user-data": "#cloud-config\n" + json.dumps(data, indent=2) + "\n",
            "meta-data": json.dumps(metadata) + "\n", "network-config": json.dumps(network) + "\n"}
