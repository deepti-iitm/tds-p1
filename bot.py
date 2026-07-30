import os
import json
import logging
import asyncio
import config
from fastapi import FastAPI
from fastapi.responses import FileResponse
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from openai import OpenAI

# 1. Setup Basic Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LOG_FILE_PATH = "run.jsonl"
app = FastAPI()

api_key_value = os.getenv("AIPIPE_TOKEN") or os.getenv("OPENAI_API_KEY")
# 2. Initialize the OpenAI client pointing at AIPipe
client = OpenAI(
    base_url="https://aipipe.org/openrouter/v1/",
    api_key=api_key_value
)

    
def append_to_log(user_input: str, output_data: dict):
    """Appends an execution transaction line into the JSONL log file."""
    log_entry = {
        "user_message": user_input,
        "bot_response": output_data
    }
    with open(LOG_FILE_PATH, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

def extract_code(text: str) -> str:
    """Extracts raw python code blocks from markdown structures."""
    if "```python" in text:
        return text.split("```python")[1].split("```")[0].strip()
    elif "```" in text:
        return text.split("```")[1].split("```")[0].strip()
    return text.strip()

def execute_pandas_code(code_string: str):
    """Executes the AI-generated code securely and extracts the tracking target variable."""
    local_vars = {}
    try:
        # The prompt explicitly mandates saving the target answer array inside a dictionary/variable named 'result'
        exec(code_string, {}, local_vars)
        return local_vars.get("result", "Error: Variable 'result' not found in script.")
    except Exception as e:
        return f"Execution Error: {str(e)}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    
    # Check if there's a historical conversational context to pass to the agent
    chat_history = context.user_data.get("history", [])
    chat_history.append({"role": "user", "content": user_message})
    
    system_prompt = (
        "You are an expert data analyst bot. Write a standalone Python script using pandas "
        "to answer the user's data query. If a link to a public dataset (like a CSV file) "
        "is present in the context, your script must download it using pandas.read_csv() "
        "or requests. Solve the problem and store the final answer in a dictionary or variable "
        "named exactly 'result' matching the exact JSON format/shape requested by the user. "
        "Output ONLY executable python code inside a single standard triple-backtick block: ```python ... ```."
    )
    
    messages = [{"role": "system", "content": system_prompt}] + chat_history
    
    try:
        # Replace the model name string with your assigned course evaluation model if needed
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini", 
            messages=messages
        )
        
        llm_output = response.choices.message.content
        chat_history.append({"role": "assistant", "content": llm_output})
        context.user_data["history"] = chat_history
        
        python_code = extract_code(llm_output)
        calculated_answer = execute_pandas_code(python_code)
        
    except Exception as err:
        calculated_answer = f"Agent processing failed: {str(err)}"

    # Capture Render's dynamic host configuration for the evaluation pipeline
    host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "://onrender.com")
    
    response_json = {
        "answer": calculated_answer, 
        "log_url": f"https://{host}/run.jsonl"
    }
    
    # Write transactions to file system storage
    append_to_log(user_message, response_json)
    
    await update.message.reply_text(json.dumps(response_json))

@app.get("/run.jsonl")
def get_log():
    """Serves the data analysis run log dynamically for evaluation grading checks."""
    if os.path.exists(LOG_FILE_PATH):
        return FileResponse(LOG_FILE_PATH, media_type="application/x-jsonlines")
    return {"error": "Log file not generated yet"}

@app.on_event("startup")
async def start_bot():
    """Asynchronously setups and attaches polling routines inside FastAPI runtime instance."""
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        logger.error("TELEGRAM_TOKEN variable missing from environment settings.")
        return
        
    bot_app = Application.builder().token(token).build()
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await bot_app.initialize()
    await bot_app.start()
    asyncio.create_task(bot_app.updater.start_polling())
    logger.info("Telegram Bot routine successfully attached and polling active.")
