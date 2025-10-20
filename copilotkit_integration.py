"""
CopilotKit integration module for the Truva application.
This module provides the necessary functions and classes to integrate with CopilotKit.
"""

from typing import List, Dict, Any, Optional
import asyncio
from agency import create_agency

class CopilotKitIntegration:
    """CopilotKit integration class for the Truva application."""
    
    def __init__(self):
        """Initialize the CopilotKit integration."""
        self.agency = create_agency()
    
    async def process_message(self, message: str) -> str:
        """
        Process a message using the agency.
        
        Args:
            message: The message to process.
            
        Returns:
            The response from the agency.
        """
        return await self.agency.get_response(message)
    
    async def process_chat(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Process a chat using the agency.
        
        Args:
            messages: The chat messages to process.
            
        Returns:
            The response from the agency.
        """
        # Extract the latest user message
        user_message = next((msg["content"] for msg in reversed(messages) 
                            if msg["role"] == "user"), "")
        
        if not user_message:
            return {"response": "No user message found", "done": True}
        
        # Process with agency
        response = await self.agency.get_response(user_message)
        
        return {
            "id": f"response-{len(messages)}",
            "response": response,
            "done": True
        }
    
    async def stream_response(self, response: str):
        """
        Stream a response in chunks.
        
        Args:
            response: The response to stream.
            
        Yields:
            Chunks of the response.
        """
        # Simulate streaming by yielding chunks of the response
        chunks = [response[i:i+10] for i in range(0, len(response), 10)]
        
        for i, chunk in enumerate(chunks):
            yield {
                "id": f"chunk-{i}",
                "chunk": chunk,
                "done": i == len(chunks) - 1
            }
            await asyncio.sleep(0.05)  # Small delay between chunks

# Create a singleton instance
copilot_integration = CopilotKitIntegration()