import requests
import random
from pathlib import Path

def download_via_invidious(video_id: str, output_path: Path) -> bool:
    print("Fetching Invidious public instances...")
    try:
        resp = requests.get("https://api.invidious.io/instances.json", timeout=10)
        resp.raise_for_status()
        instances_data = resp.json()
    except Exception as e:
        print(f"Failed to fetch Invidious instances: {e}")
        return False

    # Extract valid domains with 100% uptime and https type
    candidates = []
    for item in instances_data:
        domain = item[0]
        meta = item[1]
        
        # Check if instance is healthy
        monitor = meta.get("monitor")
        if monitor and not monitor.get("down") and monitor.get("last_status") == 200:
            # We want HTTPS instances
            if meta.get("type") == "https":
                candidates.append(domain)
    
    print(f"Found {len(candidates)} healthy Invidious instances.")
    
    # Shuffle to distribute load
    random.shuffle(candidates)
    
    # Try the top 5 candidates
    for domain in candidates[:5]:
        print(f"Attempting download via instance: {domain}...")
        
        # We try 720p (itag 22) first, then 360p (itag 18)
        for itag in [22, 18]:
            download_url = f"https://{domain}/latest_version?id={video_id}&itag={itag}&local=true"
            print(f"Requesting URL (itag {itag}): {download_url}")
            
            try:
                # Use stream=True to avoid loading large files in memory
                with requests.get(download_url, stream=True, timeout=15) as r:
                    if r.status_code == 200:
                        content_type = r.headers.get("Content-Type", "")
                        print(f"Connection successful (Status 200, Content-Type: {content_type})")
                        
                        # Save file
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(output_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                                    
                        print(f"Successfully downloaded video to: {output_path} (Size: {output_path.stat().st_size} bytes)")
                        return True
                    else:
                        print(f"Instance returned status code: {r.status_code}")
            except Exception as e:
                print(f"Failed download attempt on {domain} (itag {itag}): {e}")
                
    print("All Invidious instance download attempts failed.")
    return False

if __name__ == "__main__":
    video_id = "pvyjCvkNEQQ"
    out_file = Path("downloads/test_invidious.mp4")
    success = download_via_invidious(video_id, out_file)
    if success:
        print("TEST SUCCESSFUL!")
    else:
        print("TEST FAILED.")
