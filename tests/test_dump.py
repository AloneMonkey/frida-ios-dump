from pathlib import Path
import argparse
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile
import paramiko
import queue
import threading
import os
import shutil
import stat
import io
import runpy
import sys
from contextlib import redirect_stdout, redirect_stderr
from concurrent.futures import ThreadPoolExecutor

import dump


class DumpTests(unittest.TestCase):
    def setUp(self):
        sftp_patch = patch.object(dump, 'open_sftp')
        self.open_sftp = sftp_patch.start()
        self.addCleanup(sftp_patch.stop)
        dump.dump_error = None
        dump.finished.clear()
        dump.file_dict.clear()
        dump.bundle_metadata.clear()
        dump.message_queue = queue.Queue()

    def bundle_link(self, payload, relative, target, directory=False):
        """Match the platform's downloaded-link representation without privileges."""
        if dump.IS_WINDOWS:
            dump.bundle_metadata['Payload/' + relative] = {
                'mode': stat.S_IFLNK | 0o777, 'link': target}
        else:
            (payload / relative).symlink_to(target, target_is_directory=directory)

    def test_bundle_download_skips_exact_binary_paths_and_keeps_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "Example's App.app"
            framework = app / 'Frameworks' / 'Test.framework'
            framework.mkdir(parents=True)
            (app / 'Empty').mkdir()
            (app / 'Resources').mkdir()
            (app / 'Example').write_bytes(b'encrypted main')
            (framework / 'Test').write_bytes(b'encrypted framework')
            (app / 'Info.plist').write_bytes(b'plist')
            (app / 'Resources' / 'Example').write_bytes(b'resource with same basename')
            (app / 'Resources' / 'helper').write_bytes(b'helper')
            (app / 'Resources' / 'helper').chmod(0o755)
            destination = root / 'Payload'
            def get(remote, local, callback):
                shutil.copyfile(remote, local)
                size = Path(remote).stat().st_size
                callback(size, size)

            clients = []

            def list_directory(path):
                entries = [paramiko.SFTPAttributes.from_stat(entry.lstat(), entry.name)
                           for entry in Path(path).iterdir()]
                if Path(path) == app:
                    link = paramiko.SFTPAttributes()
                    link.filename, link.st_mode = 'InfoLink', stat.S_IFLNK | 0o777
                    entries.append(link)
                for entry in entries:
                    if entry.filename == 'helper':
                        entry.st_mode = stat.S_IFREG | 0o755
                return entries

            def open_sftp(_ssh):
                client = Mock()
                client.listdir_attr.side_effect = list_directory
                client.readlink.return_value = 'Info.plist'
                client.get.side_effect = get
                clients.append(client)
                return client

            ssh = Mock()
            self.open_sftp.side_effect = open_sftp
            received = []
            totals = []
            scanned = []
            dump.download_app(ssh, app.as_posix(), str(destination),
                              ['Example', 'Frameworks/Test.framework/Test'], received.append,
                              set_total=totals.append,
                              scan_progress=scanned.append)
            downloaded = {Path(call.args[0]).relative_to(app).as_posix()
                          for client in clients for call in client.get.call_args_list}
            self.assertEqual(downloaded, {'Info.plist', 'Resources/Example', 'Resources/helper'})
            copied_app = destination / app.name
            self.assertTrue((copied_app / 'Empty').is_dir())
            self.assertTrue((copied_app / 'Frameworks' / 'Test.framework').is_dir())
            self.assertFalse((copied_app / 'Example').exists())
            self.assertFalse((copied_app / 'Frameworks' / 'Test.framework' / 'Test').exists())
            if dump.IS_WINDOWS:
                self.assertFalse(os.path.lexists(copied_app / 'InfoLink'))
            else:
                self.assertEqual(os.readlink(copied_app / 'InfoLink'), 'Info.plist')
                self.assertEqual(stat.S_IMODE((copied_app / 'Resources' / 'helper').stat().st_mode), 0o755)
            archive_root = 'Payload/' + app.name
            self.assertEqual(dump.bundle_metadata[archive_root + '/InfoLink']['link'], 'Info.plist')
            self.assertEqual(stat.S_IMODE(dump.bundle_metadata[archive_root + '/Resources/helper']['mode']), 0o755)
            self.assertEqual(sum(received), sum((app / name).stat().st_size for name in downloaded))
            self.assertEqual(totals, [sum(received)])
            self.assertEqual(sum(scanned), len(list(app.rglob('*'))) + 1)
            self.assertLessEqual(len(clients), 5)
            for client in clients:
                client.close.assert_called_once()

    def test_download_starts_before_directory_scan_finishes(self):
        downloaded = threading.Event()
        file_entry, directory_entry = paramiko.SFTPAttributes(), paramiko.SFTPAttributes()
        file_entry.filename, file_entry.st_mode, file_entry.st_size = 'resource', stat.S_IFREG | 0o644, 1
        directory_entry.filename, directory_entry.st_mode = 'Subdir', stat.S_IFDIR | 0o755
        listing, worker = Mock(), Mock()

        def list_directory(path):
            if path == '/Test.app':
                return [file_entry, directory_entry]
            self.assertTrue(downloaded.wait(timeout=2), 'Download must start while scanning is in progress')
            return []

        def get(remote, local, callback):
            Path(local).write_bytes(b'x')
            callback(1, 1)
            downloaded.set()

        listing.listdir_attr.side_effect = list_directory
        worker.get.side_effect = get
        ssh = Mock()
        self.open_sftp.side_effect = [listing, worker]
        totals, received = [], []
        with tempfile.TemporaryDirectory() as directory:
            dump.download_app(ssh, '/Test.app', directory, [], received.append, set_total=totals.append)
        self.assertEqual(totals, [1])
        self.assertEqual(sum(received), 1)

    def test_windows_download_packages_links_and_ios_modes_without_local_symlinks(self):
        def entry(name, mode, size=0):
            result = paramiko.SFTPAttributes()
            result.filename, result.st_mode, result.st_size = name, mode, size
            return result

        tree = {
            '/Test.app': [entry('Main', stat.S_IFREG | 0o755),
                          entry('helper', stat.S_IFREG | 0o555, 4),
                          entry('ReadOnly', stat.S_IFDIR | 0o555),
                          entry('FileLink', stat.S_IFLNK | 0o777),
                          entry('DirectoryLink', stat.S_IFLNK | 0o777),
                          entry('DanglingLink', stat.S_IFLNK | 0o777)],
            '/Test.app/ReadOnly': [entry('config', stat.S_IFREG | 0o444, 2)],
        }
        links = {'/Test.app/FileLink': 'helper', '/Test.app/DirectoryLink': 'ReadOnly',
                 '/Test.app/DanglingLink': 'missing'}
        contents = {'/Test.app/helper': b'help', '/Test.app/ReadOnly/config': b'{}'}

        def download(remote, local, callback):
            Path(local).write_bytes(contents[remote])
            callback(len(contents[remote]), len(contents[remote]))

        def open_sftp(_ssh):
            client = Mock()
            client.listdir_attr.side_effect = lambda remote: tree[remote]
            client.readlink.side_effect = lambda remote: links[remote]
            client.get.side_effect = download
            return client

        ssh = Mock()
        self.open_sftp.side_effect = open_sftp
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / 'Payload'
            payload.mkdir()
            (payload / 'task.fid').write_bytes(b'decrypted executable')
            with patch.object(dump, 'IS_WINDOWS', True), \
                    patch.object(dump.os, 'symlink', side_effect=AssertionError('Requires privilege')), \
                    patch.object(dump.os, 'chmod', side_effect=AssertionError('Must keep local files writable')):
                dump.download_app(ssh, '/Test.app', str(payload), ['Main'], Mock())
                for name in ('FileLink', 'DirectoryLink', 'DanglingLink'):
                    self.assertFalse(os.path.lexists(payload / 'Test.app' / name))
                dump.file_dict.update({'app': 'Test.app', 'task.fid': 'Main'})
                with patch.object(dump, 'PAYLOAD_PATH', str(payload)):
                    dump.generate_ipa(str(payload), str(root / 'output'))
            self.assertFalse(payload.exists(), 'Read-only iOS modes must not prevent Windows cleanup')
            with zipfile.ZipFile(root / 'output.ipa') as archive:
                for name, mode in [('Main', 0o755), ('helper', 0o555), ('ReadOnly/config', 0o444)]:
                    info = archive.getinfo('Payload/Test.app/' + name)
                    self.assertEqual(info.create_system, 3)
                    self.assertEqual(stat.S_IMODE(info.external_attr >> 16), mode)
                directory = archive.getinfo('Payload/Test.app/ReadOnly/')
                self.assertEqual(stat.S_IMODE(directory.external_attr >> 16), 0o555)
                for name in ('FileLink', 'DirectoryLink', 'DanglingLink'):
                    info = archive.getinfo('Payload/Test.app/' + name)
                    self.assertTrue(stat.S_ISLNK(info.external_attr >> 16))
                    self.assertEqual(archive.read(info), links['/Test.app/' + name].encode())
                self.assertNotIn('Payload/Test.app/DirectoryLink/config', archive.namelist())
                self.assertEqual(archive.read('Payload/Test.app/Main'), b'decrypted executable')
                self.assertIsNone(archive.testzip())
            self.assertEqual(dump.bundle_metadata, {})

    def test_bundle_download_failure_is_reported_and_sftp_closed(self):
        sftp = Mock()
        sftp.listdir_attr.side_effect = OSError('connection lost')
        ssh = Mock()
        self.open_sftp.return_value = sftp
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(OSError, 'connection lost'):
                dump.download_app(ssh, '/Test.app', directory, [], Mock())
        sftp.close.assert_called_once()

    def test_parallel_file_failure_is_reported_and_all_channels_closed(self):
        entry = paramiko.SFTPAttributes()
        entry.filename = 'resource'
        entry.st_mode = stat.S_IFREG | 0o644
        entry.st_size = 1
        listing, worker = Mock(), Mock()
        listing.listdir_attr.return_value = [entry]
        worker.get.side_effect = OSError('file download failed')
        ssh = Mock()
        self.open_sftp.side_effect = [listing, worker]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(OSError, 'file download failed'):
                dump.download_app(ssh, '/Test.app', directory, [], Mock())
        listing.close.assert_called_once()
        worker.close.assert_called_once()

    def test_resource_total_preserves_bytes_downloaded_during_scan(self):
        output = io.StringIO()
        real_tqdm = dump.tqdm

        def download(ssh, app, destination, excluded, progress, set_total=None, scan_progress=None):
            scan_progress(1000)
            progress(40 * 1024 * 1024)
            set_total(80 * 1024 * 1024)
            progress(40 * 1024 * 1024)

        with patch.object(dump, 'ssh', Mock(), create=True), \
                patch.object(dump, 'download_app', side_effect=download), \
                patch.object(dump.os, 'chmod'), \
                patch.object(dump, 'tqdm', side_effect=lambda **kwargs: real_tqdm(file=output, **kwargs)):
            dump.handle_payload({'app': '/Test.app'})
        rendered = output.getvalue()
        self.assertIn('scanning: 1000 entries', rendered)
        self.assertIn('40.0M/80.0M', rendered)
        self.assertIn('50%', rendered)
        self.assertIn('100%', rendered)

    def test_message_callback_returns_without_running_transfer(self):
        message = {'type': 'send', 'payload': {'dump': '/tmp/test.fid'}}
        with patch.object(dump, 'handle_payload') as transfer:
            callback = threading.Thread(target=dump.on_message, args=(message, None), daemon=True)
            callback.start()
            callback.join(timeout=1)
            self.assertFalse(callback.is_alive(), 'Frida callback must return without waiting for SCP')
            transfer.assert_not_called()
            queued_message, data = dump.message_queue.get_nowait()
            dump.process_message(queued_message, data)
            transfer.assert_called_once_with(message['payload'])

    def test_done_waits_for_all_queued_transfers(self):
        session = Mock(is_detached=False)
        script = Mock()
        transfers = []

        def post(_):
            for payload in ({'dump': '/tmp/test.fid'}, {'app': '/tmp/Test.app'}, {'done': 'ok'}):
                dump.on_message({'type': 'send', 'payload': payload}, None)

        def transfer(payload):
            transfers.append(payload)
            if 'done' in payload:
                dump.finished.set()

        script.post.side_effect = post
        with patch.object(dump, 'load_js_file', return_value=script), \
                patch.object(dump, 'handle_payload', side_effect=transfer), \
                patch.object(dump, 'generate_ipa') as package:
            package.side_effect = lambda *args: self.assertEqual(
                transfers, [{'dump': '/tmp/test.fid'}, {'app': '/tmp/Test.app'}, {'done': 'ok'}])
            dump.start_dump(session, 'test')
        package.assert_called_once()

    def test_dump_downloads_overlap_and_finish_before_resources(self):
        session = Mock(is_detached=False)
        script = Mock()
        lock = threading.Lock()
        both_started = threading.Event()
        started, completed = set(), set()

        def post(_):
            for payload in ({'dump': 'main'}, {'dump': 'framework'}, {'app': '/Test.app'}, {'done': 'ok'}):
                dump.on_message({'type': 'send', 'payload': payload}, None)

        def transfer(payload):
            if 'dump' in payload:
                with lock:
                    started.add(payload['dump'])
                    if len(started) == 2:
                        both_started.set()
                self.assertTrue(both_started.wait(timeout=2), 'Dump downloads must overlap')
                with lock:
                    completed.add(payload['dump'])
            elif 'app' in payload:
                self.assertEqual(completed, {'main', 'framework'})
            elif 'done' in payload:
                dump.finished.set()

        script.post.side_effect = post
        with patch.object(dump, 'load_js_file', return_value=script), \
                patch.object(dump, 'handle_payload', side_effect=transfer), \
                patch.object(dump, 'generate_ipa') as package:
            dump.start_dump(session, 'test')
        package.assert_called_once()

    def test_async_dump_failure_wakes_consumer_without_agent_done(self):
        session = Mock(is_detached=False)
        script = Mock()
        script.post.side_effect = lambda _: dump.on_message(
            {'type': 'send', 'payload': {'dump': '/test.fid'}}, None)
        with patch.object(dump, 'load_js_file', return_value=script), \
                patch.object(dump, 'handle_payload', side_effect=OSError('dump download failed')), \
                patch.object(dump, 'generate_ipa') as package:
            with self.assertRaisesRegex(OSError, 'dump download failed'):
                dump.start_dump(session, 'test')
        package.assert_not_called()

    def test_interrupt_closes_active_download_before_joining(self):
        started, closed = threading.Event(), threading.Event()
        session = Mock(is_detached=False)
        ssh, script = Mock(), Mock()
        ssh.close.side_effect = closed.set

        def post(_):
            dump.on_message({'type': 'send', 'payload': {'dump': '/test.fid'}}, None)

            def interrupt():
                if started.wait(timeout=2):
                    dump.message_queue.put((KeyboardInterrupt(), None))

            threading.Thread(target=interrupt, daemon=True).start()

        def transfer(_):
            started.set()
            self.assertTrue(closed.wait(timeout=2), 'SSH must close before waiting for downloads')
            raise OSError('channel closed during cancellation')

        script.post.side_effect = post
        with patch.object(dump, 'load_js_file', return_value=script), \
                patch.object(dump, 'handle_payload', side_effect=transfer), \
                patch.object(dump, 'generate_ipa') as package:
            with self.assertRaises(KeyboardInterrupt):
                dump.start_dump(session, 'test', ssh_client=ssh)
        self.assertTrue(closed.is_set())
        package.assert_not_called()
        session.detach.assert_called_once()

    def test_failed_dump_closes_other_active_download_and_preserves_error(self):
        started, closed = threading.Event(), threading.Event()
        session = Mock(is_detached=False)
        ssh, script = Mock(), Mock()
        ssh.close.side_effect = closed.set

        def post(_):
            for payload in ({'dump': 'blocked'}, {'dump': 'failed'}, {'app': '/Test.app'}, {'done': 'ok'}):
                dump.on_message({'type': 'send', 'payload': payload}, None)

        def transfer(payload):
            if payload['dump'] == 'blocked':
                started.set()
                self.assertTrue(closed.wait(timeout=2), 'Failure must cancel other active downloads')
                raise EOFError('secondary connection error')
            self.assertTrue(started.wait(timeout=2))
            raise OSError('original download failure')

        script.post.side_effect = post
        with patch.object(dump, 'load_js_file', return_value=script), \
                patch.object(dump, 'handle_payload', side_effect=transfer), \
                patch.object(dump, 'generate_ipa') as package:
            with self.assertRaisesRegex(OSError, 'original download failure'):
                dump.start_dump(session, 'test', ssh_client=ssh)
        package.assert_not_called()

    def test_scan_error_closes_active_sftp_download_and_preserves_error(self):
        started, closed = threading.Event(), threading.Event()
        file_entry, subdir = paramiko.SFTPAttributes(), paramiko.SFTPAttributes()
        file_entry.filename, file_entry.st_mode, file_entry.st_size = 'resource', stat.S_IFREG | 0o644, 1
        subdir.filename, subdir.st_mode = 'Subdir', stat.S_IFDIR | 0o755
        listing, worker, ssh = Mock(), Mock(), Mock()
        self.open_sftp.side_effect = [listing, worker]
        ssh.close.side_effect = closed.set

        def list_directory(path):
            if path == '/Test.app':
                return [file_entry, subdir]
            self.assertTrue(started.wait(timeout=2))
            raise OSError('original scan failure')

        def get(*args, **kwargs):
            started.set()
            self.assertTrue(closed.wait(timeout=2), 'Scan failure must close active SFTP reads')
            raise EOFError('secondary SFTP error')

        listing.listdir_attr.side_effect = list_directory
        worker.get.side_effect = get
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(OSError, 'original scan failure'):
                dump.download_app(ssh, '/Test.app', directory, [], Mock())
        worker.close.assert_called_once()

    def test_interrupt_during_scan_preserves_keyboard_interrupt(self):
        listing, ssh = Mock(), Mock()
        self.open_sftp.return_value = listing
        listing.listdir_attr.side_effect = KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KeyboardInterrupt):
                dump.download_app(ssh, '/Test.app', directory, [], Mock())
        ssh.close.assert_called()

    def test_ssh_alias_uses_config_and_uses_usb(self):
        args = argparse.Namespace(ssh_host='iphone', ssh_port=None, ssh_user=None,
                                  ssh_password=None, ssh_key_filename=None)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config'
            config.write_text('Host iphone\n HostName 127.0.0.1\n Port 22\n'
                              ' User mobile\n IdentityFile ~/.ssh/test_key\n')
            options = dump.resolve_ssh_options(args, str(config))
            self.assertEqual(options['host'], '127.0.0.1')
            self.assertEqual(options['username'], 'mobile')
            self.assertTrue(options['key_filename'][0].endswith('/.ssh/test_key'))
            self.assertEqual(options['password'], dump.Password)
            self.assertTrue(options['use_usb'])
            args.ssh_user = 'other'
            args.ssh_port = '2200'
            options = dump.resolve_ssh_options(args, str(config))
            self.assertEqual(options['username'], 'other')
            self.assertEqual(options['port'], 2200)
            self.assertTrue(options['use_usb'])
            args.ssh_password = 'explicit-password'
            self.assertEqual(dump.resolve_ssh_options(args, str(config))['password'], 'explicit-password')

    def test_ssh_transport_selection_is_independent_of_device_port(self):
        cases = [
            (None, 'auto', True),
            ('localhost', 'auto', True),
            ('127.0.0.1', 'auto', True),
            ('::1', 'auto', True),
            ('192.0.2.1', 'auto', False),
            ('device.example', 'auto', False),
            ('127.0.0.1', 'tcp', False),
            (None, 'tcp', False),
            ('device.example', 'usb', True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config'
            config.write_text('')
            for host, transport, expected_usb in cases:
                with self.subTest(host=host, transport=transport):
                    args = argparse.Namespace(ssh_host=host, ssh_port='44', ssh_user=None,
                                              ssh_password=None, ssh_key_filename=None,
                                              ssh_transport=transport)
                    options = dump.resolve_ssh_options(args, str(config))
                    self.assertEqual(options['use_usb'], expected_usb)
                    self.assertEqual(options['port'], 44)

    def test_port_44_alias_opens_usb_forwarder_to_device_port(self):
        args = argparse.Namespace(ssh_host='iphone', ssh_port=None, ssh_user=None,
                                  ssh_password=None, ssh_key_filename=None, ssh_transport='auto')
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config'
            config.write_text('Host iphone\n HostName 127.0.0.1\n Port 44\n User mobile\n')
            options = dump.resolve_ssh_options(args, str(config))
            ssh, device = Mock(), Mock()
            with patch.object(dump, 'USBSSHForwarder') as forwarder:
                result = dump.connect_ssh(ssh, device, **options)
            forwarder.assert_called_once_with(device, port=44)
            self.assertIs(ssh.connect.call_args.kwargs['sock'], result.socket)
            self.assertEqual(ssh.connect.call_args.kwargs['port'], 44)

    def test_usb_connects_without_attempting_local_tcp(self):
        ssh = Mock()
        with patch.object(dump, 'USBSSHForwarder') as forwarder:
            result = dump.connect_ssh(ssh, Mock(), 'localhost', 22, 'mobile', 'test', None,
                                      use_usb=True)
        self.assertIs(result, forwarder.return_value)
        ssh.connect.assert_called_once()
        forwarder.assert_called_once_with(unittest.mock.ANY, port=22)
        self.assertIs(ssh.connect.call_args.kwargs['sock'], result.socket)

    def test_explicit_connection_does_not_fall_back(self):
        ssh = Mock()
        ssh.connect.side_effect = paramiko.ssh_exception.NoValidConnectionsError(
            {('127.0.0.1', 2223): ConnectionRefusedError()})
        with patch.object(dump, 'USBSSHForwarder') as forwarder:
            with self.assertRaises(paramiko.ssh_exception.NoValidConnectionsError):
                dump.connect_ssh(ssh, Mock(), 'localhost', 2223, 'mobile', 'test', None)
        forwarder.assert_not_called()

    def test_usb_authentication_failure_closes_forwarder(self):
        ssh = Mock()
        ssh.connect.side_effect = paramiko.AuthenticationException()
        with patch.object(dump, 'USBSSHForwarder') as forwarder:
            with self.assertRaises(paramiko.AuthenticationException):
                dump.connect_ssh(ssh, Mock(), 'localhost', 22, 'mobile', 'test', None,
                                 use_usb=True)
        forwarder.return_value.close.assert_called_once()

    def test_agent_exception_aborts_without_packaging(self):
        session = Mock(is_detached=False)
        script = Mock()
        script.post.side_effect = lambda _: dump.on_message(
            {'type': 'error', 'description': 'access violation', 'stack': 'agent stack'}, None)
        with patch.object(dump, 'load_js_file', return_value=script), \
                patch.object(dump, 'generate_ipa') as package:
            with self.assertRaisesRegex(RuntimeError, 'agent stack'):
                dump.start_dump(session, 'test')
        package.assert_not_called()
        session.detach.assert_called_once()

    def test_transfer_failure_wakes_waiter_and_ignores_done(self):
        with patch.object(dump, 'handle_payload', side_effect=OSError('SCP disconnected')) as handler:
            dump.process_message({'type': 'send', 'payload': {'dump': '/tmp/test.fid'}}, None)
            dump.process_message({'type': 'send', 'payload': {'done': 'ok'}}, None)
        self.assertTrue(dump.finished.is_set())
        self.assertIsInstance(dump.dump_error, OSError)
        handler.assert_called_once()

    def test_unexpected_detach_aborts_without_packaging(self):
        session = Mock(is_detached=True)
        script = Mock()
        session.on.side_effect = lambda signal, callback: setattr(session, 'detached_callback', callback)
        script.post.side_effect = lambda _: session.detached_callback('process-terminated')
        with patch.object(dump, 'load_js_file', return_value=script), \
                patch.object(dump, 'generate_ipa') as package:
            with self.assertRaisesRegex(RuntimeError, 'process-terminated'):
                dump.start_dump(session, 'test')
        package.assert_not_called()
        session.detach.assert_not_called()

    def test_missing_bridge_gives_install_instruction(self):
        with patch.object(dump.os.path, 'isdir', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'npm install'):
                dump.load_js_file(Mock(), dump.DUMP_JS)

    def test_listing_apps_does_not_remove_another_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            other_payload = Path(temporary) / 'Payload'
            other_payload.mkdir()
            sentinel = other_payload / 'active-download.fid'
            sentinel.write_bytes(b'other export')
            device = Mock()
            device.enumerate_applications.return_value = []
            with patch.object(dump.tempfile, 'gettempdir', return_value=temporary), \
                    patch.object(dump.frida, 'get_usb_device', return_value=device), \
                    patch.object(sys, 'argv', ['dump.py', '-l']), \
                    redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(dump.__file__, run_name='__main__')
            self.assertEqual(result.exception.code, 0)
            self.assertEqual(sentinel.read_bytes(), b'other export')

    def test_failed_exports_use_distinct_workspaces_and_clean_only_their_own(self):
        constructor = tempfile.TemporaryDirectory
        workspaces = []

        def create_workspace(*args, **kwargs):
            workspace = constructor(*args, **kwargs)
            workspaces.append(Path(workspace.name))
            return workspace

        with constructor() as temporary:
            other_payload = Path(temporary) / 'Payload'
            other_payload.mkdir()
            sentinel = other_payload / 'active-download.fid'
            sentinel.write_bytes(b'other export')
            ssh = Mock()
            ssh.connect.side_effect = paramiko.AuthenticationException('test authentication failure')
            with patch.object(dump.tempfile, 'gettempdir', return_value=temporary), \
                    patch.object(dump.tempfile, 'TemporaryDirectory', side_effect=create_workspace), \
                    patch.object(dump.frida, 'get_usb_device', return_value=Mock()), \
                    patch.object(dump.paramiko, 'SSHClient', return_value=ssh), \
                    patch.object(sys, 'argv', ['dump.py', '-H', '192.0.2.1', 'example.app']), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                for _ in range(2):
                    with self.assertRaises(SystemExit) as result:
                        runpy.run_path(dump.__file__, run_name='__main__')
                    self.assertEqual(result.exception.code, 1)
            self.assertEqual(len(workspaces), 2)
            self.assertNotEqual(workspaces[0], workspaces[1])
            self.assertTrue(all(path.parent == Path(temporary) for path in workspaces))
            self.assertTrue(all(not path.exists() for path in workspaces))
            self.assertEqual(sentinel.read_bytes(), b'other export')
            self.assertEqual(ssh.close.call_count, 2)

    def test_ipa_contains_replaced_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / 'Payload'
            (payload / 'Example.app' / 'Frameworks').mkdir(parents=True)
            (payload / 'Example.app' / 'Frameworks' / 'Example').write_bytes(b'encrypted')
            self.bundle_link(payload, 'Example.app/BinaryLink', 'Frameworks/Example')
            (payload / 'Example.fid').write_bytes(b'decrypted')
            dump.file_dict.update({'app': 'Example.app', 'Example.fid': 'Frameworks/Example'})
            with patch.object(dump, 'TEMP_DIR', temporary), \
                    patch.object(dump, 'PAYLOAD_PATH', str(payload)):
                dump.generate_ipa(str(payload), str(root / 'output'))
            with zipfile.ZipFile(root / 'output.ipa') as archive:
                self.assertEqual(archive.read('Payload/Example.app/Frameworks/Example'), b'decrypted')
                link = archive.getinfo('Payload/Example.app/BinaryLink')
                self.assertTrue(stat.S_ISLNK(link.external_attr >> 16))
                self.assertEqual(archive.read(link), b'Frameworks/Example')
                self.assertIsNone(archive.testzip())
            self.assertFalse(payload.exists())

    def test_parallel_same_name_modules_replace_the_correct_bundle_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / 'Payload'
            app = payload / 'Example.app'
            (app / 'First').mkdir(parents=True)
            (app / 'Second').mkdir()
            (app / 'First' / 'Shared').write_bytes(b'encrypted first')
            (app / 'Second' / 'Shared').write_bytes(b'encrypted second')
            entries = [
                {'dump': '/Documents/frida-ios-dump-task-0.fid',
                 'path': '/Example.app/First/Shared'},
                {'dump': '/Documents/frida-ios-dump-task-1.fid',
                 'path': '/Example.app/Second/Shared'},
            ]
            content = {entries[0]['dump']: b'decrypted first',
                       entries[1]['dump']: b'decrypted second'}
            both_started = threading.Barrier(2)

            def download(remote, destination):
                both_started.wait(timeout=2)
                (Path(destination) / Path(remote).name).write_bytes(content[remote])

            def scp_client(*args, **kwargs):
                client = Mock()
                client.__enter__ = Mock(return_value=client)
                client.__exit__ = Mock(return_value=False)
                client.get.side_effect = download
                return client

            dump.file_dict['app'] = 'Example.app'
            ssh = Mock()
            with patch.object(dump, 'TEMP_DIR', temporary), \
                    patch.object(dump, 'PAYLOAD_PATH', str(payload)), \
                    patch.object(dump, 'ssh', ssh, create=True), \
                    patch.object(dump, 'SCPClient', side_effect=scp_client), \
                    patch.object(dump, 'tqdm', return_value=Mock()):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    list(executor.map(dump.handle_payload, entries))
                self.assertEqual(dump.file_dict['frida-ios-dump-task-0.fid'], 'First/Shared')
                self.assertEqual(dump.file_dict['frida-ios-dump-task-1.fid'], 'Second/Shared')
                dump.generate_ipa(str(payload), str(root / 'output'))
            removed = [call.args[0] for call in self.open_sftp.return_value.remove.call_args_list]
            self.assertCountEqual(removed, list(content))
            self.assertEqual(self.open_sftp.return_value.close.call_count, 2)
            with zipfile.ZipFile(root / 'output.ipa') as archive:
                self.assertEqual(archive.read('Payload/Example.app/First/Shared'), content[entries[0]['dump']])
                self.assertEqual(archive.read('Payload/Example.app/Second/Shared'), content[entries[1]['dump']])
                for relative in ('First/Shared', 'Second/Shared'):
                    mode = archive.getinfo('Payload/Example.app/' + relative).external_attr >> 16
                    self.assertEqual(stat.S_IMODE(mode), 0o755)
                self.assertIsNone(archive.testzip())

    def test_dump_permission_failure_is_reported_without_recording_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = {'dump': '/Documents/test.fid', 'path': '/Example.app/Main'}
            client = Mock()
            manager = Mock()
            manager.__enter__ = Mock(return_value=client)
            manager.__exit__ = Mock(return_value=False)
            with patch.object(dump, 'PAYLOAD_PATH', temporary), \
                    patch.object(dump, 'ssh', Mock(), create=True), \
                    patch.object(dump, 'SCPClient', return_value=manager), \
                    patch.object(dump, 'tqdm', return_value=Mock()), \
                    patch.object(dump.os, 'chmod', side_effect=PermissionError('cannot set mode')):
                with self.assertRaisesRegex(PermissionError, 'cannot set mode'):
                    dump.handle_payload(payload)
            self.assertNotIn('test.fid', dump.file_dict)

    def test_repeated_export_removes_obsolete_archive_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / 'Payload'
            app = payload / 'Example.app'
            output = root / 'output.ipa'
            dump.file_dict['app'] = 'Example.app'
            with patch.object(dump, 'TEMP_DIR', temporary), \
                    patch.object(dump, 'PAYLOAD_PATH', str(payload)):
                app.mkdir(parents=True)
                (app / 'removed.txt').write_text('old resource')
                (app / 'Main').write_bytes(b'old executable')
                dump.generate_ipa(str(payload), str(root / 'output'))
                app.mkdir(parents=True)
                (app / 'current.txt').write_text('new resource')
                (app / 'Main').write_bytes(b'new executable')
                dump.generate_ipa(str(payload), str(root / 'output'))
            with zipfile.ZipFile(output) as archive:
                self.assertNotIn('Payload/Example.app/removed.txt', archive.namelist())
                self.assertEqual(archive.read('Payload/Example.app/current.txt'), b'new resource')
                self.assertEqual(archive.read('Payload/Example.app/Main'), b'new executable')
                self.assertIsNone(archive.testzip())
            self.assertEqual(list(root.glob('.frida-ios-dump-*')), [])

    def test_packaging_without_external_tools_preserves_links_and_empty_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / 'Payload'
            app = payload / 'Example.app'
            (app / 'Resources' / 'Empty').mkdir(parents=True)
            (app / 'Main').write_bytes(b'executable')
            (app / 'Main').chmod(0o755)
            (app / 'Resources' / '中文 file.txt').write_text('resource', encoding='utf-8')
            self.bundle_link(payload, 'Example.app/Current', 'Resources', directory=True)
            self.bundle_link(payload, 'Example.app/Missing', 'does-not-exist')
            dump.file_dict['app'] = 'Example.app'
            with patch.object(dump, 'PAYLOAD_PATH', str(payload)), \
                    patch.dict(os.environ, {'PATH': ''}):
                dump.generate_ipa(str(payload), str(root / 'output'))
            with zipfile.ZipFile(root / 'output.ipa') as archive:
                self.assertEqual(archive.read('Payload/Example.app/Main'), b'executable')
                self.assertEqual(archive.read('Payload/Example.app/Resources/中文 file.txt'), b'resource')
                self.assertIn('Payload/Example.app/Resources/Empty/', archive.namelist())
                self.assertNotIn('Payload/Example.app/Current/中文 file.txt', archive.namelist())
                for name, target in [('Current', b'Resources'), ('Missing', b'does-not-exist')]:
                    info = archive.getinfo('Payload/Example.app/' + name)
                    self.assertEqual(info.create_system, 3)
                    self.assertTrue(stat.S_ISLNK(info.external_attr >> 16))
                    self.assertEqual(archive.read(info), target)
                self.assertIsNone(archive.testzip())

    def test_packaging_failure_preserves_existing_ipa(self):
        def fail_packaging(payload, archive_path):
            Path(archive_path).write_bytes(b'partial archive')
            raise OSError('disk full')

        self.assert_failed_packaging_preserves_output(
            fail_packaging, OSError)

    def test_invalid_archive_preserves_existing_ipa(self):
        def invalid_archive(payload, archive_path):
            Path(archive_path).write_bytes(b'invalid archive')

        self.assert_failed_packaging_preserves_output(invalid_archive, zipfile.BadZipFile)

    def assert_failed_packaging_preserves_output(self, package, expected_error):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / 'Payload'
            (payload / 'Example.app').mkdir(parents=True)
            output = root / 'output.ipa'
            with zipfile.ZipFile(output, 'w') as archive:
                archive.writestr('original.txt', b'original export')
            original = output.read_bytes()
            dump.file_dict['app'] = 'Example.app'
            with patch.object(dump, 'TEMP_DIR', temporary), \
                    patch.object(dump, 'PAYLOAD_PATH', str(payload)), \
                    patch.object(dump, 'write_ipa', side_effect=package):
                with self.assertRaises(expected_error):
                    dump.generate_ipa(str(payload), str(root / 'output'))
            self.assertEqual(output.read_bytes(), original)
            self.assertTrue(payload.exists())
            self.assertEqual(list(root.glob('.frida-ios-dump-*')), [])


if __name__ == '__main__':
    unittest.main()
