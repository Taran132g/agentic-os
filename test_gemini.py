import asyncio
import logging
import sys
from pathlib import Path

# Add the project root to sys.path so we can import tools
sys.path.append(str(Path(__file__).parent))

from tools.llm import run_llm_command, set_preferred_provider

async def test_gemini_forced():
    logging.basicConfig(level=logging.INFO)
    
    print("--- Testing Forced Gemini ---")
    set_preferred_provider("gemini")
    
    # We use a simple prompt. 
    # We pass a dummy send_telegram to avoid errors.
    async def dummy_tg(text):
        print(f"[Telegram Mock]: {text[:50]}...")

    result = await run_llm_command(
        prompt="Reply with exactly 'GEMINI_TEST_SUCCESS'",
        send_telegram=dummy_tg
    )
    
    print("\n--- Results ---")
    print(f"Provider Used: {result.get('provider')}")
    print(f"Success: {result.get('success')}")
    print(f"Result Text: {result.get('result').strip()}")
    
    if result.get('provider') == 'gemini' and 'GEMINI_TEST_SUCCESS' in result.get('result'):
        print("\n✅ TEST PASSED: Gemini was forced and returned the correct output.")
    else:
        print("\n❌ TEST FAILED.")

if __name__ == "__main__":
    asyncio.run(test_gemini_forced())
