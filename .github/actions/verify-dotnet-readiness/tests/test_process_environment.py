"""Exercise process-only containment without touching a real user profile."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "Prepare-RunnerProcessEnvironment.ps1"
PWSH = shutil.which("pwsh")
DEAD = r"C:\actions-runner-2\_work\_temp\old\dotnet-home\.dotnet\tools"
LIVE = r"C:\actions-runner\_work\_temp\live\.dotnet\tools"
HARNESS = r'''
$ErrorActionPreference='Stop'
. $env:PROCESS_HELPER
$fixture=Get-Content -LiteralPath $env:PROCESS_FIXTURE -Raw|ConvertFrom-Json
$probe={param($Directory)
    if($fixture.probeFailure){throw 'Directory access denied'}
    return @($fixture.existing) -icontains $Directory
}.GetNewClosure()
if($fixture.initialize){
    $env:PATH=$fixture.path
    $env:DOTNET_ADD_GLOBAL_TOOLS_TO_PATH='true'
    $env:DOTNET_CLI_HOME=$fixture.home
    Initialize-RunnerProcessEnvironment -Windows $fixture.windows -DirectoryExists $probe
    @{path=$env:PATH;guard=$env:DOTNET_ADD_GLOBAL_TOOLS_TO_PATH;home=$env:DOTNET_CLI_HOME}|ConvertTo-Json -Compress
}else{
    $first=Get-RunnerProcessPath -PathValue $fixture.path -Windows $fixture.windows -DirectoryExists $probe
    $second=Get-RunnerProcessPath -PathValue $first.Path -Windows $fixture.windows -DirectoryExists $probe
    @{first=$first;second=$second}|ConvertTo-Json -Depth 4 -Compress
}
'''


class RunnerProcessEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise RuntimeError("PowerShell7 is required; process environment tests must not be skipped")

    def invoke(self, path, *, existing=(), windows=True, initialize=False, probe_failure=False):
        with tempfile.TemporaryDirectory(prefix="runner-process-fixture-") as temporary:
            root=Path(temporary)
            fixture=root / "fixture.json"
            fixture.write_text(json.dumps(dict(path=path, existing=existing, windows=windows,
                initialize=initialize, probeFailure=probe_failure, home=str(root / "cli-home"))), encoding="utf-8")
            harness=root / "test.ps1"
            harness.write_text(HARNESS, encoding="utf-8")
            output=root / "job-env.txt"
            output.write_text("KEEP=original\n", encoding="utf-8")
            result=subprocess.run([PWSH,"-NoProfile","-File",str(harness)],
                env={**os.environ,"PROCESS_HELPER":str(SCRIPT),"PROCESS_FIXTURE":str(fixture),
                     "GITHUB_ENV":str(output)}, capture_output=True, text=True, encoding="utf-8",
                errors="replace",timeout=20)
            published=output.read_text(encoding="utf-8-sig").splitlines()
            value=json.loads(result.stdout.splitlines()[-1]) if result.returncode==0 else None
            return result,value,published

    def test_only_missing_owned_temporary_tools_are_removed(self):
        unrelated=r"C:\Users\runner\.dotnet\tools"
        other_root=r"C:\other\_work\_temp\old\.dotnet\tools"
        source=";".join([r"C:\dotnet", DEAD, LIVE, unrelated, other_root])
        result,value,_=self.invoke(source,existing=[LIVE])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(value["first"]["Path"],";".join([r"C:\dotnet",LIVE,unrelated,other_root]))
        self.assertEqual(value["first"]["Removed"],1)

    def test_existing_temporary_directory_is_preserved(self):
        result,value,_=self.invoke(LIVE,existing=[LIVE.lower()])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(value["first"]["Path"],LIVE)
        self.assertEqual(value["first"]["Removed"],0)

    def test_order_duplicates_and_empty_entries_are_preserved(self):
        source=r"C:\dotnet;;C:\Tools;C:\dotnet;"
        result,value,_=self.invoke(source)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(value["first"]["Path"],source)

    def test_directory_probe_denial_is_not_treated_as_absence(self):
        result,_,published=self.invoke(DEAD,probe_failure=True,initialize=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("Directory access denied",result.stderr)
        self.assertEqual(published,["KEEP=original"])

    def test_cmd_boundary8191_is_accepted(self):
        source="A"*8191
        result,value,_=self.invoke(source)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(value["first"]["Path"],source)

    def test_unrelated_path_over8191_fails_without_publishing(self):
        result,_,published=self.invoke("A"*8192,initialize=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("8191",result.stderr)
        self.assertEqual(published,["KEEP=original"])

    def test_linux_path_is_unchanged_without_filesystem_probes(self):
        source="/usr/bin:"+"a"*9000
        result,value,_=self.invoke(source,windows=False,probe_failure=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(value["first"]["Path"],source)
        self.assertEqual(value["first"]["Removed"],0)

    def test_newline_and_nul_cannot_inject_job_environment(self):
        for source in ("safe\nINJECT=value","safe\rINJECT=value","safe\x00value"):
            with self.subTest(source=repr(source)):
                # OS environment setters truncate NUL; exercise that input at the public planner boundary.
                result,_,published=self.invoke(source,initialize="\x00" not in source)
                self.assertNotEqual(result.returncode,0)
                self.assertIn("single-line",result.stderr)
                self.assertEqual(published,["KEEP=original"])

    def test_cleaned_path_is_idempotent(self):
        result,value,_=self.invoke(r"C:\dotnet;"+DEAD)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(value["first"]["Path"],value["second"]["Path"])
        self.assertEqual(value["second"]["Removed"],0)

    def test_initialized_job_publishes_guard_and_clean_path_without_replacing_cli_home(self):
        result,value,published=self.invoke(r"C:\dotnet;"+DEAD,initialize=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(value["path"],r"C:\dotnet")
        self.assertEqual(value["guard"],"false")
        self.assertTrue(value["home"].endswith("cli-home"))
        self.assertEqual(published,["KEEP=original","DOTNET_ADD_GLOBAL_TOOLS_TO_PATH=false",r"PATH=C:\dotnet"])

    def test_unchanged_path_does_not_publish_an_unnecessary_path_override(self):
        result,value,published=self.invoke(r"C:\dotnet",initialize=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(value["guard"],"false")
        self.assertEqual(published,["KEEP=original","DOTNET_ADD_GLOBAL_TOOLS_TO_PATH=false"])
