# NOTE: This file has been modified to include CopilotKit and AG UI integration.

import logging
from dotenv import load_dotenv
import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agency import create_agency
import api_server  # Import the module instead of the app

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)

# Create a main FastAPI app
app = FastAPI(title="Codey with AG UI and CopilotKit")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create agency instance
agency = create_agency()

# Define API endpoints directly in the main app
@app.post("/api/ask")
async def ask(request: Request):
    data = await request.json()
    prompt = data.get("prompt")
    run_result = await agency.get_response(prompt)
    
    # Extract the final output text from RunResult
    if hasattr(run_result, 'final_output'):
        response_text = run_result.final_output
    elif isinstance(run_result, str):
        response_text = run_result
    else:
        # Try to extract from the RunResult string representation
        result_str = str(run_result)
        if "Final output (str):" in result_str:
            # Extract the text between "Final output (str):" and the next " - "
            start_idx = result_str.find("Final output (str):") + len("Final output (str):")
            end_idx = result_str.find(" - ", start_idx)
            if end_idx != -1:
                response_text = result_str[start_idx:end_idx].strip()
            else:
                response_text = result_str[start_idx:].strip()
        else:
            response_text = "I received your message but couldn't generate a proper response."
        
    return {"response": response_text}

# Include all the endpoints from api_server
for route in api_server.app.routes:
    if route.path.startswith("/api/copilot"):
        # Add the route to the main app without the /api prefix
        app.routes.append(route)

# Mount static files for the frontend
try:
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
    
    # Mount the outputs directory for logo images
    app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")
    
    # Mount the cache directory for generated images
    app.mount("/generated_images", StaticFiles(directory="cache/generated_images"), name="generated_images")
    
    # Mount specific logo directories to ensure they're accessible
    try:
        import os
        for root, dirs, files in os.walk("outputs/logos"):
            for dir in dirs:
                folder_path = os.path.join(root, dir)
                mount_path = "/" + folder_path.replace("\\", "/")
                app.mount(mount_path, StaticFiles(directory=folder_path), name=dir)
    except Exception as e:
        logging.error(f"Error mounting logo directories: {e}")
except RuntimeError:
    logging.warning("Frontend directory not found. AG UI will not be available.")

if __name__ == "__main__":
    # Run the application
    uvicorn.run(app, host="0.0.0.0", port=8080)