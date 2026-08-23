import os
import boto3

def upload_file():
    local_path = "/mnt/c/Users/gyou4/Downloads/h.mp4"
    bucket_name = "clips"
    object_key = "h.mp4"
    
    endpoint_url = "https://gateway.storjshare.io"
    access_key = "jvcblobm7ohkcdw5kkkzxcnpa3ka"
    secret_key = "j2kzk36xdm6ppunptxzv2xlmfmuym6i4cxls7azowraggant7vcve"
    
    print(f"Checking if local file exists: {local_path}")
    if not os.path.exists(local_path):
        print(f"Error: Local file {local_path} does not exist!")
        return
        
    file_size = os.path.getsize(local_path)
    print(f"File size: {file_size} bytes")
    
    print("Initializing S3 client...")
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key
    )
    
    print(f"Uploading {local_path} to s3://{bucket_name}/{object_key} via put_object...")
    try:
        with open(local_path, "rb") as f:
            s3.put_object(
                Bucket=bucket_name,
                Key=object_key,
                Body=f,
                ContentLength=file_size,
                ContentType="video/mp4"
            )
        print("Upload completed successfully!")
        
        # Also upload to sources/h.mp4 just in case they need it there
        print(f"Uploading to sources/{object_key}...")
        with open(local_path, "rb") as f:
            s3.put_object(
                Bucket=bucket_name,
                Key=f"sources/{object_key}",
                Body=f,
                ContentLength=file_size,
                ContentType="video/mp4"
            )
        print("Also uploaded successfully to sources/h.mp4!")
    except Exception as e:
        print(f"Upload failed: {e}")

if __name__ == "__main__":
    upload_file()
