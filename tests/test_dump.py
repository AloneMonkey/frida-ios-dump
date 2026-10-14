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

import dump


class DumpTests(unittest.TestCase):
    def setUp(self):
        dump.dump_error = None
        dump.finished.clear()
        dump.file_dict.clear()
        dump.message_queue = queue.Queue()

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
            (app / 'InfoLink').symlink_to('Info.plist')
            destination = root / 'Payload'
            def get(remote, local, callback):
                shutil.copyfile(remote, local)
                size = Path(remote).stat().st_size
                callback(size, size)

            clients = []

            def open_sftp():
                client = Mock()
                client.listdir_attr.side_effect = lambda path: [
                    paramiko.SFTPAttributes.from_stat(entry.lstat(), entry.name)
                    for entry in Path(path).iterdir()]
                client.readlink.side_effect = os.readlink
                client.get.side_effect = get
                clients.append(client)
                return client

            ssh = Mock()
            ssh.open_sftp.side_effect = open_sftp
            received = []
            totals = []
            scanned = []
            dump.download_app(ssh, str(app), str(destination),
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
            self.assertEqual(os.readlink(copied_app / 'InfoLink'), 'Info.plist')
            self.assertEqual(stat.S_IMODE((copied_app / 'Resources' / 'helper').stat().st_mode), 0o755)
            self.assertEqual(sum(received), sum((app / name).stat().st_size for name in downloaded))
            self.assertEqual(totals, [sum(received)])
            self.assertEqual(sum(scanned), len(list(app.rglob('*'))))
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
        ssh.open_sftp.side_effect = [listing, worker]
        totals, received = [], []
        with tempfile.TemporaryDirectory() as directory:
            dump.download_app(ssh, '/Test.app', directory, [], received.append, set_total=totals.append)
        self.assertEqual(totals, [1])
        self.assertEqual(sum(received), 1)

    def test_bundle_download_failure_is_reported_and_sftp_closed(self):
        sftp = Mock()
        sftp.listdir_attr.side_effect = OSError('connection lost')
        ssh = Mock()
        ssh.open_sftp.return_value = sftp
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
        ssh.open_sftp.side_effect = [listing, worker]
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
                patch.object(dump.subprocess, 'check_call'), \
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
        ssh.open_sftp.side_effect = [listing, worker]
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
        ssh.open_sftp.return_value = listing
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
            self.assertFalse(options['use_usb'])
            args.ssh_password = 'explicit-password'
            self.assertEqual(dump.resolve_ssh_options(args, str(config))['password'], 'explicit-password')

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

    def test_ipa_contains_replaced_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / 'Payload'
            (payload / 'Example.app' / 'Frameworks').mkdir(parents=True)
            (payload / 'Example.app' / 'Frameworks' / 'Example').write_bytes(b'encrypted')
            (payload / 'Example.app' / 'BinaryLink').symlink_to('Frameworks/Example')
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


if __name__ == '__main__':
    unittest.main()
