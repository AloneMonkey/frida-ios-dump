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

DUMP_JS = './dump.js'
APP_JS = './app.js'
OUTPUT = "Payload"
file_dict = {}

opened = threading.Event()
finished = threading.Event()

global session

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
				shutil.move(target+"/"+key, target + "/" + app_name + "/" + value);
		(shotname,extension) = os.path.splitext(app_name)
		os.system("zip -qr %s.ipa ./Payload" % shotname);
		os.system("rm -rf ./Payload");
	except Exception as e:
		print e
		finished.set();

def on_message(message,data):
	if message.has_key('payload'):
		payload = message['payload']
		if payload.has_key("opened"):
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
		
def loadJsFile(session, filename):
	source = ''
	with codecs.open(filename,'r','utf-8') as f:
		source = source + f.read();
	script = session.create_script(source);
	script.on("message",on_message)
	script.load()
	return script

def ClearAndQuit(session):
	if session:
	  	session.detach()
	sys.exit(0)

def createDir(path):
	path = path.strip()
	path = path.rstrip("\\")
	if not os.path.exists(path):
		os.makedirs(path)
	else:
		print path + u" is existed!"; 

def main(target):
	global session
	session = None
	device = get_usb_iphone();
	#open app
	name = u'SpringBoard';
	print "open target app......"
	session = device.attach(name);
	script = loadJsFile(session, APP_JS);
	name = target.decode('utf8');
	script.post(name);
	opened.wait();
	session.detach();
	createDir(os.getcwd()+"/"+OUTPUT)
	print "start dump target app......"
	session = device.attach(name);
	script = loadJsFile(session, DUMP_JS);
	script.post("dump");
	finished.wait();
	ClearAndQuit(session);

if __name__ == "__main__":
	if len(sys.argv) < 2:
		print "usage: ./dump.py 微信"
		sys.exit(0)
	else:
		try:
			main(sys.argv[1])
		except KeyboardInterrupt:
			if session:
				session.detach()
			sys.exit()
		except:
		    pass