"""Offline tests of boundaries, rendered devices and destructive-operation guards."""
import argparse
import base64
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render
import vm


def key():
    def field(value):
        return struct.pack('>I', len(value)) + value
    blob = base64.b64encode(field(b'ssh-ed25519') + field(bytes(range(32)))).decode()
    return f'ssh-ed25519 {blob} client'


def config():
    return {'schema': 2, 'cpus': 8, 'memory_mib': 16384, 'disk_gib': 100,
            'nvidia_package': 'nvidia-driver-575-open', 'public_keys': [key()],
            'gpu': [{'bdf': '0000:01:00.0', 'id': '10de:2b85'},
                    {'bdf': '0000:01:00.1', 'id': '10de:22e8'}]}


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_public_keys_reject_private_options_and_invalid_blobs(self):
        path = self.root / 'keys'
        for invalid in ['-----BEGIN OPENSSH PRIVATE KEY-----', 'command="id" ' + key(),
                        'ssh-ed25519 AAAA', 'ssh-ed25519 !!!']:
            path.write_text(invalid)
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                vm.public_keys(path)
        path.write_text('# client keys\n' + key() + '\n')
        self.assertEqual(vm.public_keys(path), [key()])

    def test_names_and_resources_cannot_be_paths_or_shell(self):
        for invalid in ['../host', '-x', 'a/b', 'x;id', '', 'A']:
            with self.subTest(name=invalid), self.assertRaises(ValueError):
                vm.name(invalid)
        for field, value in [('cpus', 1), ('memory_mib', -1), ('disk_gib', 4097),
                             ('nvidia_package', 'nvidia-driver-575-open;id')]:
            data = config()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                vm.validate_config(data)

    def test_domain_has_host_enforced_readonly_share_and_no_management_channels(self):
        xml = ET.fromstring(render.domain(config(), 'work', 2, '/disk', '/seed'))
        share = xml.find('./devices/filesystem')
        self.assertEqual(share.find('source').get('dir'), '/mnt/data')
        self.assertEqual(share.find('target').get('dir'), 'host-data')
        self.assertEqual(share.find('driver').get('type'), 'path')
        self.assertIsNotNone(share.find('readonly'))
        self.assertEqual(xml.find('./devices/interface/port').get('isolated'), 'yes')
        self.assertEqual(len(xml.findall('./devices/hostdev')), 2)
        self.assertTrue(all(d.get('managed') == 'no' for d in xml.findall('./devices/hostdev')))
        self.assertEqual(xml.findall('./devices/channel'), [])
        self.assertEqual(xml.findall('./devices/graphics'), [])
        self.assertEqual(xml.find('./os/firmware/feature').get('enabled'), 'no')
        self.assertEqual(xml.find('./devices/disk/driver').get('type'), 'qcow2')

    def test_cpu_builder_does_not_take_the_gpu(self):
        xml = ET.fromstring(render.domain(config(), 'builder', 2, '/disk', '/seed', gpu=False))
        self.assertEqual(xml.findall('./devices/hostdev'), [])

    def test_clones_have_distinct_network_and_cloud_init_identity(self):
        a = render.cloud_init(config(), 'one', 'uuid-one', 2, 'guest script')
        b = render.cloud_init(config(), 'two', 'uuid-two', 3, 'guest script')
        self.assertNotEqual(a['meta-data'], b['meta-data'])
        self.assertNotEqual(a['network-config'], b['network-config'])
        for data in [a, b]:
            user = json.loads(data['user-data'].split('\n', 1)[1])
            self.assertFalse(user['ssh_pwauth'])
            self.assertTrue(user['disable_root'])
            self.assertEqual(user['mounts'][0][1:3], ['/mnt/data', '9p'])
            self.assertIn('ro,', user['mounts'][0][3])
            self.assertEqual(user['users'][0]['ssh_authorized_keys'], [key()])
            self.assertEqual(user['users'][0]['sudo'], 'ALL=(ALL) NOPASSWD:ALL')

    def test_iommu_group_with_unrelated_device_is_rejected(self):
        root = self.root / 'pci'
        root.mkdir()
        for bdf, cls, devid in [('0000:01:00.0', '0x030000', '0x2b85'),
                                ('0000:01:00.1', '0x040300', '0x22e8')]:
            device = root / bdf
            device.mkdir()
            for filename, text in [('vendor', '0x10de'), ('device', devid), ('class', cls)]:
                (device / filename).write_text(text)
            group = device / 'iommu_group/devices'
            group.mkdir(parents=True)
            (group / bdf).touch()
        self.assertEqual(len(vm.gpu_devices(root=root)), 2)
        (root / '0000:01:00.0/iommu_group/devices/0000:02:00.0').touch()
        with self.assertRaisesRegex(ValueError, 'unrelated'):
            vm.gpu_devices(root=root)

    def test_prepare_gpu_rejects_display_gpu_before_writing_files(self):
        gpu = self.root / '0000:01:00.0'
        gpu.mkdir()
        (gpu / 'boot_vga').write_text('1')
        with patch.object(vm, 'SYS_PCI', self.root), patch.object(vm, 'check_gpu'), patch.object(vm, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'firmware boot GPU'):
                vm.prepare_gpu(config(), True)
            run.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'acknowledge'):
            vm.prepare_gpu(config(), False)

    def test_discard_without_explicit_yes_does_nothing(self):
        with patch.object(vm, 'virsh') as virsh, patch.object(vm.shutil, 'rmtree') as remove:
            with self.assertRaisesRegex(ValueError, '--yes'):
                vm.discard('work')
            virsh.assert_not_called()
            remove.assert_not_called()

    def test_running_guest_cannot_be_discarded(self):
        with patch.object(vm, 'managed', return_value=(self.root, {'slot': 2})), \
             patch.object(vm, 'exists', return_value=True), \
             patch.object(vm, 'virsh', return_value=argparse.Namespace(stdout='running\n')) as virsh, \
             patch.object(vm.shutil, 'rmtree') as remove:
            with self.assertRaisesRegex(ValueError, 'shut off'):
                vm.discard('work', True)
            self.assertEqual(virsh.call_count, 1)
            self.assertEqual(virsh.call_args.args, ('domstate', 'work'))
            remove.assert_not_called()

    def test_inactive_firewall_blocks_guest_start(self):
        with patch.object(vm, 'run', return_value=argparse.Namespace(returncode=1)), \
             patch.object(vm, 'virsh') as virsh:
            with self.assertRaisesRegex(ValueError, 'firewall service is inactive'):
                vm.boundary()
            virsh.assert_not_called()

    def test_gpu_owned_by_active_domain_is_not_shared(self):
        root = self.root / 'pci'
        root.mkdir()
        driver = self.root / 'vfio-pci'
        driver.mkdir()
        for d in config()['gpu']:
            device = root / d['bdf']
            device.mkdir()
            (device / 'driver').symlink_to(driver)
        xml = render.domain(config(), 'existing', 2, '/disk', '/seed')
        def response(*args, **kwargs):
            return argparse.Namespace(stdout='existing\n' if args[0] == 'list' else xml)
        with patch.object(vm, 'SYS_PCI', root), \
             patch.object(vm, 'gpu_devices', return_value=config()['gpu']), \
             patch.object(vm, 'virsh', side_effect=response):
            with self.assertRaisesRegex(ValueError, 'already assigned'):
                vm.check_gpu(config(), bound=True)

    def test_existing_name_is_not_overwritten(self):
        args = argparse.Namespace(name='work', cpu_only=False, base=None)
        with patch.object(vm, 'exists', return_value=True), patch.object(vm, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'nothing overwritten'):
                vm.create(config(), args)
            run.assert_not_called()

    def test_installer_opt_in_and_bad_key_fail_before_sudo(self):
        script = Path(__file__).resolve().parents[2] / 'setup_codex_vm.sh'
        env = dict(os.environ, DOTFILES_CODEX_VM='0')
        result = subprocess.run([str(script)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('Skipping', result.stdout)
        invalid = self.root / 'invalid'
        invalid.write_text('private key is forbidden')
        marker = self.root / 'sudo-called'
        fake_sudo = self.root / 'sudo'
        fake_sudo.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 99\n')
        fake_sudo.chmod(0o755)
        env.update(DOTFILES_CODEX_VM='1', CODEX_VM_SSH_PUBLIC_KEY=str(invalid),
                   PATH=str(self.root) + os.pathsep + os.environ['PATH'])
        result = subprocess.run([str(script)], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
