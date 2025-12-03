"""Simple OpenAI API Test"""
import os
from dotenv import load_dotenv
from pathlib import Path

# Force reload .env file with override=True to ignore cached values
env_path = Path(__file__).parent / '.env'
print(f"Loading .env from: {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

# Get API key
api_key = os.getenv('OPENAI_API_KEY')

if not api_key:
    print("❌ ERROR: OPENAI_API_KEY not found in .env file")
    exit(1)

print(f"✓ API Key found: {api_key[:15]}...{api_key[-4:]}")
print()

# Test OpenAI
try:
    import openai
    
    print("Testing OpenAI API...")
    client = openai.OpenAI(api_key=api_key)
    
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "user", "content": "Say 'Hello! OpenAI is working perfectly!'"}
        ],
        max_tokens=20
    )
    
    result = response.choices[0].message.content
    print(f"✅ SUCCESS!")
    print(f"Response: {result}")
    
except Exception as e:
    print(f"❌ ERROR: {e}")
