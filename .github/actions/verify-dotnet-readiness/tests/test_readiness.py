"""Exercise the public readiness script against an isolated dotnet command fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "Assert-DotnetReadiness.ps1"
PWSH = shutil.which("pwsh")
ISSUE = "https://github.com/Eternet/Servidores/issues/554"
SHIM = r'''
$fixture = Get-Content -LiteralPath $env:READINESS_FIXTURE -Raw | ConvertFrom-Json
$pin=if(Test-Path -LiteralPath 'global.json'){Get-Content -LiteralPath 'global.json' -Raw|ConvertFrom-Json}else{$null}
@{ arguments = @($args); directory = (Get-Location).Path; pin=$pin; globalTools=$env:DOTNET_ADD_GLOBAL_TOOLS_TO_PATH } |
    ConvertTo-Json -Compress | Add-Content -LiteralPath $env:READINESS_CALLS
if ($args.Count -ne 1) { throw 'Unexpected command arguments.' }
switch ($args[0]) {
    '--list-sdks' { $fixture.list | Write-Output; exit $fixture.listExit }
    '--version' { if($fixture.resolvePin -and $pin){$pin.sdk.version|Write-Output}else{$fixture.version | Write-Output}; exit $fixture.versionExit }
    '--list-runtimes' { $fixture.runtimes | Write-Output; exit $fixture.runtimeExit }
    default { throw "Readiness attempted a mutating/unexpected dotnet command: $($args[0])" }
}
'''

LAUNCHER = r'''
[CmdletBinding()]
param(
    [string]$DotnetVersion = '', [string]$GlobalJsonFile = '',
    [string]$WorkingDirectory = '.', [string]$Workspace,
    [string]$DotnetCommand = 'dotnet',
    [string]$SdkSelection='source', [string]$RuntimeVersion=''
)
try {
    & $env:READINESS_SCRIPT @PSBoundParameters
} catch {
    # Assert the actual exception, independently of terminal width, ANSI styling and ErrorView.
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
'''


class SdkReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise RuntimeError("PowerShell 7 is required; these readiness tests must not be skipped")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dotnet-readiness-fixture-")
        self.root = Path(self.temporary.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(self.root.name.startswith("dotnet-readiness-fixture-"))
        self.addCleanup(self.temporary.cleanup)
        self.source = self.root / "source"
        self.source.mkdir()
        self.fixture = self.root / "fixture.json"
        self.calls = self.root / "calls.jsonl"
        self.output = self.root / "output.txt"
        self.shim = self.root / "dotnet-fixture.ps1"
        self.shim.write_text(SHIM, encoding="utf-8")
        self.launcher = self.root / "invoke-readiness.ps1"
        self.launcher.write_text(LAUNCHER, encoding="utf-8")

    def invoke(self, versions="10.0.x", *, global_file=None, installed=None,
               selected="10.0.301", list_exit=0, version_exit=0, command=None,
               working_directory=".", selection="source", runtimes="", installed_runtimes=None,
               runtime_exit=0, resolve_pin=False):
        listing = installed if installed is not None else ["10.0.301 [synthetic/sdk]"]
        self.fixture.write_text(json.dumps({"list": listing, "version": selected,
            "listExit": list_exit, "versionExit": version_exit, "resolvePin":resolve_pin,
            "runtimes":installed_runtimes or [], "runtimeExit":runtime_exit}), encoding="utf-8")
        arguments = [PWSH, "-NoProfile", "-File", str(self.launcher), "-Workspace", str(self.root),
                     "-WorkingDirectory", working_directory, "-DotnetCommand", str(command or self.shim)]
        arguments += ["-SdkSelection", selection]
        if runtimes:
            arguments += ["-RuntimeVersion", runtimes]
        if versions:
            arguments += ["-DotnetVersion", versions]
        if global_file is not None:
            arguments += ["-GlobalJsonFile", global_file]
        environment = {**os.environ, "READINESS_FIXTURE": str(self.fixture),
                       "READINESS_CALLS": str(self.calls), "GITHUB_OUTPUT": str(self.output),
                       "READINESS_SCRIPT": str(SCRIPT)}
        return subprocess.run(arguments, env=environment, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=20)

    def recorded(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text(encoding="utf-8-sig").splitlines()]

    def assert_rejected(self, result, reason):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(reason, result.stdout + result.stderr)
        self.assertIn(ISSUE, result.stdout + result.stderr)
        self.assertFalse(self.output.exists(), "Failure must not publish a successful SDK output")

    def test_installed_multiline_sdks_and_actual_resolution_pass(self):
        result = self.invoke("9.0.x\n10.0.x", installed=[
            "9.0.305 [synthetic/sdk]", "10.0.301 [synthetic/sdk]"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([call["arguments"] for call in self.recorded()], [["--list-sdks"], ["--version"]])
        self.assertEqual(self.outputs(), {"dotnet-version":"10.0.301", "dotnet-root":str(self.root)})

    def outputs(self):
        return dict(line.split("=",1) for line in self.output.read_text(encoding="utf-8-sig").splitlines())

    def test_last_requested_selects_highest_installed_match_and_verifies_native_resolution(self):
        result=self.invoke("9.0.x\n10.0.x", selection="last-requested", selected="11.0.100", resolve_pin=True,
            installed=["9.0.320 [synthetic/sdk]", "10.0.99 [synthetic/sdk]", "10.0.401 [synthetic/sdk]", "11.0.100 [synthetic/sdk]"])
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.outputs(),{"dotnet-version":"10.0.401","dotnet-root":str(self.root)})
        calls=self.recorded()
        self.assertEqual([c["arguments"] for c in calls],[["--list-sdks"],["--version"]])
        self.assertEqual(calls[1]["pin"]["sdk"],{"version":"10.0.401","rollForward":"disable","allowPrerelease":False})
        self.assertEqual(calls[1]["globalTools"],"false")
        self.assertFalse(Path(calls[1]["directory"]).exists(),"Owned selection directory must be removed")
        self.assertFalse((self.root/"global.json").exists(),"Source must never be pinned by readiness")

    def test_last_requested_preserves_explicit_source_global_json_policy(self):
        pin=self.source/"global.json"
        content='{"sdk":{"version":"9.0.300","rollForward":"latestPatch","allowPrerelease":false}}'
        pin.write_text(content,encoding="utf-8")
        result=self.invoke("9.0.x\n10.0.x",selection="last-requested",global_file="source/global.json",
            installed=["9.0.320 [synthetic/sdk]","10.0.401 [synthetic/sdk]"],selected="9.0.320")
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.outputs()["dotnet-version"],"9.0.320")
        self.assertEqual(pin.read_text(encoding="utf-8"),content)
        self.assertEqual([Path(c["directory"]) for c in self.recorded()],[self.source,self.source])

    def test_last_requested_respects_implicit_ancestor_global_json(self):
        pin=self.root/"global.json"
        content='{"sdk":{"version":"9.0.320","rollForward":"disable"}}'
        pin.write_text(content,encoding="utf-8")
        result=self.invoke("9.0.x\n10.0.x",selection="last-requested",working_directory="source",
            installed=["9.0.320 [synthetic/sdk]","10.0.401 [synthetic/sdk]"],selected="9.0.320")
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.outputs()["dotnet-version"],"9.0.320")
        self.assertEqual(pin.read_text(encoding="utf-8"),content)
        self.assertEqual([Path(c["directory"]) for c in self.recorded()],[self.source,self.source])

    def test_last_requested_rejects_native_resolution_of_a_different_installed_sdk(self):
        result=self.invoke(selection="last-requested",selected="11.0.100",
            installed=["10.0.301 [synthetic/sdk]","11.0.100 [synthetic/sdk]"])
        self.assert_rejected(result,"did not resolve the requested SDK candidate")
        self.assertFalse(Path(self.recorded()[1]["directory"]).exists())

    def test_runtime_requirements_verify_both_families_from_the_selected_host(self):
        result=self.invoke(runtimes="Microsoft.NETCore.App 9.0.x\nMicrosoft.AspNetCore.App 9.0.x",
            installed_runtimes=["Microsoft.NETCore.App 9.0.11 [synthetic/shared]","Microsoft.AspNetCore.App 9.0.11 [synthetic/shared]"])
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual([c["arguments"] for c in self.recorded()],[["--list-sdks"],["--version"],["--list-runtimes"]])
        self.assertEqual(self.outputs()["dotnet-version"],"10.0.301")

    def test_missing_target_runtime_fails_even_with_both_sdk_families_installed(self):
        result=self.invoke("9.0.x\n10.0.x",runtimes="Microsoft.NETCore.App 9.0.x",
            installed=["9.0.320 [synthetic/sdk]","10.0.301 [synthetic/sdk]"],
            installed_runtimes=["Microsoft.NETCore.App 10.0.1 [synthetic/shared]"])
        self.assert_rejected(result,"Required runtime 'Microsoft.NETCore.App 9.0.x' is missing")

    def test_netcore_runtime_does_not_substitute_for_aspnetcore_runtime(self):
        result=self.invoke(runtimes="Microsoft.AspNetCore.App 9.0.x",
            installed_runtimes=["Microsoft.NETCore.App 9.0.11 [synthetic/shared]","Microsoft.AspNetCore.App 10.0.1 [synthetic/shared]"])
        self.assert_rejected(result,"Required runtime 'Microsoft.AspNetCore.App 9.0.x' is missing")

    def test_failed_runtime_inventory_is_unknown_and_never_exports_success(self):
        result=self.invoke(runtimes="Microsoft.NETCore.App 9.0.x",runtime_exit=1)
        self.assert_rejected(result,"dotnet --list-runtimes failed")

    def test_malformed_runtime_requirements_fail_before_native_execution(self):
        result=self.invoke(runtimes="Microsoft.NETCore.App 9.0.x; install")
        self.assert_rejected(result,"Unsupported runtime requirement")
        self.assertEqual(self.recorded(),[])

    def test_source_selection_default_keeps_native_host_behavior(self):
        result=self.invoke(installed=["10.0.301 [synthetic/sdk]","11.0.100 [synthetic/sdk]"],selected="11.0.100")
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.outputs()["dotnet-version"],"11.0.100")
        self.assertEqual([Path(c["directory"]) for c in self.recorded()],[self.root,self.root])

    def test_one_missing_requested_sdk_fails_before_resolution(self):
        result = self.invoke("9.0.x\n10.0.x", installed=["10.0.301 [synthetic/sdk]"])
        self.assert_rejected(result, "Required SDK '9.0.x' is missing")
        self.assertEqual([call["arguments"] for call in self.recorded()], [["--list-sdks"]])

    def test_global_json_is_resolved_in_source_directory_and_not_rewritten(self):
        pin = self.source / "global.json"
        content = b'{"sdk":{"version":"10.0.300","rollForward":"latestPatch","allowPrerelease":false}}\n'
        pin.write_bytes(content)
        result = self.invoke("", global_file="source/global.json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(pin.read_bytes(), content)
        self.assertEqual([Path(call["directory"]).resolve() for call in self.recorded()],
                         [self.source, self.source])
        self.assertEqual(sorted(p.name for p in self.source.iterdir()), ["global.json"])

    def test_failed_project_sdk_resolution_fails_even_when_requested_sdk_is_installed(self):
        (self.source / "global.json").write_text('{"sdk":{"version":"10.0.999","rollForward":"disable"}}')
        result = self.invoke("10.0.x", global_file="source/global.json", selected="", version_exit=145)
        self.assert_rejected(result, "cannot resolve an SDK for the source checkout/global.json")
        self.assertEqual([call["arguments"] for call in self.recorded()], [["--list-sdks"], ["--version"]])

    def test_nested_checkout_without_explicit_global_path_uses_that_checkout(self):
        result = self.invoke(working_directory="source")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([Path(call["directory"]).resolve() for call in self.recorded()],
                         [self.source, self.source])

    def test_exact_preview_sdk_is_required_without_accepting_it_for_a_stable_channel(self):
        preview = "11.0.100-preview.6.26359.118"
        result = self.invoke(preview, installed=[preview + " [synthetic/sdk]"], selected=preview)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.output.unlink()
        result = self.invoke("11.0.x", installed=[preview + " [synthetic/sdk]"], selected=preview)
        self.assert_rejected(result, "Required SDK '11.0.x' is missing")

    def test_feature_band_wildcard_requires_the_correct_band(self):
        result = self.invoke("10.0.3xx")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.output.unlink()
        result = self.invoke("10.0.4xx")
        self.assert_rejected(result, "Required SDK '10.0.4xx' is missing")

    def test_missing_command_and_empty_inventory_cannot_pass(self):
        result = self.invoke(command=self.root / "missing-dotnet-command")
        self.assert_rejected(result, "dotnet is not available")
        self.assertEqual(self.recorded(), [])
        result = self.invoke(installed=[])
        self.assert_rejected(result, "No installed .NET SDKs were reported")

    def test_failed_inventory_and_inconsistent_resolution_cannot_pass(self):
        result = self.invoke(list_exit=1)
        self.assert_rejected(result, "dotnet --list-sdks failed")
        result = self.invoke(selected="9.0.305")
        self.assert_rejected(result, "exactly one SDK from the installed inventory")
        result = self.invoke(selected=["10.0.301", "10.0.302"])
        self.assert_rejected(result, "exactly one SDK from the installed inventory")

    def test_missing_invalid_or_non_source_global_json_does_not_fall_back(self):
        result = self.invoke("", global_file="missing/global.json")
        self.assert_rejected(result, "does not exist")
        pin = self.source / "global.json"
        pin.write_text("{bad-json", encoding="utf-8")
        result = self.invoke("", global_file="source/global.json")
        self.assert_rejected(result, "not valid JSON")
        pin.write_text("null", encoding="utf-8")
        result = self.invoke("", global_file="source/global.json")
        self.assert_rejected(result, "does not declare an SDK version")
        pin.write_text('{"test":{"runner":"Microsoft.Testing.Platform"}}', encoding="utf-8")
        result = self.invoke("", global_file="source/global.json")
        self.assert_rejected(result, "does not declare an SDK version")
        other = self.source / "other.json"
        other.write_text('{"sdk":{"version":"10.0.300"}}', encoding="utf-8")
        result = self.invoke("", global_file="source/other.json")
        self.assert_rejected(result, "actual source global.json")
        self.assertEqual(self.recorded(), [])

    def test_empty_or_malformed_requirements_fail_without_running_dotnet(self):
        result = self.invoke("")
        self.assert_rejected(result, "Specify dotnet-version or global-json-file")
        result = self.invoke("10.0.x; install something")
        self.assert_rejected(result, "Unsupported SDK version selector")
        self.assertEqual(self.recorded(), [])


if __name__ == "__main__":
    unittest.main()
