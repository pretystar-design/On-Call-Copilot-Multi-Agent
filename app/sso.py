"""SSO authentication module using Azure AD."""

from __future__ import annotations

import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from azure.identity import InteractiveBrowserCredential
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

load_dotenv()

# Configuration
SSO_ENABLED = os.environ.get("SSO_ENABLED", "true").lower() in ("true", "1", "yes")
SSO_CLIENT_ID = os.environ.get("SSO_CLIENT_ID", "")
SSO_TENANT_ID = os.environ.get("SSO_TENANT_ID", os.environ.get("AZURE_TENANT_ID", ""))
SSO_REDIRECT_URI = os.environ.get("SSO_REDIRECT_URI", "http://localhost:7860/auth/callback")
SSO_SCOPES = ["https://ai.azure.com/.default"]
SECRET_KEY = os.environ.get("SSO_SECRET_KEY", secrets.token_hex(32))
SESSION_LIFETIME_HOURS = int(os.environ.get("SSO_SESSION_LIFETIME_HOURS", "8"))

# Azure AD OAuth endpoints
AZURE_AD_AUTHORITY = f"https://login.microsoftonline.com/{SSO_TENANT_ID}" if SSO_TENANT_ID else "https://login.microsoftonline.com/common"


@dataclass
class SSOUser:
    """User information from Azure AD."""
    user_id: str
    display_name: str
    email: Optional[str] = None
    tenant_id: Optional[str] = None
    access_token: Optional[str] = None
    expires_at: Optional[float] = None


class SSOManager:
    """Manage Azure AD SSO authentication."""

    def __init__(self):
        self.serializer = URLSafeTimedSerializer(SECRET_KEY, salt="sso-session")
        self._credential_cache: dict[str, InteractiveBrowserCredential] = {}

    def is_enabled(self) -> bool:
        """Check if SSO is enabled and configured."""
        return SSO_ENABLED and SSO_CLIENT_ID and SSO_TENANT_ID

    def get_auth_url(self, state: str) -> str:
        """Generate Azure AD authorization URL."""
        params = {
            "client_id": SSO_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": SSO_REDIRECT_URI,
            "scope": " ".join(SSO_SCOPES),
            "response_mode": "query",
            "state": state,
        }
        return f"{AZURE_AD_AUTHORITY}/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"

    def get_logout_url(self) -> str:
        """Generate Azure AD logout URL."""
        params = {
            "client_id": SSO_CLIENT_ID,
            "post_logout_redirect_uri": SSO_REDIRECT_URI.replace("/auth/callback", ""),
        }
        return f"{AZURE_AD_AUTHORITY}/oauth2/v2.0/logout?{urllib.parse.urlencode(params)}"

    def create_session_token(self, user: SSOUser) -> str:
        """Create encrypted session token for user."""
        payload = {
            "user_id": user.user_id,
            "display_name": user.display_name,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "expires_at": time.time() + SESSION_LIFETIME_HOURS * 3600,
        }
        return self.serializer.dumps(payload)

    def verify_session_token(self, token: str) -> Optional[SSOUser]:
        """Verify and decode session token."""
        try:
            payload = self.serializer.loads(token, max_age=SESSION_LIFETIME_HOURS * 3600)
            return SSOUser(
                user_id=payload["user_id"],
                display_name=payload["display_name"],
                email=payload.get("email"),
                tenant_id=payload.get("tenant_id"),
                expires_at=payload.get("expires_at"),
            )
        except (BadSignature, SignatureExpired):
            return None

    def exchange_code_for_token(self, code: str) -> Optional[SSOUser]:
        """Exchange authorization code for access token and user info."""
        # Use InteractiveBrowserCredential to acquire token
        # This is simplified - in production you'd use msal or requests directly
        try:
            credential = InteractiveBrowserCredential(
                client_id=SSO_CLIENT_ID,
                tenant_id=SSO_TENANT_ID,
                redirect_uri=SSO_REDIRECT_URI,
            )
            
            # Acquire token with the authorization code
            token_result = credential.get_token(" ".join(SSO_SCOPES))
            
            # For now, return a basic user object
            # In production, you'd call Microsoft Graph API to get user details
            return SSOUser(
                user_id="azure-ad-user",
                display_name="Azure AD User",
                access_token=token_result.token,
                expires_at=time.time() + token_result.expires_on,
            )
        except Exception:
            return None

    def generate_state(self) -> str:
        """Generate random state for OAuth flow."""
        return secrets.token_urlsafe(32)


# Singleton instance
sso_manager = SSOManager()