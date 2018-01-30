#!/usr/bin/env python
# -*- coding: utf-8 -*-

#Author : AloneMonkey
#blog: www.alonemonkey.com

import sys
import codecs
import frida
import threading 
import os
import shutil
import time
import getopt

reload(sys)
sys.setdefaultencoding('utf8')

DUMP_JS = './dump.js'
APP_JS = './app.js'
OUTPUT = "Payload"
file_dict = {}

opened = threading.Event()
finished = threading.Event()

global session
global name

#show usage
def usage():
	print '-------------------------frida-ios-dump(by AloneMonkey v2.0)----------------------------'
	print '\t%-20s\t%s' % ('-h,--help','Show help menu.');
	print '\t%-20s\t%s' % ('displayname','Decrypt the application of the specified display name. ps: ./dump.py 微信');
	print '\t%-20s\t%s' % ('-l','List the app has been installed.');
	print '\t%-20s\t%s' % ('-b bundleid','Decrypt the application of the specified bundleid. ps: ./dump.py com.tencent.xin');
	exit(0)

def get_usb_iphone():
	dManager = frida.get_device_manager();
	changed = threading.Event()
	def on_changed():
		changed.set()
	dManager.on('changed',on_changed)

	device = None
	while device is None:
		devices = [dev for dev in dManager.enumerate_devices() if dev.type == 'tether']
		if len(devices) == 0:
			print 'Waiting for usb device...'
			changed.wait()
		else:
			device = devices[0]

	dManager.off('changed',on_changed)
	
	return device

def gen_ipa(target):
	try:
		app_name = file_dict["app"]
		for key, value in file_dict.items():
			if key != "app":
				shutil.move(target +"/"+ key, target + "/" + app_name + "/" + value);
		(shotname,extension) = os.path.splitext(app_name)
		os.system(u''.join(("zip -qr ", name, ".ipa ./Payload")).encode('utf-8').strip());
		os.system("rm -rf ./Payload");
	except Exception as e:
		print e
		finished.set();

def on_message(message,data):
	global name;
	if message.has_key('payload'):
		payload = message['payload']
		if payload.has_key("opened"):
			name = payload["opened"]
			opened.set();
		if payload.has_key("dump"):
			orign_path = payload["path"]
			dumppath = payload["dump"]
			os.system(u''.join(("scp -P 2222 root@localhost:", dumppath, u" ./" + OUTPUT + u"/")).encode('utf-8').strip())
			os.system(u''.join(("chmod 655 ", u'./' + OUTPUT + u'/', os.path.basename(dumppath))).encode('utf-8').strip())
			index = orign_path.find(".app/")
			file_dict[os.path.basename(dumppath)] =  orign_path[index+5:]
		if payload.has_key("app"):
			apppath = payload["app"]
			os.system(u''.join(("scp -r -P 2222 root@localhost:", apppath, u" ./" + OUTPUT + u"/")).encode('utf-8').strip())
			os.system(u''.join(("chmod 755 ", u'./' + OUTPUT + u'/', os.path.basename(apppath))).encode('utf-8').strip())
			file_dict["app"] = os.path.basename(apppath)
		if payload.has_key("done"):
			gen_ipa(os.getcwd()+"/"+OUTPUT)
			finished.set();

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
        "Convert a cmp= function into a key= function"
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

def get_applications():
	device = get_usb_iphone()

	try:
	    applications = device.enumerate_applications()
	except Exception as e:
	    print "Failed to enumerate applications: %s" % e
	    exit(1)
	    return

	return applications

def list_applications():
	applications = get_applications()

	if len(applications) > 0:
   		pid_column_width = max(map(lambda app: len("%d" % app.pid), applications))
		name_column_width = max(map(lambda app: len(app.name), applications))
		identifier_column_width = max(map(lambda app: len(app.identifier), applications))
	else:
		pid_column_width = 0
		name_column_width = 0
		identifier_column_width = 0

	header_format = "%" + str(pid_column_width) + "s  " + "%-" + str(name_column_width) + "s  " + "%-" + str(identifier_column_width) + "s"
	print header_format % ("PID", "Name", "Identifier")
	print "%s  %s  %s" % (pid_column_width * "-", name_column_width * "-", identifier_column_width * "-")
	line_format = "%" + str(pid_column_width) + "s  " + "%-" + str(name_column_width) + "s  " + "%-" + str(identifier_column_width) + "s"
	for app in sorted(applications, key=cmp_to_key(compare_applications)):
		if app.pid == 0:
			print line_format % ("-", app.name, app.identifier)
		else:
			print line_format % (app.pid, app.name, app.identifier)

def get_pid_by_bundleid(bundleid):
	applications = get_applications()

	return [app.pid for app in applications if app.identifier == bundleid][0]
	
def find_target_app(isbundleid, value):
	applications = get_applications()

	if not isbundleid:
		return [app for app in applications if app.name == value]
	else:
		return [app for app in applications if app.identifier == value]

def load_js_file(session, filename):
	source = ''
	with codecs.open(filename,'r','utf-8') as f:
		source = source + f.read();
	script = session.create_script(source);
	script.on("message",on_message)
	script.load()
	return script

def clear_and_quit(session):
	if session:
	  	session.detach()
	sys.exit(0)

def create_dir(path):
	path = path.strip()
	path = path.rstrip("\\")
	if not os.path.exists(path):
		os.makedirs(path)
	else:
		print path + u" is existed!"; 

def open_target_app(isbundleid, value):
	device = get_usb_iphone();
	name = u'SpringBoard';
	print "open target app......"
	session = device.attach(name);
	script = load_js_file(session, APP_JS);
	if not isbundleid:
		script.post({'name': value})
	else:
		script.post({'bundleid': value})
	opened.wait();
	session.detach();
	create_dir(os.getcwd()+"/"+OUTPUT)
	print 'Waiting for the application to open......'
	time.sleep(5);

def start_dump(target):
	print "start dump target app......"
	device = get_usb_iphone();
	session = device.attach(target);
	script = load_js_file(session, DUMP_JS);
	script.post("dump");
	finished.wait();
	clear_and_quit(session);

def dump_by_display_name(display_name):
	open_target_app( 0, display_name)
	start_dump(display_name);

def dump_by_bundleid(bundleid):
	open_target_app( 1, bundleid)
	start_dump(get_pid_by_bundleid(bundleid));

def check_args():
	if len(sys.argv) < 2:
		usage()
		sys.exit(1)

	try:
		opts,args = getopt.getopt(sys.argv[1:],"hlb:",["help"]);
	except getopt.GetoptError:
		usage()
		sys.exit(2)

	for opt,value in opts:
		if opt in ("-h","--help"):
			usage()
		if opt in ("-l"):
			list_applications()
		if opt in ("-b"):
			if not find_target_app(1, value):
				print "can not find target app '%s'" % value
			else:
				dump_by_bundleid(value)

	if len(opts) == 0 and len(sys.argv) == 2:
		name = sys.argv[1].decode('utf8');
		if not find_target_app(0, name):
			print "can not find target app '%s'" % name
		else:
			dump_by_display_name(name)
		sys.exit(0)

if __name__ == "__main__":
	try:
		check_args()
		pass
	except KeyboardInterrupt:
		if session:
			session.detach()
		sys.exit()
	except:
	    pass