from __future__ import annotations

from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from gabbee.config import AppConfig
from gabbee.stt.gemini import GeminiSpeechToText


class FakeCredentials:
    def __init__(self, token: str = "test-token") -> None:
        self.token = token
        self.valid = True
        self.expired = False
        self.refresh_called = False

    def refresh(self, request) -> None:
        self.refresh_called = True
        self.token = "refreshed-token"


class TestGeminiSTT(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_config = MagicMock(spec=AppConfig)
        self.mock_config.gemini_model = "gemini-2.5-flash"
        self.mock_config.language_code = "en"
        self.mock_config.vertex_project = "project-38890c01-de5b-44c9-be4"
        self.mock_config.vertex_location = "us-central1"

    def test_transcribe_missing_vertex_project(self) -> None:
        self.mock_config.vertex_project = None
        stt = GeminiSpeechToText(self.mock_config)
        with self.assertRaisesRegex(RuntimeError, "GABBEE_VERTEX_PROJECT"):
            stt.transcribe(Path("test.wav"))

    def test_build_payload_contains_audio_and_prompt(self) -> None:
        stt = GeminiSpeechToText(self.mock_config)
        with patch("pathlib.Path.open", unittest.mock.mock_open(read_data=b"dummy_audio")):
            payload = stt._build_payload(Path("test.wav"))

        parts = payload["contents"][0]["parts"]
        self.assertIn("Output ONLY", parts[0]["text"])
        self.assertEqual(payload["contents"][0]["role"], "user")
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "audio/wav")
        self.assertEqual(parts[1]["inlineData"]["data"], "ZHVtbXlfYXVkaW8=")

    @patch("requests.post")
    @patch("gabbee.stt.gemini.GoogleAuthRequest")
    @patch("gabbee.stt.gemini.google_auth_default")
    def test_transcribe_success_uses_vertex_adc_endpoint(self, mock_auth_default, mock_auth_request, mock_post) -> None:
        credentials = FakeCredentials()
        mock_auth_default.return_value = (credentials, "adc-project")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Hello world"}
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        stt = GeminiSpeechToText(self.mock_config)
        with patch("pathlib.Path.open", unittest.mock.mock_open(read_data=b"dummy_audio")):
            result = stt.transcribe(Path("test.wav"))

        self.assertEqual(result.text, "Hello world")
        self.assertEqual(result.provider, "gemini")
        args, kwargs = mock_post.call_args
        self.assertEqual(
            args[0],
            "https://us-central1-aiplatform.googleapis.com/v1/"
            "projects/project-38890c01-de5b-44c9-be4/locations/us-central1/"
            "publishers/google/models/gemini-2.5-flash:generateContent",
        )
        self.assertNotIn("key=", args[0])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(kwargs["json"]["contents"][0]["parts"][1]["inlineData"]["data"], "ZHVtbXlfYXVkaW8=")
        mock_auth_default.assert_called_once_with(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        mock_auth_request.assert_not_called()

    @patch("gabbee.stt.gemini.GoogleAuthRequest")
    @patch("gabbee.stt.gemini.google_auth_default")
    def test_adc_credentials_are_refreshed_when_needed(self, mock_auth_default, mock_auth_request) -> None:
        credentials = FakeCredentials(token="")
        credentials.valid = False
        mock_auth_default.return_value = (credentials, "adc-project")

        stt = GeminiSpeechToText(self.mock_config)
        headers = stt._auth_headers()

        self.assertTrue(credentials.refresh_called)
        self.assertEqual(headers["Authorization"], "Bearer refreshed-token")
        mock_auth_request.assert_called_once_with()

    @patch("subprocess.run")
    @patch("gabbee.stt.gemini.google_auth_default", None)
    def test_auth_headers_fall_back_to_gcloud_adc_without_google_auth(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["gcloud"],
            returncode=0,
            stdout="gcloud-token\n",
            stderr="",
        )

        stt = GeminiSpeechToText(self.mock_config)
        with patch.object(GeminiSpeechToText, "_resolve_gcloud_command", return_value="gcloud"):
            headers = stt._auth_headers()

        self.assertEqual(headers["Authorization"], "Bearer gcloud-token")
        self.assertEqual(headers["x-goog-user-project"], "project-38890c01-de5b-44c9-be4")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["args"], ["gcloud", "auth", "application-default", "print-access-token"])
        self.assertTrue(kwargs["check"])
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])

    @patch("requests.post")
    @patch("gabbee.stt.gemini.google_auth_default")
    def test_transcribe_api_error_includes_status_and_message(self, mock_auth_default, mock_post) -> None:
        mock_auth_default.return_value = (FakeCredentials(), "adc-project")
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": {"message": "Vertex AI API has not been used"}}
        mock_response.text = "fallback text"
        mock_post.return_value = mock_response

        stt = GeminiSpeechToText(self.mock_config)
        with patch("pathlib.Path.open", unittest.mock.mock_open(read_data=b"dummy_audio")):
            with self.assertRaisesRegex(RuntimeError, "Vertex Gemini API error \\(400\\): Vertex AI API has not been used"):
                stt.transcribe(Path("test.wav"))

    @patch("requests.post")
    @patch("gabbee.stt.gemini.google_auth_default")
    def test_transcribe_empty_response(self, mock_auth_default, mock_post) -> None:
        mock_auth_default.return_value = (FakeCredentials(), "adc-project")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"candidates": []}
        mock_post.return_value = mock_response

        stt = GeminiSpeechToText(self.mock_config)
        with patch("pathlib.Path.open", unittest.mock.mock_open(read_data=b"dummy_audio")):
            with self.assertRaises(RuntimeError):
                stt.transcribe(Path("test.wav"))


if __name__ == "__main__":
    unittest.main()
