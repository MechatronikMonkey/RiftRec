<#
.SYNOPSIS
    Apply the repository rulesets in .github/rulesets/ to GitHub. Run once.

.DESCRIPTION
    The rules themselves live in `.github/rulesets/*.json`, versioned in the
    repository so they can be diffed, reviewed and restored. This script only
    uploads them; the same files can be imported by hand under
    Settings -> Rules -> Rulesets -> New ruleset -> Import a ruleset.

    See .github/rulesets/README.md for what each rule does and why.

    Re-running updates the existing rulesets (matched by name) instead of adding
    duplicates.

.PARAMETER Repo
    owner/name of the repository.

.PARAMETER RequiredApprovals
    Override the approving reviews needed to merge. The file says 0, because
    GitHub does not let anyone approve their own pull request and a review
    requirement would block a solo maintainer on every merge. Pass 1 once a
    second person is involved.

.PARAMETER Evaluate
    Upload in "evaluate" mode: violations are recorded under Rule Insights but
    nothing is blocked. The cheap way to confirm the required check names are
    right before they can block a pull request.

.PARAMETER SkipMergeSettings
    Leave the repository's merge-button settings alone.

.EXAMPLE
    gh auth login
    powershell -ExecutionPolicy Bypass -File .github\setup-repo-rules.ps1

.EXAMPLE
    # dry run first
    powershell -ExecutionPolicy Bypass -File .github\setup-repo-rules.ps1 -Evaluate

.NOTES
    IMPORTANT - order of operations:

      1. push main first, while direct pushes are still allowed
      2. let CI run once, so GitHub has seen the check names `tests` and
         `installer` (a required check that never reports blocks merging
         forever, and a typo is invisible until a pull request hangs)
      3. run this script
      4. from then on: branch -> PR -> merge

    Needs the GitHub CLI (winget install GitHub.cli) and admin rights on the
    repository.
#>
[CmdletBinding()]
param(
    [string]$Repo = "MechatronikMonkey/RiftRec",
    [ValidateRange(0, 6)][Nullable[int]]$RequiredApprovals,
    [switch]$Evaluate,
    [switch]$SkipMergeSettings
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI not found. Install it with: winget install GitHub.cli"
}
gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Not logged in. Run: gh auth login" }

$rulesetDir = Join-Path $PSScriptRoot "rulesets"
$files = Get-ChildItem -Path $rulesetDir -Filter *.json | Sort-Object Name
if (-not $files) { throw "No ruleset files found in $rulesetDir" }

Write-Host "Repository: $Repo"
Write-Host "Rulesets:   $($files.Name -join ', ')"
Write-Host ""

function Save-Ruleset($file) {
    $ruleset = Get-Content $file.FullName -Raw | ConvertFrom-Json

    if ($Evaluate) { $ruleset.enforcement = "evaluate" }

    if ($null -ne $RequiredApprovals) {
        foreach ($rule in $ruleset.rules) {
            if ($rule.type -eq "pull_request") {
                $rule.parameters.required_approving_review_count = $RequiredApprovals
            }
        }
    }

    $name = $ruleset.name
    $existing = gh api "repos/$Repo/rulesets" --jq ".[] | select(.name == `"$name`") | .id"

    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) "riftrec-ruleset.json"
    try {
        $ruleset | ConvertTo-Json -Depth 12 | Set-Content -Path $tmp -Encoding utf8
        if ($existing) {
            Write-Host "  updating '$name' (id $existing, $($ruleset.enforcement))"
            gh api --method PUT "repos/$Repo/rulesets/$existing" `
                -H "Accept: application/vnd.github+json" --input $tmp | Out-Null
        }
        else {
            Write-Host "  creating '$name' ($($ruleset.enforcement))"
            gh api --method POST "repos/$Repo/rulesets" `
                -H "Accept: application/vnd.github+json" --input $tmp | Out-Null
        }
        if ($LASTEXITCODE -ne 0) { throw "Could not save the '$name' ruleset" }
    }
    finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
    }
}

foreach ($file in $files) { Save-Ruleset $file }

if (-not $SkipMergeSettings) {
    # Squash-merge only, matching required_linear_history in main.json, and tidy
    # the branch away afterwards. Keeps main's history the one-commit-per-change
    # shape the existing log already has.
    Write-Host ""
    Write-Host "Setting merge behaviour (squash only, delete branch on merge)..."
    gh api --method PATCH "repos/$Repo" `
        -f allow_squash_merge=true `
        -f allow_merge_commit=false `
        -f allow_rebase_merge=false `
        -f delete_branch_on_merge=true `
        -f squash_merge_commit_title=PR_TITLE `
        -f squash_merge_commit_message=PR_BODY | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not update the merge settings" }
}

Write-Host ""
Write-Host "Done. Rulesets on $($Repo):" -ForegroundColor Green
gh api "repos/$Repo/rulesets" --jq '.[] | "  \(.name)  [\(.target)]  \(.enforcement)"'

Write-Host ""
Write-Host "From here on: git switch -c <branch> ... ; gh pr create" -ForegroundColor Cyan
Write-Host "If a pull request ever hangs on a check that never runs, the contexts in" -ForegroundColor Yellow
Write-Host ".github/rulesets/main.json no longer match the job names in ci.yml." -ForegroundColor Yellow
Write-Host "Settings -> Rules -> Rule Insights shows what each ruleset actually did." -ForegroundColor Yellow
