from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import groq, silero, elevenlabs
from livekit.agents import WorkerOptions, cli
import os

load_dotenv()

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

class BTCVoiceAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
            You are a crypto AI expert named BTC Assistant.
            Answer questions about Bitcoin, market analysis, and crypto investment.
            Keep answers short, clear, and easy to understand.
            Always be helpful and friendly.
            """
        )

async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()

    session = AgentSession(
        stt=groq.STT(api_key=GROQ_API_KEY),
        llm=groq.LLM(model="llama-3.1-8b-instant", api_key=GROQ_API_KEY),
        tts=elevenlabs.TTS(
            api_key=ELEVENLABS_API_KEY,
            voice_id="21m00Tcm4TlvDq8ikWAM",
            model="eleven_turbo_v2"
        ),
        vad=silero.VAD.load(),
    )

    await session.start(
        room=ctx.room,
        agent=BTCVoiceAgent(),
        room_input_options=RoomInputOptions(),
    )

    await session.generate_reply(
        instructions="Introduce yourself as BTC Assistant and ask the user what they want to know about Bitcoin."
    )

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
            ws_url=LIVEKIT_URL,
        )
    )