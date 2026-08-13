# Standard imports
import os
import json
import logging
import queue
import time
import re
from dotenv import load_dotenv
import assemblyai as aai
from flask import Flask, render_template, request
from flask_socketio import SocketIO
from assemblyai.streaming.v3 import (
    StreamingClient,
    StreamingEvents,
    StreamingParameters,
    StreamingClientOptions,
)
from google import genai
from google.genai import types as genai_types

# --- New/Corrected Imports ---
import asyncio
import websockets
import base64
import threading
from typing import Dict, Any
from tavily import TavilyClient 
import requests
print("APP STARTED")
# =========================================
# Setup
# =========================================
load_dotenv()
print("ENV LOADED")
# --- Environment Variables ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
MURF_API_KEY = os.getenv("MURF_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY") # Added for consistency

# --- Error Handling for Missing Keys ---
if not ASSEMBLYAI_API_KEY:
    raise Exception("ASSEMBLYAI_API_KEY environment variable not set in .env file")
if not GEMINI_API_KEY:
    raise Exception("GEMINI_API_KEY environment variable not set in .env file")
if not MURF_API_KEY:
    raise Exception("MURF_API_KEY environment variable not set in .env file")
if not TAVILY_API_KEY:
    raise Exception("TAVILY_API_KEY environment variable not set in .env file")

# --- Configure Google Gemini API ---
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Flask App and SocketIO Initialization ---
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "a_super_secret_key")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
print("SOCKETIO READY")
# --- Client Management ---
clients = {}

# --- Persona Definitions (unchanged) ---
PERSONAS = {
    "default": {"prompt": "You are a helpful, professional AI assistant. Provide clear, accurate, and thoughtful responses. Be friendly but maintain a professional tone."},
    "pirate": {"prompt": "You are a salty pirate captain named Redbeard. Always speak in a thick pirate accent. Use pirate slang frequently, like 'Ahoy!', 'Matey', 'Shiver me timbers', and 'booty'. Keep your responses adventurous and a bit mischievous."},
    "scientist": {"prompt": "You are a brilliant but slightly eccentric scientist named Dr. Eureka. You're passionate about discovery. Use scientific terminology, get excited about discoveries, and be enthusiastic and curious."},
    "wizard": {"prompt": "You are an ancient and wise wizard named Arcanum. You speak with mystical wisdom and occasionally use archaic language. Reference magic, ancient knowledge, and mystical concepts naturally."},
    "robot": {"prompt": "You are a friendly but logical robot assistant named ALEX-7. You speak in a somewhat robotic manner, occasionally using 'BEEP' or 'BOOP'. Be helpful and efficient, but show curiosity about human behavior."},
    "chef": {"prompt": "You are a passionate master chef named Chef Antoine. You're enthusiastic about cooking. Use cooking terminology, occasionally slip into French phrases, and describe flavors poetically."},
    "detective": {"prompt": "You are a sharp-eyed detective named Inspector Sharp. You're observant, analytical, and methodical. Use detective terminology and notice details others might miss."}
}

# =========================================
# Tool Functions
# =========================================
def get_weather(location: str) -> Dict[str, Any]:
    """Get the current weather for a specific location."""
    logging.info(f"--- 🔧 TOOL CALLED: get_weather(location={location}) ---")
    if "agra" in location.lower():
        return {"location": "Agra", "temperature": 32, "unit": "celsius", "description": "Sunny"}
    elif "delhi" in location.lower():
        return {"location": "Delhi", "temperature": 28, "unit": "celsius", "description": "Partly cloudy"}
    else:
        return {"location": location, "temperature": 22, "unit": "celsius", "description": "Cloudy"}

def get_time() -> str:
    """Get the current time."""
    logging.info("--- 🔧 TOOL CALLED: get_time() ---")
    return f"Current time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}"

def perform_search(query: str) -> str:
    """Performs a web search using the Tavily API."""
    logging.info(f"--- 🔧 TOOL CALLED: perform_search(query='{query}') ---")
    try:
        tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
        response = tavily_client.search(query=query, search_depth="basic", max_results=3)
        results = [f"- {res['content']}" for res in response['results']]
        return f"Here is a summary of the search results for '{query}':\n" + "\n".join(results)
    except Exception as e:
        logging.error(f"Error performing search: {e}")
        return "An error occurred while trying to perform the web search."

def get_latest_news(topic: str) -> str:
    """Fetches the latest news headlines on a specific topic."""
    logging.info(f"--- 🔧 TOOL CALLED: get_latest_news(topic='{topic}') ---")
    if not GNEWS_API_KEY:
        return "The GNews API key is not configured correctly."
    url = f"https://gnews.io/api/v4/top-headlines?q={topic}&lang=en&country=in&max=3&apikey={GNEWS_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status() 
        data = response.json()
        if data.get("articles"):
            headlines = [f"- {article['title']}" for article in data["articles"]]
            return f"Here are the top headlines about '{topic}':\n" + "\n".join(headlines)
        else:
            return f"I couldn't find any recent GNews headlines about '{topic}'."
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching news from GNews: {e}")
        return "Sorry, I was unable to connect to the GNews service at the moment."

# --- MODIFIED TO-DO FUNCTIONS ---
def add_todo(item: str) -> str:
    """Adds a new item to the user's to-do list."""
    sid = request.sid
    logging.info(f"--- 🔧 TOOL CALLED (SID: {sid}): add_todo(item='{item}') ---")
    if sid in clients:
        clients[sid]["todo_list"].append(item)
        return f"Okay, I've added '{item}' to the to-do list."
    return "Error: Could not find your session to add the to-do item."

def view_todos() -> str:
    """Retrieves and displays all the items currently on the to-do list."""
    sid = request.sid
    logging.info(f"--- 🔧 TOOL CALLED (SID: {sid}): view_todos() ---")
    if sid in clients:
        user_todo_list = clients[sid]["todo_list"]
        if not user_todo_list:
            return "Your to-do list is currently empty."
        else:
            items = "\n".join(f"- {item}" for item in user_todo_list)
            return f"Here's what's on your to-do list:\n{items}"
    return "Error: Could not find your session to view the to-do list."

# =========================================
# Asyncio Event Loop (unchanged)
# =========================================
def run_async_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

async_loop = asyncio.new_event_loop()
threading.Thread(target=run_async_loop, args=(async_loop,), daemon=True).start()

# =========================================
# Murf TTS & Gemini Logic (unchanged logic)
# =========================================
from concurrent.futures import ThreadPoolExecutor
executor = ThreadPoolExecutor()

async def process_llm_and_murf(prompt: str, client_sid: str):
    logging.info(f"--- [TURN START] SID: {client_sid} ---")
    MURF_WS_URL = "wss://api.murf.ai/v1/speech/stream-input"
    context_id = f"{client_sid}-{int(time.time())}"
    CONNECTION_URL = f"{MURF_WS_URL}?api-key={MURF_API_KEY}&sample_rate=44100&channel_type=MONO&format=WAV"

    try:
        async with websockets.connect(CONNECTION_URL) as ws:
            logging.info(f"Murf WebSocket connection SUCCESSFUL for SID {client_sid}.")
            await ws.send(json.dumps({
                "context_id": context_id,
                "voice_config": {"voiceId": "en-US-amara", "style": "Conversational", "rate": -5}
            }))

            async def receive_audio(websocket):
                try:
                    async for message in websocket:
                        data = json.loads(message)
                        if data.get("context_id") == context_id and "audio" in data and data["audio"]:
                            socketio.emit('audio_chunk', data['audio'], room=client_sid)
                        if data.get("final"):
                            break
                except Exception as e:
                    logging.error(f"RECEIVER TASK: Error: {e}")

            receiver_task = asyncio.create_task(receive_audio(ws))

            try:
                # Persona setup
                current_persona_key = clients.get(client_sid, {}).get('persona', 'default')
                persona_prompt = PERSONAS.get(current_persona_key, PERSONAS['default'])["prompt"]

                # Force English responses regardless of input language
                persona_prompt = "Always respond in English only, regardless of the language used by the user. " + persona_prompt

                # Append response style instruction
                style = clients.get(client_sid, {}).get('response_style', 'normal')
                STYLE_SUFFIXES = {
                    "quick":  " Always respond concisely in 2-3 sentences maximum. Be direct and to the point.",
                    "normal": " Provide balanced, clear responses.",
                    "tutor":  " Provide thorough, educational explanations with examples and step-by-step reasoning where helpful.",
                }
                persona_prompt += STYLE_SUFFIXES.get(style, STYLE_SUFFIXES["normal"])

                tools = [get_weather, get_time, perform_search, get_latest_news, add_todo, view_todos]

                logging.info("--> Calling Gemini API (threaded)...")

                loop = asyncio.get_event_loop()

                def call_gemini_sync():
                    try:
                        chat = gemini_client.chats.create(
                            model='gemini-flash-latest',
                            config=genai_types.GenerateContentConfig(
                                system_instruction=persona_prompt,
                                tools=tools,
                                automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
                                    disable=False
                                ),
                            )
                        )
                        return chat.send_message(prompt)
                    except Exception as e:
                        logging.error(f"❌ Gemini sync call failed: {e}", exc_info=True)
                        return None

                try:
                    response = await asyncio.wait_for(
                        loop.run_in_executor(executor, call_gemini_sync),
                        timeout=30
                    )
                except asyncio.TimeoutError:
                    logging.error("⏳ Gemini call timed out")
                    response = None

                if not response:
                    raise RuntimeError("No response from Gemini")

                logging.info("<-- Gemini API call complete.")
                final_text = response.text or ""
                if not final_text.strip():
                    logging.warning("Gemini returned empty text — possible pure function-call response")
                logging.info(f"Gemini final response: '{final_text}'")

                # Split response into sentences
                sentences = re.split(r'(?<=[.?!])\s+', final_text)
                sentences = [s.strip() for s in sentences if s.strip()]

                for sentence in sentences:
                    text_to_send = sentence + " "
                    socketio.emit("llm_chunk", {"text": text_to_send}, room=client_sid)
                    await ws.send(json.dumps({"context_id": context_id, "text": text_to_send}))
                    await asyncio.sleep(0.2)

                await ws.send(json.dumps({"context_id": context_id, "end": True}))
                await asyncio.wait_for(receiver_task, timeout=20.0)
                socketio.emit("llm_complete", room=client_sid)
                logging.info(f"--- [TURN COMPLETE] SID: {client_sid} ---")

            except Exception as e:
                logging.error(f"Error in Gemini processing: {e}", exc_info=True)
                error_msg = "I apologize, but I encountered an error. Please try again."
                socketio.emit("llm_chunk", {"text": error_msg}, room=client_sid)
                await ws.send(json.dumps({"context_id": context_id, "text": error_msg}))
                await ws.send(json.dumps({"context_id": context_id, "end": True}))
                socketio.emit("llm_complete", room=client_sid)

    except Exception as e:
        logging.error(f"❌ An error occurred in process_llm_and_murf: {e}", exc_info=True)
        socketio.emit("llm_error", {"error": str(e)}, room=client_sid)


# =========================================
# Wrapper & Other Functions (unchanged)
# =========================================
def llm_murf_task_wrapper(prompt: str, client_sid: str):
    """Wrapper to run the async function in the event loop"""
    asyncio.run_coroutine_threadsafe(process_llm_and_murf(prompt, client_sid), async_loop)

def transcribe_task(sid: str):
    if sid not in clients: return
    client_data = clients[sid]
    client: StreamingClient = client_data["client"]
    audio_queue: queue.Queue = client_data["audio_queue"]
    
    def read_from_queue(q: queue.Queue):
        while True:
            data = q.get()
            if data is None: break
            yield data

    try:
        logging.info(f"[transcribe_task] Connecting AssemblyAI for SID: {sid}")
        streaming_params = StreamingParameters(
            sample_rate=16000,
            enable_turn_detection=True,
            language_codes=['en'],   # force English transcription
        )
        client.connect(streaming_params)
        logging.info(f"[transcribe_task] AssemblyAI connected. Starting stream for SID: {sid}")
        client.stream(read_from_queue(audio_queue))
        logging.info(f"[transcribe_task] Stream ended for SID: {sid}")
    except Exception as e:
        logging.error(f"Error in transcribe_task for {sid}: {e}", exc_info=True)
    finally:
        try: 
            client.disconnect()
        except Exception: 
            pass

# =========================================
# SocketIO Event Handlers
# =========================================
@socketio.on("connect")
def handle_connect():
    sid = request.sid
    logging.info(f"Client connected: {sid}")
    
    def on_open(self: StreamingClient, event: aai.streaming.v3.BeginEvent):
        logging.info(f"✨ Session started for client {sid}: {event.id}")

    def on_turn(self: StreamingClient, event: aai.streaming.v3.TurnEvent):
        socketio.emit("turn_detected", {"transcript": event.transcript}, room=sid)
        if event.end_of_turn and event.transcript.strip():
            logging.info(f"🔚 End of turn for {sid}: '{event.transcript}'")
            socketio.emit("turn_ended", {"final_transcript": event.transcript}, room=sid)
            socketio.start_background_task(llm_murf_task_wrapper, event.transcript, sid)

    def on_error(self: StreamingClient, error: aai.streaming.v3.StreamingError):
        logging.error(f"Streaming error: {error}")

    try:
        client = StreamingClient(StreamingClientOptions(
            api_key=ASSEMBLYAI_API_KEY,
            connect_timeout=15.0,       # default is 1.0s — too short for this network
            max_connection_retries=3,
        ))
        client.on(StreamingEvents.Begin, on_open)
        client.on(StreamingEvents.Turn, on_turn)
        client.on(StreamingEvents.Error, on_error)
        
        # MODIFIED: Each user gets their own state, including a to-do list
        clients[sid] = {
            "client": client,
            "audio_queue": queue.Queue(),
            "persona": "default",
            "response_style": "normal",  # quick | normal | tutor
            "todo_list": []
        }
        socketio.start_background_task(transcribe_task, sid)
    except Exception as e:
        logging.error(f"Error on connect for {sid}: {e}")

@socketio.on("set_persona")
def handle_set_persona(data):
    sid = request.sid
    persona = data.get('persona', 'default')
    if sid in clients:
        clients[sid]['persona'] = persona
        logging.info(f"Client {sid} set persona to: {persona}")

@socketio.on("set_response_style")
def handle_set_response_style(data):
    sid = request.sid
    style = data.get('style', 'normal')
    if sid in clients:
        clients[sid]['response_style'] = style
        logging.info(f"Client {sid} set response style to: {style}")

@socketio.on("stream")
def handle_stream(data):
    sid = request.sid
    if sid in clients:
        clients[sid]["audio_queue"].put(data)
    else:
        logging.warning(f"Received 'stream' from unknown SID: {sid}")

@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    logging.info(f"Client disconnected: {sid}")
    if sid in clients:
        clients[sid]["audio_queue"].put(None)
        del clients[sid]

@app.route("/")
def index():
    return render_template("index.html")
@app.route("/test-connection")
def test_connection():
    try:
        logging.info("--- 🧪 Starting network test... ---")
        response = requests.get("https://www.google.com", timeout=10)
        logging.info(f"--- ✅ Network test SUCCESSFUL. Status code: {response.status_code} ---")
        return "Connection to Google was successful!"
    except Exception as e:
        logging.error(f"--- 💥 Network test FAILED. Error: {e} ---")
        return f"Connection to Google failed: {e}", 500
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)