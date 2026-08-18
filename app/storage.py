from abc import ABC, abstractmethod
import os
import mimetypes
from typing import Optional
import boto3
from botocore.client import Config

class StorageProvider(ABC):
    @abstractmethod
    def upload_file(self, local_path: str, remote_name: str) -> str:
        """Upload a file to the storage provider and return its URL or path.
        
        Args:
            local_path: Path to the local file to upload.
            remote_name: Target path/name in the storage.
            
        Returns:
            The public URL or path to the file.
        """
        pass

class LocalStorageProvider(StorageProvider):
    def __init__(self, public_url_prefix: Optional[str] = None):
        self.public_url_prefix = public_url_prefix

    def upload_file(self, local_path: str, remote_name: str) -> str:
        """For local storage, returns the URL if prefix is configured, else the absolute local path."""
        if self.public_url_prefix:
            prefix = self.public_url_prefix.rstrip('/')
            suffix = remote_name.lstrip('/')
            return f"{prefix}/{suffix}"
        return os.path.abspath(local_path)

class S3StorageProvider(StorageProvider):
    def __init__(
        self,
        bucket_name: str,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        region_name: Optional[str] = None,
        public_url_prefix: Optional[str] = None
    ):
        self.bucket_name = bucket_name
        self.public_url_prefix = public_url_prefix
        
        client_kwargs = {}
        if access_key:
            client_kwargs['aws_access_key_id'] = access_key
        if secret_key:
            client_kwargs['aws_secret_access_key'] = secret_key
        if endpoint_url:
            client_kwargs['endpoint_url'] = endpoint_url
        if region_name:
            client_kwargs['region_name'] = region_name
            
        # Enable path style addressing (s3v4) for custom endpoints like MinIO
        if endpoint_url:
            client_kwargs['config'] = Config(signature_version='s3v4')
            
        self.s3_client = boto3.client('s3', **client_kwargs)

    def upload_file(self, local_path: str, remote_name: str) -> str:
        """Uploads a file to S3, trying public-read ACL first, and returns its public URL."""
        content_type, _ = mimetypes.guess_type(local_path)
        extra_args = {}
        if content_type:
            extra_args['ContentType'] = content_type

        # Try to upload with public-read permission
        try:
            extra_args['ACL'] = 'public-read'
            self.s3_client.upload_file(
                Filename=local_path,
                Bucket=self.bucket_name,
                Key=remote_name,
                ExtraArgs=extra_args
            )
        except Exception:
            # Fall back to uploading without ACL if restricted by bucket policies
            extra_args.pop('ACL', None)
            self.s3_client.upload_file(
                Filename=local_path,
                Bucket=self.bucket_name,
                Key=remote_name,
                ExtraArgs=extra_args
            )

        if self.public_url_prefix:
            prefix = self.public_url_prefix.rstrip('/')
            suffix = remote_name.lstrip('/')
            return f"{prefix}/{suffix}"

        # Default S3 endpoint URL fallback
        endpoint = self.s3_client.meta.endpoint_url.rstrip('/')
        return f"{endpoint}/{self.bucket_name}/{remote_name.lstrip('/')}"

def get_storage_provider() -> StorageProvider:
    """Factory to retrieve the storage provider configured in database settings."""
    from .db import get_setting

    provider_type = get_setting('storage_provider', 'local')
    public_url_prefix = get_setting('s3_public_url_prefix')

    if provider_type == 's3':
        bucket_name = get_setting('s3_bucket_name')
        if not bucket_name:
            raise ValueError("S3 storage provider is configured, but s3_bucket_name setting is empty.")
        
        access_key = get_setting('s3_access_key')
        secret_key = get_setting('s3_secret_key')
        endpoint_url = get_setting('s3_endpoint_url')
        region_name = get_setting('s3_region')

        return S3StorageProvider(
            bucket_name=bucket_name,
            access_key=access_key,
            secret_key=secret_key,
            endpoint_url=endpoint_url,
            region_name=region_name,
            public_url_prefix=public_url_prefix
        )
    
    return LocalStorageProvider(public_url_prefix=public_url_prefix)
