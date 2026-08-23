import sys
from pathlib import Path

# Ensure parent directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db

def main():
    print("Setting S3 configurations in local database...")
    db.set_setting('storage_provider', 's3')
    db.set_setting('s3_endpoint_url', 'https://gateway.storjshare.io')
    db.set_setting('s3_access_key', 'jvcblobm7ohkcdw5kkkzxcnpa3ka')
    db.set_setting('s3_secret_key', 'j2kzk36xdm6ppunptxzv2xlmfmuym6i4cxls7azowraggant7vcve')
    db.set_setting('s3_bucket_name', 'clips')
    db.set_setting('s3_region', 'us1')
    db.set_setting('s3_public_url_prefix', 'https://yourautohelper-production.up.railway.app')
    print("S3 settings set successfully!")

if __name__ == '__main__':
    main()
