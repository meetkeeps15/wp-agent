from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from agency import create_agency
from agentic_chat import compile_graph, run_agentic_chat
from agent_tools import get_default_tools
import json
import asyncio

app = FastAPI()
agency = create_agency()
agent_graph = compile_graph()

# Allow AG UI and CopilotKit to call this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or specific origins like "http://localhost:3000"
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/ask")
async def ask(request: Request):
    data = await request.json()
    prompt = data.get("prompt")
    response = await agency.get_response(prompt)
    return {"response": response}

# CopilotKit endpoints
@app.post("/api/copilot/chat")
async def copilot_chat(request: Request):
    """Handle CopilotKit chat requests"""
    data = await request.json()
    messages = data.get("messages", [])
    
    # Extract the latest user message
    user_message = next((msg["content"] for msg in reversed(messages) 
                         if msg["role"] == "user"), "")
    
    if not user_message:
        return {"response": "No user message found"}
    
    # Prefer WizardAgency for domain-specific responses
    response = await agency.get_response(user_message)
    # Normalize to plain text if Agency returns a structured object
    try:
        if hasattr(response, 'final_output') and isinstance(response.final_output, str):
            response_text = response.final_output
        elif isinstance(response, str):
            response_text = response
        else:
            response_text = str(response)
    except Exception:
        response_text = str(response)
    
    return {
        "id": "response-" + str(len(messages)),
        "response": response_text,
        "done": True
    }

@app.post("/api/copilot/chat/stream")
async def copilot_chat_stream(request: Request):
    """Stream responses for CopilotKit"""
    data = await request.json()
    messages = data.get("messages", [])
    
    # Extract the latest user message
    user_message = next((msg["content"] for msg in reversed(messages) 
                         if msg["role"] == "user"), "")
    
    if not user_message:
        return StreamingResponse(stream_empty_response())
    
    # Prefer WizardAgency
    response = await agency.get_response(user_message)
    # Normalize to plain text
    try:
        if hasattr(response, 'final_output') and isinstance(response.final_output, str):
            response_text = response.final_output
        elif isinstance(response, str):
            response_text = response
        else:
            response_text = str(response)
    except Exception:
        response_text = str(response)
    
    async def stream_response():
        # Simulate streaming by yielding chunks of the response
        chunks = [response_text[i:i+10] for i in range(0, len(response_text), 10)]
        
        for i, chunk in enumerate(chunks):
            yield f"data: {json.dumps({'id': f'chunk-{i}', 'chunk': chunk, 'done': i == len(chunks)-1})}\n\n"
            await asyncio.sleep(0.05)  # Small delay between chunks
    
    return StreamingResponse(stream_response(), media_type="text/event-stream")

async def stream_empty_response():
    yield f"data: {json.dumps({'id': 'empty', 'chunk': '', 'done': True})}\n\n"

@app.websocket("/api/copilot/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            data_json = json.loads(data)
            
            # Extract message from the websocket data
            message = data_json.get("message", "")
            if not message:
                await websocket.send_json({"error": "No message provided"})
                continue
            
            # Process via LangGraph agentic chat using the single message
            response = await agency.get_response(message)
            # Normalize to plain text
            try:
                if hasattr(response, 'final_output') and isinstance(response.final_output, str):
                    response_text = response.final_output
                elif isinstance(response, str):
                    response_text = response
                else:
                    response_text = str(response)
            except Exception:
                response_text = str(response)
            
            # Send response back through websocket
            await websocket.send_json({
                "id": data_json.get("id", "response"),
                "response": response_text,
                "done": True
            })
    except WebSocketDisconnect:
        pass