import os
import yt_dlp

url = "https://www.youtube.com/watch?v=pvyjCvkNEQQ"
cookie_file = os.environ.get("CLIPFORGE_COOKIES_FILE")

clients = [
    "web", "web_safari", "mweb", "android", "ios", "tv", 
    "web_embedded", "android_embedded", "ios_embedded", "tv_embedded"
]

print("--- YTDLP CLIENT COMPATIBILITY TEST ---")
print(f"Cookies file env: {cookie_file}")
if cookie_file and os.path.exists(cookie_file):
    print("Cookies file exists and is readable.")
else:
    print("Cookies file does not exist or env not set.")

for client in clients:
    for use_cookies in [True, False]:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "extractor_args": {"youtube": {"player_client": [client]}}
        }
        if use_cookies:
            if cookie_file:
                opts["cookiefile"] = cookie_file
            else:
                continue # Skip cookie test if cookie file isn't set
            
        label = f"{client} (with cookies)" if use_cookies else f"{client} (no cookies)"
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                formats = info.get("formats", [])
                # Check for formats with valid URLs
                valid_formats = [f for f in formats if f.get("url")]
                print(f"SUCCESS: {label} - Found {len(formats)} formats (valid URLs: {len(valid_formats)})")
        except Exception as e:
            reason = str(e).split("\n")[0]
            # Clean up the output string to keep logs clean
            reason = reason.replace("ERROR: ", "").strip()
            print(f"FAILED: {label} - {reason}")
print("---------------------------------------")
