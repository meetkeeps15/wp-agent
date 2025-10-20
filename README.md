# Agency Swarm GitHub Template

A production-ready template for deploying [Agency Swarm](https://github.com/VRSEN/agency-swarm) agencies with Docker containerization and automated deployment to the [Agencii](https://agencii.ai/) cloud platform.

**🌐 [Agencii](https://agencii.ai/)** - The official cloud platform for Agency Swarm deployments  
**🔗 [GitHub App](https://github.com/apps/agencii)** - Automated deployment integration

---

## 🚀 Quick Start

### 1. Use This Template

Click **"Use this template"** to create your own repository, or:

```bash
git clone https://github.com/your-username/agency-github-template.git
cd agency-github-template
```

> **🌐 For Production**: Sign up at [agencii.ai](https://agencii.ai/) and use this template for automated cloud deployment

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Set Up Environment Variables

Create a `.env` file in the root directory:

```bash
# Required
OPENAI_API_KEY=your_openai_api_key_here

# Optional - Model selection (overrides defaults in tools and agents)
# Examples: gpt-4o, gpt-4o-mini, gpt-4.1
OPENAI_MODEL=gpt-4o

# Optional - Add any additional API keys your agents need
# EXAMPLE_API_KEY=your_api_key_here
```

### 4. Test the Example Agency

```bash
python agency.py
```

This runs the example agency in terminal mode for testing.

> **💡 Pro Tip**: For creating your own agency, open this template in [Cursor IDE](https://cursor.sh/) and use the AI assistant with the `.cursor/rules/workflow.mdc` file for automated agency creation!

---

## 🏗️ Project Structure

```
agency-github-template/
├── agency.py                 # Main entry point
├── requirements.txt          # Python dependencies
├── Dockerfile               # Container configuration
├── .env                     # Environment variables (create this)
├── example_agent/           # Your agency folder
    ├── __init__.py
    ├── example_agent.py
    ├── instructions.md
    └── tools/
        └── ExampleTool.py
├── example_agent2/
├── agency_manifesto.md  # Shared instructions
├── requirements.txt
├── .env
└──...
```

---

## 🔧 Creating Your Own Agency

### 🤖 **AI-Assisted Agency Creation with Cursor**

This template includes **AI-powered agency creation** using Cursor IDE:

1. **Open this project in Cursor IDE**

2. **Use the AI Assistant** to create your agency by referencing:
   ```
   📁 .cursor/rules/workflow.mdc
   ```
3. **Simply ask the AI:**

   > "Create a new agency using the .cursor workflow"

   The AI will guide you through the complete 7-step process:

   - ✅ PRD Creation
   - ✅ Folder Structure Setup
   - ✅ Tool Development
   - ✅ Agent Creation
   - ✅ Agency Configuration
   - ✅ Testing & Validation
   - ✅ Iteration & Refinement

### 📋 **What the AI Will Do For You**

The AI assistant will automatically:

- Create proper folder structures
- Generate agent classes and instructions
- Build custom tools with full functionality
- Set up communication flows
- Create the main agency file
- Test everything to ensure it works

### 🚀 **Manual Alternative (Advanced Users)**

If you prefer manual setup, replace the `ExampleAgency/` folder with your own agency structure following the Agency Swarm conventions.

### Agency Structure Requirements

Your agency must follow this structure:

- **Agency Folder**: Contains all agents and manifesto
- **Agent Folders**: Each agent has its own folder with:
  - `AgentName.py` - Agent class definition
  - `instructions.md` - Agent-specific instructions
  - `tools/` - Folder containing agent tools
- **agency_manifesto.md** - Shared instructions for all agents

---

## 🚀 Production Deployment with Agencii

### **🌐 Deploy to Agencii Cloud Platform**

For production deployment, use the [Agencii](https://agencii.ai/) platform:

#### **Step 1: Create Account & Use Template**

1. **Sign up** at [agencii.ai](https://agencii.ai/)
2. **Use this template** to create your repository
3. **Develop your agency** using Cursor IDE with `.cursor` workflow

#### **Step 2: Install GitHub App**

1. **Install** the [Agencii GitHub App](https://github.com/apps/agencii)
2. **Grant permissions** to your repository
3. **Configure** environment variables in Agencii dashboard

#### **Step 3: Deploy**

1. **Push to main branch** - Agencii automatically detects and deploys
2. **Monitor deployment** in your Agencii dashboard
3. **Access your live agency** via provided endpoints

### **🔄 Automatic Deployments**

- **Auto-deploy** on every push to `main` branch
- **Zero-downtime** deployments with rollback capability
- **Environment management** through Agencii dashboard

---

## 🔨 Development Workflow

### **🎯 Recommended: AI-Assisted Development**

1. **Open Cursor IDE** with this template
2. **Ask the AI**: _"Create a new agency using the .cursor workflow"_
3. **Follow the guided process** - the AI handles everything automatically
4. **Test your agency**: `python agency.py`
5. **Deploy to production**: Install [Agencii GitHub App](https://github.com/apps/agencii) and push to main

### **⚙️ Manual Development (Advanced)**

If you prefer hands-on development:

1. **Create Tools**: Build agent tools in `tools/` folders
2. **Configure Agents**: Write `instructions.md` and agent classes
3. **Test Locally**: Run `python agency.py`
4. **Deploy**: Push to your preferred platform

The `.cursor/rules/workflow.mdc` file contains the complete development specifications for manual implementation.

---

## 📚 Key Features

- **🌐 Agencii Cloud Deploy**: One-click deployment to [Agencii platform](https://agencii.ai/)
- **🤖 AI-Assisted Creation**: Built-in Cursor IDE workflow for automated agency development
- **🔄 Auto-Deploy**: Automatic deployment on push to main branch
- **🚀 Ready-to-Deploy**: Dockerfile and requirements included
- **🔧 Modular Structure**: Easy to customize and extend
- **🛠️ Example Implementation**: Complete working example
- **📦 Container Ready**: Docker configuration for any platform
- **🔒 Environment Management**: Secure API key handling via Agencii dashboard
- **🧪 Local Testing**: Terminal demo for development
- **📋 Guided Workflow**: 7-step process with AI assistance

---

## 📖 Learn More

- **[Agency Swarm Documentation](https://agency-swarm.ai/)**
- **[Agency Swarm GitHub](https://github.com/VRSEN/agency-swarm)**

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## ⚡ Quick Tips

- **Start Small**: Begin with 1-2 agents and expand
- **Test Tools**: Each tool should work independently
- **Clear Instructions**: Write detailed agent instructions
- **Environment Setup**: Always use `.env` for API keys
- **Documentation**: Update instructions as you develop

---

**Ready to build your AI agency?** 🤖✨

### 🌐 **Production Route (Recommended)**

1. **Sign up** at [agencii.ai](https://agencii.ai/)
2. **Use this template** to create your repository
3. **Install** [Agencii GitHub App](https://github.com/apps/agencii)
4. **Push to main** → Automatic deployment!

### 🛠️ **Development Route**

Open this template in **Cursor IDE** and ask the AI to create your agency using the `.cursor` workflow. The AI will handle everything from setup to testing automatically!

For manual development, replace the `ExampleAgency` with your own implementation and start deploying intelligent agent systems!

---

# AG UI Protocol Integration Documentation

## Overview

Codey — Wizard Designer is an AI-powered branding assistant. The project integrates a streaming chat UI (AG UI Protocol) with a FastAPI backend and an Agency (via `create_agency()`) to deliver step-by-step branding assistance: naming, color palettes, logos, product suggestions, and scheduling.

## Architecture

- Backend: FastAPI app (`main.py`, `api_server.py`) with CORS enabled.
- Agency: Created in `agency.py`, consumed by endpoints.
- Streaming: Server-Sent Events (SSE) for chunked responses; optional WebSocket.
- Frontend: Two UIs
  - Main UI (`frontend/index.html`, `frontend/app.js`)
  - Minimal AG UI streaming page (`frontend/agui-chat.html`)
- Static mounts: Frontend assets, generated images (`outputs`), caches (`cache/generated_images`).

## Backend Endpoints

- POST `/api/copilot/chat` (non-streaming)
  - Input: `{ messages: Array<{ role: 'user'|'assistant'|'system', content: string }> }`
  - Behavior: Extracts latest user message, calls `agency.get_response()`, normalizes to plain text, returns `{ id, response, done }`.

- POST `/api/copilot/chat/stream` (SSE streaming)
  - Input: same `messages` format.
  - Output: `text/event-stream`. Frames are emitted as:
    - `data: {"id":"chunk-N","chunk":"...","done":false|true}` followed by `\n\n`.
  - Client accumulates `chunk` until `done` is true.

- POST `/api/ask` (fallback)
  - Input: `{ prompt: string }`, returns `{ response: string }`.

- WS `/api/copilot/ws` (optional WebSocket)
  - Receives `{ id, message }` and responds with `{ id, response, done }`.

Notes
- All endpoints prefer `response.final_output` if present; otherwise, cast to `str`.
- CORS is enabled in both `main.py` and `api_server.py` to allow browser access.

## Frontend UIs

- `frontend/index.html` + `frontend/app.js` (main UI)
  - Landing card with featured chip: seeds message "I want to build my brand".
  - Carousel suggestions per agent selection.
  - Sends messages to `/api/copilot/chat/stream` and renders streamed content.

- `frontend/agui-chat.html` (AG UI streaming demo)
  - Minimal chat with textarea and send button.
  - Maintains `history` array of messages in the same format.
  - On send: posts to `/api/copilot/chat/stream`, reads SSE frames, renders a transient assistant row until `done`, then finalizes with elapsed time badge.
  - Falls back to `/api/ask` if streaming fails.

## Message Contract

- Client → Server body for chat/stream:
  - `messages: [{ role: 'user'|'assistant'|'system', content: string }, ...]`
- Server → Client (non-stream): `{ id: string, response: string, done: true }`
- Server → Client (SSE frames): `data: {"id":"chunk-N","chunk":"...","done":false|true}\n\n`
- Server → Client (WebSocket): `{ id: string, response: string, done: true }`

## Static Mounts & Assets

- `app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")`
- `app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")`
- `app.mount("/generated_images", StaticFiles(directory="cache/generated_images"), name="generated_images")`
- Additional mounts for `outputs/logos/*` to serve generated logos directly.

## Wizard Designer Tools (Highlights)

- Naming: `wizard_designer/tools/NameSelectorFusionTool.py` (OpenAI-powered naming with robust fallback).
- Color palettes: `wizard_designer/tools/ColorPaletteTool.py` (HEX and usage roles).
- Logos: `wizard_designer/tools/LogoGenerator.py`
  - FAL endpoints:
    - `POST https://fal.run/fal-ai/nano-banana` for single/multiple logo generation
    - `POST https://fal.run/fal-ai/nano-banana/edit` for image editing
- Label editing: `wizard_designer/tools/DirectLabelOnRecipientTool.py` (FAL edit).
- Products: `ProductDataRetriever.py`, Domain checking, Profit calculator, Scheduling, Social media analysis.

## Environment Variables

- `OPENAI_API_KEY` (required)
- `OPENAI_MODEL` (optional, defaults like `gpt-4o` in tools)

## Local Development

1) Install dependencies:
```
pip install -r requirements.txt
```

2) Set environment variables (e.g., `.env`):
```
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o
```

3) Run the server:
```
python -m uvicorn main:app --host 127.0.0.1 --port 8011 --reload
```

4) Open in the browser:
- Main UI: `http://127.0.0.1:8011/`
- AG UI streaming demo: `http://127.0.0.1:8011/agui-chat.html`

## Docker

- `Dockerfile` based on Python 3.13-slim.
- `docker-compose.yml` exposes port 8080 and runs `python -u main.py`.

## Troubleshooting

- Port conflicts: If 8080 is in use, run uvicorn on another port (e.g., 8011).
- Streaming fallback: If `/api/copilot/chat/stream` fails, the UI falls back to `/api/ask`.
- Syntax errors: We fixed a stray parenthesis in `frontend/app.js` (markdown table rendering) that caused `Unexpected token )`. If you see similar errors, validate JS with `node --check frontend/app.js`.
- WebSocket: Present but disabled in `app.js` (SSE is the primary streaming transport).

## Change History (Key Actions)

- Implemented Copilot endpoints (`/api/copilot/chat`, `/api/copilot/chat/stream`, `/api/ask`, optional `/api/copilot/ws`).
- Built `frontend/agui-chat.html` streaming client with SSE reader and fallback.
- Integrated main UI (`index.html`, `app.js`) with landing featured chip and suggestions carousel.
- Mounted static assets (`frontend`, `outputs`, `cache/generated_images`, plus per-logo folders).
- Reverted experimental “Brand Idea” feature from the main UI and restored the original landing behavior.
- Fixed JavaScript syntax issue in `app.js` related to markdown table rendering.

## Next Steps

- Add retry/backoff around FAL POST calls (`LogoGenerator.py`, `DirectLabelOnRecipientTool.py`) to handle transient errors.
- Consider toggling between SSE and WebSocket based on environment.
- Add a visible link to `agui-chat.html` from the main UI for quick access.
- Write endpoint tests and streaming client tests.
