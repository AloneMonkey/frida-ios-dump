#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Author : AloneMonkey
# blog: www.alonemonkey.com

import sys
import frida
import threading
import os
import shutil
import argparse
import tempfile
import subprocess
import re
import paramiko
from scp import SCPClient
from tqdm import tqdm
import traceback
import socket
import queue
import stat
import posixpath
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

script_dir = os.path.dirname(os.path.realpath(__file__))

DUMP_JS = os.path.join(script_dir, 'dump.js')

User = 'root'
Password = 'alpine'
Host = 'localhost'
Port = 22
KeyFileName = None

TEMP_DIR = tempfile.gettempdir()
PAYLOAD_DIR = 'Payload'
PAYLOAD_PATH = os.path.join(TEMP_DIR, PAYLOAD_DIR)
file_dict = {}

finished = threading.Event()
dump_error = None
message_queue = queue.Queue()


def get_usb_iphone():
    print('Waiting for USB device...')
    return frida.get_usb_device(timeout=10)


class USBSSHForwarder:
    """Expose a Frida USB channel as a socket with Paramiko's timeout support."""

    def __init__(self, device, port=22):
        self._stream = device.open_channel('tcp:{}'.format(port))
        try:
            self.socket, self._peer = socket.socketpair()
        except Exception:
            self._stream.close()
            raise
        self._closed = threading.Event()
        self._close_lock = threading.Lock()
        for direction in (self._to_device, self._from_device):
            threading.Thread(target=self._relay, args=(direction,), daemon=True).start()

    def _to_device(self):
        while not self._closed.is_set():
            data = self._peer.recv(65536)
            if not data:
                break
            self._stream.write_all(data)

    def _from_device(self):
        while not self._closed.is_set():
            data = self._stream.read(65536)
            if not data:
                break
            self._peer.sendall(data)

    def _relay(self, direction):
        try:
            direction()
        except Exception:
            # Closing the socket wakes Paramiko, which reports transport failures.
            pass
        finally:
            self.close()

    def close(self):
        with self._close_lock:
            if self._closed.is_set():
                return
            self._closed.set()
        for endpoint in (self.socket, self._peer):
            try:
                endpoint.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            endpoint.close()
        try:
            self._stream.close()
        except Exception:
            pass


def resolve_ssh_options(args, config_path=None):
    host = args.ssh_host or Host
    config = {}
    config_path = config_path or os.path.expanduser('~/.ssh/config')
    if os.path.isfile(config_path):
        with open(config_path, encoding='utf-8') as handle:
            config = paramiko.SSHConfig.from_file(handle).lookup(host)
    hostname = config.get('hostname', host)
    port = int(args.ssh_port or config.get('port', Port))
    identity = args.ssh_key_filename or config.get('identityfile') or KeyFileName
    password = args.ssh_password if args.ssh_password is not None else Password
    transport = getattr(args, 'ssh_transport', 'auto')
    if transport == 'auto':
        use_usb = not args.ssh_host or hostname.lower() in ('localhost', '127.0.0.1', '::1')
    else:
        use_usb = transport == 'usb'
    return dict(host=hostname, port=port, username=args.ssh_user or config.get('user', User),
                password=password, key_filename=identity,
                use_usb=use_usb)


def connect_ssh(ssh, device, host, port, username, password, key_filename, use_usb=False):
    options = dict(port=port, username=username, password=password,
                   key_filename=key_filename, timeout=10)
    if not use_usb:
        ssh.connect(host, **options)
        return None
    print('Connecting to device SSH over USB...')
    forwarder = USBSSHForwarder(device, port=port)
    try:
        ssh.connect(host, sock=forwarder.socket, **options)
    except BaseException:
        forwarder.close()
        raise
    return forwarder


def generate_ipa(path, display_name):
    ipa_filename = display_name + '.ipa'

    print('Generating "{}"'.format(ipa_filename))
    app_name = file_dict['app']

    for key, value in file_dict.items():
        from_dir = os.path.join(path, key)
        to_dir = os.path.join(path, app_name, value)
        if key != 'app':
            shutil.move(from_dir, to_dir)

    target_dir = './' + PAYLOAD_DIR
    output_path = os.path.abspath(ipa_filename)
    # Build a fresh archive on the destination filesystem before replacing it.
    # Updating an existing ZIP leaves entries that disappeared from the app.
    with tempfile.TemporaryDirectory(prefix='.frida-ios-dump-',
                                     dir=os.path.dirname(output_path)) as archive_dir:
        archive_path = os.path.join(archive_dir, 'export.ipa')
        zip_args = ('zip', '-qry', archive_path, target_dir)
        subprocess.check_call(zip_args, cwd=TEMP_DIR)
        with zipfile.ZipFile(archive_path) as archive:
            bad_entry = archive.testzip()
            if bad_entry is not None:
                raise RuntimeError('Corrupt IPA entry: {}'.format(bad_entry))
        os.replace(archive_path, output_path)
    shutil.rmtree(PAYLOAD_PATH)


def on_message(message, data):
    # Frida callbacks must return promptly: USB SSH also needs its event loop.
    message_queue.put((message, data))


def process_message(message, data):
    global dump_error
    if dump_error is not None:
        return
    try:
        if message.get('type') == 'error':
            raise RuntimeError(message.get('stack') or message.get('description', 'Agent error'))
        if message.get('type') == 'send':
            handle_payload(message['payload'])
    except Exception as e:
        dump_error = e
        finished.set()


def abort_downloads(executor, ssh_client):
    """Cancel queued jobs and unblock active channel reads before joining workers."""
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)
    if ssh_client is not None:
        try:
            ssh_client.close()
        except Exception:
            pass


def download_app(ssh_client, app_path, destination, excluded, progress, set_total=None,
                 scan_progress=None):
    """Download bundle resources over SFTP, skipping already dumped binaries."""
    app_path = app_path.rstrip('/')
    local_app = os.path.join(destination, posixpath.basename(app_path))
    excluded = set(excluded)
    sftp = ssh_client.open_sftp()
    futures = []
    total_size = 0
    directories = []
    worker_state = threading.local()
    worker_clients = []
    progress_lock = threading.Lock()
    first_error = []

    def file_finished(future):
        if not future.cancelled():
            error = future.exception()
            if error is not None:
                with progress_lock:
                    if not first_error:
                        first_error.append(error)
                abort_downloads(executor, ssh_client)

    def copy_directory(remote, local, relative=''):
        nonlocal total_size
        os.makedirs(local, exist_ok=True)
        for entry in sftp.listdir_attr(remote):
            if first_error:
                raise first_error[0]
            if scan_progress is not None:
                with progress_lock:
                    scan_progress(1)
            relative_path = posixpath.join(relative, entry.filename)
            if relative_path in excluded:
                continue
            remote_path = posixpath.join(remote, entry.filename)
            local_path = os.path.join(local, entry.filename)
            if stat.S_ISDIR(entry.st_mode):
                copy_directory(remote_path, local_path, relative_path)
                directories.append((local_path, stat.S_IMODE(entry.st_mode)))
            elif stat.S_ISLNK(entry.st_mode):
                os.symlink(sftp.readlink(remote_path), local_path)
            elif stat.S_ISREG(entry.st_mode):
                total_size += entry.st_size
                future = executor.submit(copy_file, (remote_path, local_path, entry))
                futures.append(future)
                future.add_done_callback(file_finished)
            else:
                raise RuntimeError('Unsupported file in app bundle: {}'.format(remote_path))

    def copy_file(item):
        remote_path, local_path, entry = item
        if not hasattr(worker_state, 'sftp'):
            client = ssh_client.open_sftp()
            with progress_lock:
                worker_clients.append(client)
            client.get_channel().settimeout(60)
            worker_state.sftp = client
        last_sent = [0]

        def on_progress(sent, size):
            with progress_lock:
                progress(sent - last_sent[0])
            last_sent[0] = sent

        worker_state.sftp.get(remote_path, local_path, callback=on_progress)
        os.chmod(local_path, stat.S_IMODE(entry.st_mode))
        if entry.st_atime is not None and entry.st_mtime is not None:
            os.utime(local_path, (entry.st_atime, entry.st_mtime))

    executor = None
    try:
        sftp.get_channel().settimeout(60)
        executor = ThreadPoolExecutor(max_workers=4)
        copy_directory(app_path, local_app)
        if set_total is not None:
            with progress_lock:
                set_total(total_size)
        for future in futures:
            future.result()
        # Apply read-only directory modes only after all children have been written.
        for local_path, mode in directories:
            os.chmod(local_path, mode)
    except BaseException as error:
        if isinstance(error, Exception):
            with progress_lock:
                if not first_error:
                    first_error.append(error)
        abort_downloads(executor, ssh_client)
        if isinstance(error, Exception) and first_error:
            raise first_error[0]
        raise
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        for client in worker_clients:
            try:
                client.close()
            except Exception:
                pass
        try:
            sftp.close()
        except Exception:
            pass


def handle_payload(payload):
    if 'done' in payload:
        finished.set()
        return
    t = tqdm(unit='B',unit_scale=True,unit_divisor=1024,miniters=1)
    last_sent = [0]

    def progress(filename, size, sent):
        baseName = os.path.basename(filename)
        if isinstance(baseName, bytes):
            t.desc = baseName.decode("utf-8")
        else:
            t.desc = baseName
        t.total = size
        t.update(sent - last_sent[0])
        last_sent[0] = 0 if size == sent else sent

    try:
        if 'dump' in payload:
            origin_path = payload['path']
            dump_path = payload['dump']
            if not dump_path:
                raise RuntimeError('Agent did not produce a dump for {}'.format(origin_path))

            scp_from = dump_path
            scp_to = PAYLOAD_PATH + '/'

            with SCPClient(ssh.get_transport(), progress = progress, socket_timeout = 60) as scp:
                scp.get(scp_from, scp_to)

            chmod_dir = os.path.join(PAYLOAD_PATH, os.path.basename(dump_path))
            chmod_args = ('chmod', '655', chmod_dir)
            try:
                subprocess.check_call(chmod_args)
            except subprocess.CalledProcessError as err:
                print(err)

            index = origin_path.find('.app/')
            file_dict[os.path.basename(dump_path)] = origin_path[index + 5:]
            # Unique task filenames must not accumulate after successful downloads.
            if re.fullmatch(r'frida-ios-dump-[A-Za-z0-9-]+\.fid', posixpath.basename(dump_path)):
                cleanup_sftp = None
                try:
                    cleanup_sftp = ssh.open_sftp()
                    cleanup_sftp.get_channel().settimeout(60)
                    cleanup_sftp.remove(dump_path)
                except Exception as error:
                    tqdm.write('Could not remove temporary dump {}: {}'.format(dump_path, error))
                finally:
                    if cleanup_sftp is not None:
                        try:
                            cleanup_sftp.close()
                        except Exception:
                            pass

        if 'app' in payload:
            app_path = payload['app']
            excluded = [value for key, value in file_dict.items() if key != 'app']

            scanned = [0]
            last_scan_refresh = [0.0]
            t.set_description_str('App resources (scanning...)')

            def scan_progress(count):
                scanned[0] += count
                t.set_description_str('App resources (scanning: {} entries)'.format(scanned[0]),
                                      refresh=False)
                now = time.monotonic()
                if now - last_scan_refresh[0] >= t.mininterval:
                    t.refresh()
                    last_scan_refresh[0] = now

            def finish_scan(total):
                # Downloads have already started; preserve their byte count and timer.
                t.total = total
                t.set_description_str('App resources (skipping {} dumped binaries)'.format(len(excluded)),
                                      refresh=True)

            download_app(ssh, app_path, PAYLOAD_PATH, excluded, t.update,
                         set_total=finish_scan, scan_progress=scan_progress)

            chmod_dir = os.path.join(PAYLOAD_PATH, os.path.basename(app_path))
            chmod_args = ('chmod', '755', chmod_dir)
            try:
                subprocess.check_call(chmod_args)
            except subprocess.CalledProcessError as err:
                print(err)

            file_dict['app'] = os.path.basename(app_path)

    finally:
        t.close()

def compare_applications(a, b):
    a_is_running = a.pid != 0
    b_is_running = b.pid != 0
    if a_is_running == b_is_running:
        if a.name > b.name:
            return 1
        elif a.name < b.name:
            return -1
        else:
            return 0
    elif a_is_running:
        return -1
    else:
        return 1


def cmp_to_key(mycmp):
    """Convert a cmp= function into a key= function"""

    class K:
        def __init__(self, obj):
            self.obj = obj

        def __lt__(self, other):
            return mycmp(self.obj, other.obj) < 0

        def __gt__(self, other):
            return mycmp(self.obj, other.obj) > 0

        def __eq__(self, other):
            return mycmp(self.obj, other.obj) == 0

        def __le__(self, other):
            return mycmp(self.obj, other.obj) <= 0

        def __ge__(self, other):
            return mycmp(self.obj, other.obj) >= 0

        def __ne__(self, other):
            return mycmp(self.obj, other.obj) != 0

    return K


def get_applications(device):
    try:
        applications = device.enumerate_applications()
    except Exception as e:
        sys.exit('Failed to enumerate applications: %s' % e)

    return applications


def list_applications(device):
    applications = get_applications(device)

    if len(applications) > 0:
        pid_column_width = max(map(lambda app: len('{}'.format(app.pid)), applications))
        name_column_width = max(map(lambda app: len(app.name), applications))
        identifier_column_width = max(map(lambda app: len(app.identifier), applications))
    else:
        pid_column_width = 0
        name_column_width = 0
        identifier_column_width = 0

    header_format = '%' + str(pid_column_width) + 's  ' + '%-' + str(name_column_width) + 's  ' + '%-' + str(
        identifier_column_width) + 's'
    print(header_format % ('PID', 'Name', 'Identifier'))
    print('%s  %s  %s' % (pid_column_width * '-', name_column_width * '-', identifier_column_width * '-'))
    line_format = '%' + str(pid_column_width) + 's  ' + '%-' + str(name_column_width) + 's  ' + '%-' + str(
        identifier_column_width) + 's'
    for application in sorted(applications, key=cmp_to_key(compare_applications)):
        if application.pid == 0:
            print(line_format % ('-', application.name, application.identifier))
        else:
            print(line_format % (application.pid, application.name, application.identifier))


def load_js_file(session, filename):
    bridge_path = os.path.join(script_dir, 'node_modules', 'frida-objc-bridge')
    if not os.path.isdir(bridge_path):
        raise RuntimeError('Missing frida-objc-bridge. Run npm install in {}'.format(script_dir))
    compiler = frida.Compiler()
    compiler.on('diagnostics', lambda diagnostics: print('Agent diagnostics:', diagnostics))
    source = compiler.build(os.path.basename(filename), project_root=os.path.dirname(filename))
    script = session.create_script(source)
    script.on('message', on_message)
    script.load()

    return script


def create_dir(path):
    path = path.strip()
    path = path.rstrip('\\')
    if os.path.exists(path):
        shutil.rmtree(path)
    try:
        os.makedirs(path)
    except os.error as err:
        print(err)


def open_target_app(device, name_or_bundleid):
    print('Start the target app {}'.format(name_or_bundleid))

    pid = ''
    session = None
    display_name = ''
    bundle_identifier = ''
    for application in get_applications(device):
        if name_or_bundleid == application.identifier or name_or_bundleid == application.name:
            pid = application.pid
            display_name = application.name
            bundle_identifier = application.identifier

    if not bundle_identifier:
        raise ValueError('Application not found: {}'.format(name_or_bundleid))
    if not pid:
        pid = device.spawn([bundle_identifier])
        try:
            session = device.attach(pid)
        finally:
            device.resume(pid)
    else:
        session = device.attach(pid)

    return session, display_name, bundle_identifier


def start_dump(session, ipa_name, ssh_client=None):
    global dump_error, message_queue
    print('Dumping {} to {}'.format(ipa_name, TEMP_DIR))
    finished.clear()
    file_dict.clear()
    dump_error = None
    message_queue = queue.Queue()
    if ssh_client is None:
        ssh_client = globals().get('ssh')

    def on_detached(reason, crash=None):
        on_message({'type': 'error', 'description': 'Target session detached: {}'.format(reason)}, None)

    pending_downloads = []
    download_errors = []
    download_pool = ThreadPoolExecutor(max_workers=4)

    def download_finished(future):
        if not future.cancelled():
            error = future.exception()
            if error is not None:
                download_errors.append(error)
                message_queue.put((error, None))
                abort_downloads(download_pool, ssh_client)

    session.on('detached', on_detached)
    try:
        script = load_js_file(session, DUMP_JS)
        if not finished.is_set():
            script.post('dump')
        while not finished.is_set():
            try:
                message, data = message_queue.get(timeout=120)
            except queue.Empty:
                raise RuntimeError('No response from the dump agent for 120 seconds. Keep the device unlocked.')
            if download_errors:
                raise download_errors[0]
            if isinstance(message, BaseException):
                raise message
            payload = message.get('payload', {}) if message.get('type') == 'send' else {}
            if 'dump' in payload:
                future = download_pool.submit(handle_payload, payload)
                pending_downloads.append(future)
                future.add_done_callback(download_finished)
                continue
            if 'app' in payload or 'done' in payload:
                # Resource exclusions and packaging need every completed dump mapping.
                for future in pending_downloads:
                    future.result()
            process_message(message, data)
        if dump_error is not None:
            raise dump_error
        generate_ipa(PAYLOAD_PATH, ipa_name)
    except BaseException as error:
        primary_error = download_errors[0] if isinstance(error, Exception) and download_errors else error
        abort_downloads(download_pool, ssh_client)
        if primary_error is not error:
            raise primary_error
        raise
    finally:
        download_pool.shutdown(wait=True, cancel_futures=True)
        session.off('detached', on_detached)
        if not session.is_detached:
            session.detach()



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='frida-ios-dump (by AloneMonkey, Frida 17+)')
    parser.add_argument('-l', '--list', dest='list_applications', action='store_true', help='List the installed apps')
    parser.add_argument('-o', '--output', dest='output_ipa', help='Specify name of the decrypted IPA')
    parser.add_argument('-H', '--host', dest='ssh_host', help='SSH hostname or alias from ~/.ssh/config')
    parser.add_argument('-p', '--port', dest='ssh_port', help='Specify SSH port')
    parser.add_argument('--ssh-transport', choices=('auto', 'usb', 'tcp'), default='auto',
                        help='SSH transport: auto uses USB for localhost and TCP for remote hosts')
    parser.add_argument('-u', '--user', dest='ssh_user', help='Specify SSH username')
    parser.add_argument('-P', '--password', dest='ssh_password', help='Specify SSH password')
    parser.add_argument('-K', '--key_filename', dest='ssh_key_filename', help='Specify SSH private key file path')
    parser.add_argument('target', nargs='?', help='Bundle identifier or display name of the target app')

    args = parser.parse_args()

    exit_code = 0
    ssh = None
    usb_ssh = None
    workspace = None

    if not len(sys.argv[1:]):
        parser.print_help()
        sys.exit(exit_code)

    if not args.list_applications and not args.target:
        parser.error('target is required unless --list is specified')

    try:
        device = get_usb_iphone()
    except Exception as e:
        sys.exit('Failed to connect to USB device: {}'.format(e))

    if args.list_applications:
        list_applications(device)
    else:
        name_or_bundleid = args.target
        output_ipa = args.output_ipa
        try:
            workspace = tempfile.TemporaryDirectory(prefix='frida-ios-dump-', dir=TEMP_DIR)
            TEMP_DIR = workspace.name
            PAYLOAD_PATH = os.path.join(TEMP_DIR, PAYLOAD_DIR)
            ssh_options = resolve_ssh_options(args)
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            usb_ssh = connect_ssh(ssh, device, **ssh_options)

            create_dir(PAYLOAD_PATH)
            (session, display_name, bundle_identifier) = open_target_app(device, name_or_bundleid)
            if output_ipa is None:
                output_ipa = display_name
            output_ipa = re.sub(r'\.ipa$', '', output_ipa)
            if session:
                start_dump(session, output_ipa, ssh_client=ssh)
        except KeyboardInterrupt:
            print('\nInterrupted; downloads cancelled.')
            exit_code = 130
        except paramiko.ssh_exception.NoValidConnectionsError as e:
            print(e)
            print('Try specifying -H/--host and/or -p/--port')
            exit_code = 1
        except paramiko.AuthenticationException as e:
            print(e)
            print('Try specifying -u/--user and/or -P/--password')
            exit_code = 1
        except Exception as e:
            print('*** Caught exception: %s: %s' % (e.__class__, e))
            traceback.print_exc()
            exit_code = 1

    try:
        if ssh:
            ssh.close()
    finally:
        try:
            if usb_ssh:
                usb_ssh.close()
        finally:
            if workspace is not None:
                workspace.cleanup()

    sys.exit(exit_code)
