# 🗓️ Conversational Calendar Booking Assistant

This project is a conversational AI agent that helps users **book appointments on their Google Calendar** through a natural, interactive chat interface. Built with **FastAPI**, **LangGraph**, and **Streamlit**, this assistant understands user intent, checks availability, suggests time slots, and confirms meetings — all in one seamless flow.

🚀 **Live Demo**: [Click to try the app](https://mrgupta04-calander-assistant-streamlit-app-fva5e5.streamlit.app/)

---

## 🔧 Tech Stack

- **Backend**: Python with [FastAPI](https://fastapi.tiangolo.com/)
- **Agent Framework**: [LangGraph](https://www.langgraph.dev/)
- **Frontend**: [Streamlit](https://streamlit.io/) for the chat interface
- **Calendar Integration**: Google Calendar API

---

## 💡 Features

- Accepts user input in **natural language**
- Guides conversations toward **booking an appointment**
- **Checks real-time availability** from Google Calendar
- **Books and confirms** time slots instantly
- Handles **ambiguous or vague queries** with follow-up questions

---

## 💬 Example Conversations

- “Hey, I want to schedule a call for tomorrow afternoon.”
- “Do you have any free time this Friday?”
- “Book a meeting between 3–5 PM next week.”
- “Can we schedule something early next week?”
- “I’m only free after 4 PM tomorrow.”

---

## 🧠 How It Works

1. **Streamlit Interface**: Users interact with the AI assistant through a simple and friendly chat UI.
2. **LangGraph Agent**: Interprets intent and manages the dialog flow.
3. **FastAPI Backend**: Handles API requests and integrates with Google Calendar.
4. **Calendar Integration**: Verifies availability and books events in real-time.

📎 Live Submission

🟢 Test the Assistant Live:
https://mrgupta04-calander-assistant-streamlit-app-fva5e5.streamlit.app/
