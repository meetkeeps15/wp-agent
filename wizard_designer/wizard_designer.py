import os
from agency_swarm import Agent
from agency_swarm.agent.file_manager import AgentFileManager

# --- UTF-8-safe patch for instructions.md ---
def read_instructions_utf8(self):
    # Check all possible path attributes
    possible_attrs = [
        "instructions_path",
        "instructions_file_path",
        "instructions_file",
        "_instructions_path",
        "_instructions_file_path",
    ]

    path = None
    for attr in possible_attrs:
        if hasattr(self, attr):
            path = getattr(self, attr)
            break

    if not path:
        # fallback: default to ./instructions.md
        import os
        path = getattr(self.agent, "instructions", "./instructions.md")
        if not os.path.isabs(path):
            import os
            path = os.path.join(os.getcwd(), path)

    # read safely with UTF-8 encoding
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        self.agent.instructions = f.read()

# Apply the UTF-8 patch
AgentFileManager.read_instructions = read_instructions_utf8

# --- Agent definition ---
from agents import ModelSettings
from openai.types.shared import Reasoning

wizard_designer = Agent(
    name="WizardDesigner",
    description="Guides users from brand name to mockup and booking.",
    instructions="./instructions.md",
    tools_folder="./tools",
    model=os.getenv("OPENAI_MODEL", "gpt-4o"),
)
