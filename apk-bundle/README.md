# apk-bundle

Empty in the repository. For a release tag, `release.yml` puts the signed APK and a
`release.json` (`version_name`, `version_code`, `file`) here before it builds the
image. The image copies this folder to `/app/apk-bundle`, and the server imports the
APK into `data/apk` on startup, so tablets update to the app from the same tag
(SPEC §4.3). Images built from `main` carry no APK.
