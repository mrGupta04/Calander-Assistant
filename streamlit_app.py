import streamlit as st
import httpx
from datetime import datetime, timedelta
from dateutil import parser
import pytz
import os
from dotenv import load_dotenv

load_dotenv()

# Configuration
BACKEND_URL = os.getenv("BACKEND_URL", "https://calander-assistant.onrender.com")
API_KEY = os.getenv("API_KEY")
TIMEZONE = os.getenv("TZ", "UTC")

st.set_page_config(
    page_title=" Calendar Assistant",
    page_icon="",
    layout="wide"
)

# Custom CSS
st.markdown("""
<style>
    .stButton button {
        transition: all 0.3s ease;
    }
    .stButton button:hover {
        transform: scale(1.02);
    }
    .event-card {
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 0.5rem;
        background-color: #f0f2f6;
    }
</style>
""", unsafe_allow_html=True)

if "messages" not in st.session_state:
    st.session_state.messages = [{
        "role": "assistant",
        "content": " Hi! I'm your calendar assistant. How can I help you today?"
    }]

# Display chat
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Sidebar
with st.sidebar:
    st.title("Calendar Options")
    
    if st.button(" New Conversation"):
        st.session_state.messages = [{
            "role": "assistant", 
            "content": " Hi! I'm your calendar assistant. How can I help you today?"
        }]
        st.rerun()
    
    st.divider()
    st.subheader("Quick Actions")
    
    if st.button("Today's Schedule"):
        st.session_state.messages.append({"role": "user", "content": "Show my schedule today"})
        st.rerun()
    
    if st.button("Tomorrow's Schedule"):
        st.session_state.messages.append({"role": "user", "content": "Show my schedule tomorrow"})
        st.rerun()
    
    if st.button("Check Availability"):
        st.session_state.messages.append({"role": "user", "content": "What times are available today?"})
        st.rerun()
    
    st.divider()
    st.subheader("Examples")
    st.markdown("""
    Try asking:
    - "What's my schedule today?"
    - "Are you free tomorrow at 2pm?"
    - "Book a meeting Friday 3-4pm"
    """)

# Chat input
if prompt := st.chat_input("How can I help with your calendar?"):
    if not prompt.strip():
        st.warning("Please enter a message")
        st.stop()
        
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        
        try:
            with st.spinner("Checking calendar..."):
                response = httpx.post(
                    f"{BACKEND_URL}/chat",
                    json={
                        "content": prompt,
                        "conversation_history": [
                            m for m in st.session_state.messages[:-1] 
                            if m["role"] in ["user", "assistant"]
                        ]
                    },
                    headers={"x-api-key": API_KEY},
                    timeout=30
                ).json()
            
            message_placeholder.markdown(response["response"])
            
            if response["action"] == "offer_booking" and response.get("slots"):
                st.subheader("Available Slots")
                cols = st.columns(2)
                for i, slot in enumerate(response["slots"]):
                    slot_time = parser.parse(slot).astimezone(pytz.timezone(TIMEZONE))
                    with cols[i % 2]:
                        if st.button(
                            f" {slot_time.strftime('%I:%M %p')}",
                            key=f"slot_{i}",
                            use_container_width=True
                        ):
                            with st.spinner("Booking..."):
                                booking_response = httpx.post(
                                    f"{BACKEND_URL}/book_appointment",
                                    json={
                                        "start": slot,
                                        "end": (slot_time + timedelta(hours=1)).isoformat(),
                                        "summary": "Meeting",
                                        "description": "Booked via Calendar Assistant",
                                        "timezone": TIMEZONE
                                    },
                                    headers={"x-api-key": API_KEY},
                                    timeout=30
                                ).json()
                                
                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": booking_response["message"]
                                })
                                st.rerun()
            
            if response.get("events"):
                st.subheader("Schedule Details")
                for event in response["events"]:
                    with st.container():
                        start = parser.parse(event["start"]).astimezone(pytz.timezone(TIMEZONE))
                        end = parser.parse(event["end"]).astimezone(pytz.timezone(TIMEZONE))
                        st.markdown(f"**{event['summary']}**")
                        st.markdown(f"🕒 {start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}")
                        if event.get("description"):
                            st.caption(event["description"])
                        st.divider()
            
            st.session_state.messages.append({
                "role": "assistant",
                "content": response["response"]
            })
        
        except httpx.ConnectError:
            error = "Couldn't connect to the backend service"
            message_placeholder.error(error)
            st.session_state.messages.append({
                "role": "assistant",
                "content": error
            })
        
        except Exception as e:
            error = f"Error: {str(e)}"
            message_placeholder.error(error)
            st.session_state.messages.append({
                "role": "assistant",
                "content": error
            })
