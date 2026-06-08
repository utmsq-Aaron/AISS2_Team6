#!/usr/bin/env python3
"""
Garmin Connect one-time authentication setup.

Run once from the project root:
    python auth/garmin_setup.py

After a successful run, garmin_server.py loads tokens automatically.
Tokens are cached in .garmin_tokens/ — never commit this directory.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

TOKEN_STORE = ".tokens"


def main() -> None:
    try:
        from garminconnect import Garmin, GarminConnectAuthenticationError
    except ImportError:
        print("garminconnect not installed.")
        print("Run: pip install garminconnect")
        sys.exit(1)

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    if not email or not password:
        print("ERROR: GARMIN_EMAIL and GARMIN_PASSWORD must be set in your .env file.")
        sys.exit(1)

    def _mfa_prompt() -> str:
        return input("Enter your Garmin MFA / OTP code: ").strip()

    print(f"Authenticating as {email} ...")
    print("If valid tokens are already cached, no MFA prompt will appear.")

    try:
        garmin = Garmin(email=email, password=password, prompt_mfa=_mfa_prompt)
        # login(tokenstore=...) loads cached tokens when present, refreshes if
        # expired, and only performs a full login + MFA when necessary.
        garmin.login(tokenstore=TOKEN_STORE)
        name = garmin.get_full_name()
        print(f"Success — logged in as: {name}")
        print(f"Tokens cached to '{TOKEN_STORE}/'. garmin_server is ready.")
    except GarminConnectAuthenticationError as e:
        print(f"Authentication failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
