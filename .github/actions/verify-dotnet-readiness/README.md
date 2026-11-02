# Verify centrally provisioned .NET SDKs

This action checks the shared runner prerequisite tracked by
[Servidores #554](https://github.com/Eternet/Servidores/issues/554). It never
downloads or installs SDKs, changes host configuration, rewrites global.json,
or removes tools needed by a subsequent Copilot session.

The input names `dotnet-version` and `global-json-file` match setup-dotnet.
Version requirements support exact stable/preview SDK versions, major/minor
channels, `x`/`*`, and feature-band wildcards such as `10.0.3xx`. Multiline
requirements must all be installed. Unsupported selectors fail explicitly.

`global-json-file` points to the actual source file named `global.json`, relative
to github.workspace unless absolute. Resolution runs in that file's directory;
the installed dotnet host enforces its rollForward and allowPrerelease policy.
The action does not require an exact SDK installation when the source resolver
legitimately accepts a later installed version. If both inputs are present,
the explicit version requirements must also be installed.

`working-directory` defaults to the checkout root. Set it to a nested checkout
such as `source` when no explicit global-json-file is supplied. SDK selection
follows that checkout's existing global.json, or the installed host's selection
rules when there is no source SDK pin. Presence checks do not create a new pin.
The `dotnet-version` output reports the actual selected SDK.

```yaml
- uses: Eternet/github/.github/actions/verify-dotnet-readiness@INTEGRATED_COMMIT
  with:
    global-json-file: global.json
```

The caller must check out the source first. PowerShell 7 and the shared dotnet
host must already be available on the Windows or Linux runner. Missing SDKs or
failed source resolution stop the job with a reference to #554; there is no
download or hosted-runner fallback.

Run the isolated command-fixture tests with Python and PowerShell 7:

```text
python -B -m unittest discover -s .github/actions/verify-dotnet-readiness/tests -p "test_*.py" -v
```
