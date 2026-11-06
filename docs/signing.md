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

## If Windows shows "Smart App Control blocked an app" (no "Run anyway")

Smart App Control (Windows 11) is stricter than SmartScreen: an unsigned,
unknown-reputation exe is **hard-blocked**, with no bypass button and no
per-app allow list. Two ways out:

### A. Sign the exe (the real fix, recommended)
A signature from any CA in the Microsoft Trusted Root Program makes SAC let
the app run - reputation no longer matters, and trust sticks to the
certificate, so every future build passes too.
* Azure Artifact Signing (formerly Trusted Signing), ~US$10/month, works from
  GitHub Actions; or an OV code-signing certificate (.pfx) as above.
* Self-signed certificates do NOT help - SAC requires a trusted CA chain.
* The workflow in this repo already signs automatically once
  `SIGN_PFX_BASE64` / `SIGN_PFX_PASS` secrets exist.

### B. One-time unblock on the shop PC (no certificate needed)
Run `tools/allow_laptopcheckpro.bat` once **as administrator** and restart the
PC. It switches Smart App Control to *evaluation mode* on that machine only:
SAC keeps logging but blocks nothing. The registry value it sets is
`HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy\VerifiedAndReputablePolicyState`
(2 = evaluation, 0 = off, 1 = enforcing). The bat file is also attached to
every Release next to the exe.
