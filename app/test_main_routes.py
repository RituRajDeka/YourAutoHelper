import unittest
from unittest.mock import patch, MagicMock
import sys
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

class TestMainRoutes(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    @patch('app.youtube.get_auth_url')
    def test_get_youtube_auth(self, mock_get_auth_url):
        """GET /api/youtube/auth?redirect_uri=http://test -> returns JSON with auth_url."""
        mock_get_auth_url.return_value = "https://accounts.google.com/mock_auth"
        response = self.client.get("/api/youtube/auth?redirect_uri=http://test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"auth_url": "https://accounts.google.com/mock_auth"})
        mock_get_auth_url.assert_called_once_with("http://test")

    @patch('app.youtube.save_oauth_callback')
    def test_get_youtube_callback(self, mock_save_callback):
        """GET /api/youtube/callback?code=123&redirect_uri=http://test -> returns 200 OK."""
        response = self.client.get("/api/youtube/callback?code=123&redirect_uri=http://test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success"})
        mock_save_callback.assert_called_once_with("123", "http://test")

    @patch('app.db.get_source_channels')
    def test_get_channels(self, mock_get_channels):
        """GET /api/channels -> returns a list of channels."""
        mock_channels = [{"id": 1, "name": "Channel 1", "url": "http://channel1"}]
        mock_get_channels.return_value = mock_channels
        response = self.client.get("/api/channels")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_channels)

    @patch('app.db.add_source_channel')
    def test_post_channels(self, mock_add_channel):
        """POST /api/channels -> accepts JSON and returns success."""
        mock_channel = {"id": 2, "name": "Test", "url": "http://youtube.com/c/test"}
        mock_add_channel.return_value = mock_channel
        response = self.client.post(
            "/api/channels",
            json={"url": "http://youtube.com/c/test", "name": "Test"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success", "channel": mock_channel})

    @patch('app.db.remove_source_channel')
    def test_delete_channel(self, mock_remove_channel):
        """DELETE /api/channels/1 -> returns status success."""
        response = self.client.delete("/api/channels/1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success"})
        mock_remove_channel.assert_called_once_with("1")

    @patch('app.db.get_setting')
    def test_get_settings(self, mock_get_setting):
        """GET /api/settings -> returns app settings."""
        def side_effect(key):
            settings = {
                "shorts_per_day": "5",
                "buffer_days": "3",
                "youtube_client_id": "client123",
                "youtube_client_secret": "secret123",
                "transcribe_device": "cuda",
                "youtube_oauth_token": "token_val"
            }
            return settings.get(key)
        mock_get_setting.side_effect = side_effect
        
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["shorts_per_day"], 5)
        self.assertEqual(data["buffer_days"], 3)
        self.assertEqual(data["youtube_client_id"], "client123")
        self.assertEqual(data["youtube_client_secret"], "secret123")
        self.assertEqual(data["transcribe_device"], "cuda")
        self.assertEqual(data["youtube_linked"], True)

    @patch('app.db.set_setting')
    def test_post_settings(self, mock_set_setting):
        """POST /api/settings -> accepts parameters and returns status success."""
        response = self.client.post("/api/settings", json={"shorts_per_day": 5})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success"})
        mock_set_setting.assert_called_once_with("shorts_per_day", 5)

    @patch('app.main.query_recent_shorts')
    def test_get_shorts(self, mock_query_shorts):
        """GET /api/shorts -> returns recent shorts."""
        mock_shorts = [{"id": "short_1", "title": "My Short"}]
        mock_query_shorts.return_value = mock_shorts
        response = self.client.get("/api/shorts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_shorts)

    @patch('app.db.list_jobs')
    def test_get_jobs(self, mock_list_jobs):
        """GET /api/jobs -> returns jobs."""
        mock_jobs = [{"id": "job_1", "status": "running"}]
        mock_list_jobs.return_value = mock_jobs
        response = self.client.get("/api/jobs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_jobs)

    def test_post_shorts_upload(self):
        """POST /api/shorts/upload/short123 -> returns status success."""
        # Using a context patch inside to mock background task execution
        with patch('app.youtube.upload_scheduled_short') as mock_upload:
            response = self.client.post("/api/shorts/upload/short123")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "success", "message": "Manual upload triggered in background."})
            # FastAPI's TestClient processes background tasks synchronously at request end
            mock_upload.assert_called_once_with("short123")

if __name__ == "__main__":
    unittest.main()
