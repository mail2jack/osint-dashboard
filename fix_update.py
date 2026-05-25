"""
OSINT Dashboard — Update Notification Diagnostic & Fix
Run: sudo -u osint /opt/osint-dashboard/venv/bin/python fix_update.py
"""
import os
import sys
import subprocess
import json

os.environ['NO_CLOUD'] = '1'

sys.path.insert(0, '/opt/osint-dashboard')
from app import app
from cms.models import db, Setting

OK = "✅"
FAIL = "❌"
SKIP = "➖"

def run(cmd_list, cwd=None, timeout=15):
    try:
        r = subprocess.run(cmd_list, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def check(msg, ok, detail=""):
    icon = OK if ok else FAIL
    print(f"  {icon} {msg}")
    if detail:
        for line in detail.strip().split("\n"):
            print(f"     {line}")

with app.app_context():
    print("=" * 60)
    print("  OSINT Dashboard — Update Notification Diagnostic")
    print("=" * 60)
    print()

    # 1. Check settings
    print("[1] Database settings")
    repo = Setting.get('update_check_repo')
    last_sha = Setting.get('last_update_commit')

    check("update_check_repo",
          repo is not None and repo != "",
          f"Current value: {repo or '(not set)'}")

    check("last_update_commit",
          last_sha is not None,
          f"Current value: {last_sha[:16] + '...' if last_sha else '(not set)'}")

    # Test setting a value works
    test_ok = Setting.set("_diag_test", "ok", "Diagnostic test", "general")
    if test_ok:
        Setting.query.filter_by(key="_diag_test").delete()
        db.session.commit()
    check("DB write test", test_ok)

    print()

    # 2. Check git access
    print("[2] Git repository access")
    project_root = app.root_path
    check("Project root exists", os.path.isdir(project_root), project_root)
    check(".git directory exists", os.path.isdir(os.path.join(project_root, ".git")))

    rc, sha, err = run(["git", "rev-parse", "HEAD"], cwd=project_root)
    check(f"git rev-parse HEAD (exit={rc})", rc == 0,
          f"SHA: {sha[:16] if sha else 'FAILED'}\n{err[:200] if err else ''}")

    rc2, origin, err2 = run(["git", "rev-parse", "origin/master"], cwd=project_root)
    check(f"git rev-parse origin/master (exit={rc2})", rc2 == 0,
          f"SHA: {origin[:16] if origin else 'FAILED'}\n{err2[:200] if err2 else ''}")

    # Check if we're already at the latest
    if rc == 0 and rc2 == 0:
        check("HEAD == origin/master (already up to date)", sha == origin)
        print()
        if sha == origin:
            print("  ⟹ HEAD matches origin/master. There are NO unpulled commits.")
            print("  ⟹ The banner will NOT show until a NEW commit is pushed to GitHub.")
        else:
            print(f"  ⟹ HEAD ({sha[:12]}) ≠ origin/master ({origin[:12]})")
            print("  ⟹ There ARE unpulled commits. Banner should show.")
    print()

    # 3. Check GitHub API access
    print("[3] GitHub API access")
    import httpx
    if repo:
        for url_name, url in [
            ("VERSION file", f"https://raw.githubusercontent.com/{repo}/master/VERSION"),
            ("Commit SHA API", f"https://api.github.com/repos/{repo}/commits/master"),
        ]:
            try:
                r = httpx.get(url, timeout=10)
                status = r.status_code
                if url_name == "VERSION file":
                    detail = f"HTTP {status}: {r.text.strip()[:50]}"
                else:
                    detail = f"HTTP {status}: {r.text.strip()[:16] if status == 200 else r.text[:100]}"
                check(f"{url_name}", status in (200, 304), detail)
            except Exception as e:
                check(f"{url_name}", False, str(e)[:200])
    else:
        check("GitHub API (skipped — no repo configured)", False, SKIP)
    print()

    # 4. Simulate check_update logic
    print("[4] Simulated check_update() logic")

    from version import get_version
    current_ver = get_version()

    if repo:
        check(f"Local version: {current_ver}", True)
        try:
            r = httpx.get(f"https://raw.githubusercontent.com/{repo}/master/VERSION", timeout=10)
            latest_ver = r.text.strip() if r.status_code == 200 else current_ver
            check(f"Remote version: {latest_ver}", True)

            version_update = latest_ver != current_ver
            check(f"Version mismatch (would trigger banner)", version_update,
                  f"Local: {current_ver} vs Remote: {latest_ver}")

            # Get remote SHA
            try:
                api_r = httpx.get(f"https://api.github.com/repos/{repo}/commits/master", timeout=10,
                                  headers={'Accept': 'application/vnd.github.v3.sha'})
                remote_sha = api_r.text.strip() if api_r.status_code == 200 else ""
                local_sha = last_sha or sha or ""
                commits_update = bool(remote_sha and local_sha and remote_sha != local_sha and not version_update)
                check(f"Commit SHA mismatch (would trigger banner)", commits_update,
                      f"Local: {local_sha[:16] or '(empty)'}  Remote: {remote_sha[:16] or '(empty)'}")
            except Exception as e:
                check("Commit SHA check", False, str(e)[:200])
        except Exception as e:
            check("Remote version fetch", False, str(e)[:200])
    else:
        check("update_check_repo (must be set for banner to work)", False)
        print("\n  ⟹ This is likely THE problem. The banner cannot show without this setting.")
    print()

    # 5. Fix
    print("[5] Fixes")
    fixes = 0

    if not repo:
        print(f"  {FAIL} Setting 'update_check_repo' is not set.")
        Setting.set('update_check_repo', 'mail2jack/osint-dashboard',
                    'GitHub repo for update checks (owner/repo)', 'general')
        repo = Setting.get('update_check_repo')
        check("  Set update_check_repo → mail2jack/osint-dashboard", repo == 'mail2jack/osint-dashboard')
        fixes += 1

    # Clear last_update_commit so auto-detect re-runs and triggers a banner
    # if there are unpulled commits
    setting_obj = Setting.query.filter_by(key='last_update_commit').first()
    if setting_obj:
        db.session.delete(setting_obj)
        db.session.commit()
        check("  Cleared last_update_commit (will re-detect on next page load)", True)
        fixes += 1
    else:
        check("  last_update_commit already empty", True)

    print()
    print("=" * 60)
    if fixes > 0:
        print(f"  {fixes} issue(s) fixed. Restart gunicorn and reload the page.")
    else:
        print("  No issues found. Everything looks configured correctly.")

    # Final verdict
    print("=" * 60)
    print()

    can_show = bool(repo)
    git_works = (rc == 0 and rc2 == 0)
    has_diff = (rc == 0 and rc2 == 0 and sha != origin)

    if not can_show:
        print("  VERDICT: ❌ Banner cannot show — update_check_repo is not set.")
        print("           (Now fixed — see [5] above)")
    elif not git_works:
        print("  VERDICT: ❌ Banner may not show — git commands fail.")
        print("           Check file permissions on /opt/osint-dashboard/.git/")
    elif has_diff:
        print("  VERDICT: ✅ Banner SHOULD show — there are unpulled commits.")
        print(f"           HEAD: {sha[:12]}  origin/master: {origin[:12]}")
    else:
        print("  VERDICT: ✓ Repository is up to date with GitHub.")
        print("           Banner will show when a NEW commit is pushed.")
        print()
        print("  Want to test it now? Run this on your LOCAL machine:")
        print("    echo '3.4.3' > VERSION && git add VERSION && git commit -m 'test: bump version' && git push")
        print("  Then visit the CMS page (DON'T pull first). The banner should appear.")
    print()

