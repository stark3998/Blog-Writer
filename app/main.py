"""Blog Writer Agent — Entrypoint.

Wraps the BlogWriterAgent with the Foundry hosting adapter
and starts the HTTP server for the Responses API.
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file (local development)
load_dotenv()

from azure.ai.agentserver.agentframework import from_agent_framework
from app.agent import BlogWriterAgent

agent = BlogWriterAgent()
app = from_agent_framework(agent)

if __name__ == "__main__":
    app.run()
