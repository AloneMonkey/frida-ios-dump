#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Author : AloneMonkey
# blog: www.alonemonkey.com

import sys
import codecs
import frida
import threading
import os
import shutil
import time
import getopt
import tempfile
import subprocess

reload(sys)
sys.setdefaultencoding('utf8')

script_dir = os.path.dirname(os.path.realpath(__file__))

DUMP_JS = os.path.join(script_dir, 'dump.js')

SSH_USER_HOST='root@localhost:'
SSH_PORT=2222

TEMP_DIR = tempfile.gettempdir()
PAYLOAD_DIR = 'Payload'
PAYLOAD_PATH = os.path.join(TEMP_DIR, PAYLOAD_DIR)
file_dict = {}

finished = threading.Event()


# show usage
def usage():
    print '-------------------------frida-ios-dump(by AloneMonkey v2.0)----------------------------'
    print '\t%-20s\t%s' % ('-h,--help', 'Show help menu.')
    print '\t%-20s\t%s' % ('name', 'Decrypt the application with the specified display name or bundle identifier. ps: ./dump.py 微信')
    print '\t%-20s\t%s' % ('-l', 'List the installed apps.')


def get_usb_iphone():
    dManager = frida.get_device_manager()
    changed = threading.Event()

    def on_changed():
        changed.set()

    dManager.on('changed', on_changed)

    device = None
    while device is None:
        devices = [dev for dev in dManager.enumerate_devices() if dev.type == 'tether']
        if len(devices) == 0:
            print 'Waiting for USB device...'
            changed.wait()
        else:
            device = devices[0]

    dManager.off('changed', on_changed)

    return device


def generate_ipa(path, display_name, bundle_identifier):
    ipa_filename = display_name.replace(' ', '\\ ') + '.ipa'

    print 'Generating {}'.format(ipa_filename)
    try:
        app_name = file_dict['app']

        for key, value in file_dict.items():
            from_dir = os.path.join(path, key)
            to_dir = os.path.join(path, app_name, value)
            if key != 'app':
                shutil.move(from_dir, to_dir)

        target_dir = './' + PAYLOAD_DIR
        zip_args = ('zip', '-qr', os.path.join(os.getcwd(), ipa_filename), target_dir)
        subprocess.check_call(zip_args, cwd=TEMP_DIR)
        shutil.rmtree(PAYLOAD_PATH)
        print
    except Exception as e:
        print e
        finished.set()


def on_message(message, data):
    global name
    if message.has_key('payload'):
        payload = message['payload']
        if payload.has_key('opened'):
            name = payload['opened']

        if payload.has_key('dump'):
            origin_path = payload['path']
            dump_path = payload['dump']

            scp_from = SSH_USER_HOST + dump_path.replace(' ', '\ ')
            scp_to = PAYLOAD_PATH + u'/'
            scp_args = ('scp', '-P {}'.format(SSH_PORT), scp_from, scp_to)
            subprocess.check_call(scp_args)

            chmod_dir = os.path.join(PAYLOAD_PATH, os.path.basename(dump_path))
            chmod_args = ('chmod', '655', chmod_dir)
            subprocess.check_call(chmod_args)

            index = origin_path.find('.app/')
            file_dict[os.path.basename(dump_path)] = origin_path[index + 5:]

        if payload.has_key('app'):
            app_path = payload['app']

            scp_from = SSH_USER_HOST + app_path.replace(' ', '\ ')
            scp_to = PAYLOAD_PATH + u'/'
            scp_args = ('scp', '-r', '-P {}'.format(SSH_PORT), scp_from, scp_to)
            subprocess.check_call(scp_args)

            chmod_dir = os.path.join(PAYLOAD_PATH, os.path.basename(app_path))
            chmod_args = ('chmod', '755', chmod_dir)
            subprocess.check_call(chmod_args)

            file_dict['app'] = os.path.basename(app_path)

        if payload.has_key('done'):
            finished.set()


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
    'Convert a cmp= function into a key= function'

    class K:
        def __init__(self, obj, *args):
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
        print 'Failed to enumerate applications: %s' % e
        return

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
    print header_format % ('PID', 'Name', 'Identifier')
    print '%s  %s  %s' % (pid_column_width * '-', name_column_width * '-', identifier_column_width * '-')
    line_format = '%' + str(pid_column_width) + 's  ' + '%-' + str(name_column_width) + 's  ' + '%-' + str(
        identifier_column_width) + 's'
    for app in sorted(applications, key=cmp_to_key(compare_applications)):
        if app.pid == 0:
            print line_format % ('-', app.name, app.identifier)
        else:
            print line_format % (app.pid, app.name, app.identifier)


def load_js_file(session, filename):
    source = ''
    with codecs.open(filename, 'r', 'utf-8') as f:
        source = source + f.read()
    script = session.create_script(source)
    script.on('message', on_message)
    script.load()

    return script


def create_dir(path):
    path = path.strip()
    path = path.rstrip('\\')
    if os.path.exists(path):
        print 'Removing {}'.format(path)
        shutil.rmtree(path)
    os.makedirs(path)


def open_target_app(device, name_or_bundleid):
    print 'Start the target app {}'.format(name_or_bundleid)

    display_name = ''
    bundle_identifier = ''
    for application in get_applications(device):
        if name_or_bundleid == application.identifier or name_or_bundleid == application.name:
            display_name = application.name
            bundle_identifier = application.identifier

    try:
        pid = device.spawn([bundle_identifier])
        device.resume(pid)
        create_dir(PAYLOAD_PATH)
        time.sleep(1)
    except Exception as e:
        print e

    return (pid, display_name, bundle_identifier)


def start_dump(device, pid, display_name, bundle_identifier):
    print 'Dumping {} to {}'.format(display_name, TEMP_DIR)

    session = device.attach(pid)
    script = load_js_file(session, DUMP_JS)
    script.post('dump')
    finished.wait()

    generate_ipa(PAYLOAD_PATH, display_name, bundle_identifier)

    if session:
        session.detach()

def check_args():
    if len(sys.argv) < 2:
        usage()
        return 1

    try:
        opts, args = getopt.getopt(sys.argv[1:], 'hl', ['help'])
    except getopt.GetoptError:
        usage()
        return 2

    for opt, value in opts:
        if opt in ('-h', '--help'):
            usage()

        if opt in '-l':
            device = get_usb_iphone()
            list_applications(device)

    if len(opts) == 0:
        name_or_bundleid = ' '.join(sys.argv[1:])

        device = get_usb_iphone()
        print "Device {}".format(device)
        (pid, display_name, bundle_identifier) = open_target_app(device, name_or_bundleid)
        start_dump(device, pid, display_name, bundle_identifier)

    return 0


if __name__ == '__main__':
    exit_code = 0
    try:
        exit_code = check_args()
    except:
        exit_code = 1

    if os.path.exists(PAYLOAD_PATH):
        print 'Deleting ' + PAYLOAD_PATH
        shutil.rmtree(PAYLOAD_PATH)

    sys.exit(exit_code)

