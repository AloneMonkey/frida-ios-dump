import socket
import struct
import threading
import unittest
from unittest.mock import Mock, patch

import paramiko

import dump


class SFTPInitializationTests(unittest.TestCase):
    def channel_pair(self):
        local, peer = socket.socketpair()
        self.addCleanup(local.close)
        self.addCleanup(peer.close)
        peer.settimeout(1)

        class Channel:
            def __init__(self):
                self.closed = threading.Event()

            def settimeout(self, value):
                local.settimeout(value)

            def invoke_subsystem(self, name):
                pass

            def get_name(self):
                return 'test-sftp'

            def send(self, data):
                return local.send(data)

            def recv(self, size):
                return local.recv(size)

            def close(self):
                self.closed.set()
                try:
                    local.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                local.close()

        channel = Channel()
        ssh = Mock()
        ssh.get_transport.return_value.open_session.return_value = channel
        return ssh, channel, peer

    def test_missing_sftp_version_response_times_out_and_closes_channel(self):
        ssh, channel, peer = self.channel_pair()
        errors = []

        def initialize():
            try:
                dump.open_sftp(ssh)
            except Exception as error:
                errors.append(error)

        with patch.object(dump, 'TRANSFER_TIMEOUT', 0.05):
            worker = threading.Thread(target=initialize, daemon=True)
            worker.start()
            try:
                self.assertTrue(peer.recv(1024), 'Client must send the SFTP version request')
                worker.join(1)
                self.assertFalse(worker.is_alive(), 'Missing version reply must not hang initialization')
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], socket.timeout)
                self.assertTrue(channel.closed.is_set())
            finally:
                channel.close()
                worker.join(1)

    def test_missing_subsystem_acknowledgement_times_out(self):
        ssh, channel, _ = self.channel_pair()

        def wait_for_acknowledgement(name):
            # Mirrors the acknowledgement wait: channel socket timeouts do not
            # interrupt it, but closing the channel wakes the waiter.
            if not channel.closed.wait(1):
                self.fail('Subsystem acknowledgement wait was not cancelled')
            raise paramiko.SSHException('Channel closed')

        channel.invoke_subsystem = wait_for_acknowledgement
        with patch.object(dump, 'TRANSFER_TIMEOUT', 0.05):
            with self.assertRaisesRegex(socket.timeout, 'SFTP initialization timed out'):
                dump.open_sftp(ssh)
        self.assertTrue(channel.closed.is_set())

    def test_successful_negotiation_retains_io_timeout_and_cancels_deadline(self):
        ssh, channel, peer = self.channel_pair()
        # A real SFTP v3 version response, consumed by Paramiko's constructor.
        peer.sendall(struct.pack('>IBI', 5, 2, 3))
        with patch.object(dump, 'TRANSFER_TIMEOUT', 0.1):
            client = dump.open_sftp(ssh)
        self.addCleanup(client.close)
        ssh.get_transport.return_value.open_session.assert_called_once_with(timeout=0.1)
        self.assertIs(client.get_channel(), channel)
        self.assertFalse(channel.closed.wait(0.2), 'Successful channel must survive its initialization deadline')
        with self.assertRaises(socket.timeout):
            channel.recv(1)

    def test_channel_open_failure_propagates_without_starting_negotiation(self):
        ssh = Mock()
        ssh.get_transport.return_value.open_session.side_effect = paramiko.SSHException('open timeout')
        with patch.object(dump, 'TRANSFER_TIMEOUT', 0.05):
            with self.assertRaisesRegex(paramiko.SSHException, 'open timeout'):
                dump.open_sftp(ssh)
        ssh.get_transport.return_value.open_session.assert_called_once_with(timeout=0.05)


if __name__ == '__main__':
    unittest.main()
