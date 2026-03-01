import asyncio
import sys
from pathlib import Path
from forge.runner import Runner
from forge.config import ForgeConfig

async def main():
    config = ForgeConfig(target_project=Path(r"C:\Users\domin\spectre"))
    runner = Runner(config)
    
    prompt = Path(r"C:\Users\domin\forge\forge\.forge_data\deep_think_prompt.txt").read_text(encoding="utf-8")
    
    print("Running Deep Think verification (this will take a few minutes)...")
    try:
        result = runner.run_deep_think(prompt, timeout=900)
        out_path = Path(r"C:\Users\domin\forge\forge\.forge_data\manual_dt_verification.md")
        out_path.write_text(result, encoding="utf-8")
        print(f"Success! Saved to {out_path}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
