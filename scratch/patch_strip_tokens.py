from pathlib import Path

main_path = Path("/home/laophan/clipforge-work/app/main.py")
if not main_path.exists():
    main_path = Path("C:/Users/gyou4/.gemini/antigravity/scratch/clipforge-work/app/main.py")

print(f"Patching strip tokens in {main_path}...")
content = main_path.read_text(encoding="utf-8")

# 1. Patch get_pending_jobs
target1 = '''@app.get("/api/jobs/pending")
def get_pending_jobs(token: Optional[str] = None):
    import os
    saved_token = db.get_setting("github_token") or os.environ.get("CLIPFORGE_GITHUB_TOKEN") or os.environ.get("github_token")'''

replacement1 = '''@app.get("/api/jobs/pending")
def get_pending_jobs(token: Optional[str] = None):
    import os
    saved_token = db.get_setting("github_token") or os.environ.get("CLIPFORGE_GITHUB_TOKEN") or os.environ.get("github_token")
    if saved_token:
        saved_token = saved_token.strip()
    if token:
        token = token.strip()'''

# 2. Patch score_candidates
target2 = '''@app.post("/api/jobs/score-candidates")
def score_candidates(req: ScoreCandidatesRequest):
    import os
    saved_token = db.get_setting("github_token") or os.environ.get("CLIPFORGE_GITHUB_TOKEN") or os.environ.get("github_token")'''

replacement2 = '''@app.post("/api/jobs/score-candidates")
def score_candidates(req: ScoreCandidatesRequest):
    import os
    saved_token = db.get_setting("github_token") or os.environ.get("CLIPFORGE_GITHUB_TOKEN") or os.environ.get("github_token")
    if saved_token:
        saved_token = saved_token.strip()
    req_token = req.token.strip() if req.token else None
    req.token = req_token'''

content = content.replace(target1, replacement1)
content = content.replace(target2, replacement2)

main_path.write_text(content, encoding="utf-8")
print("Patch applied successfully!")
