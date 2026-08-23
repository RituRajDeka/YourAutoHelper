from pathlib import Path

main_path = Path("/home/laophan/clipforge-work/app/main.py")
if not main_path.exists():
    main_path = Path("C:/Users/gyou4/.gemini/antigravity/scratch/clipforge-work/app/main.py")

print(f"Patching env keys log in {main_path}...")
content = main_path.read_text(encoding="utf-8")

target = '    logger.info(f"PENDING AUTH: token_len='
replacement = '    import os\n    logger.info(f"ENV KEYS: {list(os.environ.keys())}")\n    logger.info(f"PENDING AUTH: token_len='

content = content.replace(target, replacement)

main_path.write_text(content, encoding="utf-8")
print("Patch applied successfully!")
