from pathlib import Path

main_path = Path("/home/laophan/clipforge-work/app/main.py")
if not main_path.exists():
    main_path = Path("C:/Users/gyou4/.gemini/antigravity/scratch/clipforge-work/app/main.py")

print(f"Patching auth log in {main_path}...")
content = main_path.read_text(encoding="utf-8")

target = '    if not saved_token or token != saved_token:'
replacement = '''    logger.info(f"PENDING AUTH: token_len={len(token) if token else 0}, saved_len={len(saved_token) if saved_token else 0}, saved_token_repr={repr(saved_token[:10])}...{repr(saved_token[-10:]) if saved_token else ''}, token_repr={repr(token[:10])}...{repr(token[-10:]) if token else ''}, match={token == saved_token}")
    if not saved_token or token != saved_token:'''

content = content.replace(target, replacement, 1) # Only replace the first occurrence (which is in get_pending_jobs)

main_path.write_text(content, encoding="utf-8")
print("Patch applied successfully!")
