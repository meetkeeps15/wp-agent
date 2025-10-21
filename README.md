# BrandMeNow Truva

A full‑stack, local‑first toolkit for modern brand design. It combines a FastAPI backend and a lightweight frontend to help you ideate and preview brand assets such as logos, color palettes, and supporting visuals — all inside an intuitive chat‑style interface.

## Features
- Interactive chat UI to drive creative tasks and receive visual previews
- Logo generation workflow with editable iterations
- Automatic color palette creation with downloadable swatches
- Static outputs served under `/outputs` for easy sharing
- Docker support for reproducible deployments

## Installation
1. Prerequisites: Python 3.10+, Git, and optionally Docker.
2. Clone the project:
   git clone <your-repo-url>
   cd aaas-truva-main
3. Set up a virtual environment and install dependencies:
   python -m venv .venv
   .venv\\Scripts\\activate
   pip install -r requirements.txt
4. Configure environment variables: copy `.env.template` to `.env` and set values as needed (see below).

### Key environment variables
- FAL_KEY: required for remote logo generation via fal.ai
- OPENAI_API_KEY (optional): used by certain analysis tools
- HIGHLEVEL_ACCESS_TOKEN / HIGHLEVEL_LOCATION_ID (optional): used by integration helpers

## Running the app
- Local (FastAPI):
  python main.py
  Open http://127.0.0.1:8080/

- Uvicorn (dev hot‑reload):
  python -m uvicorn main:app --host 127.0.0.1 --port 8080 --reload

- Docker Compose:
  docker compose up --build -d
  Open http://127.0.0.1:8080/

The backend mounts `/outputs` and `/generated_images` so palette swatches and logo assets are available to the frontend.

## Usage
- Open the web UI and start a new conversation.
- Ask for a logo, palette, or visual concept. The assistant will generate previews and save assets under `outputs/`.
- Swatches appear in `outputs/palettes/<timestamp>/`, logos in `outputs/logos/<timestamp>/`.
- Use the “Download” link below each image to save a copy. If remote logo editing is enabled, you can iterate on the latest logo.

## Contribution Guidelines
- Branching: create feature branches from `main` and open pull requests for review.
- Code style: keep changes small and well‑documented; include clear commit messages.
- Testing: validate UX in the browser and ensure generated assets load correctly from `/outputs`.
- Issues: include steps to reproduce, environment info, and screenshots when relevant.

## License
This project is licensed under the MIT License. See the `LICENSE` file for details.
