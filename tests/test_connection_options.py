import argparse
import io
import os
import queue
import runpy
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

import frida
import paramiko

import dump


class ConnectionOptionsTests(unittest.TestCase):
    def setUp(self):
        dump.file_dict.clear()
        dump.bundle_metadata.clear()

    def run_cli(self, *arguments):
        with patch('sys.argv', ['dump.py', *arguments]), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(dump.__file__, run_name='__main__')
        return result.exception.code, output.getvalue()

    def test_default_still_selects_usb(self):
        with patch.object(frida, 'get_usb_device') as usb, \
                patch.object(frida, 'get_device') as specific, \
                patch.object(frida, 'get_device_manager') as manager:
            self.assertIs(dump.get_frida_device(), usb.return_value)
        usb.assert_called_once_with(timeout=10)
        specific.assert_not_called()
        manager.assert_not_called()

    def test_explicit_device_id_selects_only_that_usb_device(self):
        device = Mock(type='usb')
        with patch.object(frida, 'get_device', return_value=device) as selected, \
                patch.object(frida, 'get_usb_device') as default:
            self.assertIs(dump.get_frida_device(device_id='device-two'), device)
            selected.assert_called_once_with('device-two', timeout=10)
            default.assert_not_called()

    def test_invalid_device_does_not_fall_back(self):
        with patch.object(frida, 'get_device', side_effect=frida.InvalidArgumentError('missing')), \
                patch.object(frida, 'get_usb_device') as default:
            code, output = self.run_cli('--device-id', 'missing', '-l')
        self.assertIn('missing', str(code))
        default.assert_not_called()

    def test_device_id_rejects_local_or_remote_device(self):
        for device_type in ('local', 'remote'):
            with self.subTest(device_type=device_type), \
                    patch.object(frida, 'get_device', return_value=Mock(type=device_type)):
                with self.assertRaisesRegex(ValueError, 'USB device'):
                    dump.get_frida_device(device_id='wrong-device')

    def test_remote_listing_needs_neither_usb_nor_ssh(self):
        device = Mock()
        device.enumerate_applications.return_value = []
        with patch.object(frida, 'get_device_manager') as manager, \
                patch.object(frida, 'get_usb_device') as usb, \
                patch.object(dump.paramiko, 'SSHClient') as ssh:
            manager.return_value.add_remote_device.return_value = device
            code, output = self.run_cli('--remote', '192.0.2.10:28042', '-l')
        self.assertEqual(code, 0)
        manager.return_value.add_remote_device.assert_called_once_with('192.0.2.10:28042')
        device.enumerate_applications.assert_called_once()
        usb.assert_not_called()
        ssh.assert_not_called()

    def test_device_selection_also_applies_to_listing(self):
        device = Mock(type='usb')
        device.enumerate_applications.return_value = []
        with patch.object(frida, 'get_device', return_value=device) as selected:
            code, output = self.run_cli('-D', 'device-two', '-l')
        self.assertEqual(code, 0)
        selected.assert_called_once_with('device-two', timeout=10)
        device.enumerate_applications.assert_called_once()

    def test_remote_failure_does_not_fall_back_to_usb(self):
        with patch.object(frida, 'get_device_manager') as manager, \
                patch.object(frida, 'get_usb_device') as usb:
            manager.return_value.add_remote_device.side_effect = frida.ServerNotRunningError('refused')
            code, output = self.run_cli('-R', '192.0.2.10', '-l')
        self.assertIn('remote Frida', str(code))
        self.assertIn('refused', str(code))
        usb.assert_not_called()

    def test_invalid_options_fail_before_connecting(self):
        cases = [
            ['-R', 'host', '-D', 'id', '-l'],
            ['-D', '', '-l'],
            ['-R', 'host', '--ssh-transport', 'usb', '-l'],
            ['--agent-timeout', '0', '-l'],
            ['--agent-timeout', 'nan', '-l'],
            ['--transfer-timeout', '-1', '-l'],
            ['--transfer-timeout', 'inf', '-l'],
            ['-R', 'http://host', '-l'],
            ['-R', 'host:70000', '-l'],
            ['-R', 'user:password@host', '-l'],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments), \
                    patch.object(frida, 'get_device_manager') as manager, \
                    patch.object(frida, 'get_usb_device') as usb:
                code, output = self.run_cli(*arguments)
                self.assertEqual(code, 2)
                manager.assert_not_called()
                usb.assert_not_called()

    def test_remote_defaults_to_tcp_ssh_on_same_host_with_independent_port(self):
        for endpoint, host in [('192.0.2.10:28042', '192.0.2.10'),
                               ('localhost:28042', 'localhost'),
                               ('[2001:db8::1]:28042', '2001:db8::1')]:
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / 'config'
                config.write_text('')
                args = argparse.Namespace(remote=endpoint, ssh_host=None, ssh_port=None,
                                          ssh_user=None, ssh_password=None, ssh_key_filename=None)
                options = dump.resolve_ssh_options(args, str(config))
                self.assertEqual(options['host'], host)
                self.assertEqual(options['port'], 22)
                self.assertFalse(options['use_usb'])

    def test_remote_preserves_explicit_ssh_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config'
            config.write_text('Host iphone\n HostName 127.0.0.1\n Port 2222\n User mobile\n')
            args = argparse.Namespace(remote='192.0.2.10:28042', ssh_host='iphone', ssh_port=None,
                                      ssh_user=None, ssh_password=None, ssh_key_filename=None)
            options = dump.resolve_ssh_options(args, str(config))
        self.assertEqual(options['host'], '127.0.0.1')
        self.assertEqual(options['port'], 2222)
        self.assertEqual(options['username'], 'mobile')
        self.assertFalse(options['use_usb'])

    def test_remote_export_uses_tcp_ssh_without_usb(self):
        device, ssh = Mock(), Mock()
        ssh.connect.side_effect = paramiko.AuthenticationException('test credentials')
        with patch.object(frida, 'get_device_manager') as manager, \
                patch.object(frida, 'get_usb_device') as usb, \
                patch.object(dump.paramiko, 'SSHClient', return_value=ssh):
            manager.return_value.add_remote_device.return_value = device
            code, output = self.run_cli('-R', '192.0.2.10:28042', '--agent-timeout', '240',
                                        '--transfer-timeout', '180', 'example.app')
        self.assertEqual(code, 1)
        self.assertEqual(ssh.connect.call_args.args, ('192.0.2.10',))
        self.assertEqual(ssh.connect.call_args.kwargs['port'], 22)
        self.assertNotIn('sock', ssh.connect.call_args.kwargs)
        ssh.close.assert_called_once()
        usb.assert_not_called()

    def test_agent_silence_timeout_aborts_and_closes_transfers(self):
        session, ssh = Mock(is_detached=False), Mock()
        with patch.object(dump, 'AGENT_TIMEOUT', 0.01), \
                patch.object(dump, 'load_js_file', return_value=Mock()), \
                patch.object(dump, 'generate_ipa') as package:
            with self.assertRaisesRegex(RuntimeError, '0.01 seconds'):
                dump.start_dump(session, 'test', ssh_client=ssh)
        ssh.close.assert_called()
        package.assert_not_called()
        session.detach.assert_called_once()

    def test_remote_cli_exports_with_custom_timeouts_and_stage_diagnostics(self):
        device, session, script, ssh, sftp = Mock(), Mock(is_detached=False), Mock(), Mock(), Mock()
        device.enumerate_applications.return_value = [
            Mock(identifier='example.app', name='Example', pid=42)]
        device.attach.return_value = session
        session.create_script.return_value = script
        sftp.listdir_attr.return_value = []
        script.on.side_effect = lambda signal, callback: setattr(script, 'callback', callback)
        def post(_):
            for payload in ({'app': '/Example.app'}, {'done': 'ok'}):
                script.callback({'type': 'send', 'payload': payload}, None)
        script.post.side_effect = post
        waits = []
        queue_type = queue.Queue
        class RecordingQueue(queue_type):
            def get(self, block=True, timeout=None):
                waits.append(timeout)
                return super().get(block=block, timeout=timeout)
        isdir = os.path.isdir
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(frida, 'get_device_manager') as manager, \
                patch.object(frida, 'get_usb_device') as usb, \
                patch.object(frida, 'Compiler') as compiler, \
                patch.object(paramiko, 'SFTPClient', return_value=sftp), \
                patch.object(dump.paramiko, 'SSHClient', return_value=ssh), \
                patch.object(dump.queue, 'Queue', RecordingQueue), \
                patch.object(os.path, 'isdir', side_effect=lambda path: (
                    True if str(path).replace('\\', '/').endswith('node_modules/frida-objc-bridge') else isdir(path))):
            manager.return_value.add_remote_device.return_value = device
            compiler.return_value.build.return_value = 'test agent'
            output_path = Path(directory) / 'output.ipa'
            code, output = self.run_cli('-R', '192.0.2.10:28042', '-v', '-u', 'mobile',
                                        '--agent-timeout', '240', '--transfer-timeout', '180',
                                        '-o', str(output_path), 'example.app')
            self.assertEqual(code, 0, output)
            with zipfile.ZipFile(output_path) as archive:
                self.assertIn('Payload/Example.app/', archive.namelist())
                self.assertIsNone(archive.testzip())
        self.assertEqual(waits, [240, 240])
        self.assertEqual(compiler.return_value.build.call_args.kwargs['source_maps'], 'omitted')
        ssh.get_transport.return_value.open_session.assert_called_once_with(timeout=180)
        ssh.get_transport.return_value.open_session.return_value.settimeout.assert_called_once_with(180)
        self.assertEqual(ssh.connect.call_args.args, ('192.0.2.10',))
        self.assertEqual(ssh.connect.call_args.kwargs['username'], 'mobile')
        self.assertIn('Attaching to running PID 42', output)
        self.assertIn('Packaging IPA', output)
        device.attach.assert_called_once_with(42)
        device.spawn.assert_not_called()
        session.detach.assert_called_once()
        ssh.close.assert_called_once()
        usb.assert_not_called()

    def test_transfer_timeout_reaches_scp_and_cleanup_sftp(self):
        with tempfile.TemporaryDirectory() as directory:
            ssh, client = Mock(), Mock()
            def download(remote, destination):
                (Path(destination) / 'frida-ios-dump-test-0.fid').write_bytes(b'dumped')
            client.get.side_effect = download
            manager = Mock()
            manager.__enter__ = Mock(return_value=client)
            manager.__exit__ = Mock(return_value=False)
            with patch.object(dump, 'TRANSFER_TIMEOUT', 180), \
                    patch.object(dump, 'PAYLOAD_PATH', directory), \
                    patch.object(dump, 'ssh', ssh, create=True), \
                    patch.object(dump, 'SCPClient', return_value=manager) as scp, \
                    patch.object(paramiko, 'SFTPClient'):
                dump.handle_payload({'dump': '/Documents/frida-ios-dump-test-0.fid',
                                     'path': '/Test.app/Main'})
            self.assertEqual(scp.call_args.kwargs['socket_timeout'], 180)
            ssh.get_transport.return_value.open_session.assert_called_once_with(timeout=180)
            ssh.get_transport.return_value.open_session.return_value.settimeout.assert_called_once_with(180)

    def test_transfer_timeout_reaches_resource_scan_and_workers(self):
        listing, worker, ssh = Mock(), Mock(), Mock()
        entry = paramiko.SFTPAttributes()
        entry.filename, entry.st_mode, entry.st_size = 'resource', 0o100644, 1
        listing.listdir_attr.return_value = [entry]
        def download(remote, local, callback):
            Path(local).write_bytes(b'x')
            callback(1, 1)
        worker.get.side_effect = download
        channels = [Mock(), Mock()]
        ssh.get_transport.return_value.open_session.side_effect = channels
        with tempfile.TemporaryDirectory() as directory, patch.object(dump, 'TRANSFER_TIMEOUT', 180), \
                patch.object(paramiko, 'SFTPClient', side_effect=[listing, worker]):
            dump.download_app(ssh, '/Test.app', directory, [], Mock())
        self.assertEqual(ssh.get_transport.return_value.open_session.call_count, 2)
        for channel in channels:
            channel.settimeout.assert_called_once_with(180)

    def test_verbose_shows_attach_stage_without_credential_details(self):
        device = Mock()
        device.enumerate_applications.return_value = [Mock(identifier='example.app', name='Example', pid=42)]
        device.attach.side_effect = RuntimeError('attach failed')
        with patch.object(dump, 'VERBOSE', True), redirect_stdout(io.StringIO()) as output:
            dump.connect_ssh(Mock(), device, 'host', 22, 'mobile', 'secret-password', '/private/key')
            with self.assertRaisesRegex(RuntimeError, 'attach failed'):
                dump.open_target_app(device, 'example.app')
        self.assertIn('Attaching to running PID 42', output.getvalue())
        self.assertNotIn('secret-password', output.getvalue())
        self.assertNotIn('/private/key', output.getvalue())
        device.spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
