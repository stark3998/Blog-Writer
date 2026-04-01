"""Shared test fixtures — disable Entra ID auth for all API tests."""

import os

# Clear Entra ID env vars so the auth middleware is bypassed in tests.
# This must happen before the FastAPI app middleware checks the env at request time.
os.environ["ENTRA_CLIENT_ID"] = ""
os.environ["ENTRA_TENANT_ID"] = ""
