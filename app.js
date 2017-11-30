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
		if(proxy.localizedName().toString() == name){
			return proxy.bundleIdentifier().toString();
		}
	}
	return ""
};

function handleMessage(message) {
	const bundleid = getbundleid(message);
	if(bundleid.length > 0){
		openApplication(bundleid);
	}
	send({opened: "ok"});
}

recv(handleMessage);