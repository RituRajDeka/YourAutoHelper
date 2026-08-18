import unittest
from unittest.mock import patch, MagicMock
import os
import json

# Ensure parent directory is in sys.path
import sys
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.youtube import get_oauth_client_config, upload_scheduled_short

class TestYouTubeModule(unittest.TestCase):

    @patch('app.youtube.get_setting')
    @patch.dict(os.environ, {}, clear=True)
    def test_get_oauth_client_config_raises_value_error(self, mock_get_setting):
        """1. get_oauth_client_config() raises ValueError when credentials are not in settings/env."""
        mock_get_setting.return_value = None
        with self.assertRaises(ValueError) as context:
            get_oauth_client_config()
        self.assertIn("YouTube OAuth Client ID or Client Secret is not configured", str(context.exception))

    @patch('app.youtube.get_setting')
    def test_get_oauth_client_config_returns_settings_from_db(self, mock_get_setting):
        """2a. get_oauth_client_config() returns correct client settings when settings are mock-configured in DB."""
        def side_effect(key):
            if key == 'youtube_client_id':
                return 'db_client_id'
            if key == 'youtube_client_secret':
                return 'db_client_secret'
            return None
        mock_get_setting.side_effect = side_effect
        
        config = get_oauth_client_config(redirect_uri="https://example.com/oauth")
        self.assertEqual(config["web"]["client_id"], "db_client_id")
        self.assertEqual(config["web"]["client_secret"], "db_client_secret")
        self.assertEqual(config["web"]["redirect_uris"], ["https://example.com/oauth"])

    @patch('app.youtube.get_setting')
    @patch.dict(os.environ, {'YOUTUBE_CLIENT_ID': 'env_client_id', 'YOUTUBE_CLIENT_SECRET': 'env_client_secret'}, clear=True)
    def test_get_oauth_client_config_returns_settings_from_env(self, mock_get_setting):
        """2b. get_oauth_client_config() returns correct client settings when settings are loaded from environment."""
        mock_get_setting.return_value = None
        
        config = get_oauth_client_config()
        self.assertEqual(config["web"]["client_id"], "env_client_id")
        self.assertEqual(config["web"]["client_secret"], "env_client_secret")
        self.assertNotIn("redirect_uris", config["web"])

    @patch('app.youtube.get_generated_short')
    @patch('app.youtube.get_setting')
    @patch('app.youtube.update_short_publish_status')
    def test_upload_scheduled_short_no_token_ready_status(self, mock_update_status, mock_get_setting, mock_get_short):
        """3. upload_scheduled_short() updates short status to 'ready' when there is no YouTube OAuth token."""
        mock_get_short.return_value = {"id": "short_123", "title": "Test Title"}
        mock_get_setting.return_value = None  # No token in DB
        
        upload_scheduled_short("short_123")
        
        mock_update_status.assert_called_once_with("short_123", "ready")

    @patch('app.youtube.get_generated_short')
    @patch('app.youtube.get_setting')
    @patch('app.youtube.OAuth2Credentials')
    @patch('app.youtube.update_short_publish_status')
    def test_upload_scheduled_short_refresh_failed_status(self, mock_update_status, mock_oauth_creds, mock_get_setting, mock_get_short):
        """4. upload_scheduled_short() handles API refresh token expired or failed and updates status to 'failed'."""
        mock_get_short.return_value = {"id": "short_123", "title": "Test Title"}
        mock_get_setting.return_value = json.dumps({
            "token": "expired_token",
            "refresh_token": "refresh_val",
            "expiry": "2020-01-01T00:00:00Z"
        })
        
        # Setup mock credentials object that is expired and raises an exception on refresh
        mock_creds_inst = MagicMock()
        mock_creds_inst.expired = True
        mock_creds_inst.refresh_token = "refresh_val"
        mock_creds_inst.refresh.side_effect = Exception("Refresh token expired/invalid")
        
        mock_oauth_creds.from_authorized_user_info.return_value = mock_creds_inst
        
        upload_scheduled_short("short_123")
        
        mock_update_status.assert_called_once_with(
            "short_123",
            "failed",
            error="Failed to refresh YouTube OAuth token: Refresh token expired/invalid"
        )

if __name__ == "__main__":
    unittest.main()
