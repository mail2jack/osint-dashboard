#!/usr/bin/env python3
"""
Version update script for OSINT Dashboard

Usage:
    python update_version.py                 # Show current version
    python update_version.py --bump patch     # Bump patch: 1.0.0 -> 1.0.1
    python update_version.py --bump minor     # Bump minor: 1.0.0 -> 1.1.0
    python update_version.py --bump major    # Bump major: 1.0.0 -> 2.0.0
    python update_version.py --set 1.2.3     # Set specific version
"""

import sys
import os
import re

def read_version_file():
    with open('version.py', 'r') as f:
        return f.read()

def write_version_file(content):
    with open('version.py', 'w') as f:
        f.write(content)

def get_current_version(content):
    match = re.search(r'VERSION = \((\d+), (\d+), (\d+)\)', content)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None

def set_version(content, version_tuple, name=""):
    version_str = f"({version_tuple[0]}, {version_tuple[1]}, {version_tuple[2]})"
    content = re.sub(r'VERSION = \(\d+, \d+, \d+\)', f'VERSION = {version_str}', content)
    
    if name:
        content = re.sub(r'VERSION_NAME = "[^"]*"', f'VERSION_NAME = "{name}"', content)
    
    return content

def main():
    content = read_version_file()
    current = get_current_version(content)
    
    if len(sys.argv) < 2:
        print(f"Current version: {'.'.join(map(str, current))}")
        print("Usage: python update_version.py [--bump patch|minor|major] [--set X.Y.Z]")
        return
    
    if sys.argv[1] == '--bump' and len(sys.argv) >= 3:
        level = sys.argv[2]
        major, minor, patch = current
        
        if level == 'major':
            new = (major + 1, 0, 0)
            name = "Major Release"
        elif level == 'minor':
            new = (major, minor + 1, 0)
            name = "Feature Update"
        else:
            new = (major, minor, patch + 1)
            name = "Patch Update"
        
        content = set_version(content, new, name)
        write_version_file(content)
        print(f"Version bumped: {'.'.join(map(str, current))} -> {'.'.join(map(str, new))}")
        print(f"Name: {name}")
    
    elif sys.argv[1] == '--set' and len(sys.argv) >= 3:
        try:
            parts = sys.argv[2].split('.')
            if len(parts) != 3:
                raise ValueError()
            new = tuple(int(p) for p in parts)
            content = set_version(content, new)
            write_version_file(content)
            print(f"Version set to: {'.'.join(map(str, new))}")
        except ValueError:
            print("Invalid version format. Use X.Y.Z (e.g., 1.2.3)")
            sys.exit(1)
    
    else:
        print("Unknown command")
        sys.exit(1)

if __name__ == '__main__':
    main()
