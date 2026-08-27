<#
.SYNOPSIS
    Protect main: pull requests only, CI must be green. Run once.

.DESCRIPTION
    Applies the branch protection described in CONTRIBUTING.md:

      * direct pushes to main are refused - every change goes through a PR
      * the `tests` and `installer` checks from ci.yml must pass before merging
      * the branch must be up to date with main before it can be merged
      * force-pushes and branch deletion are refused
      * linear history: squash- or rebase-merge, no merge commits
      * NO required approvals by default - GitHub does not let anyone approve
        their own pull request, so as a solo maintainer a review requirement
        would block every merge. Raise -RequiredApprovals to 1 the day somebody
        else joins; nothing else has to change.

    Admins are deliberately not covered by the rule (`enforce_admins: false`),
    so there is an emergency route if CI itself ever breaks.

.PARAMETER Repo
    owner/name of the repository.

.PARAMETER RequiredApprovals
    Approving reviews needed to merge. 0 while working alone, 1 with a team.

.PARAMETER SkipMergeSettings
    Leave the repository's merge-button settings alone.

.EXAMPLE
    gh auth login
    powershell -ExecutionPolicy Bypass -File .github\setup-branch-protection.ps1

.NOTES
    IMPORTANT - order of operations:

      1. push main first, while direct pushes are still allowed
      2. let CI run once, so GitHub has seen the check names `tests` and
         `installer` (a required check that never reports blocks merging
         forever, and a typo here is invisible until a PR hangs)
      3. run this script
      4. from then on: branch -> PR -> merge

    Needs the GitHub CLI (winget install GitHub.cli) and admin rights on the
    repository.
#>
[CmdletBinding()]
param(
    [string]$Repo = "MechatronikMonkey/RiftRec",
    [ValidateRange(0, 6)][int]$RequiredApprovals = 0,
    [switch]$SkipMergeSettings
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI not found. Install it with: winget install GitHub.cli"
}
gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Not logged in. Run: gh auth login" }

$branch = "main"

# Job names from .github/workflows/ci.yml. If these two strings and the job
# names ever drift apart, pull requests will wait for a check that never comes.
$checks = @("tests", "installer")

Write-Host "Repository:         $Repo"
Write-Host "Branch:             $branch"
Write-Host "Required checks:    $($checks -join ', ')"
Write-Host "Required approvals: $RequiredApprovals"
Write-Host ""

$protection = @{
    required_status_checks           = @{
        strict   = $true          # branch must be up to date before merging
        contexts = $checks
    }
    enforce_admins                   = $false
    required_pull_request_reviews    = @{
        required_approving_review_count = $RequiredApprovals
        dismiss_stale_reviews           = $true
        require_last_push_approval      = $false
    }
    restrictions                     = $null
    required_linear_history          = $true
    allow_force_pushes               = $false
    allow_deletions                  = $false
    required_conversation_resolution = $true
    block_creations                  = $false
    lock_branch                      = $false
    allow_fork_syncing               = $true
}

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) "riftrec-protection.json"
try {
    $protection | ConvertTo-Json -Depth 6 | Set-Content -Path $tmp -Encoding utf8
    Write-Host "Applying branch protection..."
    gh api --method PUT "repos/$Repo/branches/$branch/protection" `
        -H "Accept: application/vnd.github+json" --input $tmp | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not apply branch protection" }
}
finally {
    if (Test-Path $tmp) { Remove-Item $tmp -Force }
}

if (-not $SkipMergeSettings) {
    # Squash-merge only, matching required_linear_history above, and tidy the
    # branch away afterwards. Keeps main's history the one-commit-per-change
    # shape the existing log already has.
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
Write-Host "Done. Current protection:" -ForegroundColor Green
gh api "repos/$Repo/branches/$branch/protection" --jq @'
{
  pull_request_required: (.required_pull_request_reviews != null),
  approvals: .required_pull_request_reviews.required_approving_review_count,
  checks: .required_status_checks.contexts,
  strict: .required_status_checks.strict,
  linear_history: .required_linear_history.enabled,
  force_pushes: .allow_force_pushes.enabled,
  admins_enforced: .enforce_admins.enabled
}
'@

Write-Host ""
Write-Host "From here on: git switch -c <branch> ... ; gh pr create" -ForegroundColor Cyan
Write-Host "If a pull request ever hangs on a check that never runs, the names in" -ForegroundColor Yellow
Write-Host "this script no longer match the job names in .github/workflows/ci.yml." -ForegroundColor Yellow
