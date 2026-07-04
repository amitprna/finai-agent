import json
import os

path = r"c:\Users\kumar\M100\antigravity\genai-bootcamp\live-sessions\langfuse\langfuse_tutorial.ipynb"
with open(path, "r", encoding="utf-8") as f:
    notebook = json.load(f)

for cell in notebook["cells"]:
    if cell["cell_type"] == "code" and any("1. Langfuse Configuration (defaults)" in line for line in cell["source"]):
        cell["source"] = [
            "import os\n",
            "from dotenv import load_dotenv\n",
            "\n",
            "# Try to load local .env, fallback to the sibling folder's .env if present\n",
            "if os.path.exists(\".env\"):\n",
            "    load_dotenv(override=True)\n",
            "elif os.path.exists(\"../finai-agent/.env\"):\n",
            "    load_dotenv(\"../finai-agent/.env\", override=True)\n",
            "\n",
            "# 1. Langfuse Configuration (defaults)\n",
            "# These will be loaded from your .env file, or you can uncomment and set them here.\n",
            "LANGFUSE_SECRET_KEY=\"sk-lf-bcf06236-177d-435c-846c-ec68ef640170\"\n",
            "LANGFUSE_PUBLIC_KEY=\"pk-lf-7ab7c3af-bcaa-4cba-98f6-dc55b9bb30ff\"\n",
            "LANGFUSE_HOST=\"https://us.cloud.langfuse.com\"\n",
            "\n",
            "# Ensure keys are loaded into environment variables for LiteLLM/Langfuse SDK to read\n",
            "if not os.getenv(\"LANGFUSE_PUBLIC_KEY\"):\n",
            "    os.environ[\"LANGFUSE_PUBLIC_KEY\"] = LANGFUSE_PUBLIC_KEY\n",
            "if not os.getenv(\"LANGFUSE_SECRET_KEY\"):\n",
            "    os.environ[\"LANGFUSE_SECRET_KEY\"] = LANGFUSE_SECRET_KEY\n",
            "if not os.getenv(\"LANGFUSE_HOST\"):\n",
            "    os.environ[\"LANGFUSE_HOST\"] = LANGFUSE_HOST\n",
            "\n",
            "# 2. OpenAI Dummy Key\n",
            "# LiteLLM/Agents SDK may require an OpenAI API key to bypass initialization checks.\n",
            "# We override it to use a dummy key to prevent 401 errors.\n",
            "os.environ[\"OPENAI_API_KEY\"] = \"dummy-key-for-agents-sdk\"\n",
            "\n",
            "# 3. AWS Bedrock Config\n",
            "os.environ.setdefault(\"BEDROCK_REGION\", \"us-east-1\")\n",
            "os.environ.setdefault(\"AWS_REGION_NAME\", \"us-east-1\")\n",
            "\n",
            "print(\"✅ Configuration variables loaded!\")\n",
            "print(\"LANGFUSE_PUBLIC_KEY:\", os.getenv(\"LANGFUSE_PUBLIC_KEY\"))\n",
            "print(\"LANGFUSE_HOST:\", os.getenv(\"LANGFUSE_HOST\"))"
        ]
        break

with open(path, "w", encoding="utf-8") as f:
    json.dump(notebook, f, indent=1, ensure_ascii=False)

print("Update completed successfully!")
