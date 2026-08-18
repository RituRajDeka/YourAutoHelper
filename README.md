# ClipForge

Kisi bhi video ko **captioned vertical shorts** bana do — **100% aapke PC par**,
koi API key nahi. NVIDIA card hua to khud tez (GPU) chalega, warna CPU par.

## Chalane ka tareeqa (Windows)

1. **[ClipForge.bat download karein](https://raw.githubusercontent.com/Ai-Haris/clipforge/main/packaging/ClipForge.bat)**
   *(link khulne par: Ctrl+S se save karein — ek khaali folder mein rakhein.)*
2. **`ClipForge.bat` par double-click** karein.
3. Bas — pehli baar sab kuch (Python, libraries, ffmpeg, model) khud download hota
   hai (~5–10 min, internet chahiye). Phir browser khud khul jata hai. Agli dafa turant.

> Kuch alag install karne ki zarurat **nahi** — Python bhi nahi.

## Zaruri baatein

- **Windows 10/11 (64-bit).**
- Pehli baar **internet** chahiye (sab kuch download hota hai). Baad mein apni file
  upload karke offline bhi chala sakte hain.
- **GPU:** NVIDIA card hua to app "Compute → Auto" par khud GPU use karti hai (bahut
  tez). Card na ho to CPU (thodi slow) — kaam phir bhi hota hai.

## Masla aaye to

- **SmartScreen / Antivirus** roke → *More info → Run anyway* (bat unsigned hai).
- **`VCRUNTIME140.dll` missing** → Microsoft Visual C++ Redistributable (x64) install
  karein, phir dobara `ClipForge.bat`.
- **Port 8000 busy** → koi aur ClipForge pehle se chal rahi hogi; usko band karein.
- Pehli run par **"Connecting…"** kaafi der dikhe → model (~1.5 GB) download ho raha
  hai, sabar karein. Ek dafa ka kaam hai.

## Yeh kya hai

Local pipeline: **yt-dlp** (video laata hai) → **faster-whisper** (word-level
captions, local) → local heuristic se best moments → **ffmpeg** se cut + reframe +
captions burn. Koi cloud AI / API nahi.

Banaya: **The Haris** — [Instagram](https://www.instagram.com/theharis.ai/) · [YouTube](https://www.youtube.com/@harisailab)
