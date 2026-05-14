"""One-time Google Drive OAuth setup.

Run:  uv run python gdrive_auth.py
Opens a browser → sign in → grants Drive access → saves token.
"""
import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES     = ["https://www.googleapis.com/auth/drive"]
KEYS_PATH  = Path.home() / ".gdrive-mcp" / "gcp-oauth.keys.json"
CREDS_PATH = Path.home() / ".gdrive-mcp" / "credentials.json"

flow  = InstalledAppFlow.from_client_secrets_file(str(KEYS_PATH), SCOPES)
creds = flow.run_local_server(port=0)

data = {
    "access_token":  creds.token,
    "refresh_token": creds.refresh_token,
    "scope":         " ".join(creds.scopes or SCOPES),
    "token_type":    "Bearer",
    "expiry_date":   int(creds.expiry.timestamp() * 1000) if creds.expiry else 0,
}
CREDS_PATH.write_text(json.dumps(data, indent=2))
print(f"✓ Drive credentials saved to {CREDS_PATH}")
print(f"  Scopes: {data['scope']}")
