# Publish As A New Git Repository

From the repo root:

```bash
cd ~/subaru-pi-platform
git init
git add .
git commit -m "Initial Subaru Pi Platform stack"
```

Create a remote repository (GitHub/GitLab), then:

```bash
git branch -M main
git remote add origin <your-remote-url>
git push -u origin main
```

## Before sharing publicly
- Keep `observability/.env` out of git (already ignored)
- Verify no secrets in commit history
- Update `README.md` clone URL placeholders
