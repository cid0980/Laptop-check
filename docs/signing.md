# Making Windows trust the exe (code signing)

Out of the box the exe is **unsigned**, so SmartScreen shows
"Windows protected your PC / Unknown publisher" the first time it runs on a
new PC. That is normal for any unsigned exe - even a perfect one. Click
**More info -> Run anyway** once per PC and it is remembered.

Three things already make the file look and behave like real software:
1. A proper icon and a UAC shield (the exe asks for admin openly).
2. Full file properties: right-click -> Properties -> Details shows product
   name, company, copyright and the exact version.
3. Releases are built by GitHub Actions from a tagged commit, so the exe on
   the Releases page always matches published source.

## To remove the warning completely (recommended when you sell reports)

1. Buy an **OV code-signing certificate** (~USD 70-200/year: Sectigo, SSL.com,
   GlobalSign). An EV certificate removes SmartScreen reputation buildup
   entirely but costs more and needs a USB token.
2. Export it as a `.pfx` with a password.
3. In the GitHub repo: Settings -> Secrets and variables -> Actions ->
   New repository secret:
   * `SIGN_PFX_BASE64` = base64 of the .pfx file
     (PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx"))`)
   * `SIGN_PFX_PASS` = the pfx password
4. Push a tag as usual. The workflow now signs the exe automatically before
   publishing the Release. Signed builds show your organisation name in the
   UAC and SmartScreen dialogs ("Verified publisher").

Nothing else changes: same one file, same repo, same release ritual.
