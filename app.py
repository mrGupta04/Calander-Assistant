from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import os
import logging
from dateparser.search import search_dates
import pytz
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dateutil import parser as dateutil_parser
import uvicorn
from dotenv import load_dotenv
import uuid
import difflib
import re

# Load environment variables
load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# App setup
app = FastAPI(title="Calendar Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Key auth
API_KEY_NAME = "x-api-key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

def get_api_key(api_key: str = Depends(api_key_header)):
    expected = os.getenv("API_KEY")
    if not expected:
        raise HTTPException(500, "Server configuration error")
    if api_key != expected:
        raise HTTPException(403, "Invalid API Key")
    return api_key

# Constants
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = os.getenv("CALENDAR_ID", "primary")
TIMEZONE = os.getenv("TZ", "UTC")
DEFAULT_MEETING_DURATION = timedelta(minutes=30)

# Models
class CalendarEvent(BaseModel):
    start: str
    end: str
    summary: str = "Meeting"
    description: Optional[str] = "Booked via Assistant"
    attendees: Optional[List[str]] = None
    timezone: str = TIMEZONE
    location: Optional[str] = None

class UserMessage(BaseModel):
    content: str
    conversation_id: Optional[str] = None
    conversation_history: Optional[List[Dict[str, Any]]] = None

class ChatResponse(BaseModel):
    response: str
    action: str
    events: Optional[List[Dict[str, Any]]] = None
    start: Optional[str] = None
    end: Optional[str] = None
    timezone: str
    conversation_id: Optional[str] = None
    suggested_responses: Optional[List[str]] = None

# Google Calendar service singleton
class CalendarService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._service = None
        return cls._instance

    def get_service(self):
        if self._service is None:
            self._service = self._authenticate()
        return self._service

    def _authenticate(self):
        token_file = "token.json"
        creds = None
        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(GoogleRequest())
            else:
                cf = "credentials.json"
                if not os.path.exists(cf):
                    raise FileNotFoundError("Missing credentials.json")
                flow = InstalledAppFlow.from_client_secrets_file(cf, SCOPES)
                creds = flow.run_local_server(port=0)
                with open(token_file, "w") as f:
                    f.write(creds.to_json())
        return build("calendar", "v3", credentials=creds)

calendar_service = CalendarService().get_service()

# Helpers
def match_intent(text: str, keywords: List[str], threshold: float = 0.6) -> bool:
    t = text.lower()
    return any(kw in t or difflib.SequenceMatcher(None, kw, t).ratio() > threshold for kw in keywords)

def parse_date_from_text(text: str) -> Optional[datetime]:
    now = datetime.now(pytz.timezone(TIMEZONE))
    t = text.lower()
    if "today" in t:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if any(k in t for k in ["tomorrow", "tomorroe", "tomorow"]):
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    parsed = search_dates(
        text,
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": TIMEZONE,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": now
        }
    )
    return parsed[0][1].replace(hour=0, minute=0, second=0, microsecond=0) if parsed else None

def format_time_range(start: datetime, end: datetime) -> str:
    s = start.strftime("%I:%M %p").lstrip("0")
    e = end.strftime("%I:%M %p").lstrip("0")
    if e == "00:00 AM":
        e = "12:00 AM"
    elif end.date() > start.date():
        e += " (next day)"
    return f"{s} - {e}"

def get_calendar_events(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    tz = pytz.timezone(TIMEZONE)
    if start.tzinfo is None:
        start = tz.localize(start)
    if end.tzinfo is None:
        end = tz.localize(end)
    try:
        events = calendar_service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime"
        ).execute().get("items", [])
        unique = {}
        for e in events:
            eid = e.get("id")
            if eid and eid not in unique:
                unique[eid] = {
                    "summary": e.get("summary", "Meeting"),
                    "start": e["start"].get("dateTime", e["start"].get("date")),
                    "end": e["end"].get("dateTime", e["end"].get("date"))
                }
        return list(unique.values())
    except Exception as exc:
        logger.error("Error retrieving events: %s", exc)
        return []

def check_time_conflict(start: datetime, end: datetime, events: List[Dict[str, Any]]):
    tz = pytz.timezone(TIMEZONE)
    conflicts = []
    for e in events:
        e_s = dateutil_parser.parse(e["start"]).astimezone(tz)
        e_e = dateutil_parser.parse(e["end"]).astimezone(tz)
        if not (end <= e_s or start >= e_e):
            conflicts.append({"summary": e["summary"], "start": e_s, "end": e_e})
    return conflicts

@app.post("/book_appointment", response_model=ChatResponse)
async def book_appointment(event: CalendarEvent, api_key: str = Depends(get_api_key)):
    try:
        tz = pytz.timezone(event.timezone)
        start = dateutil_parser.parse(event.start).astimezone(tz)
        end = dateutil_parser.parse(event.end).astimezone(tz)

        if start >= end:
         end = start + DEFAULT_MEETING_DURATION


        window_start = start - timedelta(hours=24)
        window_end = end + timedelta(hours=24)
        existing = get_calendar_events(window_start, window_end)
        conflicts = check_time_conflict(start, end, existing)

        if conflicts:
            msg = "❌ That time conflicts with:\n"
            msg += "\n".join(
                f"- {c['summary']} ({format_time_range(c['start'], c['end'])})" for c in conflicts
            )
            return ChatResponse(response=msg, action="conflict", timezone=event.timezone, conversation_id=str(uuid.uuid4()))

        body = {
            "summary": event.summary,
            "description": event.description,
            "start": {"dateTime": start.isoformat(), "timeZone": event.timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": event.timezone},
            "attendees": [{"email": e} for e in event.attendees or []],
            "location": event.location or "",
        }

        calendar_service.events().insert(
            calendarId=CALENDAR_ID,
            body=body,
            sendUpdates="all" if event.attendees else "none"
        ).execute()

        return ChatResponse(
            response=f"✅ Successfully booked '{event.summary}' from {format_time_range(start, end)}",
            action="confirm_booking",
            start=start.isoformat(),
            end=end.isoformat(),
            timezone=event.timezone,
            conversation_id=str(uuid.uuid4())
        )

    except HttpError as e:
        logger.error("Calendar API error: %s", e)
        return ChatResponse(response="Sorry, an error occurred with the calendar service.", action="error", timezone=event.timezone, conversation_id=str(uuid.uuid4()))

    except Exception as e:
        logger.error("Booking error: %s", e, exc_info=True)
        return ChatResponse(response="Sorry, something went wrong.", action="error", timezone=event.timezone, conversation_id=str(uuid.uuid4()))

# Include your other endpoints like /chat and /health
@app.post("/chat", response_model=ChatResponse)
async def chat(user_message: UserMessage, api_key: str = Depends(get_api_key)):
    text = user_message.content.strip()
    conv_id = user_message.conversation_id or str(uuid.uuid4())

    if not user_message.conversation_history:
        return ChatResponse(
            response="Hi! I’m your calendar assistant. How can I help you today?",
            action="greeting",
            timezone=TIMEZONE,
            conversation_id=conv_id,
            suggested_responses=[
                "What's my schedule today?",
                "Am I free tomorrow at 2pm?",
                "Book a meeting for June 28 at 3pm",
                "Show my schedule for tomorrow"
            ]
        )

    if match_intent(text, ["what's my schedule", "my meetings", "show schedule", "schedule for"]):
        dt = parse_date_from_text(text)
        if not dt:
            return ChatResponse(response="I couldn't understand the date. Try 'Show my schedule for today'.", action="clarify_date", timezone=TIMEZONE, conversation_id=conv_id)
        day_start = dt
        day_end = dt.replace(hour=23, minute=59, second=59)
        events = get_calendar_events(day_start, day_end)
        if not events:
            return ChatResponse(response=f"You have no events scheduled for {dt.strftime('%A, %B %d')}.", action="show_schedule", events=[], timezone=TIMEZONE, conversation_id=conv_id)
        msg = f"Your schedule for {dt.strftime('%A, %B %d')}:\n"
        msg += "\n".join(f"- {e['summary']} ({format_time_range(dateutil_parser.parse(e['start']).astimezone(pytz.timezone(TIMEZONE)), dateutil_parser.parse(e['end']).astimezone(pytz.timezone(TIMEZONE)))})" for e in events)
        return ChatResponse(response=msg, action="show_schedule", events=events, timezone=TIMEZONE, conversation_id=conv_id)

    if match_intent(text, ["are you free", "am i free", "is there time"]):
        dt = parse_date_from_text(text)
        if not dt:
            return ChatResponse(response="I couldn't understand the date. Try 'Am I free tomorrow?'.", action="clarify_date", timezone=TIMEZONE, conversation_id=conv_id)
        day_start = dt
        day_end = dt.replace(hour=23, minute=59, second=59)
        events = get_calendar_events(day_start, day_end)
        if not events:
            return ChatResponse(response=f"You're completely free on {dt.strftime('%A, %B %d')}! Would you like to schedule something?", action="show_availability", events=[], timezone=TIMEZONE, conversation_id=conv_id)
        msg = f"You have {len(events)} events on {dt.strftime('%A, %B %d')}:\n"
        msg += "\n".join(f"- {e['summary']} ({format_time_range(dateutil_parser.parse(e['start']).astimezone(pytz.timezone(TIMEZONE)), dateutil_parser.parse(e['end']).astimezone(pytz.timezone(TIMEZONE)))})" for e in events)
        return ChatResponse(response=msg, action="show_schedule", events=events, timezone=TIMEZONE, conversation_id=conv_id)

    if match_intent(text, ["book", "schedule", "meeting", "appointment"]):
        time_range = search_dates(
            text,
            settings={
                "PREFER_DATES_FROM": "future",
                "TIMEZONE": TIMEZONE,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "RELATIVE_BASE": datetime.now(pytz.timezone(TIMEZONE))
            }
        )
        if not time_range or len(time_range) < 2:
            return ChatResponse(
                response="I couldn't understand the time. Try 'Book a meeting tomorrow at 3pm'.",
                action="clarify_time",
                timezone=TIMEZONE,
                conversation_id=conv_id
            )

        start, end = time_range[0][1], time_range[1][1]

        summary = "Meeting"
        low = text.lower()
        if "with" in low:
            summary = f"Meeting with {low.split('with',1)[1].strip().title()}"
        elif "about" in low:
            summary = f"Meeting about {low.split('about',1)[1].strip().title()}"

        event = CalendarEvent(
            start=start.isoformat(),
            end=end.isoformat(),
            summary=summary,
            timezone=TIMEZONE
        )
        return await book_appointment(event, api_key)

    return ChatResponse(
        response="I can help you check your schedule or book meetings. Try: 'Book a meeting today at 2pm'.",
        action="greeting",
        timezone=TIMEZONE,
        conversation_id=conv_id
    )

@app.get("/health")
async def health():
    try:
        calendar_service.calendarList().list().execute()
        return {"status": "healthy"}
    except Exception as e:
        raise HTTPException(500, f"Unhealthy: {e}")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
