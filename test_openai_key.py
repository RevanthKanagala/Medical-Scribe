"""Test script to verify OpenAI API key is working."""
import os
from dotenv import load_dotenv

# Force reload environment variables
load_dotenv(override=True)

def test_openai_key():
    """Test if the OpenAI API key is valid and working."""
    
    # Get API key from environment
    api_key = os.getenv('OPENAI_API_KEY')
    
    if not api_key:
        print("❌ ERROR: OPENAI_API_KEY not found in .env file")
        return False
    
    print(f"✓ API key found in .env")
    print(f"  Length: {len(api_key)} characters")
    print(f"  Starts with: {api_key[:10]}...")
    print(f"  Ends with: ...{api_key[-4:]}")
    print()
    
    # Test the API key with OpenAI
    try:
        import openai as o
        print("Testing API key with OpenAI...")
        
        client = o.OpenAI(api_key=api_key)
        
        # Make a simple test call
        response = client.chat.completions.create(
            model='gpt-3.5-turbo',
            messages=[
                {"role": "user", "content": "Say 'API key is working!'"}
            ],
            max_tokens=10
        )
        
        result = response.choices[0].message.content
        print(f"✅ SUCCESS! OpenAI API key is VALID and working!")
        print(f"   Response: {result}")
        print()
        return True
        
    except Exception as e:
        print(f"❌ ERROR: API key test failed!")
        print(f"   Error: {str(e)}")
        print()
        return False

if __name__ == "__main__":
    print("="*60)
    print("OpenAI API Key Validation Test")
    print("="*60)
    print()
    
    success = test_openai_key()
    
    if success:
        print("="*60)
        print("✅ All tests passed! Your API key is ready to use.")
        print("="*60)
    else:
        print("="*60)
        print("❌ Test failed. Please check your API key in .env file")
        print("="*60)
