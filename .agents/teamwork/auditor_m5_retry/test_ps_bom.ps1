$content = @"
status
exit
"@
$content | Out-File -FilePath test_bom_audit.crew -Encoding utf8
crewai-team repl --batch test_bom_audit.crew
$ec = $LASTEXITCODE
Remove-Item test_bom_audit.crew -Force
Write-Host "POWERSHELL REPL EXIT CODE: $ec"
if ($ec -ne 0) { exit 1 }
