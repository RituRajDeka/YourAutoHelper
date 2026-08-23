import os
import boto3
from botocore.client import Config

# Storj Credentials
endpoint_url = "https://gateway.storjshare.io"
access_key = "jvcblobm7ohkcdw5kkkzxcnpa3ka"
secret_key = "j2kzk36xdm6ppunptxzv2xlmfmuym6i4cxls7azowraggant7vcve"
bucket_name = "clips"

print("--- Testing Storj S3 Configurations ---")

configs_to_test = [
    ("Default Config", Config()),
    ("Path Style addressing", Config(s3={'addressing_style': 'path'})),
    ("Path Style with payload signing disabled", Config(s3={'addressing_style': 'path', 'payload_signing_enabled': False})),
    ("Path Style + SigV4", Config(signature_version='s3v4', s3={'addressing_style': 'path'})),
]

dummy_path = "test_dummy.txt"
with open(dummy_path, "w") as f:
    f.write("Hello Storj! " * 10)
file_size = os.path.getsize(dummy_path)

try:
    for name, config in configs_to_test:
        print(f"\nTesting: {name}")
        s3 = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=config
        )
        
        # Test 1: Uploading as open file object
        try:
            print("  1. Uploading as file object...")
            with open(dummy_path, 'rb') as f:
                s3.put_object(
                    Bucket=bucket_name,
                    Key="test_file_obj.txt",
                    Body=f,
                    ContentLength=file_size
                )
            print("  -> File object upload SUCCESSFUL!")
        except Exception as e:
            print(f"  -> File object upload FAILED: {e}")
            
        # Test 2: Uploading as bytes object
        try:
            print("  2. Uploading as bytes...")
            s3.put_object(
                Bucket=bucket_name,
                Key="test_bytes.txt",
                Body=b"Hello Storj raw bytes upload!",
                ContentLength=29
            )
            print("  -> Bytes upload SUCCESSFUL!")
        except Exception as e:
            print(f"  -> Bytes upload FAILED: {e}")
            
finally:
    if os.path.exists(dummy_path):
        os.remove(dummy_path)
