import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import json
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Mock startup dependencies before importing app.main
patch_load_model = patch('app.transcriber.load_model', MagicMock())
patch_ensure_fonts = patch('app.fonts.ensure_fonts', MagicMock())
patch_ensure_dirs = patch('app.paths.ensure_dirs', MagicMock())
patch_scheduler = patch('app.scheduler.SchedulerWorker', MagicMock())

patch_load_model.start()
patch_ensure_fonts.start()
patch_ensure_dirs.start()
patch_scheduler.start()

from fastapi.testclient import TestClient
from app.main import app
from app import db, storage, jobs


class TestGHAIntegration(unittest.TestCase):

    def setUp(self):
        # Set up a temporary test database path to avoid clobbering production data
        self.test_db_path = project_root / 'test_shorts_factory.db'
        db.DB_PATH = self.test_db_path
        
        # Ensure a clean slate
        if self.test_db_path.exists():
            try:
                self.test_db_path.unlink()
            except Exception:
                pass
                
        db.init_db()
        self.client = TestClient(app)

    def tearDown(self):
        # Clean up the test database file
        if self.test_db_path.exists():
            try:
                self.test_db_path.unlink()
            except Exception:
                pass
        # Clear in-memory jobs registry
        with jobs._JOBS_LOCK:
            jobs._JOBS.clear()

    def test_get_job_config(self):
        """GET /api/jobs/{job_id}/config returns configuration if callback token is valid."""
        # Add mock source video to satisfy foreign key constraints
        db.add_source_video(
            video_id="video123",
            url="https://youtube.com/watch?v=123",
            channel_url="youtube",
            title="Test Source Video",
            status="downloaded"
        )
        
        job_id = "test_job_123"
        callback_token = "secure_token_abc"
        request_payload = {
            "video_url": "https://youtube.com/watch?v=123",
            "aspect_ratio": "9:16",
            "fit_mode": "crop",
            "caption_style": "default",
            "num_clips": 1,
            "language": "en"
        }
        edit_plan = {
            "cuts": [{"start_time": 0.0, "end_time": 10.0}]
        }
        
        db.add_job(
            job_id=job_id,
            source_video_id="video123",
            status="PENDING",
            request_json=json.dumps(request_payload),
            callback_token=callback_token,
            edit_plan_json=json.dumps(edit_plan)
        )
        
        # Configure test S3 settings in the temporary database
        db.set_setting("storage_provider", "s3")
        db.set_setting("s3_endpoint_url", "https://s3.example.com")
        db.set_setting("s3_access_key", "access_key_val")
        db.set_setting("s3_secret_key", "secret_key_val")
        db.set_setting("s3_bucket_name", "test-bucket")
        db.set_setting("s3_region", "us-west-1")
        db.set_setting("s3_public_url_prefix", "https://cdn.example.com")
        
        with patch.dict(os.environ, {"GROQ_API_KEY": "test_groq_key"}):
            # Test 1: Authenticated with callback token in authorization header
            response = self.client.get(
                f"/api/jobs/{job_id}/config",
                headers={"Authorization": f"Bearer {callback_token}"}
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            
            # Verify response contents
            self.assertEqual(data["s3_settings"]["storage_provider"], "s3")
            self.assertEqual(data["s3_settings"]["s3_endpoint_url"], "https://s3.example.com")
            self.assertEqual(data["s3_settings"]["s3_bucket_name"], "test-bucket")
            self.assertEqual(data["s3_settings"]["s3_public_url_prefix"], "https://cdn.example.com")
            self.assertEqual(data["groq_api_key"], "test_groq_key")
            self.assertEqual(data["request_payload"]["video_url"], "https://youtube.com/watch?v=123")
            self.assertEqual(data["edit_plan"]["cuts"][0]["start_time"], 0.0)
            
            # Test 2: Authenticated with callback token as a query parameter
            response_query = self.client.get(f"/api/jobs/{job_id}/config?token={callback_token}")
            self.assertEqual(response_query.status_code, 200)
            
            # Test 3: Unauthorized request (missing token)
            response_missing = self.client.get(f"/api/jobs/{job_id}/config")
            self.assertEqual(response_missing.status_code, 401)
            
            # Test 4: Forbidden request (invalid/mismatched token)
            response_invalid = self.client.get(
                f"/api/jobs/{job_id}/config",
                headers={"Authorization": "Bearer invalid_token_xyz"}
            )
            self.assertEqual(response_invalid.status_code, 403)

    def test_job_callback(self):
        """PATCH /api/jobs/{job_id}/callback updates DB states, SSE tracker, and checks tokens."""
        # Add mock source video and job
        db.add_source_video(
            video_id="video123",
            url="https://youtube.com/watch?v=123",
            channel_url="youtube",
            title="Test Source Video",
            status="downloaded"
        )
        job_id = "test_job_456"
        callback_token = "callback_token_123"
        
        db.add_job(
            job_id=job_id,
            source_video_id="video123",
            status="PENDING",
            callback_token=callback_token
        )
        
        # Inject the job into the in-memory progress registry (SSE tracker)
        from app.models import GenerateRequest
        req = GenerateRequest(video_url="https://youtube.com/watch?v=123")
        in_memory_job = jobs.Job(req)
        in_memory_job.id = job_id
        with jobs._JOBS_LOCK:
            jobs._JOBS[job_id] = in_memory_job
            
        callback_payload = {
            "status": "ready",
            "progress": 90,
            "message": "Processing finished successfully",
            "s3_url": "https://s3.example.com/test-bucket/rendered.mp4",
            "seo_metadata": {
                "title": "Optimized Short Title",
                "description": "Short Description",
                "tags": ["tag1", "tag2"]
            },
            "clips": [
                {
                    "index": 0,
                    "title": "Clip 1",
                    "start": 1.0,
                    "end": 11.0,
                    "url": "https://s3.example.com/test-bucket/clip1.mp4"
                }
            ]
        }
        
        # Test 1: Reject callback with missing token
        res_missing = self.client.patch(
            f"/api/jobs/{job_id}/callback",
            json=callback_payload
        )
        self.assertEqual(res_missing.status_code, 401)
        
        # Test 2: Reject callback with bad token
        res_bad = self.client.patch(
            f"/api/jobs/{job_id}/callback",
            headers={"Authorization": "Bearer bad_token"},
            json=callback_payload
        )
        self.assertEqual(res_bad.status_code, 403)
        
        # Test 3: Accept callback with correct token
        res_success = self.client.patch(
            f"/api/jobs/{job_id}/callback",
            headers={"Authorization": f"Bearer {callback_token}"},
            json=callback_payload
        )
        self.assertEqual(res_success.status_code, 200)
        self.assertEqual(res_success.json(), {"status": "success"})
        
        # Verify job is updated in the SQLite database
        job_db = db.get_job(job_id)
        self.assertIsNotNone(job_db)
        self.assertEqual(job_db["status"], "ready")
        self.assertEqual(job_db["progress"], 90)
        self.assertEqual(job_db["s3_url"], "https://s3.example.com/test-bucket/rendered.mp4")
        self.assertEqual(job_db["seo_title"], "Optimized Short Title")
        self.assertEqual(job_db["seo_description"], "Short Description")
        self.assertEqual(job_db["seo_tags"], "tag1,tag2")
        
        # Verify in-memory progress registry synchronization (SSE tracker)
        self.assertEqual(in_memory_job.status, "done")  # "ready" status maps to "done"
        self.assertEqual(in_memory_job.progress, 0.9)
        self.assertEqual(len(in_memory_job.clips), 1)
        self.assertEqual(in_memory_job.clips[0]["title"], "Clip 1")
        self.assertEqual(in_memory_job.clips[0]["start"], 1.0)
        self.assertEqual(in_memory_job.clips[0]["end"], 11.0)
        self.assertEqual(in_memory_job.clips[0]["url"], "https://s3.example.com/test-bucket/clip1.mp4")

    def test_storage_resolver(self):
        """get_storage_provider() returns correct provider and falls back to local."""
        # Case A: Configure valid S3 settings
        db.set_setting("storage_provider", "s3")
        db.set_setting("s3_bucket_name", "test-bucket-name")
        db.set_setting("s3_endpoint_url", "https://s3.amazonaws.com")
        db.set_setting("s3_access_key", "access")
        db.set_setting("s3_secret_key", "secret")
        
        provider = storage.get_storage_provider()
        self.assertIsInstance(provider, storage.S3StorageProvider)
        self.assertEqual(provider.bucket_name, "test-bucket-name")
        
        # Case B: Raise ValueError when s3 bucket name is missing
        db.set_setting("s3_bucket_name", "")
        with self.assertRaises(ValueError):
            storage.get_storage_provider()
            
        # Case C: Fallback to LocalStorageProvider on local config or missing configuration
        db.set_setting("storage_provider", "local")
        provider_local = storage.get_storage_provider()
        self.assertIsInstance(provider_local, storage.LocalStorageProvider)
        
        db.set_setting("storage_provider", None)
        provider_none = storage.get_storage_provider()
        self.assertIsInstance(provider_none, storage.LocalStorageProvider)

    @patch('app.github_dispatch.dispatch_workflow')
    def test_remote_generation_dispatch(self, mock_dispatch):
        """POST /api/generate triggers GHA workflow dispatch and creates pending job in DB."""
        mock_dispatch.return_value = True
        
        # Configure integration settings
        db.set_setting("run_mode", "remote")
        db.set_setting("github_token", "github_token_val")
        db.set_setting("github_repo", "test-owner/test-repo")
        db.set_setting("github_ref", "main")
        db.set_setting("github_workflow", "render.yml")
        
        payload = {
            "video_url": "https://youtube.com/watch?v=123",
            "aspect_ratio": "9:16",
            "fit_mode": "crop",
            "caption_style": "default",
            "num_clips": 1,
            "language": "en"
        }
        
        response = self.client.post("/api/generate", json=payload)
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        job_id = data.get("job_id")
        self.assertIsNotNone(job_id)
        
        # Check workflow dispatch mock execution
        mock_dispatch.assert_called_once()
        args = mock_dispatch.call_args[0]
        # (job_id, callback_token, server_url)
        self.assertEqual(args[0], job_id)
        self.assertEqual(len(args[1]), 32)  # Secure callback_token length
        
        # Check database records
        job_db = db.get_job(job_id)
        self.assertIsNotNone(job_db)
        self.assertIn(job_db["status"].upper(), ["PENDING", "QUEUED"])
