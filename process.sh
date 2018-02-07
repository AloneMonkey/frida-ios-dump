#!/bin/bash

ORIGINAL_IPA_FOLDER="${HOME}/Music/iTunes/iTunes Media/Mobile Applications"
DECRYPTED_IPA_FOLDER="${HOME}/Hacking/Decrypted Apps"

IOS_DEPLOY="/usr/local/bin/ios-deploy"
PLISTBUDDY="/usr/libexec/PlistBuddy"
FRIDA_DUMP_PY="./dump.py"
# CFBundleIdentifier in Info.plist to uninstall

ORIGINAL_IPAS=()
while IFS=  read -r -d $'\0'; do
    ORIGINAL_IPA=$( echo "${REPLY}" )
    ORIGINAL_IPAS+=( "${ORIGINAL_IPA}" )
done < <(find "${ORIGINAL_IPA_FOLDER}" -type f -name '*.ipa' -print0)

echo "Decrypting ${#ORIGINAL_IPAS[@]} apps"
APP_COUNTER=0
for ORIGINAL_IPA in "${ORIGINAL_IPAS[@]}"; do
    APP_COUNTER=$((APP_COUNTER + 1))
    echo "#### Decryting app number: ${APP_COUNTER}"
    APP_BUNDLE_ID=""
    ESCAPED_IPA_PATH=$( echo "${ORIGINAL_IPA}" | sed 's/ /\\ /g' | sed 's/&/\\&/g' )

    IPA_NAME=$( basename "${ESCAPED_IPA_PATH}" )

    # Make a temp file for the iTunesMetadata.plist
    ITUNESMETADATA_TMP_FILE=$( mktemp "/tmp/iTunesMetadata.plist.XXXXXX" ) || exit 1

    # Unzip iTunesMetadata.plist to temp file
    UNZIP_CMD="unzip -p ${ESCAPED_IPA_PATH} iTunesMetadata.plist > ${ITUNESMETADATA_TMP_FILE}"
    echo "${UNZIP_CMD}"
    eval "${UNZIP_CMD}" || exit 1

    # Extract softwareVersionBundleId from plist file
    APP_BUNDLE_ID=$( "${PLISTBUDDY}" -c 'Print softwareVersionBundleId' "${ITUNESMETADATA_TMP_FILE}" )
    echo "App bundle identifier: ${APP_BUNDLE_ID}"
    rm "${ITUNESMETADATA_TMP_FILE}"

    # Install the app
    IOS_DEPLOY_INSTALL="${IOS_DEPLOY} -W --bundle ${ESCAPED_IPA_PATH}"
    echo "${IOS_DEPLOY_INSTALL}"
    eval "${IOS_DEPLOY_INSTALL}"

    if [ $? -eq 0 ]; then
        sleep 1

        echo "Dumping ${APP_BUNDLE_ID}"
        FRIDA_DUMP_CMD="${FRIDA_DUMP_PY} --output ${IPA_NAME} ${APP_BUNDLE_ID}"
        echo "${FRIDA_DUMP_CMD}"
        eval "${FRIDA_DUMP_CMD}"
    fi

    # Uninstall the app
    IOS_DEPLOY_UNINSTALL="${IOS_DEPLOY} -W --uninstall_only --bundle_id ${APP_BUNDLE_ID}"
    echo "${IOS_DEPLOY_UNINSTALL}"
    eval "${IOS_DEPLOY_UNINSTALL}"
done