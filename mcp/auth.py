from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import os

SCOPES = ["https://www.googleapis.com/auth/calendar"]

def get_calendar_service():
    creds = None

    os.makedirs("credentials", exist_ok=True)

    if os.path.exists("credentials/token.json"):
        creds = Credentials.from_authorized_user_file(
            "credentials/token.json", SCOPES
        )

    if not creds or not creds.valid:
        if not os.path.exists("credentials/credentials.json"):
            raise FileNotFoundError(
                "Missing Google OAuth client secrets file at credentials/credentials.json"
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            "credentials/credentials.json", SCOPES
        )
        creds = flow.run_local_server(port=0)

        with open("credentials/token.json", "w") as token:
            token.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)
