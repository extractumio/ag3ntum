# Release Workflow

## Branches
- `main` -- active development, may be unstable
- `release` -- stable releases only, protected branch

## How to Release

1. **Choose version number** (semver):
   - Patch (0.1.0 -> 0.1.1): bug fixes only
   - Minor (0.1.0 -> 0.2.0): new features, non-breaking
   - Major (0.x -> 1.0.0): breaking changes, API redesigns

2. **Update VERSION file** with the new number

3. **Update CHANGELOG.md** -- add a section:
   ```
   ## [X.Y.Z] - YYYY-MM-DD

   ### Added
   - New feature description

   ### Changed
   - Modified behavior description

   ### Fixed
   - Bug fix description
   ```

4. **Commit** on `main`: `chore: prepare release X.Y.Z`

5. **Create PR**: `main` -> `release`
   - GitHub Actions will verify VERSION + CHANGELOG are updated
   - If checks fail, the PR cannot be merged

6. **Merge PR** -- GitHub Actions automatically:
   - Creates git tag `vX.Y.Z`
   - Creates GitHub Release with changelog content

## Safeguards
- Branch protection on `release` requires the "Release Gate" check to pass
- VERSION and CHANGELOG must both be modified in the PR
- The version in CHANGELOG must match the VERSION file

## One-Time Setup (after first merge to main)

```bash
# Create the release branch from main
git checkout -b release
git push -u origin release

# Create initial tag
git tag v0.1.0
git push origin v0.1.0

# Set up branch protection on `release` in GitHub Settings:
#   - Require status checks: "check-release-readiness"
#   - Require PR reviews (optional but recommended)
```
