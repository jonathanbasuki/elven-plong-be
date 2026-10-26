import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class CameraSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    rtsp_url: str


def main() -> None:
    try:
        rtsp_url = CameraSettings().rtsp_url
    except ValidationError:
        raise SystemExit(
            "Set RTSP_URL in .env before generating MediaMTX config"
        ) from None

    try:
        parsed = urlsplit(rtsp_url)
        valid_url = parsed.scheme == "rtsp" and parsed.hostname and parsed.port
    except ValueError:
        valid_url = False
    if not valid_url:
        raise SystemExit("RTSP_URL must be a valid rtsp:// URL with a host and port")

    output = ROOT / "infra" / "mediamtx.yml"
    output.write_text(
        f"paths:\n  cam1:\n    source: {json.dumps(rtsp_url)}\n",
        encoding="utf-8",
    )
    os.chmod(output, 0o600)
    print(f"Generated local MediaMTX config: {output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
