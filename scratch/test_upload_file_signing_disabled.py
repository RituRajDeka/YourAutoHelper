import os
import boto3
from botocore.client import Config

local_path = "/mnt/c/Users/gyou4/Downloads/h.mp4"
bucket_name = "clips"
object_key = "h.mp4"

endpoint_url = "https://gateway.storjshare.io"
access_key = "jvcblobm7ohkcdw5kkkzxcnpa3ka"
secret_key = "j2kzk36xdm6ppunptxzv2xlmfmuym6i4cxls7azowraggant7vcve"

print(f"File size: {os.path.getsize(local_path)} bytes")

# Configure client with payload_signing_enabled=False
config = Config(s3={'payload_signing_enabled': False})

s3 = boto3.client(
    "s3",
    endpoint_url=endpoint_url,
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
    config=config
)

print(f"Uploading via s3.upload_file to sources/{object_key}...")
try:
    s3.upload_file(local_path, bucket_name, f"sources/{object_key}")
    print("-> UPLOAD FILE SUCCESSFUL!")
except Exception as e:
    print(f"-> UPLOAD FILE FAILED: {e}")
