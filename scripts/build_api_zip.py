#!/usr/bin/env python3
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
API_DIR = ROOT / "backend" / "api"
DB_DIR = ROOT / "backend" / "database"
PLANNER_DIR = ROOT / "backend" / "planner"

def build_zip():
    print("Building api_lambda.zip...")
    
    # Create temp directory
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        
        # Install api dependencies to temp directory using uv
        print("Installing dependencies...")
        subprocess.run([
            "uv", "pip", "install",
            "--target", str(temp_dir),
            "--python-platform", "x86_64-manylinux2014",
            "--python-version", "3.12",
            "--only-binary=:all:",
            "fastapi>=0.116.1",
            "mangum>=0.19.0",
            "python-jose[cryptography]>=3.5.0",
            "python-dotenv>=1.1.1",
            "httpx>=0.28.1",
            "boto3>=1.40.29",
            "pydantic>=2.11.7",
            "uvicorn>=0.35.0"
        ], check=True)
        
        # Copy source files
        # 1. lambda_handler.py -> root
        shutil.copy2(API_DIR / "lambda_handler.py", temp_dir / "lambda_handler.py")
        
        # 2. api/main.py and api/__init__.py
        (temp_dir / "api").mkdir(exist_ok=True)
        shutil.copy2(API_DIR / "main.py", temp_dir / "api" / "main.py")
        (temp_dir / "api" / "__init__.py").touch()
        
        # 3. src/ (database package contents) -> src/
        (temp_dir / "src").mkdir(exist_ok=True)
        for f in (DB_DIR / "src").glob("*.py"):
            shutil.copy2(f, temp_dir / "src" / f.name)
            
        # 4. planner/ (for prices.py) -> planner/
        (temp_dir / "planner").mkdir(exist_ok=True)
        shutil.copy2(PLANNER_DIR / "prices.py", temp_dir / "planner" / "prices.py")
        (temp_dir / "planner" / "__init__.py").touch()
        
        # Zip everything up
        zip_path = API_DIR / "api_lambda.zip"
        if zip_path.exists():
            zip_path.unlink()
            
        print(f"Creating zip at {zip_path}...")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(temp_dir)
                    z.write(file_path, arcname)
                    
    print("api_lambda.zip created successfully [DONE]")

if __name__ == "__main__":
    build_zip()
