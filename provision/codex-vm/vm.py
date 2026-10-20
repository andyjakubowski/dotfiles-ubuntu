#!/usr/bin/env python3
"""Host-owned lifecycle commands for isolated GPU workstations.

configure/inspect are read-only except explicitly requested output files.
All lifecycle changes require root and use the system libvirt connection.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

import render

CONFIG = Path('/etc/codex-vm/config.json')
STATE = Path('/var/lib/codex-vm')
IMAGES = Path('/var/lib/libvirt/images/codex-vm')
CACHE = Path('/var/cache/cloud-images/ubuntu')
HERE = Path(__file__).resolve().parent
SYS_PCI = Path('/sys/bus/pci/devices')


def fail(message):
    raise ValueError(message)


def run(*args, capture=False, check=True):
    return subprocess.run([str(a) for a in args], check=check, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          env=dict(os.environ, LC_ALL='C', LIBGUESTFS_BACKEND='direct'))


def virsh(*args, capture=False, check=True):
    return run('virsh', '--connect', 'qemu:///system', *args, capture=capture, check=check)


def name(value):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', value):
        fail('Names must be 1–31 lowercase letters/digits/hyphens, starting with a letter.')
    return value


def public_keys(path):
    keys = []
    for line in Path(path).read_text().splitlines():
        key = line.strip()
        if not key or key.startswith('#'):
            continue
        if not re.fullmatch(r'(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/]+={0,2}(?: [^\r\n]*)?', key):
            fail('Expected plain OpenSSH public keys. Private keys and authorized_keys options are refused.')
        base64.b64decode(key.split()[1], validate=True)
        with tempfile.NamedTemporaryFile(mode='w') as temp:
            temp.write(key + '\n')
            temp.flush()
            if run('ssh-keygen', '-l', '-f', temp.name, capture=True, check=False).returncode:
                fail('Invalid OpenSSH public key.')
        keys.append(key)
    if not keys:
        fail('No public SSH keys found.')
    return keys


def gpu_devices(bdf='', root=None):
    root = root or SYS_PCI
    if not bdf:
        candidates = [p.name for p in root.iterdir()
                      if (p / 'vendor').read_text().strip() == '0x10de'
                      and (p / 'class').read_text().strip().startswith('0x03')]
        if len(candidates) != 1:
            fail('Expected one NVIDIA display GPU; set CODEX_VM_GPU_BDF explicitly.')
        bdf = candidates[0]
    if not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.0', bdf):
        fail('GPU address must have the form 0000:01:00.0.')
    primary = root / bdf
    if (primary / 'vendor').read_text().strip() != '0x10de' or not (primary / 'class').read_text().startswith('0x03'):
        fail('Selected device is not an NVIDIA display controller.')
    devices = sorted(root.glob(bdf[:-1] + '*'))
    expected = {p.name for p in devices}
    for device in devices:
        group = device / 'iommu_group/devices'
        if not group.exists():
            fail('No IOMMU group. Enable IOMMU in firmware/kernel; unsafe passthrough is not supported.')
        if not {p.name for p in group.iterdir()} <= expected:
            fail('GPU IOMMU group contains unrelated devices; refusing passthrough without isolation.')
    return [{'bdf': p.name, 'id': (p / 'vendor').read_text().strip()[2:] + ':' +
             (p / 'device').read_text().strip()[2:]} for p in devices]


def validate_config(config):
    if config.get('schema') != 2:
        fail('Unknown configuration schema; rerun the dotfiles host setup.')
    for key, minimum, maximum in [('cpus', 2, 256), ('memory_mib', 2048, 1048576), ('disk_gib', 20, 4096)]:
        value = config[key]
        if type(value) is not int or not minimum <= value <= maximum:
            fail('Invalid ' + key)
    if not re.fullmatch(r'nvidia-driver-[0-9]+-open', config['nvidia_package']):
        fail('Select an Ubuntu nvidia-driver-NNN-open package.')
    if not config['public_keys']:
        fail('No public keys configured.')
    return config


def configure(args):
    config = validate_config({'schema': 2, 'cpus': int(args.cpus), 'memory_mib': int(args.memory),
                              'disk_gib': int(args.disk), 'nvidia_package': args.nvidia_package,
                              'public_keys': public_keys(args.key), 'gpu': gpu_devices(args.gpu)})
    Path(args.output).write_text(json.dumps(config, indent=2) + '\n')
    print('Validated configuration; authorizing', len(config['public_keys']), 'public key(s).')
    print('GPU functions:', ', '.join(d['bdf'] for d in config['gpu']))


def load_config():
    return validate_config(json.loads(CONFIG.read_text()))


def check_gpu(config, bound=False):
    observed = gpu_devices(config['gpu'][0]['bdf'])
    if observed != config['gpu']:
        fail('PCI hardware changed; rerun dotfiles configuration before assigning devices.')
    if bound:
        for device in observed:
            path = SYS_PCI / device['bdf'] / 'driver'
            if not path.exists() or path.resolve().name != 'vfio-pci':
                fail('GPU is not reserved for VFIO. Run prepare-gpu, reboot and inspect first.')
        for active in virsh('list', '--name', capture=True).stdout.split():
            xml = ET.fromstring(virsh('dumpxml', active, capture=True).stdout)
            configured = {d['bdf'] for d in observed}
            for address in xml.findall('./devices/hostdev/source/address'):
                bdf = f"{int(address.get('domain', '0'), 0):04x}:{int(address.get('bus', '0'), 0):02x}:{int(address.get('slot', '0'), 0):02x}.{int(address.get('function', '0'), 0):x}"
                if bdf in configured:
                    fail('The GPU is already assigned to running domain ' + active)


def inspect(config):
    check_gpu(config)
    for device in config['gpu']:
        path = SYS_PCI / device['bdf']
        driver = (path / 'driver').resolve().name if (path / 'driver').exists() else 'none'
        print(device['bdf'], device['id'], 'driver=' + driver, 'group=' + (path / 'iommu_group').resolve().name)
    print('Models: host /mnt/data -> guest /mnt/data (QEMU read-only 9p).')
    if shutil.which('virsh'):
        virsh('list', '--all')


def require_mount():
    if not os.path.ismount('/mnt/data') or Path('/mnt/data').resolve() != Path('/mnt/data'):
        fail('/mnt/data must be the actual mounted data SSD, not a symlink or empty fallback directory.')
    # Nested mounts can expose unexpected filesystems. Only the selected SSD is exported.
    mounts = json.loads(run('findmnt', '--json', '--submounts', '/mnt/data', capture=True).stdout)
    if mounts['filesystems'][0].get('children'):
        fail('/mnt/data contains nested mounts; review them before exporting the whole directory.')


def exists(vmname):
    return virsh('dominfo', vmname, capture=True, check=False).returncode == 0


def managed(vmname):
    name(vmname)
    directory = STATE / 'instances' / vmname
    if not (directory / 'instance.json').is_file() or directory.is_symlink():
        fail('Not a managed instance: ' + vmname)
    info = json.loads((directory / 'instance.json').read_text())
    if info['name'] != vmname:
        fail('Instance record does not match its directory.')
    return directory, info


def stopped(vmname):
    if virsh('domstate', vmname, capture=True).stdout.strip() != 'shut off':
        fail('Guest must be shut off first. Use stop, then inspect its state; no forced shutdown is performed.')


def ensure_capacity(config):
    if config['cpus'] > len(os.sched_getaffinity(0)):
        fail('Requested vCPUs exceed available host CPUs.')
    available = next(int(line.split()[1]) // 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    if config['memory_mib'] + 2048 > available:
        fail('Insufficient available host RAM; leave at least 2 GiB for the host.')
    if shutil.disk_usage(IMAGES).free < (config['disk_gib'] + 5) * 1024**3:
        fail('Insufficient system-disk headroom for the guest disk and download overhead.')


def image():
    CACHE.mkdir(parents=True, exist_ok=True)
    filename = 'noble-server-cloudimg-amd64.img'
    url = 'https://cloud-images.ubuntu.com/noble/current/'
    sums = CACHE / 'SHA256SUMS'
    run('curl', '-fSL', url + 'SHA256SUMS', '-o', sums)
    candidates = [line.split()[0] for line in sums.read_text().splitlines()
                  if len(line.split()) == 2 and line.split()[1].lstrip('*') == filename]
    if len(candidates) != 1 or not re.fullmatch('[a-f0-9]{64}', candidates[0]):
        fail('Missing or ambiguous Canonical image checksum.')
    digest = candidates[0]
    target = CACHE / (digest + '.qcow2')
    if target.exists():
        with target.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() == digest:
                return target
    partial = CACHE / (digest + '.partial')
    run('curl', '-fSL', url + filename, '-o', partial)
    with partial.open('rb') as handle:
        if hashlib.file_digest(handle, 'sha256').hexdigest() != digest:
            fail('Image checksum mismatch; no guest is started. Retry to fetch a consistent current release.')
    partial.replace(target)
    return target


def choose_slot():
    network = ET.fromstring(virsh('net-dumpxml', 'codex-vm', capture=True).stdout)
    used = {int(h.get('ip').split('.')[-1]) for h in network.findall('./ip/dhcp/host')}
    for p in (STATE / 'instances').glob('*/instance.json'):
        used.add(json.loads(p.read_text())['slot'])
    for slot in range(2, 255):
        if slot not in used:
            return slot
    fail('No free private network addresses.')


def ownership(path, readonly=False):
    run('chown', 'libvirt-qemu:kvm', path)
    path.chmod(0o440 if readonly else 0o600)


def boundary():
    if run('systemctl', 'is-active', '--quiet', 'codex-vm-firewall.service', check=False).returncode:
        fail('The host firewall service is inactive; refusing to start a guest.')
    run('nft', 'list', 'table', 'inet', 'codex_vm', capture=True)
    if 'codex-vm' not in virsh('net-list', '--name', capture=True).stdout.split():
        fail('The managed guest network is inactive; rerun host setup.')


def create(config, args):
    vmname = name(args.name)
    state = STATE / 'instances' / vmname
    disks = IMAGES / 'instances' / vmname
    if exists(vmname) or state.exists() or disks.exists():
        fail('Name or partial instance already exists; nothing overwritten. Inspect it before recovery/discard.')
    boundary()
    require_mount()
    ensure_capacity(config)
    if not args.cpu_only:
        check_gpu(config, bound=True)
    if args.base:
        base = IMAGES / 'bases' / (name(args.base) + '.qcow2')
        if not base.is_file() or base.is_symlink():
            fail('Unknown base image.')
        base_size = json.loads(run('qemu-img', 'info', '--output=json', base, capture=True).stdout)['virtual-size']
        if config['disk_gib'] * 1024**3 < base_size:
            fail('Configured disk is smaller than the base; shrinking is not supported.')
    else:
        base = image()
    slot = choose_slot()
    state.mkdir(parents=True, mode=0o755)
    disks.mkdir(parents=True, mode=0o755)
    record = {'name': vmname, 'slot': slot, 'id': str(uuid.uuid4()), 'gpu': not args.cpu_only,
              'base': args.base, 'config': config}
    (state / 'instance.json').write_text(json.dumps(record, indent=2) + '\n')
    root = disks / 'root.qcow2'
    if args.base:
        run('qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', base, root, f"{config['disk_gib']}G")
    else:
        run('qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2', base, root)
        run('qemu-img', 'resize', root, f"{config['disk_gib']}G")
    ownership(root)
    for filename, content in render.cloud_init(config, vmname, record['id'], slot,
                                               (HERE / 'guest.sh').read_text()).items():
        (state / filename).write_text(content)
    seed = disks / 'seed.iso'
    run('cloud-localds', '--network-config=' + str(state / 'network-config'), seed,
        state / 'user-data', state / 'meta-data')
    ownership(seed, readonly=True)
    xml = render.domain(config, vmname, slot, root, seed, gpu=record['gpu'])
    definition = state / 'domain.xml'
    definition.write_text(xml)
    reservation = ET.Element('host', {'mac': render.mac(slot), 'name': vmname,
                                     'ip': f'192.168.250.{slot}'})
    host = ET.tostring(reservation, encoding='unicode')
    # A failed create leaves its record/disk for inspection, never erases data automatically.
    virsh('net-update', 'codex-vm', 'add', 'ip-dhcp-host', host, '--live', '--config')
    virsh('define', '--validate', definition)
    virsh('autostart', '--disable', vmname)
    virsh('start', vmname)
    print(f'Created {vmname}: codex@192.168.250.{slot}, ProxyJump ai-box. First-boot setup is asynchronous.')


def start(config, vmname):
    _, info = managed(vmname)
    stopped(vmname)
    boundary()
    require_mount()
    ensure_capacity(info['config'])
    if info['gpu']:
        check_gpu(info['config'], bound=True)
    virsh('start', vmname)


def discard(vmname, yes=False):
    if not yes:
        fail('Discard deletes guest files. Explicit --yes is required; export needed work first.')
    directory, info = managed(vmname)
    if exists(vmname):
        stopped(vmname)
    # Refuse deletion if someone attached this disk to another defined domain.
    disk = IMAGES / 'instances' / vmname / 'root.qcow2'
    for other in virsh('list', '--all', '--name', capture=True).stdout.split():
        if other != vmname:
            xml = ET.fromstring(virsh('dumpxml', other, capture=True).stdout)
            if any(n.get('file') == str(disk) for n in xml.findall('./devices/disk/source')):
                fail('Guest disk is referenced by another domain: ' + other)
    if exists(vmname):
        virsh('undefine', vmname, '--nvram')
    network = ET.fromstring(virsh('net-dumpxml', 'codex-vm', capture=True).stdout)
    for host in network.findall('./ip/dhcp/host'):
        if host.get('mac') == render.mac(info['slot']):
            virsh('net-update', 'codex-vm', 'delete', 'ip-dhcp-host', ET.tostring(host, encoding='unicode'), '--live', '--config')
    disks = IMAGES / 'instances' / vmname
    if disks.is_symlink():
        fail('Refusing to remove a symlinked instance directory.')
    if disks.exists():
        shutil.rmtree(disks)
    shutil.rmtree(directory)
    print('Discarded', vmname, '(base images and /mnt/data retained).')


def template(vmname, basename):
    directory, info = managed(vmname)
    stopped(vmname)
    source = IMAGES / 'instances' / vmname / 'root.qcow2'
    destination = IMAGES / 'bases' / (name(basename) + '.qcow2')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        fail('Base already exists; immutable bases are never overwritten.')
    # Disk inspection does not execute guest code; libguestfs uses an isolated appliance.
    run('virt-cat', '-a', source, '/var/lib/codex-vm-provisioned', capture=True)
    # A sparse overlay can be tiny while its backing image contains many GiB.
    # Reserve worst-case capacity instead of measuring only the overlay's blocks.
    if shutil.disk_usage(IMAGES).free < (info['config']['disk_gib'] + 5) * 1024**3:
        fail('Insufficient space for an independent base image.')
    pending = destination.with_suffix('.partial')
    if pending.exists():
        fail('Partial base exists; inspect before removing or retrying.')
    run('qemu-img', 'convert', '-O', 'qcow2', source, pending)
    run('virt-sysprep', '-a', pending, '--operations', 'machine-id,ssh-hostkeys,logfiles,tmp-files',
        '--delete', '/var/lib/cloud/*', '--delete', '/etc/netplan/50-cloud-init.yaml',
        '--delete', '/home/codex/.codex', '--delete', '/root/.codex',
        '--delete', '/home/codex/.ssh', '--delete', '/root/.ssh',
        '--delete', '/home/codex/.bash_history', '--delete', '/root/.bash_history')
    ownership(pending, readonly=True)
    pending.replace(destination)
    destination.with_suffix('.json').write_text(json.dumps({'source': info['id'], 'config': info['config']}, indent=2) + '\n')
    print('Created immutable base', basename, '; original guest retained. Bases are for clean provisioned guests, not used workspaces.')


def prepare_gpu(config, acknowledge):
    if not acknowledge:
        fail('Requires --acknowledge-display-loss. The 5090 will leave host use after reboot.')
    check_gpu(config)
    primary = SYS_PCI / config['gpu'][0]['bdf']
    if (primary / 'boot_vga').read_text().strip() == '1':
        fail('5090 is the firmware boot GPU. Set integrated AMD graphics as primary in UEFI, reboot, then retry. This avoids disabling host framebuffers globally.')
    ids = ','.join(sorted({d['id'] for d in config['gpu']}))
    # Avoid capturing another device with an identical PCI ID on a later machine.
    selected = {d['bdf'] for d in config['gpu']}
    for dev in SYS_PCI.iterdir():
        pci_id = (dev / 'vendor').read_text().strip()[2:] + ':' + (dev / 'device').read_text().strip()[2:]
        if pci_id in ids.split(',') and dev.name not in selected:
            fail('Duplicate PCI IDs outside selected GPU; refusing global ID-based binding.')
    module_file = Path('/etc/modprobe.d/codex-vm-vfio.conf')
    hook = Path('/etc/initramfs-tools/hooks/codex-vm-vfio')
    content = f'options vfio-pci ids={ids}\nsoftdep nvidia pre: vfio-pci\nsoftdep nouveau pre: vfio-pci\nsoftdep snd_hda_intel pre: vfio-pci\n'
    hook_content = '#!/bin/sh\nset -eu\ncase "${1:-}" in prereqs) exit 0 ;; esac\n. /usr/share/initramfs-tools/hook-functions\nforce_load vfio_pci\n'
    changed = False
    for path, data, mode in [(module_file, content, 0o644), (hook, hook_content, 0o755)]:
        if not path.exists() or path.read_text() != data:
            path.write_text(data)
            changed = True
        path.chmod(mode)
    pending = STATE / 'vfio-initramfs-pending'
    if changed or pending.exists():
        pending.touch()
        run('update-initramfs', '-u', '-k', 'all')
        pending.unlink()
    print('VFIO reservation configured. No live driver was detached. Reboot, then inspect before starting a GPU guest.')


def unprepare_gpu(config, acknowledge):
    if not acknowledge:
        fail('Requires --acknowledge-display-loss; host driver ownership changes on the next reboot.')
    for active in virsh('list', '--name', capture=True).stdout.split():
        xml = ET.fromstring(virsh('dumpxml', active, capture=True).stdout)
        if xml.findall('./devices/hostdev'):
            fail('Stop assigned-device guests before changing GPU reservation: ' + active)
    pending = STATE / 'vfio-initramfs-pending'
    for path in [Path('/etc/modprobe.d/codex-vm-vfio.conf'),
                 Path('/etc/initramfs-tools/hooks/codex-vm-vfio')]:
        if path.exists():
            pending.touch()
            path.unlink()
    if pending.exists():
        run('update-initramfs', '-u', '-k', 'all')
        pending.unlink()
    print('Dedicated VFIO reservation removed. Reboot to restore normal host driver binding.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    conf = commands.add_parser('configure')
    for flag in ['key', 'cpus', 'memory', 'disk', 'nvidia-package', 'output']:
        conf.add_argument('--' + flag, required=True)
    conf.add_argument('--gpu', default='')
    commands.add_parser('inspect')
    commands.add_parser('list')
    commands.add_parser('check-config').add_argument('path')
    prepare = commands.add_parser('prepare-gpu')
    prepare.add_argument('--acknowledge-display-loss', action='store_true')
    unprepare = commands.add_parser('unprepare-gpu')
    unprepare.add_argument('--acknowledge-display-loss', action='store_true')
    new = commands.add_parser('create')
    new.add_argument('name', type=name)
    new.add_argument('--base', type=name)
    new.add_argument('--cpu-only', action='store_true', help='Build/provision a base without assigning the GPU')
    for verb in ['start', 'stop', 'discard']:
        sub = commands.add_parser(verb)
        sub.add_argument('name', type=name)
        if verb == 'discard':
            sub.add_argument('--yes', action='store_true')
    base = commands.add_parser('template')
    base.add_argument('name', type=name)
    base.add_argument('base', type=name)
    args = parser.parse_args()
    if args.command == 'configure':
        configure(args)
        return
    if args.command == 'check-config':
        validate_config(json.loads(Path(args.path).read_text()))
        return
    config = load_config()
    if args.command == 'inspect':
        inspect(config)
        return
    if os.geteuid() != 0:
        fail('Lifecycle commands require host sudo; no host permissions are delegated to the guest.')
    STATE.mkdir(parents=True, exist_ok=True)
    os.umask(0o022)
    with Path('/run/lock/dotfiles-codex-vm.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == 'list':
            virsh('list', '--all')
            for record in sorted((STATE / 'instances').glob('*/instance.json')):
                info = json.loads(record.read_text())
                print(info['name'], '192.168.250.' + str(info['slot']), 'GPU=' + str(info['gpu']))
        elif args.command == 'create':
            create(config, args)
        elif args.command == 'start':
            start(config, args.name)
        elif args.command == 'stop':
            managed(args.name)
            virsh('shutdown', args.name)
        elif args.command == 'discard':
            discard(args.name, args.yes)
        elif args.command == 'template':
            template(args.name, args.base)
        elif args.command == 'prepare-gpu':
            prepare_gpu(config, args.acknowledge_display_loss)
        elif args.command == 'unprepare-gpu':
            unprepare_gpu(config, args.acknowledge_display_loss)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError) as error:
        sys.exit(str(error))
