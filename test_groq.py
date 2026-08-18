import os
from groq import Groq

if not os.getenv("GROQ_API_KEY"):
    raise SystemExit("GROQ_API_KEY is not set")

client = Groq(api_key=os.environ["GROQ_API_KEY"])

with open("test.mp3", "rb") as f:
    result = client.audio.transcriptions.create(
        file=("test.mp3", f.read()),
        model="whisper-large-v3-turbo",
        response_format="verbose_json",
        timestamp_granularities=["segment", "word"],
    )

print("\n===== TRANSCRIPT =====\n")
print(result.text)

print("\n===== SUCCESS =====")
