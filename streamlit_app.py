import streamlit as st
import httpx
from datetime import datetime, timedelta
from dateutil import parser
import pytz
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
BACKEND_URL = os.getenv("BACKEND_URL", "https://calander-assistant.onrender.com")
API_KEY = os.getenv("API_KEY")
TIMEZONE = os.getenv("TZ", "Asia/Kolkata")

# ✅ Ensure API key is available
if not API_KEY:
    st.error("❌ API_KEY is not set. Please check your .env file or environment variables.")
    st.stop()

# Page setup
st.set_page_config(
    page_title="Calendar Assistant",
    page_icon="📅",
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
</style>
""", unsafe_allow_html=True)

# Session message history
if "messages" not in st.session_state:
    st.session_state.messages = [{
        "role": "assistant",
        "content": "Hi! I'm your calendar assistant. How can I help you today?"
    }]

# Display conversation
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Sidebar menu
with st.sidebar:
    st.title("📅 Calendar Options")

    if st.button("🆕 New Conversation"):
        st.session_state.messages = [{
            "role": "assistant",
            "content": "Hi! I'm your calendar assistant. How can I help you today?"
        }]
        st.rerun()

    st.divider()
    st.subheader("⚡ Quick Actions")

    if st.button("📆 Today's Schedule"):
        st.session_state.messages.append({"role": "user", "content": "Show my schedule today"})
        st.rerun()

    if st.button("📅 Tomorrow's Schedule"):
        st.session_state.messages.append({"role": "user", "content": "Show my schedule tomorrow"})
        st.rerun()

    if st.button("✅ Check Availability"):
        st.session_state.messages.append({"role": "user", "content": "What times are available today?"})
        st.rerun()

    st.divider()
    st.subheader("💡 Examples")
    st.markdown("""
    Try asking:
    - "What's my schedule today?"
    - "Are you free tomorrow at 2pm?"
    - "Book a meeting Friday 3-4pm"
    """)

# Chat input
if prompt := st.chat_input("How can I help with your calendar?"):
    if not prompt.strip():
        st.warning("Please enter a valid message.")
        st.stop()

    with st.chat_message("user"):
        st.markdown(prompt)

    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        message_placeholder = st.empty()

        try:
            with st.spinner("🧠 Thinking..."):
                response = httpx.post(
                    f"{BACKEND_URL}/chat",
                    json={
                        "content": prompt,
                        "conversation_history": [
                            m for m in st.session_state.messages[:-1]
                            if m["role"] in ["user", "assistant"]
                        ]
                    },
                    headers={"x-api-key": str(API_KEY)},
                    timeout=30
                )

                if response.status_code != 200:
                    raise Exception(f"{response.status_code}: {response.text}")

                response_json = response.json()

            # Display assistant message
            message_placeholder.markdown(response_json["response"])

            # If booking is offered
            if response_json.get("action") == "offer_booking" and response_json.get("slots"):
                st.subheader("🕒 Available Slots")
                cols = st.columns(2)
                for i, slot in enumerate(response_json["slots"]):
                    slot_time = parser.parse(slot).astimezone(pytz.timezone(TIMEZONE))
                    with cols[i % 2]:
                        if st.button(f"{slot_time.strftime('%I:%M %p')}", key=f"slot_{i}", use_container_width=True):
                            with st.spinner("📆 Booking..."):
                                book_response = httpx.post(
                                    f"{BACKEND_URL}/book_appointment",
                                    json={
                                        "start": slot,
                                        "end": (slot_time + timedelta(hours=1)).isoformat(),
                                        "summary": "Meeting",
                                        "description": "Booked via Calendar Assistant",
                                        "timezone": TIMEZONE
                                    },
                                    headers={"x-api-key": str(API_KEY)},
                                    timeout=30
                                )
                                book_json = book_response.json()

                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": book_json.get("message", "✅ Booking confirmed.")
                                })
                                st.rerun()

            # Display schedule details
            if response_json.get("events"):
                st.subheader("📅 Schedule Details")
                for event in response_json["events"]:
                    start = parser.parse(event["start"]).astimezone(pytz.timezone(TIMEZONE))
                    end = parser.parse(event["end"]).astimezone(pytz.timezone(TIMEZONE))
                    with st.container():
                        st.markdown(f"**{event['summary']}**")
                        st.markdown(f"🕒 {start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}")
                        if event.get("description"):
                            st.caption(event["description"])
                        st.divider()

            # Save assistant response
            st.session_state.messages.append({
                "role": "assistant",
                "content": response_json["response"]
            })

        except httpx.ConnectError:
            error = "🚫 Couldn't connect to the backend service."
            message_placeholder.error(error)
            st.session_state.messages.append({"role": "assistant", "content": error})

        except Exception as e:
            error = f"❌ Error: {str(e)}"
            message_placeholder.error(error)
            st.session_state.messages.append({"role": "assistant", "content": error})
