from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Literal
import os
import re
import logging
from dateparser import parse
from dateparser.search import search_dates
import pytz
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dateutil import parser
import uvicorn
from dotenv import load_dotenv
import calendar
import json

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Calendar Assistant API", version="1.0.0")

# Security setup
API_KEY_NAME = "x-api-key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key: str = Depends(api_key_header)):
    expected_key = os.getenv("API_KEY")
    if not expected_key:
        raise HTTPException(status_code=500, detail="Server configuration error")
    if not api_key or api_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Constants
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = os.getenv('CALENDAR_ID', 'primary')
TIMEZONE = os.getenv('TZ', 'UTC')
DEFAULT_MEETING_DURATION = timedelta(hours=1)
SLOT_DURATION = timedelta(minutes=30)
BUSINESS_HOURS = (9, 17)  # 9am to 5pm

# Models
class CalendarEvent(BaseModel):
    start: str
    end: str
    summary: str = "Meeting"
    description: Optional[str] = "Booked via Calendar Assistant"
    attendees: Optional[List[str]] = None
    timezone: str = TIMEZONE

class UserMessage(BaseModel):
    content: str
    conversation_history: Optional[List[Dict[str, Any]]] = None

class ChatResponse(BaseModel):
    response: str
    action: Literal["greeting", "offer_booking", "show_schedule", "clarify_time", "fully_booked", "error"]
    slots: Optional[List[str]] = None
    events: Optional[List[Dict[str, Any]]] = None
    start: Optional[str] = None
    end: Optional[str] = None
    timezone: str = TIMEZONE

class BookingResponse(BaseModel):
    status: Literal["success", "error"]
    event_id: Optional[str] = None
    message: str
    timezone: str = TIMEZONE

def get_google_calendar_service():
    creds = None
    token_file = 'token.json'
    credentials_file = 'credentials.json'
    
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, 'w') as token:
            token.write(creds.to_json())
    
    return build('calendar', 'v3', credentials=creds)

def parse_time_range(text: str):
    text = text.lower().strip()
    now = datetime.now(pytz.timezone(TIMEZONE))
    
    # Handle explicit time ranges like "3-4pm" or "Friday 3-4pm"
    time_range_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text)
    if time_range_match:
        start_hour, start_min, start_ampm, end_hour, end_min, end_ampm = time_range_match.groups()
        
        # Parse start time
        start_hour = int(start_hour)
        if start_ampm == 'pm' and start_hour < 12:
            start_hour += 12
        elif start_ampm == 'am' and start_hour == 12:
            start_hour = 0
        start_min = int(start_min) if start_min else 0
        
        # Parse end time
        end_hour = int(end_hour)
        if end_ampm == 'pm' and end_hour < 12:
            end_hour += 12
        elif end_ampm == 'am' and end_hour == 12:
            end_hour = 0
        end_min = int(end_min) if end_min else 0
        
        # Handle day specification
        day_match = re.search(r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)', text, re.IGNORECASE)
        days_ahead = 0
        day_name = "today"
        if day_match:
            day_name = day_match.group(1).capitalize()
            days_ahead = (list(calendar.day_name).index(day_name) - now.weekday())
            if days_ahead <= 0:
                days_ahead += 7
        
        target_date = (now + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = target_date.replace(hour=start_hour, minute=start_min)
        end_dt = target_date.replace(hour=end_hour, minute=end_min)
        
        display_start = f"{start_hour if start_hour <= 12 else start_hour-12}:{start_min:02d}{'am' if start_hour < 12 else 'pm'}"
        display_end = f"{end_hour if end_hour <= 12 else end_hour-12}:{end_min:02d}{'am' if end_hour < 12 else 'pm'}"
        
        return start_dt, end_dt, f"{day_name} {display_start} to {display_end}"

    # Handle relative days
    day_offset = 0
    if "tomorrow" in text:
        day_offset = 1
    elif "today" in text:
        day_offset = 0
    
    # Handle specific dates (e.g., "June 30")
    month_day_match = re.search(r'(\b\w+\b)\s+(\d{1,2})\b', text)
    if month_day_match:
        month_str, day_str = month_day_match.groups()
        try:
            month_num = list(calendar.month_name).index(month_str.capitalize())
            target_date = now.replace(month=month_num, day=int(day_str), 
                                   hour=BUSINESS_HOURS[0], minute=0)
            if target_date < now:
                target_date = target_date.replace(year=target_date.year + 1)
            return (
                target_date,
                target_date.replace(hour=BUSINESS_HOURS[1]),
                f"{month_str} {day_str}"
            )
        except:
            pass
    
    # Handle day names (e.g., "Monday")
    day_name_match = re.search(r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)', text, re.IGNORECASE)
    if day_name_match:
        day_name = day_name_match.group(1).capitalize()
        days_ahead = list(calendar.day_name).index(day_name) - now.weekday()
        if days_ahead <= 0:  # Target day already happened this week
            days_ahead += 7
        target_date = now + timedelta(days=days_ahead)
        return (
            target_date.replace(hour=BUSINESS_HOURS[0], minute=0),
            target_date.replace(hour=BUSINESS_HOURS[1], minute=0),
            day_name
        )
    
    # Handle time ranges with from/to
    range_match = re.search(r'(?:from|between)\s+(.+?)\s+(?:to|until|-)\s+(.+)', text)
    if range_match:
        start_str, end_str = range_match.groups()
        start_dt = parse(start_str, settings={'PREFER_DATES_FROM': 'future', 'TIMEZONE': TIMEZONE})
        end_dt = parse(end_str, settings={'PREFER_DATES_FROM': 'future', 'TIMEZONE': TIMEZONE})
        if start_dt and end_dt:
            if day_offset > 0:
                start_dt += timedelta(days=day_offset)
                end_dt += timedelta(days=day_offset)
            return (
                start_dt.astimezone(pytz.timezone(TIMEZONE)),
                end_dt.astimezone(pytz.timezone(TIMEZONE)),
                f"{start_dt.strftime('%I:%M %p')} to {end_dt.strftime('%I:%M %p')}"
            )
    
    # Handle single time points
    dates = search_dates(text, settings={'TIMEZONE': TIMEZONE, 'PREFER_DATES_FROM': 'future'})
    if dates:
        parsed_text, parsed_date = dates[0]
        parsed_date = parsed_date.astimezone(pytz.timezone(TIMEZONE))
        if day_offset > 0:
            parsed_date += timedelta(days=day_offset)
        return (
            parsed_date,
            parsed_date + DEFAULT_MEETING_DURATION,
            parsed_text
        )
    
    # Default to business hours
    target_date = now + timedelta(days=day_offset)
    start_dt = target_date.replace(hour=BUSINESS_HOURS[0], minute=0)
    end_dt = target_date.replace(hour=BUSINESS_HOURS[1], minute=0)
    return start_dt, end_dt, f"{target_date.strftime('%A')} {BUSINESS_HOURS[0]}am-{BUSINESS_HOURS[1]-12}pm"

def get_calendar_events(start: datetime, end: datetime):
    try:
        service = get_google_calendar_service()
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = []
        for event in events_result.get('items', []):
            if event.get('status') == 'cancelled':
                continue
                
            start_time = event['start'].get('dateTime', event['start'].get('date'))
            end_time = event['end'].get('dateTime', event['end'].get('date'))
            
            if start_time and end_time:
                events.append({
                    "id": event.get('id'),
                    "summary": event.get('summary', 'No title'),
                    "start": parser.parse(start_time).astimezone(pytz.timezone(TIMEZONE)).isoformat(),
                    "end": parser.parse(end_time).astimezone(pytz.timezone(TIMEZONE)).isoformat(),
                    "description": event.get('description', '')
                })
        
        return sorted(events, key=lambda x: x['start'])
    except Exception as e:
        logger.error(f"Error getting calendar events: {str(e)}")
        return []

def get_availability(start: datetime, end: datetime):
    events = get_calendar_events(start - timedelta(hours=1), end + timedelta(hours=1))
    
    # If checking a specific time range (not whole day)
    if start.hour != BUSINESS_HOURS[0] or end.hour != BUSINESS_HOURS[1]:
        is_available = True
        for event in events:
            event_start = parser.parse(event['start'])
            event_end = parser.parse(event['end'])
            if not (end <= event_start or start >= event_end):
                is_available = False
                break
        return ([start.isoformat()] if is_available else [], events)
    
    # Whole day availability check
    current_time = start.replace(minute=0, second=0, microsecond=0)
    available_slots = []
    
    while current_time + SLOT_DURATION <= end:
        slot_end = current_time + SLOT_DURATION
        
        # Skip outside business hours
        if BUSINESS_HOURS[0] <= current_time.hour < BUSINESS_HOURS[1]:
            is_available = True
            for event in events:
                event_start = parser.parse(event['start'])
                event_end = parser.parse(event['end'])
                if not (slot_end <= event_start or current_time >= event_end):
                    is_available = False
                    break
                    
            if is_available:
                available_slots.append(current_time.isoformat())
        
        current_time += SLOT_DURATION
    
    return available_slots, events

@app.post("/chat", response_model=ChatResponse)
async def chat(user_message: UserMessage, api_key: str = Depends(get_api_key)):
    try:
        text = user_message.content.lower()
        
        if not user_message.conversation_history:
            return ChatResponse(
                response="👋 Hi! I'm your calendar assistant. How can I help you today?",
                action="greeting",
                timezone=TIMEZONE
            )
        
        # Handle schedule requests
        if any(phrase in text for phrase in ["schedule", "what's my", "what is my", "show my"]):
            start_dt, end_dt, parsed_text = parse_time_range(text)
            if not start_dt:
                return ChatResponse(
                    response="Please specify a time frame like 'my schedule today' or 'my schedule June 30'",
                    action="clarify_time",
                    timezone=TIMEZONE
                )
            
            events = get_calendar_events(start_dt, end_dt)
            if events:
                event_list = "\n".join(
                    f"• {e['summary']}: {parser.parse(e['start']).strftime('%A, %b %d %I:%M %p')} to {parser.parse(e['end']).strftime('%I:%M %p')}"
                    for e in events
                )
                response = f"📅 Your schedule for {parsed_text}:\n{event_list}"
            else:
                response = f"📅 You have no events scheduled for {parsed_text}"
            
            return ChatResponse(
                response=response,
                action="show_schedule",
                events=events,
                start=start_dt.isoformat(),
                end=end_dt.isoformat(),
                timezone=TIMEZONE
            )
        
        # Handle availability requests
        if any(phrase in text for phrase in ["free", "available", "busy", "book", "meeting", "slot"]):
            start_dt, end_dt, parsed_text = parse_time_range(text)
            if not start_dt:
                return ChatResponse(
                    response="Please specify a time like 'available today' or 'free tomorrow 2-4pm'",
                    action="clarify_time",
                    timezone=TIMEZONE
                )
            
            slots, events = get_availability(start_dt, end_dt)
            if slots:
                if len(slots) == 1 and slots[0] == start_dt.isoformat():
                    # Specific time slot requested
                    response = f"✅ The time slot {parsed_text} is available!"
                else:
                    # General availability
                    slot_list = "\n".join(f"• {parser.parse(s).strftime('%I:%M %p')}" for s in slots)
                    response = f"🗓️ Available times on {start_dt.strftime('%A, %B %d')}:\n{slot_list}"
                
                if events:
                    event_list = "\n".join(
                        f"• {e['summary']}: {parser.parse(e['start']).strftime('%I:%M %p')}-{parser.parse(e['end']).strftime('%I:%M %p')}"
                        for e in events
                    )
                    response += f"\n\nYour current schedule:\n{event_list}"
                
                return ChatResponse(
                    response=response,
                    action="offer_booking",
                    slots=slots,
                    events=events,
                    start=start_dt.isoformat(),
                    end=end_dt.isoformat(),
                    timezone=TIMEZONE
                )
            else:
                response = f"❌ I'm booked on {start_dt.strftime('%A, %B %d')}"
                if events:
                    event_list = "\n".join(
                        f"• {e['summary']}: {parser.parse(e['start']).strftime('%I:%M %p')}-{parser.parse(e['end']).strftime('%I:%M %p')}"
                        for e in events
                    )
                    response += f"\n\nYour current schedule:\n{event_list}"
                
                return ChatResponse(
                    response=response,
                    action="fully_booked",
                    events=events,
                    timezone=TIMEZONE
                )
        
        # Default response
        return ChatResponse(
            response="I can help with your calendar. Try asking:\n- 'What's my schedule today?'\n- 'Are you free tomorrow at 2pm?'\n- 'Book a meeting Friday at 3pm'",
            action="greeting",
            timezone=TIMEZONE
        )
    
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return ChatResponse(
            response="Sorry, I encountered an error. Please try again.",
            action="error",
            timezone=TIMEZONE
        )

@app.post("/book_appointment", response_model=BookingResponse)
async def book_appointment(event: CalendarEvent, api_key: str = Depends(get_api_key)):
    try:
        start = parser.parse(event.start).astimezone(pytz.timezone(event.timezone))
        end = parser.parse(event.end).astimezone(pytz.timezone(event.timezone))
        
        if start >= end:
            raise ValueError("End time must be after start time")
        if start < datetime.now(pytz.timezone(event.timezone)):
            raise ValueError("Cannot book in the past")
        
        # Check conflicts
        service = get_google_calendar_service()
        events = get_calendar_events(start - timedelta(minutes=30), end + timedelta(minutes=30))
        for e in events:
            e_start = parser.parse(e['start'])
            e_end = parser.parse(e['end'])
            if not (end <= e_start or start >= e_end):
                return BookingResponse(
                    status="error",
                    message=f"❌ Conflicts with: {e['summary']} ({e_start.strftime('%I:%M %p')}-{e_end.strftime('%I:%M %p')})",
                    timezone=event.timezone
                )
        
        # Create event
        event_body = {
            'summary': event.summary,
            'description': event.description,
            'start': {'dateTime': start.isoformat(), 'timeZone': event.timezone},
            'end': {'dateTime': end.isoformat(), 'timeZone': event.timezone},
            'attendees': [{'email': e} for e in (event.attendees or [])],
            'reminders': {'useDefault': True}
        }
        
        created_event = service.events().insert(
            calendarId=CALENDAR_ID,
            body=event_body,
            sendUpdates='all' if event.attendees else 'none'
        ).execute()
        
        return BookingResponse(
            status="success",
            event_id=created_event['id'],
            message=f"✅ Booked '{event.summary}' on {start.strftime('%A, %B %d at %I:%M %p')}",
            timezone=event.timezone
        )
    
    except HttpError as e:
        error = json.loads(e.content.decode()).get('error', {})
        return BookingResponse(
            status="error",
            message=f"❌ Google Calendar error: {error.get('message', 'Unknown error')}",
            timezone=event.timezone
        )
    except Exception as e:
        return BookingResponse(
            status="error",
            message=f"❌ Error: {str(e)}",
            timezone=event.timezone
        )

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)