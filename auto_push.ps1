# Auto-commit & push any local changes in this project.
# Run periodically by the "shmu-pocko auto-push" scheduled task.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "C:\Users\niko\kodujem\shmu_pocko"

git add -A
$changes = git status --porcelain
if ($changes) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    git commit -q -m "Auto-commit $ts"
    git push -q origin main
    Write-Output "$ts pushed:`n$changes"
} else {
    Write-Output "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') no changes"
}
