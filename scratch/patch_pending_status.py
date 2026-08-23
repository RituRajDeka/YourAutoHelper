from pathlib import Path

main_path = Path("/home/laophan/clipforge-work/app/main.py")
if not main_path.exists():
    main_path = Path("C:/Users/gyou4/.gemini/antigravity/scratch/clipforge-work/app/main.py")

print(f"Patching pending status in {main_path}...")
content = main_path.read_text(encoding="utf-8")

target = "if j.get('status') == \"PENDING\""
replacement = "if j.get('status') in (\"PENDING\", \"DOWNLOAD_QUEUED\")"

if target in content:
    content = content.replace(target, replacement)
    main_path.write_text(content, encoding="utf-8")
    print("Patch applied successfully!")
else:
    print("Target not found. Patch already applied or file format mismatch.")
