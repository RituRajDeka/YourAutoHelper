from pathlib import Path

main_path = Path("/home/laophan/clipforge-work/app/main.py")
if not main_path.exists():
    main_path = Path("C:/Users/gyou4/.gemini/antigravity/scratch/clipforge-work/app/main.py")

print(f"Patching token env in {main_path}...")
content = main_path.read_text(encoding="utf-8")

# 1. Replace GHA dispatch config
target1 = "        github_token = db.get_setting('github_token')"
replacement1 = "        import os\n        github_token = db.get_setting('github_token') or os.environ.get('CLIPFORGE_GITHUB_TOKEN') or os.environ.get('github_token')"

# 2. Replace pending jobs auth
target2 = '    saved_token = db.get_setting("github_token")'
replacement2 = '    import os\n    saved_token = db.get_setting("github_token") or os.environ.get("CLIPFORGE_GITHUB_TOKEN") or os.environ.get("github_token")'

content = content.replace(target1, replacement1)
content = content.replace(target2, replacement2) # This will replace both pending jobs auth and score-candidates auth since they have the exact same string!

main_path.write_text(content, encoding="utf-8")
print("Patch applied successfully!")
