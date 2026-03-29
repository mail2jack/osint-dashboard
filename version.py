"""
Version management for OSINT Dashboard

Version follows Semantic Versioning (SemVer): MAJOR.MINOR.PATCH
- MAJOR: Incompatible API changes
- MINOR: New functionality in backwards compatible manner
- PATCH: Bug fixes

To update version:
1. Open this file
2. Update VERSION tuple
3. Add entry to CHANGELOG

Git integration (optional):
- Run: python version.py --bump [major|minor|patch]
- This will auto-update VERSION and create a git tag
"""

from datetime import datetime

VERSION = (2, 1, 0)
VERSION_STRING = ".".join(map(str, VERSION))
VERSION_NAME = "Iveras OSINT"

CHANGELOG = {
    (1, 0, 0): "Initial Release - Core OSINT tools (Email, Username, Social Media, People Search)",
    (1, 1, 0): "Dark/Light mode, False positive filtering, Phone lookup, Combined email search, Platform health dashboard",
    (1, 2, 0): "AI Assistant with Ollama integration, Natural language search, AI result summaries",
    (2, 0, 0): "Major UI overhaul with toolbar, Combined tools, Confidence scoring, Real-time streaming",
    (2, 1, 0): "Webcams tool, Platform selectors, Time estimates, Cancel functionality, Improved detection, 24 dork queries",
}

def get_version():
    return VERSION_STRING

def get_version_info():
    return {
        "version": VERSION_STRING,
        "major": VERSION[0],
        "minor": VERSION[1],
        "patch": VERSION[2],
        "name": VERSION_NAME,
        "changelog": CHANGELOG.get(VERSION, ""),
        "release_date": datetime.now().strftime("%Y-%m-%d")
    }

def bump_version(level="patch"):
    """
    Bump version number based on SemVer rules:
    - patch: 1.0.0 -> 1.0.1 (bug fixes)
    - minor: 1.0.0 -> 1.1.0 (new features)
    - major: 1.0.0 -> 2.0.0 (breaking changes)
    """
    major, minor, patch = VERSION
    if level == "major":
        return (major + 1, 0, 0)
    elif level == "minor":
        return (major, minor + 1, 0)
    else:
        return (major, minor, patch + 1)

if __name__ == "__main__":
    import argparse
    import subprocess
    
    parser = argparse.ArgumentParser(description="Version management")
    parser.add_argument("--bump", choices=["major", "minor", "patch"], help="Bump version")
    args = parser.parse_args()
    
    if args.bump:
        new_ver = bump_version(args.bump)
        print(f"Bumping {VERSION_STRING} -> {'.'.join(map(str, new_ver))}")
        
        # Read current file
        with open(__file__, 'r') as f:
            content = f.read()
        
        # Replace VERSION tuple
        new_content = content.replace(
            f"VERSION = {VERSION}",
            f"VERSION = {new_ver}"
        )
        
        # Write back
        with open(__file__, 'w') as f:
            f.write(new_content)
        
        # Create git tag
        try:
            tag = f"v{'.'.join(map(str, new_ver))}"
            subprocess.run(["git", "add", __file__], check=True)
            subprocess.run(["git", "commit", "-m", f"Bump version to {tag}"], check=True)
            subprocess.run(["git", "tag", tag], check=True)
            print(f"Created git tag: {tag}")
        except Exception as e:
            print(f"Git tag failed: {e}")
