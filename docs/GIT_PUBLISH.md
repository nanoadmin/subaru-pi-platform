# Publish Changes To GitHub

This repo already has a configured `origin`.

## Standard publish flow
```bash
cd ~/subaru-pi-platform
git status
git add -A
git commit -m "Describe your changes"
git push origin main
```

## If branch does not track remote yet
```bash
git push -u origin main
```

## Before pushing
- Keep secrets out of git (`observability/.env` is ignored)
- Review diffs for local-only artifacts
- Ensure dashboards/services still start after changes
