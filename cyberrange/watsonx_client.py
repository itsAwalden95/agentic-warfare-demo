"""
Thin IBM watsonx.ai client used by the LLM commander layer.

Design goals:
  * Zero hard dependency at import time -- the whole demo must run with no
    watsonx credentials (heuristic fallback). watsonx is an *enhancement*.
  * Credentials come only from environment variables, never the code:
        WATSONX_API_KEY      IAM api key
        WATSONX_URL          e.g. https://us-south.ml.cloud.ibm.com
        WATSONX_PROJECT_ID   watsonx.ai project id
        WATSONX_MODEL_ID     e.g. meta-llama/llama-3-3-70b-instruct
  * Short timeout + graceful failure so a slow/unavailable model never stalls
    the simulation; the commander falls back to heuristics on any error.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional


class WatsonxClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("WATSONX_API_KEY", "")
        self.url = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
        self.project_id = os.getenv("WATSONX_PROJECT_ID", "")
        self.model_id = os.getenv("WATSONX_MODEL_ID",
                                  "meta-llama/llama-3-3-70b-instruct")
        self._token: Optional[str] = None
        self._token_exp: float = 0.0
        self._model = None
        self._init_sdk()

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.project_id)

    def _init_sdk(self) -> None:
        """Prefer the official ibm-watsonx-ai SDK if it's installed."""
        if not self.available:
            return
        try:
            from ibm_watsonx_ai import Credentials  # type: ignore
            from ibm_watsonx_ai.foundation_models import ModelInference  # type: ignore
            creds = Credentials(url=self.url, api_key=self.api_key)
            self._model = ModelInference(
                model_id=self.model_id,
                credentials=creds,
                project_id=self.project_id,
                params={"max_new_tokens": 400, "temperature": 0.3,
                        "decoding_method": "greedy"},
            )
        except Exception:
            # SDK not installed or init failed -> use REST path lazily.
            self._model = None

    # -- IAM token for the REST fallback path ----------------------------
    def _iam_token(self) -> Optional[str]:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        try:
            import urllib.parse
            import urllib.request
            data = urllib.parse.urlencode({
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                "apikey": self.api_key,
            }).encode()
            req = urllib.request.Request(
                "https://iam.cloud.ibm.com/identity/token", data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=10) as r:
                body = json.loads(r.read())
            self._token = body["access_token"]
            self._token_exp = time.time() + body.get("expires_in", 3600)
            return self._token
        except Exception:
            return None

    def generate(self, prompt: str, timeout: float = 12.0) -> Optional[str]:
        """Return the model's text completion, or None on any failure."""
        if not self.available:
            return None
        # Path 1: official SDK
        if self._model is not None:
            try:
                resp = self._model.generate_text(prompt=prompt)
                return resp if isinstance(resp, str) else str(resp)
            except Exception:
                return None
        # Path 2: REST
        token = self._iam_token()
        if not token:
            return None
        try:
            import urllib.request
            payload = json.dumps({
                "model_id": self.model_id,
                "project_id": self.project_id,
                "input": prompt,
                "parameters": {"decoding_method": "greedy",
                               "max_new_tokens": 400, "temperature": 0.3},
            }).encode()
            endpoint = (f"{self.url}/ml/v1/text/generation"
                        f"?version=2024-05-01")
            req = urllib.request.Request(
                endpoint, data=payload,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read())
            return body["results"][0]["generated_text"]
        except Exception:
            return None
