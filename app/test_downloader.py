import os
import subprocess

url = "https://www.youtube.com/watch?v=pvyjCvkNEQQ"
cookie_file = "downloads/cookies.txt"

print("--- YTDLP FORMAT DIAGNOSIS ---")
if os.path.exists(cookie_file):
    print("Cookie file exists for diagnosis.")
else:
    print("Cookie file does not exist.")

cmd = ["yt-dlp", "-F", url]
if os.path.exists(cookie_file):
    cmd.extend(["--cookies", cookie_file])

try:
    print(f"Running command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    print("--- STDOUT ---")
    print(result.stdout)
    print("--- STDERR ---")
    print(result.stderr)
except Exception as e:
    print(f"Failed to run diagnosis command: {e}")
print("------------------------------")
