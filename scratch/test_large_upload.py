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

def run_test(name, config=None, use_bytes=False, set_env=False):
    print(f"\n--- Testing: {name} ---")
    if set_env:
        os.environ['AWS_REQUEST_CHECKSUM_CALCULATION'] = 'WHEN_REQUIRED'
        os.environ['AWS_RESPONSE_CHECKSUM_VALIDATION'] = 'WHEN_REQUIRED'
    else:
        os.environ.pop('AWS_REQUEST_CHECKSUM_CALCULATION', None)
        os.environ.pop('AWS_RESPONSE_CHECKSUM_VALIDATION', None)
        
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=config
    )
    
    try:
        if use_bytes:
            print("Reading file into memory...")
            with open(local_path, "rb") as f:
                body_data = f.read()
            print(f"Uploading as bytes (len: {len(body_data)})...")
            s3.put_object(
                Bucket=bucket_name,
                Key=object_key,
                Body=body_data,
                ContentLength=len(body_data),
                ContentType="video/mp4"
            )
        else:
            print("Uploading as file object...")
            with open(local_path, "rb") as f:
                s3.put_object(
                    Bucket=bucket_name,
                    Key=object_key,
                    Body=f,
                    ContentLength=os.path.getsize(local_path),
                    ContentType="video/mp4"
                )
        print("-> SUCCESS!")
        return True
    except Exception as e:
        print(f"-> FAILED: {e}")
        return False

# Test cases
# 1. Default config, file object
run_test("Default config, file object")

# 2. Default config, bytes body
run_test("Default config, bytes body", use_bytes=True)

# 3. Payload signing disabled, file object
config_no_signing = Config(s3={'payload_signing_enabled': False})
run_test("Payload signing disabled, file object", config=config_no_signing)

# 4. Config with signature_version='s3v4' disabled or using path style and payload signing disabled
config_path_no_signing = Config(s3={'addressing_style': 'path', 'payload_signing_enabled': False})
run_test("Path style + Payload signing disabled, file object", config=config_path_no_signing)

# 5. Environment variables + file object
run_test("Environment variables + file object", set_env=True)
