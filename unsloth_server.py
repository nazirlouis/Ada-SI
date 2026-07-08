"""
Local Unsloth server that mimics OpenAI API format.
Run this in a separate terminal: python unsloth_server.py

This provides unlimited local LLM inference as a fallback when Groq rate limits are hit.
"""
import json
import logging
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

import torch
from unsloth import FastLanguageModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Model configuration
MODEL_NAME = os.environ.get("UNSLOTH_MODEL", "unsloth/Llama-3.2-1B-Instruct")
MAX_SEQ_LENGTH = int(os.environ.get("UNSLOTH_MAX_SEQ", "2048"))
LOAD_IN_4BIT = os.environ.get("UNSLOTH_4BIT", "true").lower() == "true"

logger.info(f"Loading model: {MODEL_NAME}")
logger.info(f"Max sequence length: {MAX_SEQ_LENGTH}")
logger.info(f"4-bit quantization: {LOAD_IN_4BIT}")

try:
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_NAME,
        max_seq_length=MAX_SEQ_LENGTH,
        dtype=None,
        load_in_4bit=LOAD_IN_4BIT,
    )
    logger.info("✅ Model loaded successfully")
except Exception as e:
    logger.error(f"❌ Failed to load model: {e}")
    raise


class UnslothHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible chat completion handler for local Unsloth inference."""

    def do_POST(self):
        """Handle POST requests to /v1/chat/completions endpoint."""
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return

        try:
            # Read request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            messages = body.get("messages", [])
            temperature = body.get("temperature", 0.7)
            max_tokens = body.get("max_tokens", 512)

            if not messages:
                raise ValueError("No messages provided")

            # Build prompt from messages
            prompt_parts = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    prompt_parts.append(f"System: {content}")
                elif role == "user":
                    prompt_parts.append(f"User: {content}")
                elif role == "assistant":
                    prompt_parts.append(f"Assistant: {content}")

            prompt = "\n".join(prompt_parts) + "\nAssistant:"

            logger.debug(f"Prompt: {prompt[:200]}...")

            # Tokenize and generate
            inputs = tokenizer.encode(prompt, return_tensors="pt")
            
            with torch.no_grad():
                outputs = model.generate(
                    inputs,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                    top_p=0.95,
                    do_sample=True,
                )

            # Decode response
            full_response = tokenizer.decode(outputs[0], skip_special_tokens=True)
            # Extract only the new tokens (remove prompt)
            response_text = full_response[len(prompt):].strip()

            # OpenAI-compatible response format
            response = {
                "id": "unsloth-local",
                "object": "chat.completion",
                "created": 0,
                "model": "unsloth/local",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": response_text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": len(inputs[0]),
                    "completion_tokens": len(outputs[0]) - len(inputs[0]),
                    "total_tokens": len(outputs[0]),
                },
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

            logger.info(f"✅ Completed request - {response['usage']['total_tokens']} tokens")

        except ValueError as e:
            logger.error(f"Validation error: {e}")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

        except Exception as e:
            logger.error(f"Error processing request: {e}", exc_info=True)
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"error": "Internal server error", "details": str(e)}).encode()
            )

    def log_message(self, format, *args):
        """Override to use our logger instead of stderr."""
        logger.debug(format % args)


def run_server(host="127.0.0.1", port=8765):
    """Start the Unsloth HTTP server."""
    server = HTTPServer((host, port), UnslothHandler)
    logger.info(f"🚀 Unsloth server running at http://{host}:{port}")
    logger.info(f"📝 Use in Ada-SI: Set LITE_MODEL=openai/unsloth-local")
    logger.info(f"⚙️  Configure LiteLLM to route to this server")
    logger.info("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    # Optional: warm up the model
    logger.info("Warming up model...")
    with torch.no_grad():
        test_input = tokenizer.encode("Hello", return_tensors="pt")
        _ = model.generate(test_input, max_new_tokens=10)
    logger.info("Model ready!")

    run_server()
