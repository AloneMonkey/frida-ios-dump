//by: AloneMonkey

const LSApplicationWorkspace = ObjC.classes.LSApplicationWorkspace;

function openApplication(appid){
	const workspace = LSApplicationWorkspace.defaultWorkspace();
	return workspace.openApplicationWithBundleID_(appid);
}

function getbundleid(name){
	const workspace = LSApplicationWorkspace.defaultWorkspace();
	const apps = workspace.allApplications();
	var result;
	for(var index = 0; index < apps.count(); index++){
		var proxy = apps.objectAtIndex_(index);
		if(proxy.localizedName() && proxy.localizedName().toString() == name){
			return proxy.bundleIdentifier().toString();
		}
	}
	return ""
};

function getdisplayname(bundleid){
	const workspace = LSApplicationWorkspace.defaultWorkspace();
	const apps = workspace.allApplications();
	var result;
	for(var index = 0; index < apps.count(); index++){
		var proxy = apps.objectAtIndex_(index);
		if(proxy.bundleIdentifier() && proxy.bundleIdentifier().toString() == bundleid){
			return proxy.localizedName().toString();
		}
	}
	return ""
}

function handleMessage(message) {
	var bundleid;
	var displayname;
	if(message['name']){
		displayname = message['name']
		bundleid = getbundleid(displayname);
	}else if(message['bundleid']){
		bundleid = message['bundleid']
		displayname = getdisplayname(bundleid);
	}
	if(bundleid.length > 0){
		openApplication(bundleid);
	}
	send({ opened: displayname });
}

recv(handleMessage);