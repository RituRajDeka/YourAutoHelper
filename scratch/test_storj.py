import os
import boto3
from botocore.client import Config

# Storj Credentials
endpoint_url = "https://gateway.storjshare.io"
access_key = "jvcblobm7ohkcdw5kkkzxcnpa3ka"
secret_key = "j2kzk36xdm6ppunptxzv2xlmfmuym6i4cxls7azowraggant7vcve"
bucket_name = "clips"

print("--- Testing Storj S3 Connection ---")

configs_to_test = [
    ("SigV4 with payload_signing disabled", Config(signature_version='s3v4', s3={'payload_signing_enabled': False})),
    ("SigV4 only", Config(signature_version='s3v4')),
    ("Default Config", Config()),
]

dummy_path = "test_dummy.txt"
with open(dummy_path, "w") as f:
    f.write("Hello Storj! " * 1000)
file_size = os.path.getsize(dummy_path)
print(f"Created dummy file: {dummy_path} ({file_size} bytes)")

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
        
        try:
            print("Listing buckets...")
            resp = s3.list_buckets()
            print("Buckets found:", [b['Name'] for b in resp['Buckets']])
            
            print(f"Uploading file to bucket '{bucket_name}'...")
            with open(dummy_path, 'rb') as f:
                s3.put_object(
                    Bucket=bucket_name,
                    Key="test_upload.txt",
                    Body=f,
                    ContentLength=file_size
                )
            print("Upload SUCCESSFUL!")
            
            print("Verifying uploaded object...")
            s3.head_object(Bucket=bucket_name, Key="test_upload.txt")
            print("Verification SUCCESSFUL!")
            
        except Exception as e:
            print(f"FAILED with error: {e}")
            
finally:
    if os.path.exists(dummy_path):
        os.remove(dummy_path)
