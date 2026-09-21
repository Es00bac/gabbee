from __future__ import annotations

import base64
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import requests
try:
    from google.auth import default as google_auth_default
    from google.auth.transport.requests import Request as GoogleAuthRequest
except ImportError:  # pragma: no cover - exercised only when optional dependency is absent.
    google_auth_default = None
    GoogleAuthRequest = None

from ..config import AppConfig
from ..models import TranscriptionResult


class GeminiSpeechToText:
    provider_name = "gemini"
    cloud_platform_scope = "https://www.googleapis.com/auth/cloud-platform"

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.model = config.gemini_model
        self.language_code = config.language_code
        self.project = config.vertex_project
        self.location = config.vertex_location

    def _build_payload(self, audio_path: Path) -> dict[str, Any]:
        with audio_path.open("rb") as handle:
            audio_data = base64.b64encode(handle.read()).decode("utf-8")

        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Transcribe this audio accurately. "
                                f"Language: {self.language_code}. "
                                "Output ONLY the transcribed text, with no preamble, "
                                "conversational filler, or formatting."
                            )
                        },
                        {
                            "inlineData": {
                                "mimeType": "audio/wav",
                                "data": audio_data,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
            },
        }

    def _endpoint(self) -> str:
        if not self.project:
            raise RuntimeError(
                "GABBEE_VERTEX_PROJECT is missing. Set it to the Google Cloud project "
                "that has Vertex AI Gemini access enabled."
            )
        location = self.location or "us-central1"
        return (
            f"https://{location}-aiplatform.googleapis.com/v1/"
            f"projects/{self.project}/locations/{location}/publishers/google/models/"
            f"{self.model}:generateContent"
        )

    def _resolve_gcloud_command(self) -> str:
        explicit_names = (
            "GCLOUD_BIN",
            "GCLOUD_BINARY",
            "CLOUDSDK_GCLOUD_BIN",
            "SIGNAL_LOOM_GCLOUD_BIN",
        )
        for name in explicit_names:
            value = os.environ.get(name)
            if value:
                return shutil.which(value) or str(Path(value).expanduser())

        for candidate in (
            shutil.which("gcloud"),
            "/usr/bin/gcloud",
            "/usr/local/bin/gcloud",
            "/opt/google-cloud-sdk/bin/gcloud",
            str(Path.home() / ".local" / "bin" / "gcloud"),
        ):
            if candidate and Path(candidate).expanduser().exists():
                return str(Path(candidate).expanduser())

        return "gcloud"

    def _gcloud_access_token(self) -> str:
        command = self._resolve_gcloud_command()
        args = [command, "auth", "application-default", "print-access-token"]
        try:
            completed = subprocess.run(
                args=args,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = ""
            stderr = getattr(exc, "stderr", None)
            if stderr:
                detail = f" {str(stderr).strip()}"
            raise RuntimeError(
                "Could not get a Google access token from gcloud ADC. "
                f"Tried `{' '.join(args)}`. Run `gcloud auth application-default login` "
                f"or set GOOGLE_APPLICATION_CREDENTIALS.{detail}"
            ) from exc

        token = completed.stdout.strip()
        if not token:
            raise RuntimeError("gcloud returned an empty Vertex ADC access token.")
        return token

    def _adc_access_token(self) -> str:
        if google_auth_default is None:
            return self._gcloud_access_token()

        try:
            credentials, _ = google_auth_default(scopes=[self.cloud_platform_scope])
        except Exception:
            return self._gcloud_access_token()

        if not getattr(credentials, "valid", False) or not getattr(credentials, "token", None):
            if GoogleAuthRequest is None:
                return self._gcloud_access_token()
            try:
                credentials.refresh(GoogleAuthRequest())
            except Exception:
                return self._gcloud_access_token()

        token = getattr(credentials, "token", None)
        if not token:
            return self._gcloud_access_token()
        return token

    def _auth_headers(self) -> dict[str, str]:
        token = self._adc_access_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if self.project:
            headers["x-goog-user-project"] = self.project
        return headers

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        response = requests.post(
            self._endpoint(),
            headers=self._auth_headers(),
            json=self._build_payload(audio_path),
            timeout=60,
        )
        if response.status_code != 200:
            message = response.text
            try:
                body = response.json()
                if isinstance(body, dict):
                    error = body.get("error")
                    if isinstance(error, dict):
                        message = str(error.get("message") or message)
            except ValueError:
                pass
            raise RuntimeError(f"Vertex Gemini API error ({response.status_code}): {message}")

        data = response.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected response format from Vertex Gemini API: {data}") from exc

        if not text:
            raise RuntimeError("Vertex Gemini returned an empty transcript.")

        return TranscriptionResult(
            text=text,
            provider=self.provider_name,
            language_code=self.language_code,
        )
